const PptxGenJS = require("pptxgenjs");
const pres = new PptxGenJS();
pres.layout = "LAYOUT_16x9";
pres.title = "TriageIQ — ER Multi-Agent Triage System";

const DARK    = "0F172A"; const TEAL    = "0D9488"; const TEAL_LT = "E0F2FE";
const PURPLE  = "7C3AED"; const INK     = "1E293B"; const MUTED   = "64748B";
const BORDER  = "E2E8F0"; const WHITE   = "FFFFFF"; const SURFACE = "F8FAFC";
const CRIT    = "DC2626"; const HIGH    = "EA580C"; const WARN    = "D97706";

const makeShadow = () => ({ type:"outer", blur:8, offset:2, angle:135, color:"000000", opacity:0.08 });

function hdr(s, title, sub) {
  s.addShape(pres.shapes.RECTANGLE, { x:0, y:0, w:10, h:0.85, fill:{color:DARK}, line:{color:DARK} });
  s.addText(title, { x:0.5, y:0, w:7, h:0.85, fontSize:22, fontFace:"Georgia", bold:true, color:WHITE, valign:"middle", margin:0 });
  if (sub) s.addText(sub, { x:0.5, y:0, w:9.3, h:0.85, fontSize:10, fontFace:"Calibri", color:"475569", align:"right", valign:"middle", margin:0 });
}

// ── SLIDE 1 — TITLE ──────────────────────────────────────────────────────────
{
  const s = pres.addSlide();
  s.background = { color: DARK };
  s.addShape(pres.shapes.RECTANGLE, { x:0, y:0, w:0.14, h:5.625, fill:{color:TEAL}, line:{color:TEAL} });

  // Left content
  s.addText("TriageIQ", { x:0.4, y:1.1, w:5.5, h:1.2, fontSize:54, fontFace:"Georgia", bold:true, color:WHITE, margin:0 });
  s.addText("ER Multi-Agent Decision Support", { x:0.4, y:2.4, w:5.5, h:0.45, fontSize:17, fontFace:"Calibri Light", italic:true, color:"94A3B8", margin:0 });
  s.addShape(pres.shapes.RECTANGLE, { x:0.4, y:3.0, w:4.5, h:0.025, fill:{color:TEAL, transparency:40}, line:{color:TEAL, transparency:40} });
  s.addText("4 specialist agents · Parallel execution · 60-second triage decision", { x:0.4, y:3.15, w:5.5, h:0.4, fontSize:12, fontFace:"Calibri", color:"64748B", margin:0 });
  s.addText(["Project 2/3  ·  Week 2  ·  BUAN 6v99.s01  ·  UT Dallas  ·  April 2026"].join(""), { x:0.4, y:4.6, w:5.5, h:0.35, fontSize:9.5, fontFace:"Calibri", color:"334155", margin:0 });

  // Right panel
  s.addShape(pres.shapes.RECTANGLE, { x:6.3, y:1.2, w:3.4, h:3.0, fill:{color:WHITE}, line:{color:WHITE} });
  s.addText("140M", { x:6.3, y:1.3, w:3.4, h:1.2, fontSize:56, fontFace:"Georgia", bold:true, color:DARK, align:"center", margin:0 });
  s.addText("ER visits per year (US)", { x:6.3, y:2.55, w:3.4, h:0.35, fontSize:12, fontFace:"Calibri", color:MUTED, align:"center", margin:0 });
  s.addText("Triage errors are preventable.", { x:6.3, y:3.0, w:3.4, h:0.35, fontSize:11, fontFace:"Calibri", color:TEAL, bold:true, align:"center", margin:0 });
}

