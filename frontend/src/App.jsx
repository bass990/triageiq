import { useState, useEffect, useRef } from "react";
import "./App.css";

const API = import.meta.env.VITE_API_URL || "http://localhost:8001";

const ESI_CONFIG = {
  1: { label: "ESI 1 — Immediate",   color: "#EF4444", bg: "rgba(239,68,68,0.08)",   border: "rgba(239,68,68,0.25)" },
  2: { label: "ESI 2 — Emergent",    color: "#F97316", bg: "rgba(249,115,22,0.08)",  border: "rgba(249,115,22,0.25)" },
  3: { label: "ESI 3 — Urgent",      color: "#EAB308", bg: "rgba(234,179,8,0.08)",   border: "rgba(234,179,8,0.25)" },
  4: { label: "ESI 4 — Less Urgent", color: "#3B82F6", bg: "rgba(59,130,246,0.08)",  border: "rgba(59,130,246,0.25)" },
  5: { label: "ESI 5 — Non-Urgent",  color: "#22C55E", bg: "rgba(34,197,94,0.08)",   border: "rgba(34,197,94,0.25)" },
};

const AGENT_LABELS = {
  coordinator: "Coordinator",
  vitals:      "Vitals Analyzer",
  symptoms:    "Symptom Classifier",
  protocols:   "Protocol Matcher",
  beds:        "Bed Allocator",
  synthesizer: "Synthesizer",
};

