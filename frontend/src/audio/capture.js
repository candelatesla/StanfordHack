// Audio capture that lives for the whole page session.
//  - Devices are requested ONCE (Start). Pause/Resume only gates sending; tracks stay open, so the
//    browser never re-prompts. End call pauses too; "New call" resumes on the same streams.
//  - The backend socket reconnects by itself (server restart, Wi-Fi blip) without touching the devices.
//  - 16kHz mono PCM16 in 100ms chunks -> WS /ws/audio?speaker=customer|rep|auto
import { wsUrl } from "../ws.js";

const WORKLET = `
class PcmCapture extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) this.port.postMessage(ch.slice(0));
    return true;
  }
}
registerProcessor("pcm-capture", PcmCapture);
`;
const CHUNK_SAMPLES = 1600; // 100ms at 16kHz

export const MODES = {
  mic: { label: "This mic only", hint: "One mic hears both people; speakers are auto-detected" },
  meet: { label: "Meet tab + mic", hint: "Tab audio = customer, your mic = rep (most reliable)" },
};

// Opens the devices for a mode. Returns [{ speaker, stream }].
export async function openDevices(mode) {
  const mic = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
  });
  if (mode !== "meet") return [{ speaker: "auto", stream: mic }];
  let tab;
  try {
    // User picks the Meet tab and ticks "Also share tab audio".
    tab = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
  } catch (e) {
    mic.getTracks().forEach((t) => t.stop());
    throw e;
  }
  tab.getVideoTracks().forEach((t) => t.stop());
  if (!tab.getAudioTracks().length) {
    mic.getTracks().forEach((t) => t.stop());
    throw new Error('No tab audio. Share again and tick "Also share tab audio".');
  }
  return [
    { speaker: "customer", stream: new MediaStream(tab.getAudioTracks()) },
    { speaker: "rep", stream: mic },
  ];
}

// Streams one MediaStream to the backend until close(). Survives socket drops.
export async function createSender(stream, speaker, { onStatus, onEnded } = {}) {
  const ctx = new AudioContext({ sampleRate: 16000 });
  const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
  await ctx.audioWorklet.addModule(url);
  URL.revokeObjectURL(url);
  const source = ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ctx, "pcm-capture");
  source.connect(node);

  let ws = null;
  let closed = false;
  let paused = false;
  let retry = 0;
  let fatal = "";

  const connect = () => {
    if (closed) return;
    ws = new WebSocket(wsUrl(`/ws/audio?speaker=${speaker}`));
    ws.binaryType = "arraybuffer";
    ws.onopen = () => {
      retry = 0;
      onStatus?.("live");
    };
    ws.onclose = (e) => {
      if (closed) return;
      if (e.code === 1011) {
        fatal = e.reason || "Speech-to-text unavailable";
        onStatus?.("error", fatal);
        return;
      }
      onStatus?.("reconnecting");
      setTimeout(connect, Math.min(500 * 2 ** retry++, 4000));
    };
  };
  connect();

  let buf = new Int16Array(CHUNK_SAMPLES);
  let n = 0;
  let level = 0;
  node.port.onmessage = ({ data }) => {
    let peak = 0;
    for (let i = 0; i < data.length; i++) {
      const s = Math.max(-1, Math.min(1, data[i]));
      peak = Math.max(peak, Math.abs(s));
      buf[n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      if (n === CHUNK_SAMPLES) {
        if (!paused && ws?.readyState === WebSocket.OPEN) ws.send(buf.buffer);
        buf = new Int16Array(CHUNK_SAMPLES);
        n = 0;
      }
    }
    level = paused ? 0 : level * 0.8 + peak * 0.2;
  };

  // e.g. the user clicks Chrome's "Stop sharing" bar
  stream.getTracks().forEach((t) => t.addEventListener("ended", () => onEnded?.(speaker)));

  return {
    speaker,
    level: () => level,
    setPaused(p) {
      paused = p;
      if (ctx.state === "suspended" && !p) ctx.resume();
    },
    close() {
      closed = true;
      source.disconnect();
      node.disconnect();
      stream.getTracks().forEach((t) => t.stop());
      ctx.close();
      ws?.close();
    },
  };
}
