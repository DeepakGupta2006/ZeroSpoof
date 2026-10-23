"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type Verdict = "pending" | "pass" | "review" | "fail";
interface Mod { score: number; confidence: number; available: boolean; details?: Record<string, unknown> }
interface Flag { module: string; code: string; severity: string; message: string }
interface Fp { device_id?: string; camera?: string; resolution?: string; frame_rate?: number; platform?: string; user_agent?: string; timezone?: string }
interface Sess {
  id: string; external_id?: string | null; status: string; verdict: Verdict; probability?: number | null;
  created_at: number; finished_at?: number | null; elapsed_s?: number;
  modules?: Record<string, Mod>; flags?: Flag[]; reasons?: string[]; fingerprint?: Fp | null;
  latency_ms?: { last: number; p50: number };
  timeline?: Array<Record<string, number | string>>;
}
interface Usage { checks: number; by_verdict: Record<string, number>; estimated_total_usd: number; included_checks: number; base_price_usd: number }
interface AuditRow { id: number; ts: number; event: string; hash: string; prev_hash: string; payload: Record<string, unknown> }

const MODULE_COLORS: Record<string, string> = {
  p: "#e6ebf5", rppg: "#ff7a90", moire: "#5b9dff", depth: "#b48cff", audio: "#3ccf91", fingerprint: "#f2b84b", stream: "#4dd0e1", challenge: "#ff9f5a",
};

function ago(ts: number) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

function Chip({ v }: { v: string }) {
  return <span className={`chip ${v}`}>{v}</span>;
}

function Timeline({ points }: { points: Array<Record<string, number | string>> }) {
  const W = 560, H = 190, pad = { l: 30, r: 8, t: 8, b: 20 };
  const keys = useMemo(() => {
    const s = new Set<string>();
    points.forEach((p) => Object.keys(p).forEach((k) => { if (k !== "t" && k !== "v") s.add(k); }));
    return ["p", ...[...s].filter((k) => k !== "p")];
  }, [points]);
  if (points.length < 2) return <div className="empty">Timeline appears once the session has streamed for a few seconds.</div>;
  const tMax = Math.max(...points.map((p) => Number(p.t))) || 1;
  const x = (t: number) => pad.l + (t / tMax) * (W - pad.l - pad.r);
  const y = (v: number) => pad.t + (1 - v) * (H - pad.t - pad.b);
  return (
    <div className="timeline">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Score timeline">
        {[0, 0.25, 0.5, 0.75, 1].map((g) => (
          <g key={g}>
            <line x1={pad.l} x2={W - pad.r} y1={y(g)} y2={y(g)} stroke="#263044" strokeDasharray={g === 0 || g === 1 ? "" : "3 4"} />
            <text x={pad.l - 6} y={y(g) + 4} fill="#8b98b2" fontSize="10" textAnchor="end">{g}</text>
          </g>
        ))}
        <line x1={pad.l} x2={W - pad.r} y1={y(0.7)} y2={y(0.7)} stroke="#3ccf91" strokeOpacity=".5" strokeDasharray="2 3" />
        <line x1={pad.l} x2={W - pad.r} y1={y(0.4)} y2={y(0.4)} stroke="#ff5d6c" strokeOpacity=".5" strokeDasharray="2 3" />
        {keys.map((k) => {
          const pts = points.filter((p) => typeof p[k] === "number");
          if (pts.length < 2) return null;
          const d = pts.map((p, i) => `${i ? "L" : "M"}${x(Number(p.t)).toFixed(1)},${y(Number(p[k])).toFixed(1)}`).join(" ");
          return <path key={k} d={d} fill="none" stroke={MODULE_COLORS[k] ?? "#8b98b2"} strokeWidth={k === "p" ? 2.6 : 1.4} strokeOpacity={k === "p" ? 1 : 0.8} />;
        })}
        <text x={W - pad.r} y={H - 5} fill="#8b98b2" fontSize="10" textAnchor="end">seconds</text>
      </svg>
      <div className="legend">
        {keys.map((k) => (
          <span key={k}><i style={{ background: MODULE_COLORS[k] ?? "#8b98b2" }} />{k === "p" ? "fused" : k}</span>
        ))}
      </div>
    </div>
  );
}

