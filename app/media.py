import math
import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

FFMPEG = os.environ.get("FFMPEG_BINARY", "ffmpeg")


def _run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        tail = "\n".join(detail[-5:])
        raise RuntimeError(f"فشل FFmpeg أثناء تجهيز الفيديو. {tail[-900:]}")


async def make_voiceover(text: str, voice: str, destination: Path) -> None:
    import edge_tts

    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        await edge_tts.Communicate(text=text, voice=voice, rate="-4%", pitch="-1Hz").save(str(destination))
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
            d = math.sqrt((x - cx) ** 2 + (y - cy) ** 2) / radius
            strength = max(0.0, 1.0 - d) ** 2 * 0.36
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
    draw.text((margin, int(height * 0.78)), f"FRAME {number:02d}  ·  VISUAL STUDY", font=_font(max(13, int(width * 0.018))), fill=(225, 232, 235, 170))
    draw.text((margin, int(height * 0.83)), title, font=_font(max(16, int(width * 0.024))), fill=(246, 245, 236, 235))
    image.save(path, format="JPEG", quality=88)


def prepare_scene_video(source: Path | None, keyword: str, destination: Path, ratio: str, duration: float, number: int, work_dir: Path) -> None:
    width, height = (1280, 720) if ratio == "16:9" else (720, 1280)
    destination.parent.mkdir(parents=True, exist_ok=True)
    clip_duration = max(1.0, duration)
    vf = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,fps=24,format=yuv420p"
    if source is None:
        image_path = work_dir / f"visual_{number:02d}.jpg"
        _make_artwork(image_path, width, height, number, keyword)
        command = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-framerate", "24", "-i", str(image_path), "-t", f"{clip_duration:.3f}", "-vf", vf, "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "27", "-movflags", "+faststart", str(destination)]
    else:
        command = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-stream_loop", "-1", "-i", str(source), "-t", f"{clip_duration:.3f}", "-vf", vf, "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "25", "-movflags", "+faststart", str(destination)]
    _run(command)


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


def render_video(scene_videos: list[Path], audio_paths: list[Path], music_path: Path | None, ass_path: Path, destination: Path, ratio: str) -> None:
    from moviepy import AudioFileClip, CompositeAudioClip, VideoFileClip, concatenate_videoclips

    destination.parent.mkdir(parents=True, exist_ok=True)
    clips = []
    voice_clips = []
    music_clip = None
    final_video = None
    mixed_audio = None
    try:
        offsets = []
        cursor = 0.0
        for video_path, audio_path in zip(scene_videos, audio_paths):
            video = VideoFileClip(str(video_path), audio=False)
            voice = AudioFileClip(str(audio_path))
            offsets.append(cursor)
            clips.append(video)
            voice_clips.append(voice.with_start(cursor))
            cursor += video.duration
        final_video = concatenate_videoclips(clips, method="compose")
        if music_path and music_path.is_file():
            music_clip = AudioFileClip(str(music_path)).with_duration(cursor).with_volume_scaled(0.10)
        mixed_audio = CompositeAudioClip(voice_clips + ([music_clip] if music_clip else [])).with_duration(cursor)
        final_video = final_video.with_audio(mixed_audio)
        silent_path = destination.with_name(destination.stem + "_silent-captions.mp4")
        final_video.write_videofile(
            str(silent_path), fps=24, codec="libx264", audio_codec="aac", preset="ultrafast", threads=2,
            bitrate="2400k", logger=None,
        )
        escaped_ass = str(ass_path.resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        _run([
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(silent_path),
            "-vf", f"subtitles=filename='{escaped_ass}'", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
            "-c:a", "copy", "-movflags", "+faststart", str(destination),
        ])
        silent_path.unlink(missing_ok=True)
    finally:
        if final_video is not None:
            final_video.close()
        if mixed_audio is not None:
            mixed_audio.close()
        for clip in clips:
            clip.close()
        for clip in voice_clips:
            clip.close()
        if music_clip is not None:
            music_clip.close()
