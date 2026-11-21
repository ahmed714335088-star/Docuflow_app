import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.models import GenerateRequest
from app.pipeline import run_pipeline

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / "data" / "outputs"
TEMPLATES = ROOT / "app" / "templates"
STATIC = ROOT / "app" / "static"
OUTPUTS.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="DocuFlow", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
jobs: dict[str, dict[str, Any]] = {}
job_tasks: set[asyncio.Task] = set()


def _push(job_id: str, **changes: Any) -> None:
    item = jobs.get(job_id)
    if item is None:
        return
    item.update(changes)
    event = {
        "status": item["status"],
        "progress": item["progress"],
        "stage": item["stage"],
        "message": item["message"],
        "at": datetime.now(timezone.utc).isoformat(),
    }
    item["events"].append(event)
    if len(item["events"]) > 100:
        item["events"] = item["events"][-100:]


def _topics(value: str) -> list[str]:
    return [line.strip(" •\t-") for line in value.splitlines() if line.strip(" •\t-")]


@app.get("/", response_class=HTMLResponse)
async def home() -> HTMLResponse:
    return HTMLResponse((TEMPLATES / "index.html").read_text(encoding="utf-8"))


@app.get("/manus-routes.json", include_in_schema=False)
async def route_manifest() -> FileResponse:
    return FileResponse(ROOT / "public" / "manus-routes.json", media_type="application/json")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "app": "DocuFlow"}


@app.post("/api/generate")
async def start_generation(request: GenerateRequest) -> dict[str, str]:
    topics = _topics(request.topics)
    if not topics:
        raise HTTPException(status_code=422, detail="أدخل موضوعاً واحداً على الأقل.")
    if len(topics) > 8:
        raise HTTPException(status_code=422, detail="الحد الأقصى 8 موضوعات في الدفعة الواحدة.")
    if request.provider == "groq" and not request.keys.groq.strip():
        raise HTTPException(status_code=422, detail="أدخل مفتاح Groq أو اختر الوضع التجريبي.")
    if request.provider == "gemini" and not request.keys.gemini.strip():
        raise HTTPException(status_code=422, detail="أدخل مفتاح Gemini أو اختر الوضع التجريبي.")
    if request.provider == "claude" and not request.keys.claude.strip():
        raise HTTPException(status_code=422, detail="أدخل Claude API Key قبل اختيار هذا المزود.")
    if request.allow_gemini_images and not request.keys.gemini.strip():
        raise HTTPException(status_code=422, detail="توليد صور Gemini يحتاج مفتاح Gemini صالحاً.")
    if request.provider == "pollinations" and not request.keys.pollinations.strip():
        raise HTTPException(status_code=422, detail="أدخل Pollinations API Key قبل اختيار موفر النص هذا.")
    if request.provider == "pollinations" and not request.allow_pollinations:
        raise HTTPException(status_code=422, detail="فعّل موافقة Pollinations قبل استخدام الخدمة.")
    if request.allow_pollinations and not request.keys.pollinations.strip():
        raise HTTPException(status_code=422, detail="توليد الصور الاحتياطية عبر Pollinations يحتاج مفتاح API.")

    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "stage": "queued",
        "message": "أُضيفت المهمة إلى قائمة الإنتاج.",
        "topics": topics,
        "results": [],
        "error": None,
        "events": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _push(job_id, status="queued", progress=0, stage="queued", message="أُضيفت المهمة إلى قائمة الإنتاج.")

    async def progress_callback(**changes: Any) -> None:
        _push(job_id, **changes)

    async def worker() -> None:
        _push(job_id, status="running", message="بدأ خط الإنتاج.")
        try:
            results = await run_pipeline(job_id, request, topics, progress_callback, OUTPUTS)
            _push(job_id, status="completed", progress=100, stage="complete", message="اكتمل إنتاج الفيديو.", results=results)
        except Exception as exc:
            message = str(exc).strip() or "حدث خطأ أثناء توليد الفيديو."
            _push(job_id, status="failed", stage="error", message=message, error=message)

    task = asyncio.create_task(worker(), name=f"docuflow-{job_id}")
    job_tasks.add(task)
    task.add_done_callback(job_tasks.discard)
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    item = jobs.get(job_id)
    if item is None:
        raise HTTPException(status_code=404, detail="المهمة غير موجودة. ربما أُعيد تشغيل التطبيق.")
    return {key: value for key, value in item.items() if key != "events"}


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="المهمة غير موجودة.")

    async def event_stream():
        index = 0
        while True:
            item = jobs.get(job_id)
            if item is None:
                break
            events = item["events"]
            while index < len(events):
                yield f"data: {json.dumps(events[index], ensure_ascii=False)}\n\n"
                index += 1
            if item["status"] in {"completed", "failed"}:
                break
            await asyncio.sleep(0.7)
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/history")
async def history() -> dict[str, list[dict[str, Any]]]:
    items = []
    for meta_path in OUTPUTS.glob("*.meta.json"):
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            filename = data.get("filename", "")
            video = OUTPUTS / filename
            if video.is_file():
                items.append({**data, "size_bytes": video.stat().st_size})
        except (OSError, json.JSONDecodeError):
            continue
    items.sort(key=lambda entry: entry.get("created_at", ""), reverse=True)
    return {"items": items[:50]}


def _safe_output(filename: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.mp4", filename):
        raise HTTPException(status_code=400, detail="اسم ملف غير صالح.")
    candidate = (OUTPUTS / filename).resolve()
    if candidate.parent != OUTPUTS.resolve() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="الفيديو غير موجود.")
    return candidate


@app.get("/api/videos/{filename}")
async def video_file(filename: str) -> FileResponse:
    return FileResponse(_safe_output(filename), media_type="video/mp4", filename=filename)


@app.get("/api/download/{filename}")
async def download_video(filename: str) -> FileResponse:
    return FileResponse(_safe_output(filename), media_type="video/mp4", filename=filename, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/credits/{filename}")
async def download_visual_credits(filename: str) -> FileResponse:
    _safe_output(filename)
    credits = OUTPUTS / f"{Path(filename).stem}.credits.txt"
    if not credits.is_file():
        raise HTTPException(status_code=404, detail="لا يوجد ملف إسناد مرئيات لهذا الفيديو.")
    return FileResponse(credits, media_type="text/plain; charset=utf-8", filename=credits.name)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "3000")), reload=False)