function Detail({ id }: { id: string }) {
  const [s, setS] = useState<Sess | null>(null);
  const [audit, setAudit] = useState<AuditRow[] | null>(null);
  const [chain, setChain] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setS(null); setAudit(null); setChain(null);
    const tick = async () => {
      try {
        const r = await fetch(`/api/sessions/${id}`, { cache: "no-store" });
        const b = await r.json();
        if (!alive) return;
        if (!r.ok) return setErr(b.detail ?? "error");
        setErr(null); setS(b);
      } catch { if (alive) setErr("Cannot reach the dashboard API"); }
    };
    tick();
    const h = setInterval(tick, 1000);
    return () => { alive = false; clearInterval(h); };
  }, [id]);

  const loadAudit = async () => {
    const r = await fetch(`/api/sessions/${id}/audit`, { cache: "no-store" });
    const b = await r.json();
    setAudit(b.data ?? []);
  };
  const verify = async () => {
    const r = await fetch("/api/audit-verify", { cache: "no-store" });
    const b = await r.json();
    setChain(b.ok ? `Chain intact: ${b.checked} entries verified` : `TAMPERING DETECTED at entry #${b.first_bad_id}`);
  };

  if (err && !s) return <div className="card"><div className="banner-err">{err}</div></div>;
  if (!s) return <div className="card"><div className="empty">Loading…</div></div>;
  const fp = s.fingerprint;

  return (
    <div className="detail">
      <div className="card">
        <div className="detail-head">
          <Chip v={s.verdict} />
          <span className="p">{typeof s.probability === "number" ? s.probability.toFixed(2) : "–"}</span>
          <span className="muted">fused liveness probability</span>
          <span style={{ flex: 1 }} />
          <Chip v={s.status} />
        </div>
        <dl className="kv" style={{ marginTop: 12 }}>
          <dt>Session</dt><dd className="mono">{s.id}</dd>
          {s.external_id && (<><dt>Your reference</dt><dd>{s.external_id}</dd></>)}
          <dt>Started</dt><dd>{new Date(s.created_at * 1000).toLocaleString()} ({ago(s.created_at)})</dd>
          <dt>Streamed</dt><dd>{s.elapsed_s ?? 0}s{s.latency_ms ? `, decision latency ${s.latency_ms.last} ms (median ${s.latency_ms.p50} ms)` : ""}</dd>
        </dl>
        {s.reasons && s.reasons.length > 0 && (
          <ul style={{ margin: "12px 0 0", paddingLeft: 18 }} className="muted">
            {s.reasons.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        )}
      </div>

      <div className="card">
        <h3>Score timeline</h3>
        <Timeline points={s.timeline ?? []} />
      </div>

      <div className="card">
        <h3>Modules</h3>
        <table>
          <thead><tr><th>Module</th><th>Score</th><th>Confidence</th><th>Note</th></tr></thead>
          <tbody>
            {Object.entries(s.modules ?? {}).map(([name, m]) => (
              <tr key={name}>
                <td><i style={{ display: "inline-block", width: 9, height: 9, borderRadius: 3, marginRight: 8, background: MODULE_COLORS[name] ?? "#8b98b2" }} />{name}</td>
                <td className="num">{m.available ? m.score.toFixed(2) : "n/a"}</td>
                <td className="num">{m.confidence.toFixed(2)}</td>
                <td className="muted">{m.available ? "" : String(m.details?.reason ?? "not enough data yet")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <h3>Threat alerts ({s.flags?.length ?? 0})</h3>
        {(s.flags ?? []).length === 0 && <div className="muted">No flags raised.</div>}
        {(s.flags ?? []).map((f, i) => (
          <div key={i} className={`flag ${f.severity}`}>
            <small>{f.severity}</small><span>{f.message}</span><small style={{ marginLeft: "auto" }}>{f.module}/{f.code}</small>
          </div>
        ))}
      </div>

      <div className="card">
        <h3>Device fingerprint</h3>
        {fp ? (
          <dl className="kv">
            <dt>Device id</dt><dd className="mono">{fp.device_id}</dd>
            <dt>Camera</dt><dd>{fp.camera}</dd>
            <dt>Resolution</dt><dd>{fp.resolution} @ {fp.frame_rate ?? "?"} fps</dd>
            <dt>Platform</dt><dd>{fp.platform}</dd>
            <dt>Timezone</dt><dd>{fp.timezone}</dd>
            <dt>User agent</dt><dd className="muted">{fp.user_agent}</dd>
          </dl>
        ) : <div className="muted">No fingerprint received yet.</div>}
      </div>

      <div className="card">
        <h3>Tamper-evident audit trail</h3>
        <div className="row-actions">
          <button className="btn small" onClick={loadAudit}>Show entries</button>
          <button className="btn small" onClick={verify}>Verify hash chain</button>
          {chain && <span className={chain.startsWith("TAMPER") ? "chip fail" : "chip pass"}>{chain}</span>}
        </div>
        {audit && (
          <div className="audit" style={{ marginTop: 10 }}>
            {audit.map((a) => (
              <div key={a.id}>
                <b>#{a.id} {a.event}</b> · {new Date(a.ts * 1000).toLocaleTimeString()}<br />
                hash {a.hash.slice(0, 20)}… ← prev {a.prev_hash.slice(0, 12)}…
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function Dashboard() {
  const [rows, setRows] = useState<Sess[]>([]);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | Verdict>("all");
  const first = useRef(true);

  const load = useCallback(async () => {
    try {
      const [a, b] = await Promise.all([
        fetch("/api/sessions", { cache: "no-store" }),
        fetch("/api/usage", { cache: "no-store" }),
      ]);
      const ja = await a.json();
      if (!a.ok) throw new Error(ja.detail ?? `HTTP ${a.status}`);
      setRows(ja.data ?? []);
      if (b.ok) setUsage(await b.json());
      setErr(null);
      if (first.current && ja.data?.length) { setSel(ja.data[0].id); first.current = false; }
    } catch (e) {
      setErr(e instanceof Error ? e.message : "failed to load");
    }
  }, []);

  useEffect(() => {
    load();
    const h = setInterval(load, 2000);
    return () => clearInterval(h);
  }, [load]);

  const shown = rows.filter((r) => filter === "all" || r.verdict === filter);
  const count = (v: string) => rows.filter((r) => r.verdict === v).length;
  const active = rows.filter((r) => r.status === "active").length;

  return (
    <main className="container">
      {err && <div className="banner-err">{err}</div>}

      <div className="grid kpis">
        <div className="card kpi"><h3>Sessions (recent)</h3><div className="v">{rows.length}</div><div className="s">{active} live now</div></div>
        <div className="card kpi"><h3>Passed</h3><div className="v" style={{ color: "var(--pass)" }}>{count("pass")}</div></div>
        <div className="card kpi"><h3>Needs review</h3><div className="v" style={{ color: "var(--review)" }}>{count("review")}</div></div>
        <div className="card kpi"><h3>Failed</h3><div className="v" style={{ color: "var(--fail)" }}>{count("fail")}</div></div>
        <div className="card kpi">
          <h3>Usage this month</h3>
          <div className="v">{usage ? usage.checks : "–"}</div>
          <div className="s">{usage ? `est. $${usage.estimated_total_usd} (illustrative pricing)` : ""}</div>
        </div>
      </div>

      <div className="dash">
        <div className="card">
          <div className="row-actions" style={{ marginBottom: 10 }}>
            <h3 style={{ margin: 0 }}>Live sessions</h3>
            <span style={{ flex: 1 }} />
            {(["all", "pass", "review", "fail"] as const).map((f) => (
              <button key={f} className={`btn small ${filter === f ? "primary" : ""}`} onClick={() => setFilter(f)}>{f}</button>
            ))}
          </div>
          {shown.length === 0 ? (
            <div className="empty">
              No sessions yet. Run the <a href="/">live demo</a> or <code>python tools/simulate_client.py</code>.
            </div>
          ) : (
            <table>
              <thead><tr><th>When</th><th>Verdict</th><th>p</th><th>Session</th><th>Status</th></tr></thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={r.id} className={`row ${sel === r.id ? "sel" : ""}`} onClick={() => setSel(r.id)}>
                    <td className="muted">{ago(r.created_at)}</td>
                    <td><Chip v={r.verdict} /></td>
                    <td className="num">{typeof r.probability === "number" ? r.probability.toFixed(2) : "–"}</td>
                    <td className="mono muted">{r.external_id ?? r.id.slice(0, 14)}</td>
                    <td><Chip v={r.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div>{sel ? <Detail id={sel} /> : <div className="card"><div className="empty">Select a session.</div></div>}</div>
      </div>
    </main>
  );
}
