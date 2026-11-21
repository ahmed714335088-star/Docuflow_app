import math
import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

FFMPEG = os.environ.get("FFMPEG_BINARY", "ffmpeg")
FPS = 24


def _run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        tail = "\n".join(detail[-5:])
        raise RuntimeError(f"فشل FFmpeg أثناء تجهيز الفيديو. {tail[-900:]}")


async def make_voiceover(text: str, voice: str, destination: Path, rate: int = -12) -> None:
    import edge_tts

    if not -25 <= rate <= 10:
        raise ValueError("سرعة الراوي يجب أن تكون بين ‎-25% و‎+10%.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        await edge_tts.Communicate(text=text, voice=voice, rate=f"{rate:+d}%", pitch="-3Hz").save(str(destination))
    except Exception as exc:
        raise RuntimeError("تعذر إنشاء التعليق الصوتي عبر edge-tts. تحقق من اتصال الإنترنت واختيار الصوت.") from exc


def audio_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("تعذر قراءة مدة ملف التعليق الصوتي.")
    return max(0.25, float(result.stdout.strip()))


def pad_audio_with_silence(path: Path, target_duration: float) -> None:
    """Pad voice at its tail without changing spoken speed or pitch."""
    current = audio_duration(path)
    if target_duration <= current + 0.015:
        return
    temporary = path.with_name(path.stem + "_padded.mp3")
    _run([
        FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
        "-map", "0:a:0", "-af", f"apad=pad_dur={target_duration - current:.3f}",
        "-t", f"{target_duration:.3f}", "-vn", "-sn", "-dn", "-c:a", "libmp3lame", "-q:a", "3", str(temporary),
    ])
    temporary.replace(path)


@lru_cache(maxsize=3)
def _whisper_model(model_size: str):
    from faster_whisper import WhisperModel

    return WhisperModel(model_size, device="cpu", compute_type="int8", cpu_threads=max(2, min(6, os.cpu_count() or 2)))


_PYAV_COMPAT_PATCHED = False


def _ensure_pyav_compatibility() -> None:
    global _PYAV_COMPAT_PATCHED
    import av

    if _PYAV_COMPAT_PATCHED:
        return
    current_open = av.open

    def compatible_open(*args, **kwargs):
        # faster-whisper passes this optional kwarg; newer PyAV versions removed it.
        kwargs.pop("metadata_errors", None)
        return current_open(*args, **kwargs)

    av.open = compatible_open
    _PYAV_COMPAT_PATCHED = True


def transcribe_bounds(path: Path, model_size: str = "tiny", language: str = "en") -> tuple[float, float, str]:
    try:
        _ensure_pyav_compatibility()
        model = _whisper_model(model_size)
        segments, _info = model.transcribe(str(path), beam_size=1, vad_filter=True, language=language)
        points = [(float(segment.start), float(segment.end), str(segment.text).strip()) for segment in segments]
        if not points:
            return 0.0, audio_duration(path), ""
        return max(0.0, min(p[0] for p in points)), max(p[1] for p in points), " ".join(p[2] for p in points)
    except Exception as exc:
        raise RuntimeError("تعذر تفريغ الصوت وتحديد توقيت الترجمة عبر faster-whisper. أعد المحاولة أو اختر وضعاً أصغر للنموذج.") from exc


def transcribe_word_timings(path: Path, model_size: str = "tiny", language: str = "en") -> list[tuple[float, float, str]]:
    """Return word-level speech timestamps; distribute segment timestamps if Whisper has no word marks."""
    try:
        _ensure_pyav_compatibility()
        model = _whisper_model(model_size)
        segments, _info = model.transcribe(
            str(path), beam_size=1, vad_filter=True, language=language, word_timestamps=True
        )
        cues: list[tuple[float, float, str]] = []
        for segment in segments:
            words = getattr(segment, "words", None) or []
            if words:
                for word in words:
                    text = str(getattr(word, "word", "")).strip()
                    start = getattr(word, "start", None)
                    end = getattr(word, "end", None)
                    if text and start is not None and end is not None:
                        cues.append((max(0.0, float(start)), max(float(start) + 0.04, float(end)), text))
                continue
            tokens = str(segment.text).split()
            start, end = float(segment.start), float(segment.end)
            if tokens:
                step = max(0.04, (end - start) / len(tokens))
                cues.extend((start + i * step, min(end, start + (i + 1) * step), token) for i, token in enumerate(tokens))
        return cues
    except Exception as exc:
        raise RuntimeError("تعذر استخراج توقيت الكلمات عبر faster-whisper. أعد المحاولة أو اختر وضعاً أصغر للنموذج.") from exc


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ):
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            pass
    return ImageFont.load_default()


