// Pre-call screen: pick an existing customer (search -> dashboard with history) or create a new one, then start the call.
import { useEffect, useMemo, useState } from "react";
import DealPanel from "./DealPanel.jsx";

const money = (n) => (n >= 1000 ? `$${Math.round(n / 1000)}K` : `$${n || 0}`);
const FIELD = {
  tier: "Tier", discount_pct: "Discount %", term_years: "Term (years)", payment_terms_days: "Payment terms (days)",
  liability_cap_multiple: "Liability cap (x)", hipaa_baa: "HIPAA BAA", eu_residency: "EU residency", stage: "Stage",
  implementation_fee_waived: "Impl. fee waived", renewal_cap_pct: "Renewal cap %", pilot_days: "Pilot (days)",
  start_delay_days: "Start delay (days)", extra_creator_seats: "Extra creator seats",
};

async function api(path, opts) {
  const r = await fetch(path, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `Request failed (${r.status})`);
  return body;
}

export default function Setup({ initialAccount, onStart }) {
  const [tab, setTab] = useState("existing");
  const [list, setList] = useState({ accounts: [], tiers: [], industries: [] });
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState(initialAccount || null);
  const [account, setAccount] = useState(null);
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);

  const loadList = () => api("/api/accounts").then(setList).catch((e) => setError(e.message));
  useEffect(() => {
    loadList();
  }, []);
  useEffect(() => {
    setAccount(null);
    if (selected) api(`/api/accounts/${selected}`).then(setAccount).catch((e) => setError(e.message));
  }, [selected]);

  const shown = useMemo(() => {
    const s = q.trim().toLowerCase();
    return list.accounts.filter((a) => !s || `${a.name} ${a.industry} ${a.contact_name}`.toLowerCase().includes(s));
  }, [q, list.accounts]);

  const start = async () => {
    setStarting(true);
    setError("");
    try {
      await onStart(account.id);
    } catch (e) {
      setError(e.message);
      setStarting(false);
    }
  };

  const created = async (acc) => {
    await loadList();
    setTab("existing");
    setQ("");
    setSelected(acc.id);
  };

  return (
    <div className="setup">
      <header className="topbar">
        <div className="brand">
          <span className="logo">◆</span> CLOSER <span className="crumb">Before the call</span>
        </div>
      </header>
      {error && <div className="banner">{error}</div>}
      <main className="setup-grid">
        <aside className="panel picker">
          <div className="segmented">
            <button className={tab === "existing" ? "on" : ""} onClick={() => setTab("existing")}>Existing customer</button>
            <button className={tab === "new" ? "on" : ""} onClick={() => setTab("new")}>New customer</button>
          </div>
          {tab === "existing" ? (
            <>
              <input className="search" autoFocus value={q} onChange={(e) => setQ(e.target.value)}
                placeholder="Search company, industry or contact…" />
              <ul className="account-list">
                {shown.map((a) => (
                  <li key={a.id}>
                    <button className={selected === a.id ? "on" : ""} onClick={() => setSelected(a.id)}>
                      <span className="acc-name">{a.name}</span>
                      <span className="acc-meta">
                        {a.industry} · <span className={`tag ${a.status.toLowerCase()}`}>{a.status}</span>
                        {a.open_deal && <> · {a.open_deal.stage}</>}
                      </span>
                    </button>
                  </li>
                ))}
                {shown.length === 0 && (
                  <li className="empty">
                    No match. <button className="link" onClick={() => setTab("new")}>Add “{q}” as a new customer</button>
                  </li>
                )}
              </ul>
            </>
          ) : (
            <NewCustomer tiers={list.tiers} industries={list.industries} defaultName={q} onCreated={created} />
          )}
        </aside>

        <section className="panel dashboard">
          {!selected && <div className="empty">Pick a customer to load their deals and call history, or add a new one.</div>}
          {selected && !account && <div className="empty">Loading…</div>}
          {account && <Dashboard a={account} onStart={start} starting={starting} />}
        </section>
      </main>
    </div>
  );
}

