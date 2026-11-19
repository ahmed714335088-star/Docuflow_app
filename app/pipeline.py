import asyncio
import hashlib
import json
import math
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.media import audio_duration, create_ambient_music, make_voiceover, prepare_scene_video, render_video, transcribe_word_timings, write_arabic_ass
from app.models import GenerateRequest
from app.providers import PollinationsError, fetch_pixabay_image, fetch_pixabay_video, fetch_pollinations_image, generate_script, select_pollinations_image_model

Progress = Callable[..., Awaitable[None]]
STAGES = [
    ("script", "إنشاء النص"),
    ("voice", "إنشاء التعليق الصوتي"),
    ("visuals", "اختيار وتصميم المرئيات"),
    ("subtitles", "مزامنة الترجمات العربية"),
    ("render", "تصيير الفيديو النهائي"),
]
SCENE_CLIP_SECONDS = 4.0
TRANSITION_SECONDS = 0.22
SCENE_INTERVAL_SECONDS = SCENE_CLIP_SECONDS - TRANSITION_SECONDS


def _clean_topic(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()[:160]


def _filename(topic: str, job_id: str, index: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:32] or "documentary"
    return f"{slug}-{job_id}-{index:02d}.mp4"


def _caption_chunks(text: str, max_words: int = 5) -> list[str]:
    words = text.split()
    if not words:
        return []
    count = max(1, (len(words) + max_words - 1) // max_words)
    base, extra = divmod(len(words), count)
    chunks, cursor = [], 0
    for index in range(count):
        size = base + (1 if index < extra else 0)
        chunks.append(" ".join(words[cursor:cursor + size]))
        cursor += size
    return chunks


def _align_caption_chunks(
    text: str, word_times: list[tuple[float, float, str]], offset: float, scene_duration: float, speech_duration: float,
) -> list[dict[str, Any]]:
    chunks = _caption_chunks(text)
    if not chunks:
        return []
    speech_words = word_times or []
    cue_count = len(chunks)
    cues: list[dict[str, Any]] = []
    for index, phrase in enumerate(chunks):
        if speech_words:
            left = min(len(speech_words) - 1, index * len(speech_words) // cue_count)
            right = max(left, min(len(speech_words) - 1, (index + 1) * len(speech_words) // cue_count - 1))
            local_start = max(0.0, speech_words[left][0] - 0.04)
            local_end = min(scene_duration, speech_words[right][1] + 0.12)
        else:
            local_start = speech_duration * index / cue_count
            local_end = speech_duration * (index + 1) / cue_count
        local_end = min(scene_duration, max(local_start + 0.48, local_end))
        local_start = min(max(0.0, local_start), max(0.0, scene_duration - 0.48))
        cues.append({"start": offset + local_start, "end": offset + min(scene_duration, max(local_start + 0.48, local_end)), "text": phrase})
    return cues


async def run_pipeline(job_id: str, request: GenerateRequest, topics: list[str], report: Progress, outputs: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    keys = request.keys
    total_topics = len(topics)
    for topic_index, raw_topic in enumerate(topics):
        topic = _clean_topic(raw_topic)
        work = outputs.parent / "work" / f"{job_id}-{topic_index + 1:02d}"
        work.mkdir(parents=True, exist_ok=True)

        async def step(number: int, message: str) -> None:
            global_progress = int(((topic_index + number / len(STAGES)) / total_topics) * 100)
            await report(status="running", progress=min(global_progress, 99), stage=STAGES[number][0], message=f"{topic}: {message}")

        language_name = "العربية" if request.video_language == "ar" else "الإنجليزية"
        requested_seconds = int(request.duration_minutes) * 60
        scene_count = math.ceil((requested_seconds - TRANSITION_SECONDS) / SCENE_INTERVAL_SECONDS)
        scene_interval = SCENE_INTERVAL_SECONDS
        await step(0, f"نكتب خطافاً وسيناريو من مشاهد قصيرة باللغة {language_name}.")
        script = await generate_script(topic, request.provider, keys, request.video_language, request.aspect_ratio, scene_count)
        scenes = script["scenes"]
        if len(scenes) != scene_count:
            raise RuntimeError(f"يتطلب الفيديو {scene_count} مشهداً لضبط مدته إلى {request.duration_minutes} دقيقة.")

        audio_paths: list[Path] = []
        speech_durations: list[float] = []
        scene_durations: list[float] = [scene_interval] * len(scenes)
        for number, scene in enumerate(scenes, start=1):
            await step(1, f"إنشاء الصوت {language_name} للمشهد {number} من {len(scenes)} بصوت {request.voice}.")
            audio_path = work / f"voice_{number:02d}.mp3"
            await make_voiceover(scene["narration"], request.voice, audio_path, rate="+10%")
            speech_time = audio_duration(audio_path)
            if speech_time > scene_interval:
                # Preserve the complete sentence while meeting the strict four-second visual beat.
                for rate in ("+22%", "+34%", "+45%", "+58%"):
                    await make_voiceover(scene["narration"], request.voice, audio_path, rate=rate)
                    speech_time = audio_duration(audio_path)
                    if speech_time <= scene_interval:
                        break
            if speech_time > scene_interval:
                raise RuntimeError(f"تعذر ضغط نطق المشهد {number} إلى 4 ثوانٍ دون قص الكلام؛ أعد المحاولة بنص أقصر.")
            speech_durations.append(speech_time)
            audio_paths.append(audio_path)

        await step(2, f"البحث عن فيديو حقيقي أو صورة سينمائية وحركة للمشهد 1 من {len(scenes)}.")
        orientation = "landscape" if request.aspect_ratio == "16:9" else "portrait"
        visual_items: list[dict[str, Any] | None] = [None] * len(scenes)
        used_video_ids: set[str] = set()
        semaphore = asyncio.Semaphore(3)
        pollinations_unavailable = asyncio.Event()
        visual_warnings: list[str] = []
        pollinations_image_model = None
        if request.allow_pollinations and keys.pollinations.strip():
            try:
                pollinations_image_model = await select_pollinations_image_model()
            except PollinationsError as exc:
                pollinations_unavailable.set()
                visual_warnings.append(str(exc))

        async def resolve_visual(index: int, scene: dict[str, str]) -> None:
            async with semaphore:
                number = index + 1
                await step(2, f"اختيار فيديو أو صورة وحركة للمشهد {number} من {len(scenes)}.")
                if keys.pixabay.strip():
                    video_path = work / f"pixabay_{number:02d}.mp4"
                    has_video = await fetch_pixabay_video(
                        scene["visual_keywords"], keys.pixabay, orientation, video_path, used_ids=used_video_ids
                    )
                    if has_video:
                        visual_items[index] = {"path": video_path, "kind": "pixabay_video"}
                        return
                if request.allow_pollinations and keys.pollinations.strip() and not pollinations_unavailable.is_set():
                    image_path = work / f"pollinations_{number:02d}.jpg"
                    try:
                        image = await fetch_pollinations_image(
                            scene.get("image_prompt") or scene["visual_keywords"],
                            keys.pollinations,
                            request.aspect_ratio,
                            image_path,
                            seed=int.from_bytes(hashlib.sha256(f"{topic}-{number}".encode("utf-8")).digest()[:4], "big") % 2_000_000_000,
                            model_choice=pollinations_image_model,
                        )
                        visual_items[index] = {**image, "kind": "pollinations_image"}
                        return
                    except PollinationsError as exc:
                        visual_warnings.append(f"المشهد {number}: {exc}")
                        if exc.status_code in (401, 402, 403):
                            pollinations_unavailable.set()
                if keys.pixabay.strip():
                    image_path = work / f"pixabay_still_{number:02d}.jpg"
                    has_image = await fetch_pixabay_image(scene["visual_keywords"], keys.pixabay, orientation, image_path)
                    if has_image:
                        visual_items[index] = {"path": image_path, "kind": "pixabay_image"}
                        return
                visual_items[index] = {"path": None, "kind": "local_motion"}

        await asyncio.gather(*(resolve_visual(index, scene) for index, scene in enumerate(scenes)))
        sources = [item["path"] if item else None for item in visual_items]
        source_counts = Counter(item["kind"] for item in visual_items if item)
        source_parts = []
        if source_counts["pixabay_video"]:
            source_parts.append(f"فيديو Pixabay: {source_counts['pixabay_video']}")
        if source_counts["pixabay_image"]:
            source_parts.append(f"صور Pixabay بحركة كاميرا: {source_counts['pixabay_image']}")
        if source_counts["pollinations_image"]:
            source_parts.append(f"صور Pollinations متحركة: {source_counts['pollinations_image']}")
        if source_counts["local_motion"]:
            source_parts.append(f"خلفيات محلية بحركة كاميرا: {source_counts['local_motion']}")
        visual_note = " · ".join(source_parts) or "مشاهد بحركة كاميرا"

        scene_videos: list[Path] = []
        for number, (scene, source, duration) in enumerate(zip(scenes, sources, scene_durations), start=1):
            visual_path = source if source is not None else None
            video_path = work / f"scene_{number:02d}.mp4"
            await asyncio.to_thread(
                prepare_scene_video,
                visual_path,
                scene["visual_keywords"],
                video_path,
                request.aspect_ratio,
                duration,
                number,
                work,
                TRANSITION_SECONDS,
            )
            scene_videos.append(video_path)

        await step(3, f"يفرّغ faster-whisper كل مقطع صوت {language_name} ويحدد توقيت الترجمة العربية.")
        captions: list[dict[str, Any]] = []
        offset = 0.0
        whisper_language = "ar" if request.video_language == "ar" else "en"
        for scene, audio_path, duration, speech_time in zip(scenes, audio_paths, scene_durations, speech_durations):
            word_times = await asyncio.to_thread(
                transcribe_word_timings, audio_path, request.whisper_model, whisper_language
            )
            captions.extend(_align_caption_chunks(scene["translation_ar"], word_times, offset, duration, speech_time))
            offset += duration
        ass_path = work / "arabic_subtitles.ass"
        write_arabic_ass(captions, ass_path, request.aspect_ratio)

        await step(4, "تركيب حركة الكاميرا والصوت والموسيقى الهادئة وحرق الترجمة العربية.")
        full_duration = sum(scene_durations) + TRANSITION_SECONDS
        music_path = None
        if request.background_music:
            music_path = work / "ambient_documentary.mp3"
            await asyncio.to_thread(create_ambient_music, music_path, full_duration)
        filename = _filename(topic, job_id, topic_index + 1)
        destination = outputs / filename
        await asyncio.to_thread(
            render_video, scene_videos, audio_paths, music_path, ass_path, destination, request.aspect_ratio, scene_durations
        )

        pollinations_image_items = [item for item in visual_items if item and item["kind"] == "pollinations_image"]
        estimated_pollen = sum(float(item.get("estimated_pollen_per_image", 0.0) or 0.0) for item in pollinations_image_items)
        metadata = {
            "filename": filename,
            "topic": topic,
            "title": script.get("title", topic),
            "hook": script.get("hook", ""),
            "aspect_ratio": request.aspect_ratio,
            "duration_minutes": request.duration_minutes,
            "video_language": request.video_language,
            "voice": request.voice,
            "provider": request.provider,
            "script_model": script.get("model", ""),
            "demo": bool(script.get("demo")),
            "visual_source": visual_note,
            "visual_sources_by_scene": [item["kind"] if item else "local_motion" for item in visual_items],
            "pixabay_video_ids": sorted(used_video_ids),
            "scene_count": len(scenes),
            "scene_durations_seconds": [round(value, 2) for value in scene_durations],
            "scene_speech_durations_seconds": [round(value, 2) for value in speech_durations],
            "pollinations_image_models": sorted({item["model"] for item in pollinations_image_items if item.get("model")}),
            "estimated_pollinations_image_pollen": round(estimated_pollen, 4),
            "visual_warnings": visual_warnings,
            "camera_motion": "ffmpeg-zoompan-24fps-with-crossfade",
            "transition_seconds": TRANSITION_SECONDS,
            "audio_mastering": {"integrated_lufs_target": -14, "true_peak_dbfs_ceiling": -1.0, "limiter": "ffmpeg-alimiter"},
            "subtitle_chunk_count": len(captions),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": round(full_duration, 2),
            "scenes": scenes,
        }
        (outputs / f"{Path(filename).stem}.meta.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        results.append(metadata)
        shutil.rmtree(work, ignore_errors=True)
        await report(
            status="running",
            progress=int(((topic_index + 1) / total_topics) * 100),
            stage="topic_done",
            message=f"اكتمل: {topic} · {len(scenes)} مشهداً · {visual_note}",
            results=results,
        )

    return results