// ── SLIDE 2 — THE PROBLEM ────────────────────────────────────────────────────
{
  const s = pres.addSlide();
  s.background = { color: WHITE };
  hdr(s, "The Problem", "Healthcare · Emergency Medicine");

  s.addShape(pres.shapes.RECTANGLE, { x:0.5, y:1.05, w:9, h:1.5, fill:{color:TEAL_LT}, line:{color:TEAL_LT} });
  s.addShape(pres.shapes.RECTANGLE, { x:0.5, y:1.05, w:0.07, h:1.5, fill:{color:TEAL}, line:{color:TEAL} });
  s.addText("Triage nurses assess patients in under 3 minutes — under stress, with incomplete data, in a noisy environment. The static ESI checklist gives no intelligent guidance. Mistriaged patients deteriorate in waiting rooms.", {
    x:0.75, y:1.12, w:8.6, h:1.35, fontSize:14, fontFace:"Georgia", italic:true, color:DARK, valign:"middle", margin:0
  });

  const cards = [
    { n:"3 min", sub:"average triage\ntime per patient",  color:CRIT, bg:"FEF2F2" },
    { n:"27%",   sub:"of ED deaths linked\nto triage error", color:HIGH, bg:"FFF7ED" },
    { n:"0",     sub:"affordable AI tools\nfor triage nurses", color:TEAL, bg:TEAL_LT },
  ];
  cards.forEach((c, i) => {
    const x = 0.5 + i*3.15;
    s.addShape(pres.shapes.RECTANGLE, { x, y:2.85, w:2.95, h:2.0, fill:{color:c.bg}, line:{color:c.color, width:1}, shadow:makeShadow() });
    s.addText(c.n, { x:x+0.15, y:2.95, w:2.65, h:0.7, fontSize:32, fontFace:"Georgia", bold:true, color:c.color, align:"center", margin:0 });
    s.addText(c.sub, { x:x+0.15, y:3.7, w:2.65, h:0.95, fontSize:11, fontFace:"Calibri", color:INK, align:"center", margin:0 });
  });
  s.addText("Triage is not one problem — it is four simultaneous problems: vitals, symptoms, protocols, and resources.", {
    x:0.5, y:5.05, w:9, h:0.35, fontSize:10, fontFace:"Calibri", italic:true, color:MUTED, align:"center", margin:0
  });
}

// ── SLIDE 3 — THE ARCHITECTURE ───────────────────────────────────────────────
{
  const s = pres.addSlide();
  s.background = { color: WHITE };
  hdr(s, "Multi-Agent Architecture", "5 agents · Parallel execution · Claude SDK");
  s.addText("One coordinator. Four specialists. One synthesizer.", { x:0.5, y:1.0, w:9, h:0.4, fontSize:14, fontFace:"Georgia", italic:true, color:TEAL, align:"center", margin:0 });

  // Coordinator
  s.addShape(pres.shapes.RECTANGLE, { x:3.5, y:1.55, w:3.0, h:0.6, fill:{color:DARK}, line:{color:DARK} });
  s.addText("COORDINATOR AGENT", { x:3.5, y:1.55, w:3.0, h:0.6, fontSize:10, fontFace:"Calibri", bold:true, color:WHITE, align:"center", valign:"middle", margin:0 });

  // Arrows down
  [1.5, 3.0, 4.5, 6.0].forEach(x => {
    s.addShape(pres.shapes.LINE, { x, y:2.15, w:0, h:0.4, line:{color:BORDER, width:1} });
  });

  // 4 specialist boxes
  const specialists = [
    { label:"Vitals\nAnalyzer",    color:CRIT, bg:"FEF2F2",  x:0.35 },
    { label:"Symptom\nClassifier", color:HIGH, bg:"FFF7ED",  x:2.7  },
    { label:"Protocol\nMatcher",   color:TEAL, bg:TEAL_LT,   x:5.05 },
    { label:"Bed\nAllocator",      color:PURPLE, bg:"F5F0FF", x:7.4  },
  ];
  specialists.forEach(sp => {
    s.addShape(pres.shapes.RECTANGLE, { x:sp.x, y:2.55, w:2.2, h:1.1, fill:{color:sp.bg}, line:{color:sp.color, width:1}, shadow:makeShadow() });
    s.addText(sp.label, { x:sp.x+0.1, y:2.6, w:2.0, h:1.0, fontSize:10.5, fontFace:"Calibri", bold:true, color:sp.color, align:"center", valign:"middle", margin:0 });
  });

  // "PARALLEL" label
  s.addText("← run in parallel →", { x:0.35, y:3.7, w:9.3, h:0.3, fontSize:9, fontFace:"Calibri", italic:true, color:MUTED, align:"center", margin:0 });

  // Arrows to synthesizer
  [1.5, 3.0, 4.5, 6.0].forEach(x => {
    s.addShape(pres.shapes.LINE, { x, y:4.05, w:0, h:0.35, line:{color:BORDER, width:1} });
  });

  // Synthesizer
  s.addShape(pres.shapes.RECTANGLE, { x:3.2, y:4.4, w:3.6, h:0.6, fill:{color:TEAL}, line:{color:TEAL} });
  s.addText("SYNTHESIZER — Final ESI Score + Action Plan", { x:3.2, y:4.4, w:3.6, h:0.6, fontSize:9.5, fontFace:"Calibri", bold:true, color:WHITE, align:"center", valign:"middle", margin:0 });

  s.addShape(pres.shapes.LINE, { x:5.0, y:5.0, w:0, h:0.35, line:{color:BORDER, width:1} });
  s.addShape(pres.shapes.RECTANGLE, { x:3.5, y:5.35, w:3.0, h:0.45, fill:{color:"F0FDFA"}, line:{color:TEAL, width:1} });
  s.addText("Triage report → nurse dashboard", { x:3.5, y:5.35, w:3.0, h:0.45, fontSize:10, fontFace:"Calibri", color:TEAL, bold:true, align:"center", valign:"middle", margin:0 });
}

