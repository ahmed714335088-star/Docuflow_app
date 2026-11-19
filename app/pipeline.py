import asyncio
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.media import audio_duration, create_ambient_music, make_voiceover, prepare_scene_video, render_video, transcribe_bounds, write_arabic_ass
from app.models import GenerateRequest
from app.providers import generate_script, fetch_pixabay_video

Progress = Callable[..., Awaitable[None]]
STAGES = [
    ("script", "إنشاء النص"),
    ("voice", "إنشاء التعليق الصوتي"),
    ("visuals", "جلب مقاطع Pixabay"),
    ("subtitles", "مزامنة الترجمات العربية"),
    ("render", "تصيير الفيديو النهائي"),
]


def _clean_topic(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()[:160]


def _filename(topic: str, job_id: str, index: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:32] or "documentary"
    return f"{slug}-{job_id}-{index:02d}.mp4"


async def run_pipeline(job_id: str, request: GenerateRequest, topics: list[str], report: Progress, outputs: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    keys = request.keys
    total = len(topics)
    for topic_index, raw_topic in enumerate(topics):
        topic = _clean_topic(raw_topic)
        work = outputs.parent / "work" / f"{job_id}-{topic_index + 1:02d}"
        work.mkdir(parents=True, exist_ok=True)

        async def step(number: int, message: str) -> None:
            global_progress = int(((topic_index + number / len(STAGES)) / total) * 100)
            await report(status="running", progress=min(global_progress, 99), stage=STAGES[number][0], message=f"{topic}: {message}")

        await step(0, "نكتب سيناريو المشاهد والخطاف والترجمة العربية.")
        script = await generate_script(topic, request.provider, keys)
        scenes = script["scenes"]
        audio_paths: list[Path] = []
        for number, scene in enumerate(scenes, start=1):
            await step(1, f"إنشاء صوت المشهد {number} من {len(scenes)} بصوت {request.voice}.")
            audio_path = work / f"voice_{number:02d}.mp3"
            await make_voiceover(scene["narration_en"], request.voice, audio_path)
            audio_paths.append(audio_path)

        await step(2, "البحث عن لقطات مطابقة لكلمات المشاهد.")
        orientation = "landscape" if request.aspect_ratio == "16:9" else "portrait"
        sources: list[Path | None] = []
        for number, scene in enumerate(scenes, start=1):
            source: Path | None = None
            if keys.pixabay.strip():
                downloaded = work / f"pixabay_{number:02d}.mp4"
                has_clip = await fetch_pixabay_video(scene["visual_keywords"], keys.pixabay, orientation, downloaded)
                if has_clip:
                    source = downloaded
            sources.append(source)
        visual_note = "مقاطع Pixabay" if keys.pixabay.strip() and all(sources) else "مقاطع Pixabay مع خلفيات محلية للمشاهد التي لم تتوفر لها لقطات" if any(sources) else "خلفيات تجريبية محلية (أضف مفتاح Pixabay لجلب لقطات مخزون)"
        scene_videos: list[Path] = []
        durations: list[float] = []
        for number, (scene, source, audio_path) in enumerate(zip(scenes, sources, audio_paths), start=1):
            duration = audio_duration(audio_path) + 0.55
            durations.append(duration)
            video_path = work / f"scene_{number:02d}.mp4"
            await asyncio.to_thread(prepare_scene_video, source, scene["visual_keywords"], video_path, request.aspect_ratio, duration, number, work)
            scene_videos.append(video_path)

        await step(3, "يفرّغ faster-whisper الصوت الإنجليزي ويحدد توقيت كل ترجمة عربية.")
        captions: list[dict[str, Any]] = []
        offset = 0.0
        for scene, audio_path, duration in zip(scenes, audio_paths, durations):
            start, end, _transcript = await asyncio.to_thread(transcribe_bounds, audio_path, request.whisper_model)
            cue_start = max(0.0, offset + max(0.0, start - 0.12))
            cue_end = min(offset + duration, offset + min(duration, max(start + 0.55, end + 0.38)))
            if cue_end <= cue_start:
                cue_end = min(offset + duration, cue_start + 1.1)
            captions.append({"start": cue_start, "end": cue_end, "text": scene["translation_ar"]})
            offset += duration
        ass_path = work / "arabic_subtitles.ass"
        write_arabic_ass(captions, ass_path, request.aspect_ratio)

        await step(4, "دمج التعليق والموسيقى الخفيفة وحرق ترجمات ASS.")
        duration = sum(durations)
        music_path = None
        if request.background_music:
            music_path = work / "ambient_documentary.mp3"
            await asyncio.to_thread(create_ambient_music, music_path, duration)
        filename = _filename(topic, job_id, topic_index + 1)
        destination = outputs / filename
        await asyncio.to_thread(render_video, scene_videos, audio_paths, music_path, ass_path, destination, request.aspect_ratio)

        metadata = {
            "filename": filename,
            "topic": topic,
            "title": script.get("title", topic),
            "hook": script.get("hook", ""),
            "aspect_ratio": request.aspect_ratio,
            "voice": request.voice,
            "provider": request.provider,
            "demo": bool(script.get("demo")),
            "visual_source": visual_note,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": round(duration, 2),
            "scenes": scenes,
        }
        (outputs / f"{Path(filename).stem}.meta.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        results.append(metadata)
        shutil.rmtree(work, ignore_errors=True)
        await report(status="running", progress=int(((topic_index + 1) / total) * 100), stage="topic_done", message=f"اكتمل: {topic}", results=results)

    return results
