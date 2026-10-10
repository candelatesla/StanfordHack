export default function ContractDiff({ diff, lastChanged }) {
  const changed = new Set(lastChanged);
  return (
    <div className="contract">
      <h2>Contract diff <span className="muted">vs. original order form</span></h2>
      {diff.length === 0 ? (
        <div className="empty">No changes yet.</div>
      ) : (
        <ul className="diff">
          {diff.map((d) => (
            <li key={d.clause} className={changed.has(d.clause) ? "flash" : ""}>
              <span className="clause">{d.clause}</span>
              <del>{d.old}</del>
              <span className="arrow">→</span>
              <ins>{d.new}</ins>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
