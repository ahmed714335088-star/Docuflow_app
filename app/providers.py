import json
import re
from pathlib import Path
from typing import Any

import httpx

from app.models import ApiKeys

GROQ_MODEL = "llama-3.3-70b-versatile"
GEMINI_MODEL = "gemini-2.5-flash"


DEMO_SCENES = [
    {
        "narration_en": "Imagine waking up to a world where the Sun has vanished. The sky would not go dark at once; sunlight takes eight minutes to reach Earth.",
        "translation_ar": "تخيّل أن تستيقظ في عالم اختفت منه الشمس. لن تظلم السماء فوراً؛ فضوء الشمس يحتاج إلى ثماني دقائق ليصل إلى الأرض.",
        "visual_keywords": "Earth from space, sunrise, sunlight, planet",
    },
    {
        "narration_en": "Then the planet would begin to lose its warmth. Oceans would freeze from the surface downward, while life deep below continued for a little longer.",
        "translation_ar": "بعدها سيبدأ كوكبنا بفقدان دفئه. ستتجمد المحيطات من سطحها إلى الأسفل، فيما تواصل الحياة في الأعماق زمناً أطول بقليل.",
        "visual_keywords": "ocean from above, winter landscape, frozen water, deep sea",
    },
    {
        "narration_en": "But this is only a thought experiment. Our Sun is expected to shine for billions of years, giving Earth an extraordinary amount of time.",
        "translation_ar": "لكن هذا مجرد افتراض للتأمل. يُتوقع أن تواصل شمسنا الإشراق مليارات السنين، مانحةً الأرض زمناً هائلاً.",
        "visual_keywords": "stars, solar flare, scientific observatory, night sky",
    },
]


def demo_script(topic: str) -> dict[str, Any]:
    scenes = []
    for i, sample in enumerate(DEMO_SCENES):
        scenes.append({**sample, "visual_keywords": f"{topic}, {sample['visual_keywords']}"})
    return {
        "title": f"A documentary thought experiment: {topic}",
        "hook": "What would really happen if the rules of our world suddenly changed?",
        "scenes": scenes,
        "demo": True,
    }


def _parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    first, last = text.find("{"), text.rfind("}")
    if first < 0 or last <= first:
        raise ValueError("لم تُرجع خدمة النص بنية JSON صالحة.")
    payload = json.loads(text[first : last + 1])
    scenes = payload.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("استجابة خدمة النص لا تحتوي مشاهد.")
    normalized = []
    for scene in scenes[:5]:
        en = str(scene.get("narration_en", "")).strip()
        ar = str(scene.get("translation_ar", "")).strip()
        keywords = str(scene.get("visual_keywords", "")).strip()
        if en and ar and keywords:
            normalized.append({"narration_en": en, "translation_ar": ar, "visual_keywords": keywords})
    if not normalized:
        raise ValueError("تعذر العثور على مشاهد صالحة في استجابة الخدمة.")
    return {
        "title": str(payload.get("title") or "Documentary short").strip()[:160],
        "hook": str(payload.get("hook") or "").strip()[:400],
        "scenes": normalized,
        "demo": False,
    }


def _prompt(topic: str) -> str:
    return f"""Create a concise, factual, engaging English documentary voiceover about: {topic!r}.
Return ONLY valid JSON with this exact shape:
{{"title":"short English title","hook":"one strong opening hook","scenes":[{{"narration_en":"one natural spoken English paragraph, 18-35 words","translation_ar":"faithful fluent Modern Standard Arabic translation of exactly that scene","visual_keywords":"4-8 English stock-footage search keywords"}}]}}
Produce 3 or 4 scenes, total narration around 90-130 words. Be accurate, avoid invented facts, use a compelling hook, ensure each scene has its own matching visuals, and do not include markdown."""


