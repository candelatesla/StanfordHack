// Tab audio (customer) + mic (rep) -> 16kHz mono PCM16 -> WS /ws/audio?speaker=
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

export async function getCustomerStream() {
  // User must pick the Meet tab and tick "Also share tab audio".
  const tab = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
  tab.getVideoTracks().forEach((t) => t.stop());
  if (!tab.getAudioTracks().length) throw new Error('No tab audio. Re-share and tick "Also share tab audio".');
  return new MediaStream(tab.getAudioTracks());
}

export async function getRepStream() {
  return navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
  });
}

// Streams one MediaStream to the backend. Returns { stop, level } where level() is 0..1 for a meter.
export async function streamToBackend(stream, speaker, onClose) {
  const ctx = new AudioContext({ sampleRate: 16000 });
  const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
  await ctx.audioWorklet.addModule(url);
  URL.revokeObjectURL(url);

  const source = ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ctx, "pcm-capture");
  const ws = new WebSocket(wsUrl(`/ws/audio?speaker=${speaker}`));
  ws.binaryType = "arraybuffer";
  ws.onclose = (e) => onClose?.(e.reason || "audio socket closed");

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
        if (ws.readyState === WebSocket.OPEN) ws.send(buf.buffer);
        buf = new Int16Array(CHUNK_SAMPLES);
        n = 0;
      }
    }
    level = level * 0.8 + peak * 0.2;
  };
  source.connect(node);

  return {
    level: () => level,
    stop() {
      source.disconnect();
      node.disconnect();
      stream.getTracks().forEach((t) => t.stop());
      ctx.close();
      ws.close();
    },
  };
}
