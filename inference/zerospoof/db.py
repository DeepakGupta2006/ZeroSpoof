"""Relational storage (PostgreSQL in production, SQLite by default) via SQLAlchemy Core.

Tables
  api_keys    hashed API keys (admin / customer)
  webhooks    customer webhook endpoints + signing secrets
  sessions    one row per liveness session (final verdict and summary)
  audit_log   APPEND-ONLY, hash-chained: each row stores sha256(prev_hash || canonical row).
              UPDATE/DELETE are blocked by database triggers, and `verify_chain()` detects tampering.
  usage_events one row per completed check, for billing (per 1,000 checks)
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from typing import Any

import sqlalchemy as sa

from .config import settings

metadata = sa.MetaData()

api_keys = sa.Table(
    "api_keys", metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("name", sa.String(120), nullable=False),
    sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
    sa.Column("role", sa.String(16), nullable=False, default="customer"),
    sa.Column("created_at", sa.Float, nullable=False),
    sa.Column("active", sa.Boolean, nullable=False, default=True),
)
webhooks = sa.Table(
    "webhooks", metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("key_id", sa.String(32), nullable=False, index=True),
    sa.Column("url", sa.Text, nullable=False),
    sa.Column("secret", sa.String(80), nullable=False),
    sa.Column("events", sa.Text, nullable=False),  # JSON list
    sa.Column("active", sa.Boolean, nullable=False, default=True),
)
sessions = sa.Table(
    "sessions", metadata,
    sa.Column("id", sa.String(40), primary_key=True),
    sa.Column("key_id", sa.String(32), nullable=False, index=True),
    sa.Column("external_id", sa.String(200)),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("verdict", sa.String(16), nullable=False),
    sa.Column("probability", sa.Float),
    sa.Column("created_at", sa.Float, nullable=False, index=True),
    sa.Column("finished_at", sa.Float),
    sa.Column("summary", sa.Text),  # JSON: modules, flags, fingerprint
)
audit_log = sa.Table(
    "audit_log", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("ts", sa.Float, nullable=False),
    sa.Column("session_id", sa.String(40), index=True),
    sa.Column("key_id", sa.String(32)),
    sa.Column("event", sa.String(60), nullable=False),
    sa.Column("payload", sa.Text, nullable=False),
    sa.Column("prev_hash", sa.String(64), nullable=False),
    sa.Column("hash", sa.String(64), nullable=False),
)
usage_events = sa.Table(
    "usage_events", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("key_id", sa.String(32), nullable=False, index=True),
    sa.Column("ts", sa.Float, nullable=False),
    sa.Column("session_id", sa.String(40), nullable=False),
    sa.Column("verdict", sa.String(16), nullable=False),
)

GENESIS = "0" * 64


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _canonical(ts: float, session_id: str | None, key_id: str | None, event: str, payload: str, prev: str) -> str:
    return json.dumps([ts, session_id, key_id, event, payload, prev], separators=(",", ":"), sort_keys=True)


class Database:
    def __init__(self, url: str) -> None:
        kwargs: dict[str, Any] = {"future": True}
        # Accept the usual postgres:// / postgresql:// URLs and use the psycopg 3 driver for them.
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        self.engine = sa.create_engine(url, **kwargs)
        self.dialect = self.engine.dialect.name
        self._chain_lock = threading.Lock()

    # -------------------------------------------------------------- setup
    def init(self) -> None:
        metadata.create_all(self.engine)
        with self.engine.begin() as c:
            if self.dialect == "sqlite":
                for op in ("UPDATE", "DELETE"):
                    c.exec_driver_sql(
                        f"CREATE TRIGGER IF NOT EXISTS audit_no_{op.lower()} BEFORE {op} ON audit_log "
                        f"BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;"
                    )
            elif self.dialect == "postgresql":
                c.exec_driver_sql(
                    "CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger AS $$ "
                    "BEGIN RAISE EXCEPTION 'audit_log is append-only'; END; $$ LANGUAGE plpgsql;"
                )
                c.exec_driver_sql("DROP TRIGGER IF EXISTS audit_no_change ON audit_log;")
                c.exec_driver_sql(
                    "CREATE TRIGGER audit_no_change BEFORE UPDATE OR DELETE ON audit_log "
                    "FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();"
                )

    def seed_dev_keys(self) -> None:
        """Dev convenience: make sure the well-known dev keys exist. Disabled when ZS_ENV=prod."""
        if settings.env == "prod":
            return
        self.ensure_key("admin", settings.admin_key, "admin")
        self.ensure_key("dev customer", settings.dev_api_key, "customer")

    # -------------------------------------------------------------- keys
    def ensure_key(self, name: str, raw: str, role: str) -> str:
        h = hash_key(raw)
        with self.engine.begin() as c:
            row = c.execute(sa.select(api_keys.c.id).where(api_keys.c.key_hash == h)).first()
            if row:
                return row[0]
            kid = secrets.token_hex(8)
            c.execute(api_keys.insert().values(id=kid, name=name, key_hash=h, role=role, created_at=time.time(), active=True))
            return kid

    def create_key(self, name: str, role: str = "customer") -> tuple[str, str]:
        raw = f"zs_{'live' if role == 'customer' else 'admin'}_{secrets.token_urlsafe(24)}"
        return self.ensure_key(name, raw, role), raw

    def lookup_key(self, raw: str) -> dict[str, Any] | None:
        with self.engine.connect() as c:
            row = c.execute(sa.select(api_keys).where(api_keys.c.key_hash == hash_key(raw), api_keys.c.active.is_(True))).first()
        return dict(row._mapping) if row else None

    # -------------------------------------------------------------- audit chain
    def audit(self, event: str, session_id: str | None = None, key_id: str | None = None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = json.dumps(payload or {}, separators=(",", ":"), sort_keys=True, default=str)
        ts = round(time.time(), 6)
        with self._chain_lock, self.engine.begin() as c:
            if self.dialect == "postgresql":
                c.exec_driver_sql("SELECT pg_advisory_xact_lock(727001)")
            last = c.execute(sa.select(audit_log.c.hash).order_by(audit_log.c.id.desc()).limit(1)).first()
            prev = last[0] if last else GENESIS
            h = hashlib.sha256((prev + _canonical(ts, session_id, key_id, event, body, prev)).encode()).hexdigest()
            res = c.execute(audit_log.insert().values(ts=ts, session_id=session_id, key_id=key_id, event=event, payload=body, prev_hash=prev, hash=h))
            return {"id": res.inserted_primary_key[0], "hash": h}

    def audit_for_session(self, session_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as c:
            rows = c.execute(sa.select(audit_log).where(audit_log.c.session_id == session_id).order_by(audit_log.c.id)).all()
        return [{**dict(r._mapping), "payload": json.loads(r.payload)} for r in rows]

    def verify_chain(self) -> dict[str, Any]:
        """Recompute every hash. Returns {ok, checked, first_bad_id}."""
        prev = GENESIS
        n = 0
        with self.engine.connect() as c:
            for r in c.execute(sa.select(audit_log).order_by(audit_log.c.id)):
                expect = hashlib.sha256((prev + _canonical(r.ts, r.session_id, r.key_id, r.event, r.payload, prev)).encode()).hexdigest()
                if r.prev_hash != prev or r.hash != expect:
                    return {"ok": False, "checked": n, "first_bad_id": r.id}
                prev = r.hash
                n += 1
        return {"ok": True, "checked": n, "first_bad_id": None}

    # -------------------------------------------------------------- sessions
    def upsert_session(self, sid: str, **vals: Any) -> None:
        if "summary" in vals and not isinstance(vals["summary"], str):
            vals["summary"] = json.dumps(vals["summary"], default=str)
        with self.engine.begin() as c:
            exists = c.execute(sa.select(sessions.c.id).where(sessions.c.id == sid)).first()
            if exists:
                c.execute(sessions.update().where(sessions.c.id == sid).values(**vals))
            else:
                c.execute(sessions.insert().values(id=sid, **vals))

    def get_session(self, sid: str) -> dict[str, Any] | None:
        with self.engine.connect() as c:
            row = c.execute(sa.select(sessions).where(sessions.c.id == sid)).first()
        return self._session_row(row) if row else None

    def list_sessions(self, key_id: str | None, limit: int = 50) -> list[dict[str, Any]]:
        q = sa.select(sessions).order_by(sessions.c.created_at.desc()).limit(limit)
        if key_id:
            q = q.where(sessions.c.key_id == key_id)
        with self.engine.connect() as c:
            return [self._session_row(r) for r in c.execute(q)]

    @staticmethod
    def _session_row(row) -> dict[str, Any]:
        d = dict(row._mapping)
        d["summary"] = json.loads(d["summary"]) if d.get("summary") else {}
        return d

    # -------------------------------------------------------------- webhooks
    def add_webhook(self, key_id: str, url: str, events: list[str]) -> dict[str, Any]:
        wid, secret = secrets.token_hex(8), "whsec_" + secrets.token_urlsafe(24)
        with self.engine.begin() as c:
            c.execute(webhooks.insert().values(id=wid, key_id=key_id, url=url, secret=secret, events=json.dumps(events), active=True))
        return {"id": wid, "url": url, "events": events, "secret": secret}

    def list_webhooks(self, key_id: str, with_secret: bool = False) -> list[dict[str, Any]]:
        with self.engine.connect() as c:
            rows = c.execute(sa.select(webhooks).where(webhooks.c.key_id == key_id, webhooks.c.active.is_(True))).all()
        out = []
        for r in rows:
            d = {"id": r.id, "url": r.url, "events": json.loads(r.events)}
            if with_secret:
                d["secret"] = r.secret
            out.append(d)
        return out

    def delete_webhook(self, key_id: str, wid: str) -> bool:
        with self.engine.begin() as c:
            res = c.execute(webhooks.update().where(webhooks.c.id == wid, webhooks.c.key_id == key_id).values(active=False))
            return res.rowcount > 0

    # -------------------------------------------------------------- usage
    def record_usage(self, key_id: str, session_id: str, verdict: str) -> None:
        with self.engine.begin() as c:
            c.execute(usage_events.insert().values(key_id=key_id, ts=time.time(), session_id=session_id, verdict=verdict))

    def usage_since(self, key_id: str | None, since_ts: float) -> dict[str, int]:
        q = sa.select(usage_events.c.verdict, sa.func.count()).where(usage_events.c.ts >= since_ts).group_by(usage_events.c.verdict)
        if key_id:
            q = q.where(usage_events.c.key_id == key_id)
        with self.engine.connect() as c:
            return {v: int(n) for v, n in c.execute(q)}
