import sys, os, json, asyncio
from typing import Optional
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from backend.orchestrator import run_triage_agent
from backend.tools import MOCK_PATIENTS, PATIENT_ACUITY


class FreeTextRequest(BaseModel):
    description: str = Field(..., min_length=10, max_length=2000)
    vitals: Optional[str] = Field(None, max_length=500)

    @field_validator("description")
    @classmethod
    def must_not_be_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("description cannot be blank")
        return v.strip()

app = FastAPI(title="TriageIQ API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000", "http://127.0.0.1:3000",
        "http://localhost:3001", "http://127.0.0.1:3001",
        "http://localhost:5173", "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok", "service": "TriageIQ"}


@app.get("/patients")
def list_patients():
    """Return list of available demo patients."""
    patients = []
    for pid, data in sorted(MOCK_PATIENTS.items(), key=lambda x: PATIENT_ACUITY.get(x[0], 3)):
        cc = data["chief_complaint"]
        patients.append({
            "id": pid,
            "name": data["name"],
            "chief_complaint": cc[:80] + "..." if len(cc) > 80 else cc,
            "acuity_hint": PATIENT_ACUITY.get(pid, 3)
        })
    return {"patients": patients}


@app.get("/triage/stream/{patient_id}")
async def triage_stream(patient_id: str):
    """Stream triage analysis as Server-Sent Events."""

    async def event_stream():
        def sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data)}\n\n"

        step_labels = {
            1: "Retrieving patient data...",
            2: "Vitals Analyzer running...",
            3: "Symptom Classifier running...",
            4: "Protocol Matcher searching knowledge base...",
            5: "Bed Allocator checking resources...",
            6: "Synthesizing final triage decision...",
            7: "Triage complete."
        }
        total_steps = 7

        yield sse("status", {"step": 0, "total": total_steps, "message": "Starting triage...",
                              "agents": ["vitals", "symptoms", "protocols", "beds"]})
        await asyncio.sleep(0.1)

        result_holder = {}

        def on_progress(step, message, meta=None):
            result_holder["last_step"] = step
            result_holder["last_message"] = message
            result_holder["last_meta"] = meta or {}

        # Run in thread to not block event loop
        from concurrent.futures import ThreadPoolExecutor
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as pool:
            future = loop.run_in_executor(pool, run_triage_agent, patient_id, on_progress)

            # Poll progress while agent runs
            last_reported = 0
            while not future.done():
                current_step = result_holder.get("last_step", 0)
                if current_step != last_reported:
                    last_reported = current_step
                    meta = result_holder.get("last_meta", {})
                    yield sse("status", {
                        "step": current_step,
                        "total": total_steps,
                        "message": result_holder.get("last_message", ""),
                        "agent": _step_to_agent(current_step),
                        **meta
                    })
                await asyncio.sleep(0.3)

            result = await future

        if result.get("success"):
            yield sse("status", {"step": total_steps, "total": total_steps, "message": "Done!"})
            yield sse("complete", {"report": result["report"]})
        else:
            yield sse("error", {"message": result.get("error", "Unknown error")})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@app.post("/triage/freetext")
async def triage_freetext(body: FreeTextRequest):
    """Triage a free-text patient description."""
    from concurrent.futures import ThreadPoolExecutor
    patient_input = body.description
    vitals_str = body.vitals.strip() if body.vitals and body.vitals.strip() else None

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as pool:
        result = await loop.run_in_executor(pool, run_triage_agent, patient_input, None, vitals_str)

    if result.get("success"):
        return {"report": result["report"]}
    raise HTTPException(status_code=500, detail=result.get("error", "Analysis failed"))



@app.post("/chat")
async def chat(body: dict):
    import anthropic as _anthropic
    from config import ANTHROPIC_API_KEY
    message = body.get("message", "")
    report = body.get("report", {})
    history = body.get("history", [])
    if not message:
        raise HTTPException(status_code=400, detail="message required")
    client = _anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    system = f"""You are a clinical AI assistant helping an ER triage nurse understand a triage report.
Be concise, clinical, and direct. Answer questions about the patient, ESI score reasoning,
protocol steps, or clinical findings. Always remind the nurse they make the final call.

TRIAGE REPORT CONTEXT:
- Patient: {report.get('patient_summary', 'Unknown')}
- ESI Score: {report.get('esi_score', 'Unknown')} ({report.get('esi_label', '')})
- Confidence: {round((report.get('confidence', 0.85))*100)}%
- Vitals Analysis: {report.get('vitals_findings', report.get('vitals_detail', ''))}
- Symptoms: {report.get('symptom_findings', report.get('symptom_detail', ''))}
- Protocol: {report.get('protocol_findings', report.get('protocol_detail', ''))}
- Bed/Resources: {report.get('bed_recommendation', report.get('bed_detail', ''))}"""
    msgs = []
    for h in history:
        role = h.get("role", "user")
        if role in ("user", "assistant"):
            msgs.append({"role": role, "content": h.get("text", "")})
    msgs.append({"role": "user", "content": message})
    response = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=512, system=system, messages=msgs)
    reply = next((b.text for b in response.content if hasattr(b, "text")), "I couldn't generate a response.")
    return {"reply": reply}


def _step_to_agent(step: int) -> str:
    return {1: "coordinator", 2: "vitals", 3: "symptoms",
            4: "protocols", 5: "beds", 6: "synthesizer", 7: "done"}.get(step, "")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8001, reload=True)
