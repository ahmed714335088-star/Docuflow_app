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

from app.media import audio_duration, create_ambient_music, make_voiceover, pad_audio_with_silence, prepare_scene_video, render_video, transcribe_word_timings, write_arabic_ass
from app.models import GenerateRequest
from app.providers import GeminiImageError, PollinationsError, fetch_pollinations_image, generate_gemini_image, generate_script, select_pollinations_image_model
from app.visual_sources import fetch_pixabay_video

Progress = Callable[..., Awaitable[None]]
STAGES = [
    ("script", "إنشاء النص"),
    ("voice", "إنشاء التعليق الصوتي"),
    ("visuals", "اختيار وتصميم المرئيات"),
    ("subtitles", "مزامنة الترجمات العربية"),
    ("render", "تصيير الفيديو النهائي"),
]
SCENE_TARGET_SECONDS = 3.3
VOICE_PAUSE_SECONDS = 0.5
TRANSITION_SECONDS = 0.22


def _scene_count_for_duration(requested_seconds: int) -> int:
    shortest_interval = SCENE_TARGET_SECONDS - TRANSITION_SECONDS
    return max(1, math.floor((requested_seconds - TRANSITION_SECONDS) / shortest_interval))


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
        scene_count = _scene_count_for_duration(requested_seconds)
        scene_interval = (requested_seconds - TRANSITION_SECONDS) / scene_count
        speech_budget = scene_interval - VOICE_PAUSE_SECONDS
        await step(0, f"نكتب خطافاً وسيناريو من مشاهد قصيرة باللغة {language_name}.")
        script = await generate_script(
            topic, request.provider, keys, request.video_language, request.aspect_ratio, scene_count, requested_seconds
        )
        scenes = script["scenes"]
        if len(scenes) != scene_count:
            raise RuntimeError(f"يتطلب الفيديو {scene_count} مشهداً لضبط مدته إلى {request.duration_minutes} دقيقة.")
        if any(scene.get("visual_type") == "ai_image" for scene in scenes) and (
            not request.allow_gemini_images or not keys.gemini.strip()
        ):
            raise RuntimeError(
                "يتضمن السيناريو مشاهد ai_image تتطلب Gemini API Key وموافقة توليد الصور لهذا التشغيل. "
                "لم نبدأ إنشاء الصوت أو استبدلنا الصور بلقطات عامة."
            )

        audio_paths: list[Path] = []
        speech_durations: list[float] = []
        scene_durations: list[float] = []
        for number, scene in enumerate(scenes, start=1):
            await step(1, f"إنشاء الصوت {language_name} للمشهد {number} من {len(scenes)} بصوت {request.voice} وبسرعة {request.voice_rate:+d}%.")
            audio_path = work / f"voice_{number:02d}.mp3"
            await make_voiceover(scene["narration"], request.voice, audio_path, rate=request.voice_rate)
            speech_time = audio_duration(audio_path)
            if speech_time > speech_budget:
                raise RuntimeError(
                    f"جملة المشهد {number} تستغرق {speech_time:.2f} ثانية، بينما مساحة الكلام لهذه المدة {speech_budget:.2f} ثانية "
                    f"عند السرعة المختارة ({request.voice_rate:+d}%). "
                    "لن يُسرّع الصوت تلقائياً؛ أعد صياغة الجملة أقصر أو اختر سرعة أخرى."
                )
            speech_durations.append(speech_time)
            if speech_time < speech_budget:
                pad_audio_with_silence(audio_path, speech_budget)
            # The scene interval is allocated from the chosen runtime; preserve an exact 0.5s voice gap.
            scene_durations.append(scene_interval)
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

        gemini_image_limited = asyncio.Event()

        async def resolve_visual(index: int, scene: dict[str, str]) -> None:
            async with semaphore:
                number = index + 1
                visual_type = scene.get("visual_type")
                if visual_type not in {"ai_image", "stock_video"}:
                    raise RuntimeError(f"المشهد {number} لا يحمل visual_type معتمداً؛ أوقفنا الإنتاج بدلاً من اختيار مرئيات عشوائية.")
                await step(2, f"إخراج {visual_type} مطابق للمشهد {number} من {len(scenes)}.")
                anchor = str(scene.get("visual_anchor") or script.get("visual_anchor") or "")
                async def pixabay_video_fallback() -> dict[str, Any] | None:
                    if not keys.pixabay.strip():
                        return None
                    path = work / f"pixabay_{number:02d}.mp4"
                    return await fetch_pixabay_video(scene["visual_keywords"], keys.pixabay, orientation, path,
                                                     used_ids=used_video_ids, visual_anchor=anchor)

                if visual_type == "stock_video":
                    stock = await pixabay_video_fallback()
                    if stock:
                        visual_items[index] = stock
                        return
                    if not request.allow_gemini_images or not keys.gemini.strip():
                        raise RuntimeError(
                            f"المشهد {number}: لم نعثر على فيديو Pixabay مطابق بوسومه. يلزم مفتاح Gemini وموافقة توليد الصور لهذا التشغيل؛ لا نستخدم لقطة عامة."
                        )
                elif not request.allow_gemini_images or not keys.gemini.strip():
                    raise RuntimeError(
                        f"المشهد {number} موسوم ai_image، ويتطلب Gemini API Key وموافقة توليد الصور لهذا التشغيل."
                    )

                if request.allow_gemini_images and keys.gemini.strip() and not gemini_image_limited.is_set():
                    image_path = work / f"gemini_{number:02d}.jpg"
                    try:
                        generated = await generate_gemini_image(
                            scene.get("image_prompt") or scene["visual_keywords"], keys.gemini,
                            request.aspect_ratio, image_path,
                        )
                        visual_items[index] = generated
                        return
                    except GeminiImageError as exc:
                        if exc.status_code != 429:
                            raise
                        gemini_image_limited.set()
                        visual_warnings.append(f"المشهد {number}: {exc} يجري الرجوع إلى Pixabay.")

                # Only an exact, tag-matched Pixabay video is accepted as a Gemini quota fallback.
                fallback = await pixabay_video_fallback()
                if fallback:
                    visual_items[index] = fallback
                    return
                if request.allow_pollinations and keys.pollinations.strip() and pollinations_image_model and not pollinations_unavailable.is_set():
                    image_path = work / f"pollinations_fallback_{number:02d}.jpg"
                    try:
                        generated = await fetch_pollinations_image(
                            scene.get("image_prompt") or scene["visual_keywords"], keys.pollinations,
                            request.aspect_ratio,
                            image_path,
                            seed=int.from_bytes(hashlib.sha256(f"{topic}-{number}".encode("utf-8")).digest()[:4], "big") % 2_000_000_000,
                            model_choice=pollinations_image_model,
                        )
                        visual_items[index] = {**generated, "kind": "pollinations_image"}
                        return
                    except PollinationsError as exc:
                        visual_warnings.append(f"المشهد {number}: تعذر البديل الاختياري Pollinations: {exc}")
                        if exc.status_code in (401, 402, 403):
                            pollinations_unavailable.set()
                reason = "Gemini reached its image quota and Pixabay had no matching video" if gemini_image_limited.is_set() else "Pixabay has no tag-matched video and Gemini image generation is unavailable"
                raise RuntimeError(
                    f"المشهد {number}: {reason}. راجع المرساة والوسوم والمفاتيح/الموافقة؛ لم نستبدله بمرئيات عامة."
                )

        outcomes = await asyncio.gather(*(resolve_visual(index, scene) for index, scene in enumerate(scenes)), return_exceptions=True)
        errors = [str(item) for item in outcomes if isinstance(item, BaseException)]
        if errors:
            shutil.rmtree(work, ignore_errors=True)
            raise RuntimeError("توقف الإنتاج بسبب تعذر المرئيات المطابقة: " + " | ".join(errors[:4]))
        missing = [index for index, item in enumerate(visual_items, start=1) if item is None]
        if missing:
            details = "; ".join(f"{index}: {scenes[index - 1]['visual_keywords']}" for index in missing[:5])
            more = f"، إضافة إلى {len(missing) - 5} مشاهد أخرى" if len(missing) > 5 else ""
            shutil.rmtree(work, ignore_errors=True)
            raise RuntimeError(
                f"توقّف الإنتاج قبل التصيير: لم نعثر على أصل بصري موثوق الصلة لـ{len(missing)} من {len(scenes)} مشهداً. "
                f"لا تُستخدم خلفية عامة بديلة. راجع الاستعلامات أو أضف Pixabay API Key. المشاهد: {details}{more}"
            )
        assets = [item for item in visual_items if item is not None]
        sources = [item["path"] for item in assets]
        source_counts = Counter(item["kind"] for item in assets)
        source_parts = []
        if source_counts["pixabay_video"]:
            source_parts.append(f"فيديو Pixabay: {source_counts['pixabay_video']}")
        if source_counts["gemini_image"]:
            source_parts.append(f"صور Gemini المولدة: {source_counts['gemini_image']}")
        if source_counts["pollinations_image"]:
            source_parts.append(f"صور Pollinations متحركة: {source_counts['pollinations_image']}")
        visual_note = " · ".join(source_parts) or "مشاهد بحركة كاميرا"

        scene_videos: list[Path] = []
        for number, (scene, source, duration) in enumerate(zip(scenes, sources, scene_durations), start=1):
            visual_path = source
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
        visual_credits = [
            {
                "scene": index,
                "title": item.get("title", ""),
                "artist": item.get("artist", ""),
                "license": item.get("license", ""),
                "license_url": item.get("license_url", ""),
                "page_url": item.get("page_url", ""),
            }
            for index, item in enumerate(visual_items, start=1)
            if item and item.get("kind") == "commons_image"
        ]
        metadata = {
            "filename": filename,
            "topic": topic,
            "title": script.get("title", topic),
            "hook": script.get("hook", ""),
            "aspect_ratio": request.aspect_ratio,
            "duration_minutes": request.duration_minutes,
            "video_language": request.video_language,
            "voice": request.voice,
            "voice_rate": request.voice_rate,
            "voice_pitch": "-3Hz",
            "provider": request.provider,
            "script_model": script.get("model", ""),
            "demo": bool(script.get("demo")),
            "visual_anchor": script.get("visual_anchor") or (scenes[0].get("visual_anchor") if scenes else ""),
            "fact_sources": script.get("fact_sources", []),
            "visual_source": visual_note,
            "visual_sources_by_scene": [item["kind"] for item in assets],
            "pixabay_video_ids": sorted(used_video_ids),
            "commons_visual_credits": visual_credits,
            "scene_count": len(scenes),
            "scene_interval_seconds": round(scene_interval, 4),
            "scene_durations_seconds": [round(value, 2) for value in scene_durations],
            "scene_speech_durations_seconds": [round(value, 2) for value in speech_durations],
            "pollinations_image_models": sorted({item["model"] for item in pollinations_image_items if item.get("model")}),
            "estimated_pollinations_image_pollen": round(estimated_pollen, 4),
            "visual_warnings": visual_warnings,
            "camera_motion": "ffmpeg-zoompan-24fps-with-crossfade",
            "transition_seconds": TRANSITION_SECONDS,
            "inter_scene_voice_pause_seconds": VOICE_PAUSE_SECONDS,
            "audio_mastering": {"integrated_lufs_target": -14, "true_peak_dbfs_ceiling": -1.0, "limiter": "ffmpeg-alimiter"},
            "subtitle_chunk_count": len(captions),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": round(full_duration, 2),
            "scenes": scenes,
        }
        (outputs / f"{Path(filename).stem}.meta.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if visual_credits:
            credit_lines = ["Wikimedia Commons image credits", ""]
            for credit in visual_credits:
                credit_lines.append(
                    f"Scene {credit['scene']}: {credit['title']} — {credit['artist']} — {credit['license']} ({credit['license_url']}) — {credit['page_url']}"
                )
            outputs.joinpath(f"{Path(filename).stem}.credits.txt").write_text(
                "\n".join(credit_lines) + "\n", encoding="utf-8"
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