function renderMarkdown(text) {
  if (!text) return null;
  const html = text
    .replace(/^### (.+)$/gm, '<h4>$1</h4>')
    .replace(/^## (.+)$/gm, '<h3>$1</h3>')
    .replace(/^# (.+)$/gm, '<h2>$1</h2>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/^- (.+)$/gm, '<li>$1</li>')
    .replace(/(<li>[^<]*<\/li>\n?)+/g, (match) => `<ul>${match}</ul>`)
    .replace(/^---$/gm, '<hr/>')
    .replace(/\n{2,}/g, '</p><p>')
    .replace(/^(?!<[huli]|<hr|<p|<\/p)(.+)$/gm, '<p>$1</p>');
  return <div className="detail-text markdown" dangerouslySetInnerHTML={{ __html: html }} />;
}

function inlineMarkdown(text) {
  if (!text) return "";
  return text
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>');
}

function Sparkline({ data, color, width = 80, height = 32 }) {
  if (!data || data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * width;
    const y = height - ((v - min) / range) * (height - 6) - 3;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const last = pts.split(" ").pop().split(",");
  return (
    <svg width={width} height={height} style={{ display: "block", overflow: "visible" }}>
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" opacity="0.8" />
      <circle cx={last[0]} cy={last[1]} r="2.5" fill={color} />
    </svg>
  );
}

function VitalsTrends({ vitals, trends }) {
  const defs = [
    { key: "hr",   label: "Heart Rate",  unit: "bpm",  color: "#EF4444", normal: "60–100" },
    { key: "spo2", label: "SpO₂",        unit: "%",    color: "#3B82F6", normal: ">95" },
    { key: "rr",   label: "Resp. Rate",  unit: "/min", color: "#8B5CF6", normal: "12–20" },
    { key: "temp", label: "Temperature", unit: "°C",   color: "#F97316", normal: "36.1–37.2" },
    { key: "gcs",  label: "GCS",         unit: "",     color: "#22C55E", normal: "15" },
  ];
  let sysBP = null;
  if (vitals.bp) { sysBP = parseFloat(vitals.bp.toString().split("/")[0]); }
  return (
    <div className="vitals-grid">
      {sysBP !== null && (
        <div className="vital-card">
          <div className="vital-header">
            <span className="vital-label">Blood Pressure</span>
            <span className="vital-normal">Normal: 90–140/60–90</span>
          </div>
          <div className="vital-value" style={{ color: sysBP < 90 ? "#EF4444" : sysBP > 140 ? "#F97316" : "#22C55E" }}>
            {vitals.bp} <span className="vital-unit">mmHg</span>
          </div>
          {trends?.bp_sys && <div className="vital-sparkline">
            <Sparkline data={trends.bp_sys} color="#EF4444" />
            <span className="vital-trend-label">{trends.bp_sys.length} readings</span>
          </div>}
        </div>
      )}
      {defs.map(({ key, label, unit, color, normal }) => {
        const val = vitals[key];
        if (val === undefined) return null;
        return (
          <div key={key} className="vital-card">
            <div className="vital-header">
              <span className="vital-label">{label}</span>
              <span className="vital-normal">Normal: {normal}</span>
            </div>
            <div className="vital-value" style={{ color }}>
              {val} <span className="vital-unit">{unit}</span>
            </div>
            {trends?.[key] && (
              <div className="vital-sparkline">
                <Sparkline data={trends[key]} color={color} />
                <span className="vital-trend-label">{trends[key].length} readings</span>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function ConfidenceMeter({ score }) {
  const pct = Math.round((score || 0.85) * 100);
  const color = pct >= 80 ? "#22C55E" : pct >= 60 ? "#EAB308" : "#EF4444";
  const label = pct >= 80 ? "High" : pct >= 60 ? "Moderate" : "Low";
  return (
    <div className="confidence-meter">
      <div className="confidence-header">
        <span className="confidence-label">AI Confidence</span>
        <span className="confidence-pct" style={{ color }}>{pct}% · {label}</span>
      </div>
      <div className="confidence-bar-bg">
        <div className="confidence-bar-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
    </div>
  );
}

function NurseOverride({ currentESI, onOverride, override }) {
  const [open, setOpen] = useState(false);
  const [sel, setSel] = useState(currentESI);
  const [reason, setReason] = useState("");
  if (override) {
    return (
      <div className="override-applied">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M2.5 7l3 3 6-6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>
        Nurse override: ESI {override.esi} — <em>{override.reason}</em>
      </div>
    );
  }
  return (
    <div className="override-container">
      {!open ? (
        <button className="override-btn" onClick={() => setOpen(true)}>Override ESI Score</button>
      ) : (
        <div className="override-panel">
          <div className="override-title">Nurse Override</div>
          <div className="override-esi-row">
            {[1,2,3,4,5].map(n => (
              <button key={n} className={`override-esi-btn ${sel === n ? "sel" : ""}`}
                style={sel === n ? { background: ESI_CONFIG[n].color, borderColor: ESI_CONFIG[n].color, color: "#fff" } : {}}
                onClick={() => setSel(n)}>{n}</button>
            ))}
          </div>
          <textarea className="override-reason" placeholder="Reason for override (required)..."
            value={reason} onChange={e => setReason(e.target.value)} rows={2} />
          <div className="override-actions">
            <button className="override-cancel" onClick={() => setOpen(false)}>Cancel</button>
            <button className="override-confirm" disabled={!reason.trim()}
              onClick={() => { onOverride({ esi: sel, reason }); setOpen(false); }}>Confirm Override</button>
          </div>
        </div>
      )}
    </div>
  );
}

function ConversationChat({ report }) {
  const [messages, setMessages] = useState([
    { role: "assistant", text: "Triage complete. Ask me anything about this patient — the clinical reasoning, protocol steps, or why I assigned this ESI score." }
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef(null);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);
  const send = async () => {
    if (!input.trim() || loading) return;
    const userMsg = input.trim();
    setInput("");
    setMessages(prev => [...prev, { role: "user", text: userMsg }]);
    setLoading(true);
    try {
      const res = await fetch(`${API}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: userMsg, report, history: messages })
      });
      const data = await res.json();
      setMessages(prev => [...prev, { role: "assistant", text: data.reply }]);
    } catch {
      setMessages(prev => [...prev, { role: "assistant", text: "Couldn't reach the backend." }]);
    }
    setLoading(false);
  };
  return (
    <div className="chat-container">
      <div className="chat-messages">
        {messages.map((m, i) => (
          <div key={i} className={`chat-bubble ${m.role}`}>
            {m.role === "assistant" && <div className="chat-avatar">+</div>}
            <div
              className="chat-text"
              dangerouslySetInnerHTML={{ __html: inlineMarkdown(m.text) }}
            />
          </div>
        ))}
        {loading && (
          <div className="chat-bubble assistant">
            <div className="chat-avatar">+</div>
            <div className="chat-typing"><span/><span/><span/></div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>
      <div className="chat-input-row">
        <input className="chat-input" value={input} onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === "Enter" && send()} placeholder="Ask about this patient..." />
        <button className="chat-send" onClick={send} disabled={!input.trim() || loading}>→</button>
      </div>
    </div>
  );
}

function printReport(report, override, confidence) {
  const esi = override ? override.esi : report.esi_score;
  const esiInfo = ESI_CONFIG[esi];
  const pct = Math.round((confidence || 0.85) * 100);
  const win = window.open("", "_blank");
  win.document.write(`<!DOCTYPE html><html><head><title>TriageIQ Report</title>
  <style>
    body{font-family:'Helvetica Neue',sans-serif;max-width:800px;margin:40px auto;color:#0f172a;font-size:13px;line-height:1.6}
    h1{font-size:20px;margin-bottom:2px} .meta{color:#64748b;font-size:11px;margin-bottom:20px}
    .banner{border:2px solid ${esiInfo.color};border-radius:8px;padding:14px 18px;margin-bottom:18px;display:flex;align-items:center;gap:14px}
    .esi-n{font-size:44px;font-weight:700;color:${esiInfo.color};line-height:1}
    .esi-l{font-size:15px;font-weight:600;color:${esiInfo.color}} .esi-s{font-size:12px;color:#475569;margin-top:3px}
    .section{margin-bottom:18px;border-top:1px solid #e2e8f0;padding-top:12px}
    .stitle{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#0d9488;margin-bottom:6px}
    .sbody{white-space:pre-wrap;font-size:12px}
    .override{background:#fef9c3;border:1px solid #fde047;border-radius:6px;padding:8px 12px;margin-bottom:14px;font-size:12px}
    .footer{margin-top:36px;border-top:1px solid #e2e8f0;padding-top:10px;font-size:10px;color:#94a3b8}
  </style></head><body>
  <h1>TriageIQ Triage Report</h1>
  <div class="meta">Generated ${new Date().toLocaleString()} &nbsp;·&nbsp; AI Confidence: ${pct}% &nbsp;·&nbsp; Decision-support only · Nurse makes final call</div>
  ${override ? `<div class="override">⚠️ Nurse Override Applied: ESI changed to ${override.esi} — ${override.reason}</div>` : ""}
  <div class="banner">
    <div class="esi-n">${esi}</div>
    <div><div class="esi-l">${esiInfo.label}</div><div class="esi-s">${report.patient_summary}</div></div>
  </div>
  <div class="section"><div class="stitle">Vitals Analysis</div><div class="sbody">${report.vitals_detail || report.vitals_findings || ""}</div></div>
  <div class="section"><div class="stitle">Symptom Assessment</div><div class="sbody">${report.symptom_detail || report.symptom_findings || ""}</div></div>
  <div class="section"><div class="stitle">Protocol Match</div><div class="sbody">${report.protocol_detail || report.protocol_findings || ""}</div></div>
  <div class="section"><div class="stitle">Bed & Resources</div><div class="sbody">${report.bed_detail || report.bed_recommendation || ""}</div></div>
  <div class="footer">TriageIQ · ER Multi-Agent Decision Support · Decision-support only, does not replace nurse judgment.</div>
  </body></html>`);
  win.document.close();
  setTimeout(() => win.print(), 300);
}

function AgentStatus({ activeAgents, completedAgents, agentTimes }) {
  return (
    <div className="agent-grid">
      {Object.entries(AGENT_LABELS).map(([key, label]) => {
        const isActive = activeAgents.includes(key);
        const isDone = completedAgents.includes(key);
        return (
          <div key={key} className={`agent-card ${isActive ? "active" : ""} ${isDone ? "done" : ""}`}>
            <div className="agent-indicator">
              {isDone ? (
                <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M2 6l3 3 5-5" stroke="white" strokeWidth="1.5" strokeLinecap="round"/></svg>
              ) : isActive ? <div className="agent-spinner"/> : <div className="agent-dot"/>}
            </div>
            <span className="agent-label">{label}</span>
            {isDone && agentTimes?.[key] && <span className="agent-time">{agentTimes[key]}s</span>}
          </div>
        );
      })}
    </div>
  );
}

function TriageReport({ report }) {
  const [activeTab, setActiveTab] = useState("summary");
  const [override, setOverride] = useState(null);
  const displayESI = override ? override.esi : report.esi_score;
  const esi = ESI_CONFIG[displayESI] || ESI_CONFIG[3];
  const confidence = report.confidence || 0.85;
  const tabs = ["summary", "vitals", "symptoms", "protocol", "resources", "chat"];
  return (
    <div className="report-container">
      <div className="esi-banner" style={{ background: esi.bg, border: `1px solid ${esi.border}` }}>
        <div className="esi-score" style={{ color: esi.color }}>{displayESI}</div>
        <div className="esi-info">
          <div className="esi-label" style={{ color: esi.color }}>{esi.label}</div>
          <div className="esi-patient">{report.patient_summary}</div>
        </div>
        <div className="esi-right">
          <ConfidenceMeter score={confidence} />
          <div className="esi-note">Decision-support · Nurse makes final call</div>
        </div>
      </div>

      <NurseOverride currentESI={report.esi_score} override={override} onOverride={setOverride} />

      <div className="report-tabs">
        {tabs.map(tab => (
          <button key={tab} className={`tab-btn ${activeTab === tab ? "active" : ""}`} onClick={() => setActiveTab(tab)}>
            {{"summary":"Summary","vitals":"Vitals","symptoms":"Symptoms","protocol":"Protocol","resources":"Resources","chat":"Ask AI"}[tab]}
          </button>
        ))}
        <button className="print-btn" onClick={() => printReport(report, override, confidence)}>↓ Print</button>
      </div>

      <div className="tab-content">
        {activeTab === "summary" && (
          <div className="summary-tab">
            {report.vitals_findings && (
              <div className="detail-section">
                <div className="detail-label">Vitals Assessment</div>
                {renderMarkdown(report.vitals_findings)}
              </div>
            )}
            {report.symptom_findings && (
              <div className="detail-section">
                <div className="detail-label">Symptom Assessment</div>
                {renderMarkdown(report.symptom_findings)}
              </div>
            )}
            <div className="detail-section">
              <div className="detail-label">Protocol Match</div>
              {renderMarkdown(report.protocol_findings)}
            </div>
            <div className="detail-section">
              <div className="detail-label">Bed Assignment</div>
              {renderMarkdown(report.bed_recommendation)}
            </div>
          </div>
        )}
        {activeTab === "vitals" && (
          <div>
            {report.vitals_raw
              ? <VitalsTrends vitals={report.vitals_raw} trends={report.vitals_trends} />
              : <div className="detail-section">{renderMarkdown(report.vitals_detail)}</div>
            }
            {report.vitals_raw && (
              <div className="detail-section" style={{ marginTop: "1.25rem" }}>
                <div className="detail-label">Analysis</div>
                {renderMarkdown(report.vitals_detail)}
              </div>
            )}
          </div>
        )}
        {activeTab === "symptoms" && (
          <div className="detail-section">{renderMarkdown(report.symptom_detail)}</div>
        )}
        {activeTab === "protocol" && (
          <div className="detail-section">{renderMarkdown(report.protocol_detail)}</div>
        )}
        {activeTab === "resources" && (
          <div className="detail-section">{renderMarkdown(report.bed_detail)}</div>
        )}
        {activeTab === "chat" && <ConversationChat report={report} />}
      </div>
    </div>
  );
}

export default function App() {
  const [patients, setPatients] = useState([]);
  const [selectedPatient, setSelectedPatient] = useState(null);
  const [inputMode, setInputMode] = useState("patient");
  const [phase, setPhase] = useState("select");
  const [isLoading, setIsLoading] = useState(false);
  const [activeAgents, setActiveAgents] = useState([]);
  const [completedAgents, setCompletedAgents] = useState([]);
  const [progressMsg, setProgressMsg] = useState("");
  const [report, setReport] = useState(null);
  const [errorMsg, setErrorMsg] = useState("");
  const [darkMode, setDarkMode] = useState(false);
  const [freeText, setFreeText] = useState("");
  const [freeTextVitals, setFreeTextVitals] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const [agentTimes, setAgentTimes] = useState({});
  const elapsedRef = useRef(null);
  const agentStartTimesRef = useRef({});

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", darkMode ? "dark" : "light");
  }, [darkMode]);

  useEffect(() => {
    fetch(`${API}/patients`).then(r => r.json()).then(d => setPatients(d.patients || [])).catch(() => {});
  }, []);

  const agentOrder = ["coordinator", "vitals", "symptoms", "protocols", "beds", "synthesizer"];

  const runTriage = async (patientIdOrText) => {
    if (isLoading) return;
    setIsLoading(true);
    setPhase("analyzing");
    setActiveAgents(["coordinator"]);
    setCompletedAgents([]);
    setReport(null);
    setElapsed(0);
    setAgentTimes({});
    agentStartTimesRef.current = {};
    clearInterval(elapsedRef.current);
    elapsedRef.current = setInterval(() => setElapsed(s => s + 1), 1000);

    try {
      if (patientIdOrText.startsWith("PT-")) {
        const evtSource = new EventSource(`${API}/triage/stream/${patientIdOrText}`);
        evtSource.addEventListener("status", (e) => {
          const data = JSON.parse(e.data);
          setProgressMsg(data.message);
          if (data.active_agents) {
            const now = Date.now();
            data.active_agents.forEach(a => { agentStartTimesRef.current[a] = now; });
            setActiveAgents(data.active_agents);
            setCompletedAgents(prev => [...new Set([...prev, "coordinator"])]);
          } else if (data.agent_done) {
            const start = agentStartTimesRef.current[data.agent_done];
            if (start) {
              const duration = ((Date.now() - start) / 1000).toFixed(1);
              setAgentTimes(prev => ({ ...prev, [data.agent_done]: duration }));
            }
            setActiveAgents(prev => prev.filter(a => a !== data.agent_done));
            setCompletedAgents(prev => [...new Set([...prev, data.agent_done])]);
          } else if (data.agent && AGENT_LABELS[data.agent]) {
            setActiveAgents([data.agent]);
            setCompletedAgents(agentOrder.slice(0, agentOrder.indexOf(data.agent)));
          }
        });
        evtSource.addEventListener("complete", (e) => {
          evtSource.close();
          const data = JSON.parse(e.data);
          clearInterval(elapsedRef.current);
          setReport(data.report); setActiveAgents([]);
          setCompletedAgents(agentOrder); setPhase("results");
          setIsLoading(false);
        });
        evtSource.addEventListener("error", (e) => {
          evtSource.close();
          clearInterval(elapsedRef.current);
          try { setErrorMsg(JSON.parse(e.data).message); }
          catch { setErrorMsg("Connection error. Is the backend running on port 8001?"); }
          setPhase("error");
          setIsLoading(false);
        });
      } else {
        // Fire POST immediately, animate progress while it's in flight
        const fetchPromise = fetch(`${API}/triage/freetext`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ description: patientIdOrText, vitals: freeTextVitals.trim() || undefined })
        });

        setActiveAgents(["coordinator"]);
        setProgressMsg("Retrieving patient data...");

        await Promise.race([fetchPromise, new Promise(r => setTimeout(r, 2000))]);
        setActiveAgents(["vitals", "symptoms", "protocols", "beds"]);
        setCompletedAgents(["coordinator"]);
        setProgressMsg("Running all specialists in parallel...");

        const res = await fetchPromise;

        setActiveAgents(["synthesizer"]);
        setCompletedAgents(["coordinator", "vitals", "symptoms", "protocols", "beds"]);
        setProgressMsg("Synthesizing final triage decision...");
        await new Promise(r => setTimeout(r, 600));

        if (!res.ok) {
          const body = await res.json().catch(() => null);
          const detail = body?.detail;
          const msg = Array.isArray(detail)
            ? detail.map(e => e.msg).join(", ")
            : detail || `Server error (${res.status})`;
          throw new Error(msg);
        }

        const data = await res.json();
        clearInterval(elapsedRef.current);
        setReport(data.report); setActiveAgents([]);
        setCompletedAgents(agentOrder); setPhase("results");
        setIsLoading(false);
      }
    } catch (err) {
      clearInterval(elapsedRef.current);
      setErrorMsg(err.message); setPhase("error");
      setIsLoading(false);
    }
  };

  const reset = () => {
    clearInterval(elapsedRef.current);
    setElapsed(0); setAgentTimes({});
    setPhase("select"); setSelectedPatient(null); setReport(null);
    setActiveAgents([]); setCompletedAgents([]); setProgressMsg("");
    setErrorMsg(""); setIsLoading(false); setFreeText(""); setFreeTextVitals("");
  };

  return (
    <div className="app">
      <header className="app-header">
        <div className="header-logo">
          <div className="header-icon">+</div>
          <span className="header-brand">TriageIQ</span>
        </div>
        <div className="header-tagline">ER Multi-Agent Decision Support</div>
        <div className="header-badge">Decision-support only · Nurse makes final call</div>
        <div className="header-actions">
          <button className="dark-toggle" onClick={() => setDarkMode(d => !d)}>{darkMode ? "☀️" : "🌙"}</button>
          {phase !== "select" && <button className="header-reset" onClick={reset}>New Patient</button>}
        </div>
      </header>
      <main className="app-main">
        {phase === "select" && (
          <div className="select-phase">
            <div className="select-hero">
              <h1>A triage team in<br /><em>60 seconds.</em></h1>
              <p>4 specialist agents analyze vitals, symptoms, protocols, and resources in parallel — then synthesize a priority score and action plan.</p>
            </div>
            <div className="mode-tabs">
              <button className={`mode-tab ${inputMode === "patient" ? "active" : ""}`} onClick={() => setInputMode("patient")}>Demo Patients</button>
              <button className={`mode-tab ${inputMode === "freetext" ? "active" : ""}`} onClick={() => setInputMode("freetext")}>Free Text Input</button>
            </div>
            {inputMode === "patient" && (
              <div className="patient-list">
                {patients.map(p => (
                  <div key={p.id} className={`patient-card ${selectedPatient === p.id ? "selected" : ""}`}
                    onClick={() => { if (!isLoading) { setSelectedPatient(p.id); runTriage(p.id); } }}>
                    <div className="patient-card-header">
                      <div className="patient-name">{p.name}</div>
                      {p.acuity_hint && (
                        <div className="patient-acuity" style={{ background: ESI_CONFIG[p.acuity_hint]?.color }}>
                          ESI {p.acuity_hint}
                        </div>
                      )}
                    </div>
                    <div className="patient-complaint">{p.chief_complaint}</div>
                  </div>
                ))}
                {patients.length === 0 && <div className="no-patients">Loading patients... (is backend running on port 8001?)</div>}
              </div>
            )}
            {inputMode === "freetext" && (
              <div className="freetext-container">
                <label className="freetext-label">Patient Description</label>
                <textarea className="freetext-input" value={freeText} onChange={e => setFreeText(e.target.value)}
                  placeholder="e.g. 65M presenting with chest pain radiating to left arm, diaphoresis, nausea for 45 minutes. HTN, type 2 diabetes." rows={4} />
                <label className="freetext-label">Vitals <span className="freetext-label-optional">(optional)</span></label>
                <textarea className="freetext-input" value={freeTextVitals} onChange={e => setFreeTextVitals(e.target.value)}
                  placeholder="e.g. BP 88/60, HR 112, RR 22, SpO₂ 94%, Temp 37.1°C, GCS 15" rows={2} />
                <button className={`triage-btn ${freeText.trim() && !isLoading ? "ready" : "disabled"}`}
                  onClick={() => freeText.trim() && runTriage(freeText)} disabled={!freeText.trim() || isLoading}>
                  Run Triage →
                </button>
              </div>
            )}
          </div>
        )}
        {phase === "analyzing" && (
          <div className="analyzing-phase">
            <h2>Dispatching specialist team...</h2>
            <div className="elapsed-timer">{Math.floor(elapsed/60)}:{(elapsed%60).toString().padStart(2,'0')}</div>
            <p className="progress-msg">{progressMsg}</p>
            <button className="cancel-btn" onClick={reset}>Cancel</button>
            <AgentStatus activeAgents={activeAgents} completedAgents={completedAgents} agentTimes={agentTimes} />
          </div>
        )}
        {phase === "error" && (
          <div className="error-phase">
            <div className="error-icon">!</div>
            <h2>Triage failed</h2>
            <p>{errorMsg}</p>
            <button className="triage-btn ready" onClick={reset}>Try Again</button>
          </div>
        )}
        {phase === "results" && report && (
          <div className="results-phase">
            <TriageReport report={report} />
          </div>
        )}
      </main>
    </div>
  );
}
