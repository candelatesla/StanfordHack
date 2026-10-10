import { useEffect, useReducer, useRef, useState } from "react";
import { connectEvents } from "./ws.js";
import { getCustomerStream, getRepStream, streamToBackend } from "./audio/capture.js";
import Transcript from "./components/Transcript.jsx";
import SuggestionCard from "./components/SuggestionCard.jsx";
import DealPanel from "./components/DealPanel.jsx";
import ContractDiff from "./components/ContractDiff.jsx";
import PostCallSummary from "./components/PostCallSummary.jsx";

const GOLDEN = [
  "Can you do 20% off if we sign for 3 years?",
  "We're in healthcare. Do you sign a HIPAA BAA?",
  "We need net-60 payment terms.",
  "Can you raise the liability cap to 3x fees?",
];

const initial = {
  transcript: [],
  partial: {},
  suggestions: [],
  escalations: {},
  deal: null,
  contract: {},
  contractDiff: [],
  lastChanged: [],
  summary: null,
  status: { llm: false, stt: false },
};

function reducer(state, ev) {
  switch (ev.type) {
    case "init":
      return {
        ...initial,
        transcript: ev.transcript || [],
        suggestions: ev.suggestions || [],
        deal: ev.deal,
        contract: ev.contract,
        contractDiff: ev.contract_diff || [],
        status: ev.status || state.status,
      };
    case "transcript":
      if (!ev.is_final) return { ...state, partial: { ...state.partial, [ev.speaker]: ev.text } };
      return {
        ...state,
        transcript: [...state.transcript, ev],
        partial: { ...state.partial, [ev.speaker]: null },
      };
    case "suggestion": {
      const exists = state.suggestions.some((s) => s.id === ev.id);
      const suggestions = exists
        ? state.suggestions.map((s) => (s.id === ev.id ? { ...s, ...ev } : s))
        : [...state.suggestions, ev];
      return { ...state, suggestions };
    }
    case "suggestion_resolved":
      return {
        ...state,
        suggestions: state.suggestions.map((s) => (s.id === ev.id ? { ...s, resolution: ev.resolution } : s)),
      };
    case "deal_update":
      return {
        ...state,
        deal: ev.fields,
        contract: ev.contract,
        contractDiff: ev.contract_diff,
        lastChanged: (ev.changes || []).map((c) => c.clause),
      };
    case "escalation":
      return { ...state, escalations: { ...state.escalations, [ev.id]: ev } };
    case "summary":
      return { ...state, summary: ev };
    case "close_summary":
      return { ...state, summary: null };
    default:
      return state;
  }
}

function LevelMeters({ sources }) {
  const [levels, setLevels] = useState([0, 0]);
  useEffect(() => {
    let raf;
    const tick = () => {
      setLevels(sources.map((s) => s.level()));
      raf = requestAnimationFrame(tick);
    };
    tick();
    return () => cancelAnimationFrame(raf);
  }, [sources]);
  return (
    <span className="meters" title="Audio input levels">
      {["Customer", "Rep"].map((label, i) => (
        <span key={label} className="meter">
          {label}
          <span className="meter-bar"><span style={{ width: `${Math.min(100, (levels[i] || 0) * 250)}%` }} /></span>
        </span>
      ))}
    </span>
  );
}

