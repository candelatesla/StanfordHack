import { useState } from "react";

function toMarkdown(s) {
  const list = (xs) => (xs?.length ? xs.map((x) => `- ${x}`).join("\n") : "- None");
  return [
    `## Call summary: ${s.customer}`,
    s.recap,
    `### Agreed\n${list(s.agreed)}`,
    `### Open items\n${list(s.open_items)}`,
    `### CRM changes\n${list(s.crm_changes.map((c) => `${c.field}: ${c.old} → ${c.new}`))}`,
    `### Contract redlines\n${list(s.contract_diff.map((d) => `${d.clause}: ${d.old} → ${d.new}`))}`,
    `### Next steps\n${list(s.next_steps)}`,
  ].filter(Boolean).join("\n\n");
}

export default function PostCallSummary({ summary: s, onClose }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(toMarkdown(s));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Post-call summary · {s.customer}</h2>
          <div>
            <button className="btn sm" onClick={copy}>{copied ? "✓ Copied" : "Copy as Markdown"}</button>
            <button className="btn ghost" onClick={onClose}>✕</button>
          </div>
        </div>
        {s.recap && <p className="recap">{s.recap}</p>}
        <Section title="✓ Agreed" items={s.agreed} empty="Nothing agreed yet." />
        <Section title="⚠ Open items" items={s.open_items} empty="No open items." />
        <h3>CRM changes</h3>
        {s.crm_changes.length ? (
          <table>
            <tbody>
              {s.crm_changes.map((c) => (
                <tr key={c.field}>
                  <td>{c.field}</td>
                  <td><del>{String(c.old)}</del></td>
                  <td><ins>{String(c.new)}</ins></td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="empty">No CRM changes.</p>
        )}
        <h3>Contract redlines</h3>
        <ul className="diff">
          {s.contract_diff.map((d) => (
            <li key={d.clause}>
              <span className="clause">{d.clause}</span>
              <del>{d.old}</del>
              <span className="arrow">→</span>
              <ins>{d.new}</ins>
            </li>
          ))}
        </ul>
        <Section title="Next steps" items={s.next_steps} empty="None." />
      </div>
    </div>
  );
}

function Section({ title, items, empty }) {
  return (
    <>
      <h3>{title}</h3>
      {items?.length ? <ul>{items.map((x, i) => <li key={i}>{x}</li>)}</ul> : <p className="empty">{empty}</p>}
    </>
  );
}
