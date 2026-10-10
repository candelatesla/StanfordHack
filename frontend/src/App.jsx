import { useEffect, useReducer, useRef, useState } from "react";
import { connectEvents } from "./ws.js";
import { MODES, createSender, openDevices } from "./audio/capture.js";
import Transcript from "./components/Transcript.jsx";
import SuggestionCard from "./components/SuggestionCard.jsx";
import DealPanel from "./components/DealPanel.jsx";
import ContractDiff from "./components/ContractDiff.jsx";
import PostCallSummary from "./components/PostCallSummary.jsx";
import Setup from "./components/Setup.jsx";

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
  callEnded: false,
  calibrating: false,
  stats: { turns: 0, skipped: 0, llm_calls: 0, embed_calls: 0, echoes: 0, voices: [] },
  status: { llm: false, stt: false, voice: false },
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
        stats: ev.stats || initial.stats,
      };
    case "transcript":
      if (!ev.is_final) return { ...state, partial: { ...state.partial, [ev.speaker]: ev.text } };
      return {
        ...state,
        transcript: [...state.transcript, ev],
        partial: ev.auto ? {} : { ...state.partial, [ev.speaker]: null },
      };
    case "transcript_update":
      return {
        ...state,
        transcript: state.transcript.map((t) => (t.id === ev.id ? { ...t, speaker: ev.speaker, auto: ev.auto, via: ev.via } : t)),
      };
    case "suggestion": {
      const exists = state.suggestions.some((s) => s.id === ev.id);
      const suggestions = exists
        ? state.suggestions.map((s) => (s.id === ev.id ? { ...s, ...ev } : s))
        : [...state.suggestions, ev];
      return { ...state, suggestions };
    }
    case "suggestion_removed":
      return { ...state, suggestions: state.suggestions.filter((s) => s.id !== ev.id) };
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
      return { ...state, summary: ev, callEnded: true };
    case "stats":
      return { ...state, stats: ev };
    case "calibrate_start":
      return { ...state, calibrating: true };
    case "calibrated":
      return { ...state, calibrating: false };
    case "close_summary":
      return { ...state, summary: null };
    default:
      return state;
  }
}

function LevelMeters({ senders }) {
  const [levels, setLevels] = useState([]);
  useEffect(() => {
    let raf;
    const tick = () => {
      setLevels(senders.map((s) => s.level()));
      raf = requestAnimationFrame(tick);
    };
    tick();
    return () => cancelAnimationFrame(raf);
  }, [senders]);
  const label = { customer: "Customer", rep: "Rep", auto: "Mic" };
  return (
    <span className="meters" title="Audio input levels">
      {senders.map((s, i) => (
        <span key={s.speaker} className="meter">
          {label[s.speaker]}
          <span className="meter-bar"><span style={{ width: `${Math.min(100, (levels[i] || 0) * 250)}%` }} /></span>
        </span>
      ))}
    </span>
  );
}

const MODE_KEY = "dealbrain.mode.v2"; // v2: call-tab mode became the default
const loadMode = () => {
  try {
    return localStorage.getItem(MODE_KEY) || "meet";
  } catch {
    return "meet";
  }
};