async def generate_script(topic: str, provider: str, keys: ApiKeys) -> dict[str, Any]:
    if provider == "demo":
        return demo_script(topic)
    prompt = _prompt(topic)
    timeout = httpx.Timeout(45.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        if provider == "groq":
            if not keys.groq.strip():
                raise ValueError("أدخل مفتاح Groq في الإعدادات أو اختر الوضع التجريبي.")
            try:
                response = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {keys.groq.strip()}"},
                    json={
                        "model": GROQ_MODEL,
                        "temperature": 0.65,
                        "response_format": {"type": "json_object"},
                        "messages": [
                            {"role": "system", "content": "You write concise, accurate documentary scripts and Arabic translations."},
                            {"role": "user", "content": prompt},
                        ],
                    },
                )
            except httpx.HTTPError as exc:
                raise RuntimeError("تعذر الاتصال بخدمة Groq. تحقق من الإنترنت وحاول مجدداً.") from exc
            if response.status_code >= 400:
                raise RuntimeError(f"رفضت خدمة Groq الطلب (HTTP {response.status_code}). راجع المفتاح أو الحصة المتاحة.")
            try:
                text = response.json()["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                raise RuntimeError("تعذر قراءة استجابة Groq.") from exc
            return _parse_json(text)

        if provider == "gemini":
            if not keys.gemini.strip():
                raise ValueError("أدخل مفتاح Gemini في الإعدادات أو اختر الوضع التجريبي.")
            try:
                response = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
                    params={"key": keys.gemini.strip()},
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"temperature": 0.65, "responseMimeType": "application/json"},
                    },
                )
            except httpx.HTTPError as exc:
                raise RuntimeError("تعذر الاتصال بخدمة Gemini. تحقق من الإنترنت وحاول مجدداً.") from exc
            if response.status_code >= 400:
                raise RuntimeError(f"رفضت خدمة Gemini الطلب (HTTP {response.status_code}). راجع المفتاح أو الحصة المتاحة.")
            try:
                text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                raise RuntimeError("تعذر قراءة استجابة Gemini.") from exc
            return _parse_json(text)

    raise ValueError("مزود النص غير معروف.")


async def fetch_pixabay_video(query: str, key: str, orientation: str, destination: Path) -> bool:
    """Download one suitable Pixabay video; return False when search has no match."""
    if not key.strip():
        return False
    portrait = orientation == "portrait"
    params = {
        "key": key.strip(),
        "q": " ".join(query.split())[:100] or "nature",
        "per_page": 20,
        "video_type": "film",
        "safesearch": "true",
        "order": "popular",
        "min_width": 720 if portrait else 1280,
        "min_height": 1280 if portrait else 720,
    }
    timeout = httpx.Timeout(60.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        try:
            response = await client.get("https://pixabay.com/api/videos/", params=params)
        except httpx.HTTPError as exc:
            raise RuntimeError("تعذر الاتصال بـPixabay؛ تحقق من المفتاح والإنترنت.") from exc
        if response.status_code in (401, 403):
            raise RuntimeError("رفض Pixabay المفتاح. راجع مفتاح API في الإعدادات.")
        if response.status_code >= 400:
            raise RuntimeError(f"رفض Pixabay البحث (HTTP {response.status_code}).")
        try:
            hits = response.json().get("hits", [])
        except (ValueError, AttributeError, TypeError) as exc:
            raise RuntimeError("تعذر قراءة استجابة Pixabay.") from exc
        if not isinstance(hits, list) or not hits:
            return False
        selected_url = ""
        max_size = 90 * 1024 * 1024
        for hit in hits:
            variants = hit.get("videos", {}) if isinstance(hit, dict) else {}
            candidates = []
            for quality in ("large", "medium", "small", "tiny"):
                item = variants.get(quality, {}) if isinstance(variants, dict) else {}
                url = str(item.get("url", "")) if isinstance(item, dict) else ""
                width = int(item.get("width", 0) or 0) if isinstance(item, dict) else 0
                height = int(item.get("height", 0) or 0) if isinstance(item, dict) else 0
                size = int(item.get("size", 0) or 0) if isinstance(item, dict) else 0
                if not url.startswith("https://") or width <= 0 or height <= 0:
                    continue
                if size > max_size:
                    continue
                if quality == "large" and size == 0:
                    continue
                if portrait and (height <= width or height < 720):
                    continue
                if not portrait and (width < height or width < 1280 or height < 720):
                    continue
                candidates.append((width * height, url))
            if candidates:
                selected_url = max(candidates, key=lambda candidate: candidate[0])[1]
                break
        if not selected_url:
            return False
        destination.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        try:
            async with client.stream("GET", selected_url) as download:
                download.raise_for_status()
                with destination.open("wb") as output:
                    async for chunk in download.aiter_bytes(1024 * 256):
                        total += len(chunk)
                        if total > max_size:
                            raise RuntimeError("تجاوز مقطع Pixabay حد الحجم الآمن (90 MB).")
                        output.write(chunk)
        except httpx.HTTPError as exc:
            destination.unlink(missing_ok=True)
            raise RuntimeError("تعذر تنزيل المقطع المرئي من Pixabay.") from exc
        except RuntimeError:
            destination.unlink(missing_ok=True)
            raise
        if destination.exists() and destination.stat().st_size > 50_000:
            return True
        destination.unlink(missing_ok=True)
        return False
