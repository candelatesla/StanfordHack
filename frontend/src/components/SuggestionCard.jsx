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
        {s.check && (
          <span className={`check ${s.check}`} title={CHECK_TITLE[s.check]}>
            {CHECK_LABEL[s.check] || s.check}
          </span>
        )}
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
          {dedupe(s.sources).map((src, i) => (
            <div key={src.title} className="source-row">
              <span className="source" title={MATCH_TITLE[src.match]}>
                📄 {src.title}<span className="match">{src.match}</span>
              </span>
              {i === 0 && <blockquote className="snippet">{src.snippet}</blockquote>}
            </div>
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

const CHECK_TITLE = {
  verified: "Instant extraction, confirmed by the LLM reading the whole sentence in context",
  revised: "The LLM read the sentence differently; card recomputed",
  llm: "Extracted by the LLM (phrasing the instant extractor doesn't cover)",
  kept: "Keyword match the LLM didn't list; kept so nothing is missed",
  rules: "Instant extraction (LLM unavailable)",
};
const CHECK_LABEL = { verified: "✓ LLM", revised: "↻ revised", llm: "✓ LLM", kept: "keyword", rules: "rules" };
const MATCH_TITLE = {
  hybrid: "Matched on meaning (embedding of the whole utterance) + keywords (BM25)",
  keyword: "Matched on keywords (BM25); meaning-based match still loading or unavailable",
};
const dedupe =(xs) => xs.filter((x, i) => xs.findIndex((y) => y.title === x.title) === i);
const fmt = (v) => (v === true ? "yes" : v === false ? "no" : v);
const timingTitle = (t) =>
  t ? Object.entries(t).filter(([, v]) => v != null).map(([k, v]) => `${k}: ${v}ms`).join("\n") : "";