// ── SLIDE 4 — LIVE DEMO ──────────────────────────────────────────────────────
{
  const s = pres.addSlide();
  s.background = { color: DARK };
  hdr(s, "Live Demo", "");

  const steps = [
    { t:"0:00", a:"Open browser → localhost:3001" },
    { t:"1:00", a:"Select demo patient PT-001: '65M, chest pain, BP 88/60, HR 112'" },
    { t:"1:30", a:"Click Run Triage → watch agent grid: 4 agents activate simultaneously" },
    { t:"3:00", a:"Show terminal: 4 specialist loops running — vitals, symptoms, protocol, beds" },
    { t:"4:30", a:"ESI Level 1 — Immediate appears. Walk through action checklist." },
    { t:"6:00", a:"Click each tab: Vitals / Symptoms / Protocol / Resources — show depth" },
    { t:"7:00", a:"'4 agents ran in parallel. A single agent would take 4x longer and miss the cross-specialty insight.'" },
  ];
  steps.forEach((d, i) => {
    const y = 1.05 + i * 0.62;
    s.addShape(pres.shapes.RECTANGLE, { x:0.5, y, w:0.9, h:0.5, fill:{color:TEAL}, line:{color:TEAL} });
    s.addText(d.t, { x:0.5, y, w:0.9, h:0.5, fontSize:9.5, fontFace:"Courier New", bold:true, color:WHITE, align:"center", valign:"middle", margin:0 });
    s.addShape(pres.shapes.RECTANGLE, { x:1.55, y, w:8.1, h:0.5, fill:{color:"162032"}, line:{color:"1E3A5F"} });
    s.addText(d.a, { x:1.7, y, w:7.8, h:0.5, fontSize:10.5, fontFace:"Calibri", color:"94A3B8", valign:"middle", margin:0 });
  });
  s.addText("Key moment: agent grid shows 4 simultaneous indicators — this is visible parallel execution", { x:0.5, y:5.38, w:9, h:0.3, fontSize:9, fontFace:"Calibri", italic:true, color:"334155", align:"center", margin:0 });
}

