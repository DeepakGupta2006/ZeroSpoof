"use client";

/**
 * <LivenessCheck /> : the drop-in widget.
 *
 *   <LivenessCheck
 *     createSession={() => fetch("/api/session", { method: "POST" }).then(r => r.json())}
 *     onComplete={(r) => console.log(r.verdict)}
 *   />
 *
 * `createSession` must call YOUR backend, which calls ZeroSpoof's POST /v1/sessions with your secret API key and
 * hands the browser only the returned one-time client token. Never put your API key in the browser.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { LivenessSession } from "./capture";
import type { LivenessOptions, Phase, ServerUpdate, SessionInfo } from "./types";

export interface LivenessCheckProps {
  createSession: () => Promise<SessionInfo>;
  onComplete?: (result: ServerUpdate) => void;
  onError?: (message: string) => void;
  options?: Partial<LivenessOptions>;
  /** show live scores while the check runs (only works if the server exposes them: dev mode) */
  showDebug?: boolean;
}

const CHALLENGE_TEXT: Record<string, string> = {
  turn_left: "Slowly turn your head to YOUR LEFT",
  turn_right: "Slowly turn your head to YOUR RIGHT",
  blink_twice: "Blink twice, slowly",
};

const PHASE_TEXT: Record<Phase, string> = {
  idle: "",
  starting: "Starting camera...",
  prepare: "Look at the camera",
  hold: "Hold still and look straight at the camera",
  challenge: "",
  finishing: "Analysing...",
  done: "Done",
  error: "",
};

export function LivenessCheck({ createSession, onComplete, onError, options, showDebug = false }: LivenessCheckProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const sessionRef = useRef<LivenessSession | null>(null);
  const [consent, setConsent] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [secondsLeft, setSecondsLeft] = useState(0);
  const [challengeType, setChallengeType] = useState<string | null>(null);
  const [face, setFace] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [update, setUpdate] = useState<ServerUpdate | null>(null);
  const [result, setResult] = useState<ServerUpdate | null>(null);

  useEffect(() => () => sessionRef.current?.stop(), []);

  const start = useCallback(async () => {
    setError(null);
    setResult(null);
    setUpdate(null);
    try {
      const info = await createSession();
      setChallengeType(info.challenge?.type ?? null);
      const s = new LivenessSession(info, videoRef.current!, {
        onPhase: (p, i) => {
          setPhase(p);
          setSecondsLeft(i.secondsLeft);
        },
        onUpdate: setUpdate,
        onFinal: (r) => {
          setResult(r);
          onComplete?.(r);
        },
        onFace: (found) => setFace((prev) => (prev === found ? prev : found)),
        onError: (m) => {
          setError(m);
          onError?.(m);
        },
      }, options);
      sessionRef.current = s;
      await s.start();
    } catch (e) {
      const m = e instanceof Error ? e.message : String(e);
      setPhase("error");
      setError(m);
      onError?.(m);
    }
  }, [createSession, onComplete, onError, options]);

  const running = phase !== "idle" && phase !== "done" && phase !== "error";
  const instruction =
    phase === "challenge" && challengeType ? CHALLENGE_TEXT[challengeType] ?? "Follow the instruction" : PHASE_TEXT[phase];

  return (
    <div className="zs-widget">
      <div className="zs-stage">
        <video ref={videoRef} className="zs-video" playsInline muted />
        {!running && phase !== "done" && <div className="zs-stage-empty">{phase === "error" ? "" : "Camera preview appears here"}</div>}
        {running && (
          <div className={`zs-oval ${face ? "ok" : "bad"} ${phase === "challenge" ? "challenge" : ""}`} aria-hidden />
        )}
        {running && (
          <div className="zs-banner" role="status" aria-live="polite">
            <span>{instruction}</span>
            {(phase === "hold" || phase === "challenge" || phase === "prepare") && <b>{secondsLeft}s</b>}
          </div>
        )}
        {running && !face && <div className="zs-hint">Move your face into the oval</div>}
      </div>

      {phase === "idle" || phase === "error" || phase === "done" ? (
        <div className="zs-controls">
          {phase !== "done" && (
            <label className="zs-consent">
              <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
              <span>
                I agree to use my camera and microphone for identity verification. Video and audio are processed on this
                device; only derived signals (colour averages, a small greyscale face crop, audio) are sent, and none of
                it is stored after the check.
              </span>
            </label>
          )}
          {error && <p className="zs-error">{error}</p>}
          {phase !== "done" && (
            <button className="btn primary" disabled={!consent} onClick={start}>
              {phase === "error" ? "Try again" : "Start liveness check"}
            </button>
          )}
          {phase === "done" && (
            <button className="btn" onClick={() => { setPhase("idle"); setResult(null); }}>
              Run another check
            </button>
          )}
        </div>
      ) : null}

      {result && (
        <div className={`zs-result ${result.verdict}`}>
          <strong>
            {result.verdict === "pass" && "Verified: live person"}
            {result.verdict === "review" && "Needs review"}
            {result.verdict === "fail" && "Check failed"}
            {result.verdict === "pending" && "Inconclusive"}
          </strong>
          {typeof result.probability === "number" && <span> liveness probability {result.probability.toFixed(2)}</span>}
        </div>
      )}

      {showDebug && (update || result) && <DebugPanel u={result ?? update!} />}
    </div>
  );
}

function DebugPanel({ u }: { u: ServerUpdate }) {
  return (
    <div className="zs-debug">
      <div className="zs-debug-head">
        <span>verdict: <b>{u.verdict}</b></span>
        {typeof u.probability === "number" && <span>p = {u.probability.toFixed(2)}</span>}
        {u.latency_ms && <span>{u.latency_ms.last} ms</span>}
      </div>
      {u.modules &&
        Object.entries(u.modules).map(([name, m]) => (
          <div key={name} className="zs-bar-row">
            <span>{name}</span>
            <div className="zs-bar"><i style={{ width: `${m.available ? m.score * 100 : 0}%` }} className={m.available ? "" : "off"} /></div>
            <em>{m.available ? m.score.toFixed(2) : "n/a"}</em>
          </div>
        ))}
      {u.modules?.rppg?.available && u.modules.rppg.details && (
        <div className="zs-pulse">
          pulse check: {String(u.modules.rppg.details.bpm)} bpm, signal {String(u.modules.rppg.details.snr_db)} dB, brightness{" "}
          {String(u.modules.rppg.details.mean_luma)}/255 (want 80-200), used: {String(u.modules.rppg.details.segment)}
        </div>
      )}
      {u.flags?.slice(0, 4).map((f, i) => (
        <div key={i} className={`zs-flag ${f.severity}`}>{f.message}</div>
      ))}
    </div>
  );
}

export default LivenessCheck;
