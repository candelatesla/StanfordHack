const money = (n) => (n >= 1000 ? `$${(n / 1000).toFixed(n % 1000 ? 1 : 0)}K` : `$${n}`);

export default function DealPanel({ deal, lastChanged }) {
  if (!deal) return <div className="deal">Loading deal…</div>;
  const changed = new Set(lastChanged);
  const items = [
    ["Tier", deal.tier, "Subscription Tier"],
    ["ARR", money(deal.arr), "Annual Fee"],
    ["Discount", `${deal.discount_pct}%`, "Discount"],
    ["Term", `${deal.term_years} yr${deal.term_years > 1 ? "s" : ""}`, "Term"],
    ["Payment", deal.payment_terms, "Payment Terms"],
    ["Liability", `${deal.liability_cap_multiple}x`, "Limitation of Liability"],
    ["HIPAA BAA", deal.hipaa_baa ? "✓" : "—", "HIPAA BAA"],
  ];
  return (
    <div className="deal">
      <div className="deal-title">
        <h2>Deal</h2>
        <span className="stage">{deal.customer} · {deal.stage}</span>
      </div>
      <div className="stats">
        {items.map(([label, value, clause]) => (
          <div key={label} className={`stat ${changed.has(clause) ? "flash" : ""}`}>
            <span className="stat-label">{label}</span>
            <span className="stat-value">{value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
