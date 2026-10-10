import { useEffect, useRef } from "react";

export default function Transcript({ turns, partial }) {
  const end = useRef(null);
  useEffect(() => end.current?.scrollIntoView({ block: "end" }), [turns.length, partial]);

  return (
    <div className="transcript">
      {turns.length === 0 && !partial.customer && !partial.rep && (
        <div className="empty">No speech yet. Start listening, or use the dev input below.</div>
      )}
      {turns.map((t, i) => (
        <div key={i} className={`turn ${t.speaker}`}>
          <span className="who">{t.speaker === "customer" ? "Customer" : "Rep"}</span>
          <span className="text">{t.text}</span>
        </div>
      ))}
      {["customer", "rep"].map((sp) =>
        partial[sp] ? (
          <div key={sp} className={`turn ${sp} partial`}>
            <span className="who">{sp === "customer" ? "Customer" : "Rep"}</span>
            <span className="text">{partial[sp]}</span>
          </div>
        ) : null
      )}
      <div ref={end} />
    </div>
  );
}
