const LABEL = { approved: "Approved", counter: "Counter", escalate: "Escalate", info: "Info" };

export default function SuggestionCard({ s, escalation, onAccept, onEscalate }) {
  const streaming = s.status === "streaming";
  const changes = Object.entries(s.proposed_changes || {});
  const fast = s.latency_ms < 500;

  return (
    <article className={`card v-${s.verdict} ${s.resolution ? "resolved" : ""}`}>
      <div className="card-head">
        <span className={`verdict v-${s.verdict}`}>{LABEL[s.verdict] || s.verdict}</span>
        <span className="request">{s.request}</span>
        <span className={`latency ${fast ? "fast" : "slow"}`} title={timingTitle(s.timing)}>
          ⚡ {s.latency_ms}ms
        </span>
      </div>
      {s.customer_text && <div className="quote">“{s.customer_text}”</div>}
      <p className="answer">{s.answer || (streaming ? <span className="shimmer">Checking Company Brain…</span> : s.reason)}</p>
      {(s.counteroffer || streaming) && (
        <div className="say">
          <span className="say-label">Say</span>
          <span>{s.counteroffer || <span className="shimmer">…</span>}</span>
        </div>
      )}
      {s.sources?.length > 0 && (
        <div className="sources">
          {dedupe(s.sources).map((src) => (
            <span key={src.title} className="source" title={src.snippet}>📄 {src.title}</span>
          ))}
        </div>
      )}
      <div className="card-foot">
        {changes.length > 0 && (
          <span className="changes">
            {changes.map(([k, v]) => `${k.replace(/_/g, " ")}: ${fmt(v)}`).join(" · ")}
          </span>
        )}
        {s.resolution === "accepted" && <span className="resolution ok">✓ Applied to CRM + contract</span>}
        {s.resolution === "escalated" && (
          <span className="resolution esc">↗ Sent to {escalation?.to || s.approval_needed || "manager"}</span>
        )}
        {!s.resolution && (
          <div className="actions">
            {changes.length > 0 && (
              <button className="btn primary sm" onClick={onAccept} disabled={streaming}>Accept</button>
            )}
            <button className="btn sm" onClick={onEscalate} disabled={streaming}>
              Escalate{s.approval_needed ? ` to ${s.approval_needed}` : ""}
            </button>
          </div>
        )}
      </div>
    </article>
  );
}

const dedupe = (xs) => xs.filter((x, i) => xs.findIndex((y) => y.title === x.title) === i);
const fmt = (v) => (v === true ? "yes" : v === false ? "no" : v);
const timingTitle = (t) =>
  t ? Object.entries(t).filter(([, v]) => v != null).map(([k, v]) => `${k}: ${v}ms`).join("\n") : "";
