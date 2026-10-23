/**
 * Device fingerprint sent once in the `hello` message. It is deliberately small and is used only to
 * spot virtual cameras / tampered media APIs / automation. Raw device IDs are never sent.
 *
 * Limitation: a determined attacker controlling the browser can forge any of this. Treat it as a
 * strong hint; the server also checks the video signal itself.
 */

function isNative(fn: unknown): boolean {
  try {
    return typeof fn === "function" && /\[native code\]/.test(Function.prototype.toString.call(fn));
  } catch {
    return false;
  }
}

function fnv1a(str: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(16).padStart(8, "0");
}

function canvasHash(): string {
  try {
    const c = document.createElement("canvas");
    c.width = 120;
    c.height = 30;
    const ctx = c.getContext("2d");
    if (!ctx) return "";
    ctx.textBaseline = "top";
    ctx.font = "14px 'Arial'";
    ctx.fillStyle = "#f60";
    ctx.fillRect(10, 5, 60, 20);
    ctx.fillStyle = "#069";
    ctx.fillText("ZeroSpoof ✓", 4, 8);
    return fnv1a(c.toDataURL());
  } catch {
    return "";
  }
}

function webglRenderer(): string {
  try {
    const gl = document.createElement("canvas").getContext("webgl");
    if (!gl) return "";
    const ext = gl.getExtension("WEBGL_debug_renderer_info");
    return ext ? String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)) : "";
  } catch {
    return "";
  }
}

export async function collectFingerprint(stream: MediaStream): Promise<Record<string, unknown>> {
  const md = navigator.mediaDevices;
  let videoInputs: { label: string }[] = [];
  let audioInputs: { label: string }[] = [];
  try {
    const devices = await md.enumerateDevices();
    videoInputs = devices.filter((d) => d.kind === "videoinput").map((d) => ({ label: d.label }));
    audioInputs = devices.filter((d) => d.kind === "audioinput").map((d) => ({ label: d.label }));
  } catch {
    /* ignore */
  }
  const track = stream.getVideoTracks()[0];
  const s = track?.getSettings?.() ?? {};
  return {
    userAgent: navigator.userAgent,
    platform: navigator.platform,
    languages: Array.from(navigator.languages ?? []),
    hardwareConcurrency: navigator.hardwareConcurrency,
    deviceMemory: (navigator as unknown as { deviceMemory?: number }).deviceMemory,
    screen: { w: screen.width, h: screen.height, dpr: window.devicePixelRatio },
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    webdriver: Boolean((navigator as unknown as { webdriver?: boolean }).webdriver),
    webglRenderer: webglRenderer(),
    canvasHash: canvasHash(),
    activeTrack: {
      label: track?.label ?? "",
      settings: {
        width: s.width,
        height: s.height,
        frameRate: s.frameRate,
        facingMode: s.facingMode,
        deviceId: s.deviceId ? "present" : "",
      },
    },
    videoInputs,
    audioInputs,
    nativeApis: {
      getUserMedia: isNative(md.getUserMedia),
      enumerateDevices: isNative(md.enumerateDevices),
      getSettings: isNative(MediaStreamTrack.prototype.getSettings),
    },
  };
}
