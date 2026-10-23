"use client";

import { useCallback } from "react";
import { LivenessCheck } from "@/sdk";
import type { SessionInfo } from "@/sdk";

export default function Home() {
  // This calls the demo "customer backend" (/api/session), which holds the secret API key.
  const createSession = useCallback(async (): Promise<SessionInfo> => {
    const res = await fetch("/api/session", { method: "POST" });
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail ?? "Could not create a session");
    return body as SessionInfo;
  }, []);

  return (
    <main className="container">
      <section className="hero">
        <div>
          <h1>Is the person on this call a real, live human?</h1>
          <p className="lead">
            ZeroSpoof checks a short camera and microphone sample for replays, deepfakes, virtual cameras and cloned
            voices, and returns <b>pass</b>, <b>review</b> or <b>fail</b>.
          </p>
          <ul className="threats">
            <li><b>Replay / screen</b> moiré and glare analysis</li>
            <li><b>Photo / mask</b> texture and flatness analysis</li>
            <li><b>Face-swap deepfake</b> pulse (rPPG) consistency</li>
            <li><b>Virtual camera</b> device and stream integrity</li>
            <li><b>Voice clone / TTS</b> audio anti-spoofing</li>
          </ul>
          <p className="note">
            <b>Try it:</b> allow the camera, keep your face in the oval, hold still for 10 seconds, then follow the
            head-turn instruction. Open the <a href="/dashboard">dashboard</a> in another tab to watch the session
            appear. Scores are shown below the preview because this build runs in dev mode.
          </p>
        </div>
        <LivenessCheck createSession={createSession} showDebug />
      </section>
    </main>
  );
}
