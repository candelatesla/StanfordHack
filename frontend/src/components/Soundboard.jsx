// /customer — open on the "customer" laptop in the Meet. Plays the scripted lines as ElevenLabs TTS
// out loud so the rep laptop hears them through Meet. "Type it" sends the line as text instead.
import { useEffect, useRef, useState } from "react";
import { connectEvents } from "../ws.js";

export default function Soundboard() {
  const [lines, setLines] = useState([]);
  const [tts, setTts] = useState(false);
  const [playing, setPlaying] = useState(null);
  const [done, setDone] = useState(new Set());
  const [error, setError] = useState("");
  const audio = useRef(new Audio());
  const events = useRef(null);

  useEffect(() => {
    fetch("/api/demo/lines").then((r) => r.json()).then((d) => { setLines(d.lines); setTts(d.tts); });
    events.current = connectEvents(() => {});
    // warm the TTS cache so the first click plays instantly
    return () => events.current.close();
  }, []);

  useEffect(() => {
    if (tts) lines.forEach((_, i) => fetch(`/api/demo/audio/${i}.mp3`).catch(() => {}));
  }, [tts, lines]);

  const play = async (i) => {
    setError("");
    const a = audio.current;
    a.pause();
    a.src = `/api/demo/audio/${i}.mp3`;
    a.onended = () => setPlaying(null);
    setPlaying(i);
    try {
      await a.play();
      setDone((d) => new Set(d).add(i));
    } catch (e) {
      setPlaying(null);
      setError(`Couldn't play line ${i}: ${e.message}`);
    }
  };

  const typeIt = (i) => {
    events.current.send({ type: "text_turn", speaker: "customer", text: lines[i] });
    setDone((d) => new Set(d).add(i));
  };

  return (
    <div className="soundboard">
      <header>
        <h1>🎙 Customer soundboard</h1>
        <p className="muted">Acme Health buyer · play each line into the Meet when the rep pauses.</p>
        {!tts && <div className="banner">ELEVENLABS_API_KEY not set on the backend; use “Type it” or read the lines aloud.</div>}
        {error && <div className="banner">{error}</div>}
      </header>
      <ol>
        {lines.map((line, i) => (
          <li key={i} className={`${playing === i ? "playing" : ""} ${done.has(i) ? "done" : ""}`}>
            <span className="line">{line}</span>
            <div className="actions">
              <button className="btn primary" onClick={() => play(i)} disabled={!tts}>
                {playing === i ? "▶ Playing…" : "▶ Play"}
              </button>
              <button className="btn" onClick={() => typeIt(i)}>Type it</button>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
