/**
 * Face tracking with MediaPipe Face Landmarker (WebAssembly, runs entirely in the browser).
 * Gives us: the face box (for cropping), head yaw and blink (for the challenge-response).
 *
 * If it cannot load (offline, blocked CDN, old browser) the SDK falls back to a centred crop and
 * reports no yaw/blink: the challenge then cannot pass, so the session ends in "review", never "pass".
 */

export interface FaceObservation {
  /** face box in video pixels */
  box: { x: number; y: number; w: number; h: number };
  /** degrees; negative = the user turned to THEIR left */
  yaw: number;
  /** 0..1 eye-closure score */
  blink: number;
}

export interface FaceTracker {
  detect(video: HTMLVideoElement, nowMs: number): FaceObservation | null;
  close(): void;
}

const WASM_URL =
  process.env.NEXT_PUBLIC_MEDIAPIPE_WASM_URL ??
  "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm";
const MODEL_URL =
  process.env.NEXT_PUBLIC_FACE_MODEL_URL ??
  "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task";

export async function createFaceTracker(): Promise<FaceTracker | null> {
  try {
    const { FaceLandmarker, FilesetResolver } = await import("@mediapipe/tasks-vision");
    const fileset = await FilesetResolver.forVisionTasks(WASM_URL);
    const landmarker = await FaceLandmarker.createFromOptions(fileset, {
      baseOptions: { modelAssetPath: MODEL_URL, delegate: "GPU" },
      runningMode: "VIDEO",
      numFaces: 1,
      outputFaceBlendshapes: true,
    });
    let lastTs = -1;
    return {
      detect(video, nowMs) {
        const ts = Math.max(nowMs, lastTs + 1); // MediaPipe needs strictly increasing timestamps
        lastTs = ts;
        const res = landmarker.detectForVideo(video, ts);
        const lm = res.faceLandmarks?.[0];
        if (!lm || lm.length < 468) return null;
        const W = video.videoWidth;
        const H = video.videoHeight;
        let minX = 1, minY = 1, maxX = 0, maxY = 0;
        for (const p of lm) {
          if (p.x < minX) minX = p.x;
          if (p.x > maxX) maxX = p.x;
          if (p.y < minY) minY = p.y;
          if (p.y > maxY) maxY = p.y;
        }
        // Yaw from the nose tip's offset between the two cheek-edge landmarks (1 = nose, 234/454 = cheeks).
        // In the (un-mirrored) camera image, turning to the user's LEFT moves the nose to the image right.
        const nose = lm[1].x;
        const a = lm[234].x;
        const b = lm[454].x;
        const r = (nose - (a + b) / 2) / Math.max(1e-6, Math.abs(b - a));
        const yaw = -Math.asin(Math.max(-1, Math.min(1, 2 * r))) * (180 / Math.PI);
        const cats = res.faceBlendshapes?.[0]?.categories ?? [];
        const bl = cats.find((c) => c.categoryName === "eyeBlinkLeft")?.score ?? 0;
        const br = cats.find((c) => c.categoryName === "eyeBlinkRight")?.score ?? 0;
        return {
          box: { x: minX * W, y: minY * H, w: (maxX - minX) * W, h: (maxY - minY) * H },
          yaw,
          blink: (bl + br) / 2,
        };
      },
      close() {
        landmarker.close();
      },
    };
  } catch (e) {
    console.warn("[zerospoof] face tracker unavailable, using centred crop:", e);
    return null;
  }
}