// ── SLIDE 5 — BUILD vs BEND ───────────────────────────────────────────────────
{
  const s = pres.addSlide();
  s.background = { color: WHITE };
  hdr(s, "Why We BUILD — 6/6 Score", "Professor's 6-Dimension Framework");
  s.addText("The multi-agent architecture is the product. You cannot outsource your moat.", { x:0.5, y:1.0, w:9, h:0.4, fontSize:13, fontFace:"Georgia", italic:true, color:TEAL, align:"center", margin:0 });

  const dims = [
    { d:"Control",         v:"BUILD", why:"Non-linear multi-agent orchestration — platforms can't do this" },
    { d:"Cost",            v:"BUILD", why:"SDK ~$0.05/triage vs $50K+ clinical decision platforms" },
    { d:"Time to Value",   v:"BUILD", why:"Demo-ready in one week with mock EHR data" },
    { d:"Integration",     v:"BUILD", why:"Custom EHR interface + protocol KB + parallel agent dispatch" },
    { d:"Maintenance",     v:"BUILD", why:"Team owns all specialist logic — cannot hand to a vendor" },
    { d:"Differentiation", v:"BUILD", why:"Multi-agent triage is the core IP — outsourcing = surrendering it" },
  ];
  dims.forEach((d, i) => {
    const row = Math.floor(i/2); const col = i%2;
    const x = 0.4 + col*4.85; const y = 1.55 + row*1.1;
    s.addShape(pres.shapes.RECTANGLE, { x, y, w:4.65, h:0.95, fill:{color:TEAL_LT}, line:{color:TEAL, width:0.75}, shadow:makeShadow() });
    s.addShape(pres.shapes.RECTANGLE, { x, y, w:0.06, h:0.95, fill:{color:TEAL}, line:{color:TEAL} });
    s.addText(d.d, { x:x+0.12, y:y+0.07, w:1.5, h:0.26, fontSize:10, fontFace:"Calibri", bold:true, color:INK, margin:0 });
    s.addShape(pres.shapes.RECTANGLE, { x:x+3.3, y:y+0.09, w:1.2, h:0.24, fill:{color:TEAL}, line:{color:TEAL} });
    s.addText("BUILD", { x:x+3.3, y:y+0.09, w:1.2, h:0.24, fontSize:8, fontFace:"Calibri", bold:true, color:WHITE, align:"center", valign:"middle", margin:0 });
    s.addText(d.why, { x:x+0.12, y:y+0.38, w:4.4, h:0.48, fontSize:9, fontFace:"Calibri", color:MUTED, margin:0 });
  });
  s.addShape(pres.shapes.RECTANGLE, { x:0.4, y:4.85, w:9.2, h:0.44, fill:{color:TEAL}, line:{color:TEAL} });
  s.addText("Score: 6/6 BUILD → Maximum SDK investment. More complex than Project 1 — intentionally.", { x:0.5, y:4.85, w:9, h:0.44, fontSize:10.5, fontFace:"Calibri", bold:true, color:WHITE, align:"center", valign:"middle", margin:0 });
}

// ── SLIDE 6 — HOW IT ESCALATES ───────────────────────────────────────────────
{
  const s = pres.addSlide();
  s.background = { color: WHITE };
  hdr(s, "Escalation from Project 1", "Why TriageIQ is architecturally harder");

  const rows = [
    { dim:"Agents",         p1:"1 agent",             p2:"5 agents (4 parallel + synthesizer)" },
    { dim:"Execution",      p1:"Sequential tool calls", p2:"Parallel specialist dispatch" },
    { dim:"Trigger",        p1:"User uploads files",    p2:"Patient arrival triggers pipeline" },
    { dim:"Domain knowledge",p1:"Contract text (static)","p2":"Live EHR + protocol knowledge base" },
    { dim:"Output",         p1:"Ranked conflict list",  p2:"ESI score + action plan + resource allocation" },
    { dim:"Architecture",   p1:"Single loop",          p2:"Coordinator → specialists → synthesizer" },
  ];

  s.addTable(
    [
      [
        { text:"Dimension",    options:{bold:true, color:WHITE, fill:{color:DARK}} },
        { text:"ClauseGuard (P1)", options:{bold:true, color:WHITE, fill:{color:"1E3A5F"}} },
        { text:"TriageIQ (P2)",    options:{bold:true, color:WHITE, fill:{color:TEAL}} },
      ],
      ...rows.map((r, i) => [
        { text:r.dim,  options:{bold:true, color:INK,  fill:{color: i%2===0 ? SURFACE:"F0FDFA"}} },
        { text:r.p1,   options:{color:MUTED, fill:{color: i%2===0 ? SURFACE:"F0FDFA"}} },
        { text:r.p2,   options:{color:TEAL,  bold:true, fill:{color: i%2===0 ? TEAL_LT:"E0F2FE"}} },
      ])
    ],
    { x:0.5, y:1.0, w:9.0, h:4.3, colW:[2.0, 3.3, 3.7],
      border:{pt:0.5, color:BORDER},
      fontSize:10.5, fontFace:"Calibri",
      rowH:0.55
    }
  );
}