export default function App() {
  const [state, dispatch] = useReducer(reducer, initial);
  const [conn, setConn] = useState("connecting");
  const [listening, setListening] = useState(false);
  const [audioError, setAudioError] = useState("");
  const [draft, setDraft] = useState("");
  const [speaker, setSpeaker] = useState("customer");
  const events = useRef(null);
  const audio = useRef([]);
  const feedEnd = useRef(null);

  useEffect(() => {
    events.current = connectEvents(dispatch, setConn);
    return () => events.current.close();
  }, []);

  useEffect(() => {
    feedEnd.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [state.suggestions.length]);

  const send = (msg) => events.current?.send(msg);

  const sendTurn = (text, who = speaker) => {
    if (!text.trim()) return;
    send({ type: "text_turn", speaker: who, text });
    setDraft("");
  };

  const startListening = async () => {
    setAudioError("");
    try {
      const customer = await getCustomerStream();
      const rep = await getRepStream();
      const onClose = (reason) => {
        setAudioError(reason);
        stopListening();
      };
      audio.current = [
        await streamToBackend(customer, "customer", onClose),
        await streamToBackend(rep, "rep", onClose),
      ];
      setListening(true);
    } catch (e) {
      setAudioError(e.message || String(e));
      stopListening();
    }
  };

  const stopListening = () => {
    audio.current.forEach((a) => a.stop());
    audio.current = [];
    setListening(false);
  };

  const endCall = () => {
    stopListening();
    send({ type: "end_call" });
  };

  const reset = () => {
    stopListening();
    send({ type: "reset" });
  };

  const deal = state.deal;
  const pending = state.suggestions.filter((s) => !s.resolution).length;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">◆</span> DealBrain
          <span className="crumb">Nimbus × {deal?.customer || "…"}</span>
        </div>
        <div className="topbar-right">
          <span className={`pill ${listening ? "live" : conn === "connected" ? "ok" : "warn"}`}>
            <span className="dot" />
            {listening ? "Listening" : conn === "connected" ? "Ready" : "Reconnecting"}
          </span>
          {listening && <LevelMeters sources={audio.current} />}
          <span className="pill subtle" title="Model mode">
            {state.status.llm ? "Nebius LLM" : "Rules-only mode"}
          </span>
          {listening ? (
            <button className="btn" onClick={stopListening}>Pause audio</button>
          ) : (
            <button className="btn" onClick={startListening} disabled={!state.status.stt}
              title={state.status.stt ? "Capture Meet tab + your mic" : "Set ELEVENLABS_API_KEY on the backend"}>
              ● Start listening
            </button>
          )}
          <button className="btn danger" onClick={endCall}>End call</button>
          <button className="btn ghost" onClick={reset} title="Reset demo deal">↺</button>
        </div>
      </header>
      {audioError && <div className="banner">{audioError}</div>}

      <main className="grid">
        <section className="panel">
          <h2>Live transcript</h2>
          <Transcript turns={state.transcript} partial={state.partial} />
          <div className="composer">
            <div className="golden">
              {GOLDEN.map((g, i) => (
                <button key={g} className="chip" onClick={() => sendTurn(g, "customer")} title={g}>
                  {i + 1}. {g.slice(0, 28)}…
                </button>
              ))}
            </div>
            <form onSubmit={(e) => { e.preventDefault(); sendTurn(draft); }}>
              <select value={speaker} onChange={(e) => setSpeaker(e.target.value)}>
                <option value="customer">Customer</option>
                <option value="rep">Rep</option>
              </select>
              <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Type a turn (dev mode)…" />
              <button className="btn primary" type="submit">Send</button>
            </form>
          </div>
        </section>

        <section className="panel">
          <h2>
            Suggestions {pending > 0 && <span className="count">{pending}</span>}
          </h2>
          <div className="feed">
            {state.suggestions.length === 0 && (
              <div className="empty">Listening for pricing, terms, compliance and legal questions…</div>
            )}
            {[...state.suggestions].map((s) => (
              <SuggestionCard
                key={s.id}
                s={s}
                escalation={state.escalations[s.id]}
                onAccept={() => send({ type: "accept", id: s.id })}
                onEscalate={() => send({ type: "escalate", id: s.id })}
              />
            ))}
            <div ref={feedEnd} />
          </div>
        </section>
      </main>

      <footer className="dealbar">
        <DealPanel deal={deal} lastChanged={state.lastChanged} />
        <ContractDiff diff={state.contractDiff} lastChanged={state.lastChanged} />
      </footer>

      {state.summary && (
        <PostCallSummary summary={state.summary} onClose={() => dispatch({ type: "close_summary" })} />
      )}
    </div>
  );
}
