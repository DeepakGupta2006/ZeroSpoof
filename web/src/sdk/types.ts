export type ChallengeType = "turn_left" | "turn_right" | "blink_twice";

/** What your backend returns after calling POST /v1/sessions on ZeroSpoof. */
export interface SessionInfo {
  session_id: string;
  client_token: string;
  ws_url: string;
  challenge: { type: ChallengeType; nonce: string } | null;
  min_seconds: number;
  expires_in: number;
}

export type Phase =
  | "idle"
  | "starting"
  | "prepare"
  | "hold"
  | "challenge"
  | "finishing"
  | "done"
  | "error";

export type Verdict = "pending" | "pass" | "review" | "fail";

export interface ModuleScore {
  score: number;
  confidence: number;
  available: boolean;
  details: Record<string, unknown>;
}

export interface Flag {
  module: string;
  code: string;
  severity: "high" | "medium" | "low" | "info";
  message: string;
}

/** Messages from the server. In production the server may send only {verdict, elapsed_s}. */
export interface ServerUpdate {
  type: "update" | "final";
  verdict: Verdict;
  elapsed_s: number;
  probability?: number;
  modules?: Record<string, ModuleScore>;
  flags?: Flag[];
  reasons?: string[];
  latency_ms?: { last: number; p50: number };
}

export interface LivenessOptions {
  holdSeconds: number;
  prepareSeconds: number;
  challengeSeconds: number;
  /** send microphone audio for voice anti-spoofing */
  audio: boolean;
}

export const DEFAULT_OPTIONS: LivenessOptions = {
  holdSeconds: 10,
  prepareSeconds: 2,
  challengeSeconds: 4,
  audio: true,
};

export interface CaptureCallbacks {
  onPhase: (phase: Phase, info: { secondsLeft: number; challenge?: SessionInfo["challenge"] }) => void;
  onUpdate: (u: ServerUpdate) => void;
  onFinal: (u: ServerUpdate) => void;
  onFace: (found: boolean, yaw: number) => void;
  onError: (message: string) => void;
}