// ── SLIDE 7 — BUSINESS CASE ───────────────────────────────────────────────────
{
  const s = pres.addSlide();
  s.background = { color: WHITE };
  hdr(s, "Business Case", "Decision-support tool · Not a replacement system");

  const buyers = [
    { t:"Hospital Networks",   pain:"Triage errors increase liability and LOS",  value:"Reduce mistriages by 40%+, lower malpractice risk", roi:"$2M+ per prevented wrong-triage death", c:DARK, bg:SURFACE },
    { t:"Telemedicine Platforms", pain:"Remote triage has zero clinical support",  value:"AI-backed triage decision support for remote nurses", roi:"Enter hospital contracts, premium feature", c:TEAL, bg:TEAL_LT },
    { t:"Rural / Urgent Care", pain:"No specialist on-call for complex cases",   value:"Protocol matching + specialist routing in seconds", roi:"Handle critical cases before transfer", c:PURPLE, bg:"F5F0FF" },
  ];
  buyers.forEach((b, i) => {
    const x = 0.4 + i*3.1;
    s.addShape(pres.shapes.RECTANGLE, { x, y:1.05, w:2.9, h:4.25, fill:{color:b.bg}, line:{color:b.c, width:0.75}, shadow:makeShadow() });
    s.addShape(pres.shapes.RECTANGLE, { x, y:1.05, w:2.9, h:0.5, fill:{color:b.c}, line:{color:b.c} });
    s.addText(b.t, { x:x+0.1, y:1.05, w:2.7, h:0.5, fontSize:11, fontFace:"Calibri", bold:true, color:WHITE, valign:"middle", margin:0 });
    [["Pain", b.pain], ["Value", b.value], ["ROI", b.roi]].forEach(([lbl, txt], j) => {
      const y = 1.75 + j*1.15;
      s.addText(lbl.toUpperCase(), { x:x+0.15, y, w:2.6, h:0.22, fontSize:7.5, fontFace:"Calibri", bold:true, color:b.c, margin:0 });
      s.addText(txt, { x:x+0.15, y:y+0.22, w:2.6, h:0.75, fontSize:9.5, fontFace:"Calibri", color:INK, margin:0 });
    });
  });
  s.addText("Always framed as: decision-support tool for nurses · The nurse always makes the final call", { x:0.5, y:5.4, w:9, h:0.3, fontSize:9.5, fontFace:"Calibri", italic:true, color:MUTED, align:"center", margin:0 });
}

// ── SLIDE 8 — CLOSING ─────────────────────────────────────────────────────────
{
  const s = pres.addSlide();
  s.background = { color: DARK };
  s.addShape(pres.shapes.RECTANGLE, { x:0, y:0, w:0.18, h:5.625, fill:{color:TEAL}, line:{color:TEAL} });
  s.addText("TriageIQ", { x:0.5, y:1.2, w:9, h:1.1, fontSize:54, fontFace:"Georgia", bold:true, color:WHITE, align:"center", margin:0 });
  s.addText("A triage team in 60 seconds.\nNot a bot. A specialist team.", { x:0.5, y:2.4, w:9, h:0.9, fontSize:16, fontFace:"Georgia", italic:true, color:"94A3B8", align:"center", margin:0 });
  s.addShape(pres.shapes.RECTANGLE, { x:3.2, y:3.5, w:3.6, h:0.05, fill:{color:TEAL, transparency:50}, line:{color:TEAL, transparency:50} });
  const stats = [{ n:"5", l:"agents" }, { n:"4", l:"in parallel" }, { n:"<60s", l:"to ESI score" }, { n:"6/6", l:"BUILD score" }];
  stats.forEach((st, i) => {
    const x = 0.8 + i*2.2;
    s.addText(st.n, { x, y:3.7, w:2, h:0.65, fontSize:30, fontFace:"Georgia", bold:true, color:WHITE, align:"center", margin:0 });
    s.addText(st.l, { x, y:4.35, w:2, h:0.3, fontSize:10, fontFace:"Calibri", color:"475569", align:"center", margin:0 });
  });
  s.addText("Project 2/3  ·  BUAN 6v99.s01  ·  UT Dallas  ·  April 2026", { x:0.5, y:5.1, w:9, h:0.3, fontSize:9, fontFace:"Calibri", color:"334155", align:"center", margin:0 });
}

pres.writeFile({ fileName: "/home/claude/triageiq/TriageIQ_Presentation.pptx" })
  .then(() => console.log("TriageIQ presentation created."))
  .catch(e => { console.error(e); process.exit(1); });
