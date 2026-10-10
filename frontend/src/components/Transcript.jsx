import { useEffect, useRef } from "react";

const NAME = { customer: "Customer", rep: "Rep", auto: "…" };

export default function Transcript({ turns, partial, onFlip }) {
  const end = useRef(null);
  // braces matter: newer Chrome returns a Promise from scrollIntoView, which React would call as a cleanup
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [turns.length, partial]);

  const live = Object.entries(partial).filter(([, text]) => text);
  return (
    <div className="transcript">
      {turns.length === 0 && live.length === 0 && (
        <div className="empty">No speech yet. Start listening, or type / click a line below.</div>
      )}
      {turns.map((t) => (
        <div key={t.id} className={`turn ${t.speaker}`}>
          <button className="who" onClick={() => onFlip(t)}
            title={`${t.auto ? "Auto-detected" : "Set"} as ${NAME[t.speaker]}. Click to switch to ${t.speaker === "customer" ? "Rep" : "Customer"}.`}>
            {NAME[t.speaker]}
            {t.auto && <span className="auto-tag">auto</span>}
          </button>
          <span className="text">{t.text}</span>
        </div>
      ))}
      {live.map(([sp, text]) => (
        <div key={sp} className={`turn ${sp} partial`}>
          <span className="who">{NAME[sp] || sp}</span>
          <span className="text">{text}</span>
        </div>
      ))}
      <div ref={end} />
    </div>
  );
}