function Dashboard({ a, onStart, starting }) {
  const open = a.deals.find((d) => d.open);
  const past = a.deals.filter((d) => !d.open);
  return (
    <div className="dash">
      <div className="dash-head">
        <div>
          <h1>{a.name}</h1>
          <div className="acc-meta">
            <span className={`tag ${a.status.toLowerCase()}`}>{a.status}{a.customer_since ? ` since ${a.customer_since.slice(0, 4)}` : ""}</span>
            {" "}{[a.industry, a.region, a.employees ? `${a.employees.toLocaleString()} employees` : ""].filter(Boolean).join(" · ")}
          </div>
          {a.contact_name && <div className="acc-meta">Contact: <b>{a.contact_name}</b>{a.contact_title && `, ${a.contact_title}`}</div>}
        </div>
        <div className="start-box">
          <button className="btn primary lg" onClick={onStart} disabled={starting}>{starting ? "Starting…" : "● Start call"}</button>
          <span className="muted">{open ? `On open deal: ${open.name}` : "Opens a new deal for this call"}</span>
        </div>
      </div>

      {a.notes && <p className="notes">{a.notes}</p>}

      {open && <DealPanel deal={{ ...open, customer: open.name }} lastChanged={[]} />}

      <h3>Previous deals</h3>
      {past.length ? (
        <table className="table">
          <thead><tr><th>Deal</th><th>Tier</th><th>ARR</th><th>Terms</th><th>Outcome</th><th>Closed</th></tr></thead>
          <tbody>
            {past.map((d) => (
              <tr key={d.id}>
                <td>{d.name}</td>
                <td>{d.tier}</td>
                <td>{money(d.arr)}</td>
                <td className="muted">{d.term_years} yr · {d.discount_pct}% off · {d.payment_terms}</td>
                <td><span className={`tag ${d.stage === "Closed Won" ? "won" : "lost"}`}>{d.stage.replace("Closed ", "")}</span></td>
                <td className="muted">{d.closed_on}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : <div className="empty">No previous deals.</div>}

      <h3>Previous calls</h3>
      {a.calls.length ? (
        <ul className="calls">
          {a.calls.map((c) => (
            <li key={c.id}>
              <div className="call-head"><b>{c.title}</b><span className="muted">{c.ts}</span></div>
              {c.recap && <p>{c.recap}</p>}
              {c.agreed.length > 0 && <p className="muted">Agreed: {c.agreed.join("; ")}</p>}
              {c.open_items.length > 0 && <p className="muted">Open: {c.open_items.join("; ")}</p>}
              {c.next_steps.length > 0 && <p className="muted">Next: {c.next_steps.join("; ")}</p>}
            </li>
          ))}
        </ul>
      ) : <div className="empty">No calls logged yet.</div>}

      {a.activity.length > 0 && (
        <>
          <h3>CRM changes</h3>
          <ul className="diff">
            {a.activity.map((x, i) => (
              <li key={i}>
                <span className="clause">{FIELD[x.field] || x.field}</span>
                <del>{x.old}</del><span className="arrow">→</span><ins>{x.new}</ins>
                <span className="muted">{x.deal} · {x.ts.slice(0, 16)}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function NewCustomer({ tiers, industries, defaultName, onCreated }) {
  const [f, setF] = useState({ name: defaultName || "", industry: "", region: "", employees: "", contact_name: "",
    contact_title: "", tier: "Growth" });
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });

  const submit = async (e) => {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      onCreated(await api("/api/accounts", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(f),
      }));
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <form className="form" onSubmit={submit}>
      <label>Company name *<input required autoFocus value={f.name} onChange={set("name")} placeholder="e.g. Zeta Pharma" /></label>
      <label>Industry
        <select value={f.industry} onChange={set("industry")}>
          <option value="">Select…</option>
          {industries.map((i) => <option key={i}>{i}</option>)}
        </select>
      </label>
      <div className="row">
        <label>Region<input value={f.region} onChange={set("region")} placeholder="US, EU…" /></label>
        <label>Employees<input type="number" min="0" value={f.employees} onChange={set("employees")} /></label>
      </div>
      <div className="row">
        <label>Contact<input value={f.contact_name} onChange={set("contact_name")} placeholder="Name" /></label>
        <label>Title<input value={f.contact_title} onChange={set("contact_title")} placeholder="Role" /></label>
      </div>
      <label>Interested in
        <select value={f.tier} onChange={set("tier")}>
          {tiers.map((t) => <option key={t}>{t}</option>)}
        </select>
      </label>
      {error && <div className="form-error">{error}</div>}
      <button className="btn primary" type="submit" disabled={saving || !f.name.trim()}>{saving ? "Creating…" : "Create customer"}</button>
    </form>
  );
}