def _make_artwork(path: Path, width: int, height: int, number: int, keyword: str) -> None:
    """Offline cinematic fallback artwork; FFmpeg later gives it continuous camera motion."""
    path.parent.mkdir(parents=True, exist_ok=True)
    palette = [(12, 24, 35), (18, 30, 41), (22, 25, 38), (15, 33, 41)]
    base = palette[(number - 1) % len(palette)]
    image = Image.new("RGB", (width, height), base)
    pixels = image.load()
    if pixels is None:
        raise RuntimeError("تعذر إعداد لوحة بكسلات الصورة التجريبية.")
    cx, cy = int(width * (0.58 if number % 2 else 0.43)), int(height * 0.48)
    radius = max(width, height) * 0.63
    glow = (118, 84, 43) if number % 2 else (34, 86, 104)
    for y in range(height):
        for x in range(width):
            distance = math.sqrt((x - cx) ** 2 + (y - cy) ** 2) / radius
            strength = max(0.0, 1.0 - distance) ** 2 * 0.36
            edge = max(0.0, (y / height - 0.50)) * 0.08
            pixels[x, y] = tuple(min(255, int(base[c] * (1 - strength - edge) + glow[c] * strength)) for c in range(3))
    draw = ImageDraw.Draw(image, "RGBA")
    for offset in range(0, width, max(50, width // 18)):
        draw.line((offset, 0, offset - int(height * 0.20), height), fill=(205, 174, 120, 12), width=1)
    radius = int(min(width, height) * 0.30)
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=(213, 169, 92, 56), width=max(1, width // 700))
    draw.ellipse((cx - radius * 0.68, cy - radius * 0.68, cx + radius * 0.68, cy + radius * 0.68), outline=(213, 169, 92, 25), width=max(1, width // 1100))
    margin = int(width * 0.07)
    draw.text((margin, int(height * 0.13)), "DOCUFLOW  /  FIELD NOTES", font=_font(max(15, int(width * 0.021))), fill=(218, 180, 108, 215))
    title = " ".join(keyword.replace(",", " ").split())[:72]
    # Keep the lower third clear for burned-in Arabic captions.
    draw.text((margin, int(height * 0.65)), f"SCENE {number:02d}  ·  MOTION STUDY", font=_font(max(13, int(width * 0.018))), fill=(225, 232, 235, 170))
    draw.text((margin, int(height * 0.71)), title, font=_font(max(16, int(width * 0.024))), fill=(246, 245, 236, 235))
    image.save(path, format="JPEG", quality=88)


def _camera_filter(width: int, height: int, duration: float, number: int) -> str:
    frames = max(FPS, int(math.ceil(duration * FPS)))
    progress = f"on/{frames}"
    mode = (number - 1) % 4
    if mode == 0:
        zoom = f"1+0.10*{progress}"
        x = f"(iw-iw/zoom)*(0.12+0.76*{progress})"
        y = "(ih-ih/zoom)*0.42"
    elif mode == 1:
        zoom = f"1.10-0.10*{progress}"
        x = f"(iw-iw/zoom)*(0.86-0.72*{progress})"
        y = "(ih-ih/zoom)*0.58"
    elif mode == 2:
        zoom = f"1.035+0.025*{progress}"
        x = f"(iw-iw/zoom)*(0.50+0.34*sin(2*PI*{progress}))"
        y = f"(ih-ih/zoom)*(0.50-0.30*{progress})"
    else:
        zoom = f"1.08-0.06*{progress}"
        x = f"(iw-iw/zoom)*(0.12+0.66*{progress})"
        y = f"(ih-ih/zoom)*(0.18+0.64*{progress})"
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},fps={FPS},"
        f"zoompan=z='{zoom}':x='{x}':y='{y}':d=1:s={width}x{height}:fps={FPS},"
        f"setsar=1,format=yuv420p"
    )


def prepare_scene_video(
    source: Path | None, keyword: str, destination: Path, ratio: str, duration: float,
    number: int, work_dir: Path, transition_tail: float = 0.0,
) -> None:
    width, height = (1280, 720) if ratio == "16:9" else (720, 1280)
    destination.parent.mkdir(parents=True, exist_ok=True)
    clip_duration = max(1.0, duration + max(0.0, transition_tail))
    if source is None:
        source = work_dir / f"visual_{number:02d}.jpg"
        _make_artwork(source, width, height, number, keyword)
    filter_graph = _camera_filter(width, height, clip_duration, number)
    is_video = source.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
    if is_video:
        input_args = ["-stream_loop", "-1", "-i", str(source)]
        quality = "25"
    else:
        input_args = ["-loop", "1", "-framerate", str(FPS), "-i", str(source)]
        quality = "27"
    _run([
        FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *input_args,
        "-map", "0:v:0", "-map_metadata", "-1", "-map_chapters", "-1",
        "-t", f"{clip_duration:.3f}", "-vf", filter_graph,
        "-an", "-sn", "-dn", "-c:v", "libx264",
        "-preset", "ultrafast", "-crf", quality, "-r", str(FPS),
        "-write_tmcd", "0", "-movflags", "+faststart", str(destination),
    ])


def create_ambient_music(destination: Path, duration: float) -> None:
    duration = max(5.0, duration)
    fade_start = max(0.1, duration - min(2.5, duration / 3))
    filter_complex = (
        "[0:a]volume=0.22[a0];[1:a]volume=0.13[a1];"
        "[2:a]lowpass=f=650,volume=0.10[a2];"
        f"[a0][a1][a2]amix=inputs=3:duration=longest,afade=t=in:st=0:d=1.8,afade=t=out:st={fade_start:.2f}:d={duration-fade_start:.2f},volume=0.50[out]"
    )
    _run([
        FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"sine=frequency=110:sample_rate=44100:duration={duration:.2f}",
        "-f", "lavfi", "-i", f"sine=frequency=164.81:sample_rate=44100:duration={duration:.2f}",
        "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.025:sample_rate=44100:duration={duration:.2f}",
        "-filter_complex", filter_complex, "-map", "[out]", "-ac", "2", "-c:a", "libmp3lame", "-b:a", "96k", str(destination),
    ])


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    whole = int(seconds % 60)
    centiseconds = int((seconds - int(seconds)) * 100)
    return f"{hours}:{minutes:02d}:{whole:02d}.{centiseconds:02d}"


def write_arabic_ass(captions: list[dict[str, Any]], destination: Path, ratio: str) -> None:
    width, height = (1280, 720) if ratio == "16:9" else (720, 1280)
    font_size = 42 if ratio == "16:9" else 38
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]", "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Arabic,Noto Sans Arabic,{font_size},&H00FFFFFF,&H000000FF,&HCC07111A,&H8807111A,0,0,0,0,100,100,0,0,1,2.6,0.6,2,46,46,{int(height * 0.075)},1", "",
        "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for cue in captions:
        text = str(cue.get("text", "")).replace("\r", " ").replace("\n", " ").replace("{", "(").replace("}", ")")
        start = max(0.0, float(cue["start"]))
        end = max(start + 0.45, float(cue["end"]))
        # libass shapes Arabic and applies the right-to-left paragraph direction in ASS.
        lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Arabic,,0,0,0,,{{\\an2\\q2}}{text}")
    destination.write_text("\ufeff" + "\n".join(lines) + "\n", encoding="utf-8")


def _normalize_loudness(source: Path, destination: Path) -> None:
    """Two-pass EBU R128 loudness normalization with a −1 dBFS sample limiter."""
    analysis = subprocess.run(
        [FFMPEG, "-hide_banner", "-nostats", "-i", str(source), "-map", "0:a:0",
         "-af", "loudnorm=I=-14:TP=-1.0:LRA=7:print_format=json", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    if analysis.returncode != 0:
        raise RuntimeError("تعذر تحليل ارتفاع الصوت وفق EBU R128.")
    import json
    import re

    candidates = re.findall(r"\{[^{}]+\}", analysis.stderr)
    measured: dict[str, str] = {}
    if candidates:
        try:
            measured = json.loads(candidates[-1])
        except json.JSONDecodeError:
            measured = {}
    fields = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    finite = all(measured.get(name) not in (None, "-inf", "inf", "nan") for name in fields)
    if finite:
        loudnorm = (
            "loudnorm=I=-14:TP=-1.0:LRA=7:"
            f"measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
            f"measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}:"
            f"offset={measured['target_offset']}:linear=true:print_format=summary"
        )
    else:
        loudnorm = "loudnorm=I=-14:TP=-1.0:LRA=7:print_format=summary"
    audio_filter = f"{loudnorm},alimiter=limit=0.891251:level=false"
    _run([
        FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
        "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy", "-af", audio_filter,
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(destination),
    ])


def render_video(
    scene_videos: list[Path], audio_paths: list[Path], music_path: Path | None,
    ass_path: Path, destination: Path, ratio: str, scene_durations: list[float] | None = None,
) -> None:
    from moviepy import AudioFileClip, CompositeAudioClip, CompositeVideoClip, VideoFileClip
    from moviepy.video.fx import CrossFadeIn

    destination.parent.mkdir(parents=True, exist_ok=True)
    source_clips = []
    layers = []
    voice_sources = []
    voice_clips = []
    music_clip = None
    final_video = None
    mixed_audio = None
    mixed_path = destination.with_name(destination.stem + "_premaster.mp4")
    caption_path = destination.with_name(destination.stem + "_captions.mp4")
    width, height = (1280, 720) if ratio == "16:9" else (720, 1280)
    fade = 0.22
    try:
        cursor = 0.0
        for index, (video_path, audio_path) in enumerate(zip(scene_videos, audio_paths)):
            video = VideoFileClip(str(video_path), audio=False)
            voice = AudioFileClip(str(audio_path))
            source_clips.append(video)
            voice_sources.append(voice)
            layer = video.with_start(cursor)
            if index > 0:
                layer = layer.with_effects([CrossFadeIn(min(fade, max(0.08, video.duration / 5)))])
            layers.append(layer)
            voice_clips.append(voice.with_start(cursor))
            interval = scene_durations[index] if scene_durations and index < len(scene_durations) else video.duration
            cursor += float(interval)
        final_duration = cursor + fade
        final_video = CompositeVideoClip(layers, size=(width, height), bg_color=(0, 0, 0)).with_duration(final_duration)
        if music_path and music_path.is_file():
            music_clip = AudioFileClip(str(music_path)).with_duration(final_duration).with_volume_scaled(0.10)
        mixed_audio = CompositeAudioClip(voice_clips + ([music_clip] if music_clip else [])).with_duration(final_duration)
        final_video = final_video.with_audio(mixed_audio)
        final_video.write_videofile(
            str(mixed_path), fps=FPS, codec="libx264", audio_codec="aac", preset="ultrafast", threads=2,
            bitrate="2400k", logger=None,
        )
        escaped_ass = str(ass_path.resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        _run([
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(mixed_path),
            "-vf", f"subtitles=filename='{escaped_ass}'", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
            "-c:a", "copy", "-movflags", "+faststart", str(caption_path),
        ])
        _normalize_loudness(caption_path, destination)
    finally:
        if final_video is not None:
            final_video.close()
        if mixed_audio is not None:
            mixed_audio.close()
        for clip in source_clips:
            clip.close()
        for clip in voice_sources:
            clip.close()
        if music_clip is not None:
            music_clip.close()
        mixed_path.unlink(missing_ok=True)
        caption_path.unlink(missing_ok=True)