export default function App() {
  const [state, dispatch] = useReducer(reducer, initial);
  const [conn, setConn] = useState("connecting");
  const [mode, setMode] = useState(loadMode);
  const [senders, setSenders] = useState([]); // open for the whole page session once started
  const [paused, setPaused] = useState(false);
  const [starting, setStarting] = useState(false);
  const [audioStatus, setAudioStatus] = useState("");
  const [audioError, setAudioError] = useState("");
  const [draft, setDraft] = useState("");
  const [screen, setScreen] = useState(null); // "setup" (pick customer) | "call"; null until the server says hi
  const events = useRef(null);
  const feedEnd = useRef(null);

  useEffect(() => {
    events.current = connectEvents(dispatch, setConn);
    const release = () => sendersRef.current.forEach((s) => s.close());
    window.addEventListener("beforeunload", release);
    return () => {
      window.removeEventListener("beforeunload", release);
      events.current.close();
    };
  }, []);
  const sendersRef = useRef([]);
  sendersRef.current = senders;

  useEffect(() => {
    feedEnd.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [state.suggestions.length]);

  // First load: straight back into a call that is already running (page reload), otherwise the pre-call screen.
  useEffect(() => {
    if (screen === null && state.deal) setScreen(state.transcript.length ? "call" : "setup");
  }, [state.deal]);

  const send = (msg) => events.current?.send(msg);
  const live = senders.length > 0;
  const singleMic = senders.some((s) => s.speaker === "auto");
  const tabMode = senders.some((s) => s.speaker === "customer");
  const calibrate = () => {
    send({ type: "calibrate" });
    dispatch({ type: "calibrate_start" });
    setTimeout(() => dispatch({ type: "calibrated" }), 15000);
  };
  const st = state.stats;
  const repKnown = (st.voices || []).some((v) => v.role === "rep");
  const listening = live && !paused && !state.callEnded;

  const sendTurn = (text) => {
    if (!text.trim()) return;
    send({ type: "text_turn", speaker: "auto", text });
    setDraft("");
  };

  const chooseMode = (m) => {
    setMode(m);
    try {
      localStorage.setItem(MODE_KEY, m);
    } catch {
      /* private mode: just don't remember */
    }
  };

  // Asks for devices exactly once; everything after this is pause/resume on the same streams.
  const start = async () => {
    setAudioError("");
    setStarting(true);
    try {
      const devices = await openDevices(mode);
      const made = await Promise.all(
        devices.map(({ speaker, stream }) =>
          createSender(stream, speaker, {
            onStatus: (s, reason) => {
              setAudioStatus(s);
              if (s === "error") setAudioError(reason);
            },
            onEnded: (sp) =>
              setAudioError(`${sp === "customer" ? "Tab sharing" : "Microphone"} stopped. Click "Stop audio" then start again.`),
          })
        )
      );
      setSenders(made);
      setPaused(false);
    } catch (e) {
      setAudioError(e.name === "NotAllowedError" ? "Permission denied. Allow the mic (and tab share) to listen." : e.message || String(e));
    } finally {
      setStarting(false);
    }
  };

  const togglePause = () => {
    const p = !paused;
    senders.forEach((s) => s.setPaused(p));
    setPaused(p);
  };

  // Fully releases devices (the browser will ask again next time).
  const stopAudio = () => {
    senders.forEach((s) => s.close());
    setSenders([]);
    setPaused(false);
    setAudioStatus("");
  };

  const endCall = () => {
    if (state.callEnded) return;
    senders.forEach((s) => s.setPaused(true));
    send({ type: "end_call" });
  };

  const restartCall = () => {
    send({ type: "reset" });
    senders.forEach((s) => s.setPaused(false));
    setPaused(false);
  };

  // Pre-call screen -> live call on that account (audio devices stay open; they just resume).
  const startCall = async (accountId) => {
    const r = await fetch(`/api/accounts/${accountId}/call`, { method: "POST" });
    if (!r.ok) throw new Error(`Could not start the call (${r.status})`);
    senders.forEach((s) => s.setPaused(false));
    setPaused(false);
    setScreen("call");
  };

  const backToAccounts = () => {
    senders.forEach((s) => s.setPaused(true));
    setPaused(true);
    setScreen("setup");
  };

  const deal = state.deal;
  const pending = state.suggestions.filter((s) => !s.resolution).length;
  const pill = state.callEnded
    ? ["subtle", "Call ended"]
    : listening
      ? [audioStatus === "reconnecting" ? "warn" : "live", audioStatus === "reconnecting" ? "Reconnecting audio" : "Listening"]
      : live
        ? ["warn", "Paused"]
        : conn === "connected"
          ? ["ok", "Ready"]
          : ["warn", "Reconnecting"];

  if (screen !== "call") {
    return screen === "setup"
      ? <Setup initialAccount={deal?.account_id} onStart={startCall} />
      : <div className="empty center">Connecting to DealBrain…</div>;
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          {(!listening || state.callEnded) && (
            <button className="btn ghost sm" onClick={backToAccounts} title="Back to the customer's page">←</button>
          )}
          <span className="logo">◆</span> DealBrain
          <span className="crumb">Nimbus × {deal?.customer || "…"}{deal?.name ? ` · ${deal.name}` : ""}</span>
        </div>
        <div className="topbar-right">
          <span className={`pill ${pill[0]}`}>
            <span className="dot" />
            {pill[1]}
          </span>
          {listening && <LevelMeters senders={senders} />}
          <span className="pill subtle" title={`${st.turns} turns checked; ${st.skipped} were small talk or acknowledgements and cost nothing`}>
            API {st.llm_calls} LLM · {st.embed_calls} embed · {st.skipped} free
          </span>
          {singleMic && state.status.voice && (
            <span className="pill subtle" title="Voices heard on this call (single-mic voice identification)">
              🎙 {(st.voices || []).length} voice{(st.voices || []).length === 1 ? "" : "s"}{repKnown ? " · rep known" : ""}
            </span>
          )}
          <span className="pill subtle" title="Model mode">
            {state.status.llm ? "Nebius LLM" : "Rules-only mode"}
          </span>

          {!live && (
            <span className="start-group">
              <select value={mode} onChange={(e) => chooseMode(e.target.value)} title={MODES[mode].hint} disabled={starting}>
                {Object.entries(MODES).map(([k, m]) => (
                  <option key={k} value={k}>{m.label}</option>
                ))}
              </select>
              <button className="btn" onClick={start} disabled={!state.status.stt || starting || state.callEnded}
                title={state.status.stt ? MODES[mode].hint : "Set ELEVENLABS_API_KEY on the backend"}>
                {starting ? "Allow access…" : "● Start listening"}
              </button>
            </span>
          )}
          {live && !state.callEnded && (
            <>
              {singleMic && state.status.voice && (
                <button className="btn" onClick={calibrate} disabled={state.calibrating}
                  title="Rep: click, then say one sentence. Your voice is remembered for the rest of the call.">
                  {state.calibrating ? "Speak now…" : repKnown ? "Recalibrate" : "Calibrate my voice"}
                </button>
              )}
              <button className="btn" onClick={togglePause}>{paused ? "▶ Resume" : "❚❚ Pause"}</button>
              <button className="btn ghost" onClick={stopAudio} title="Release the mic / tab share">Stop audio</button>
            </>
          )}
          {state.callEnded ? (
            <button className="btn primary" onClick={backToAccounts}>Back to {deal?.customer || "customer"}</button>
          ) : (
            <button className="btn danger" onClick={endCall}>End call</button>
          )}
          <button className="btn ghost" onClick={restartCall} title="Restart this call and undo its CRM changes">↺</button>
        </div>
      </header>
      {audioError && <div className="banner">{audioError}</div>}
      {state.calibrating && <div className="banner info">Rep: say one sentence now so DealBrain learns your voice.</div>}
      {tabMode && st.echoes > 0 && !state.callEnded && (
        <div className="banner info">
          Your mic is picking up the customer from your speakers ({st.echoes} echo{st.echoes === 1 ? "" : "es"} filtered). <b>Wear headphones</b> for clean speaker labels.
        </div>
      )}
      {singleMic && state.status.voice && !repKnown && !state.calibrating && !state.callEnded && (
        <div className="banner info">Single-mic mode: click <b>Calibrate my voice</b> and say one sentence for the most reliable speaker detection.</div>
      )}

      <main className="grid">
        <section className="panel">
          <h2>
            Live transcript <span className="muted">{tabMode ? "speakers from audio source" : "speakers auto-detected"} · click a label to correct</span>
          </h2>
          <Transcript
            turns={state.transcript}
            partial={state.partial}
            onFlip={(t) => send({ type: "set_speaker", id: t.id, speaker: t.speaker === "customer" ? "rep" : "customer" })}
          />
          <div className="composer">
            <form onSubmit={(e) => { e.preventDefault(); sendTurn(draft); }}>
              <input value={draft} onChange={(e) => setDraft(e.target.value)}
                placeholder="No audio? Type what was said; the speaker is detected automatically" />
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
            {state.suggestions.map((s) => (
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
