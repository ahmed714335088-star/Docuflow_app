import json
import re
from pathlib import Path
from typing import Any

import httpx

from app.models import ApiKeys

GROQ_MODEL = "llama-3.3-70b-versatile"
GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_MODEL_PREFERENCE = (
    "gemini-4-hydrogen",
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
)
GEMINI_STATIC_FALLBACK = GEMINI_MODEL_PREFERENCE[:4]

DEMO_SCENES_EN = [
    {
        "narration": "Imagine waking up to a world where the Sun has vanished. The sky would not go dark at once; sunlight takes eight minutes to reach Earth.",
        "translation_ar": "تخيّل أن تستيقظ في عالم اختفت منه الشمس. لن تظلم السماء فوراً؛ فضوء الشمس يحتاج إلى ثماني دقائق ليصل إلى الأرض.",
        "visual_keywords": "Earth from space, sunrise, sunlight, planet",
    },
    {
        "narration": "Then the planet would begin to lose its warmth. Oceans would freeze from the surface downward, while life deep below continued for a little longer.",
        "translation_ar": "بعدها سيبدأ كوكبنا بفقدان دفئه. ستتجمد المحيطات من سطحها إلى الأسفل، فيما تواصل الحياة في الأعماق زمناً أطول بقليل.",
        "visual_keywords": "ocean from above, winter landscape, frozen water, deep sea",
    },
    {
        "narration": "But this is only a thought experiment. Our Sun is expected to shine for billions of years, giving Earth an extraordinary amount of time.",
        "translation_ar": "لكن هذا مجرد افتراض للتأمل. يُتوقع أن تواصل شمسنا الإشراق مليارات السنين، مانحةً الأرض زمناً هائلاً.",
        "visual_keywords": "stars, solar flare, scientific observatory, night sky",
    },
]

DEMO_SCENES_AR = [
    {
        "narration": "هل تخيّلت يوماً أن وراء موضوعنا حكاية لا نراها للوهلة الأولى؟ أحياناً، تفصيلة صغيرة تقلب فهمنا للصورة كلها.",
        "translation_ar": "هل تخيّلت يوماً أن وراء موضوعنا حكاية لا نراها للوهلة الأولى؟ أحياناً، تفصيلة صغيرة تقلب فهمنا للصورة كلها.",
        "visual_keywords": "cinematic documentary footage, mysterious detail, slow reveal",
    },
    {
        "narration": "كلما اقتربنا من التفاصيل، ظهرت أسئلة جديدة. وما يبدو مألوفاً من بعيد، قد يخفي في داخله مفاجأة تستحق أن نتوقف عندها.",
        "translation_ar": "كلما اقتربنا من التفاصيل، ظهرت أسئلة جديدة. وما يبدو مألوفاً من بعيد، قد يخفي في داخله مفاجأة تستحق أن نتوقف عندها.",
        "visual_keywords": "macro close up, science documentary, cinematic discovery",
    },
    {
        "narration": "وهنا تكمن الفكرة: المعرفة لا تمنحنا إجابة فقط، بل تجعلنا ننظر إلى العالم بطريقة مختلفة. فما السؤال الذي ستبحث عن إجابته الآن؟",
        "translation_ar": "وهنا تكمن الفكرة: المعرفة لا تمنحنا إجابة فقط، بل تجعلنا ننظر إلى العالم بطريقة مختلفة. فما السؤال الذي ستبحث عن إجابته الآن؟",
        "visual_keywords": "wide cinematic landscape, curious researcher, documentary ending",
    },
]


def _english_visual_keywords(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9\s,;.'-]", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[,;]\s*[,;]+", ", ", text)
    text = re.sub(r"\s+", " ", text).strip(" ,;.-")[:180]
    return text or "documentary, science, cinematic stock footage"


def demo_script(topic: str, video_language: str = "en") -> dict[str, Any]:
    if video_language == "ar":
        scenes = []
        for sample in DEMO_SCENES_AR:
            narration = sample["narration"].replace("موضوعنا", f"«{topic}»")
            scenes.append({**sample, "narration": narration, "translation_ar": narration})
        return {
            "title": f"ما لا نراه في {topic}",
            "hook": f"هل تعرف الحكاية الخفية وراء «{topic}»؟",
            "scenes": scenes,
            "demo": True,
            "model": "demo",
        }
    return {
        "title": f"A documentary thought experiment: {topic}",
        "hook": "What would really happen if the rules of our world suddenly changed?",
        "scenes": [dict(scene) for scene in DEMO_SCENES_EN],
        "demo": True,
        "model": "demo",
    }


def _parse_json(text: str, video_language: str = "en") -> dict[str, Any]:
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
        if not isinstance(scene, dict):
            continue
        narration = str(scene.get("narration") or scene.get("narration_en") or "").strip()
        translation = str(scene.get("translation_ar", "")).strip()
        if video_language == "ar":
            # In Arabic-video mode the spoken narration is also the exact subtitle text.
            translation = narration
        keywords = _english_visual_keywords(scene.get("visual_keywords"))
        if narration and (translation or video_language == "ar"):
            normalized.append({
                "narration": narration,
                "translation_ar": translation,
                "visual_keywords": keywords,
            })
    if not normalized:
        raise ValueError("تعذر العثور على مشاهد صالحة في استجابة الخدمة.")
    default_title = "فيلم وثائقي قصير" if video_language == "ar" else "Documentary short"
    return {
        "title": str(payload.get("title") or default_title).strip()[:160],
        "hook": str(payload.get("hook") or "").strip()[:400],
        "scenes": normalized,
        "demo": False,
    }


def _prompt(topic: str, video_language: str = "en") -> str:
    if video_language == "ar":
        return f"""اكتب نصاً عربياً جذاباً لفيديو وثائقي قصير عن: {topic!r}.
استخدم عربية فصحى سهلة وسلسة، وسرداً قصصياً مشوقاً بإيقاع سريع وخطاف قوي في البداية، دون تقليد عبارات قناة بعينها. اجعل المعلومات دقيقة ولا تخترع حقائق. اكتب العنوان والخطاف والسرد بالعربية. يجب أن يطابق النص في translation_ar السرد العربي المنطوق حرفياً تقريباً، لأنهما سيُستخدمان للصوت والترجمة المتزامنة. اكتب كلمات البحث المرئي بالإنجليزية فقط (4 إلى 8 كلمات مفتاحية قصيرة لكل مشهد) كي يبحث Pixabay بها.
أعد JSON صالحاً فقط بهذا الشكل:
{{"title":"عنوان عربي قصير","hook":"خطاف عربي قوي","scenes":[{{"narration":"فقرة عربية طبيعية منطوقة، نحو 18-35 كلمة","translation_ar":"النص العربي نفسه للترجمة","visual_keywords":"4-8 English stock-footage search keywords"}}]}}
أنشئ 3 أو 4 مشاهد بإجمالي يقارب 90-130 كلمة منطوقة. لا تضف Markdown."""
    return f"""Create a concise, factual, engaging English documentary voiceover about: {topic!r}.
Return ONLY valid JSON with this exact shape:
{{"title":"short English title","hook":"one strong opening hook","scenes":[{{"narration":"one natural spoken English paragraph, 18-35 words","translation_ar":"faithful fluent Modern Standard Arabic translation of exactly that scene","visual_keywords":"4-8 English stock-footage search keywords"}}]}}
Produce 3 or 4 scenes, total narration around 90-130 words. Be accurate, avoid invented facts, use a compelling hook, ensure each scene has its own matching visuals, and keep all visual keywords in English for Pixabay search. Do not include markdown."""


async def _discover_gemini_models(client: httpx.AsyncClient, api_key: str) -> list[str]:
    """List current, known-free Gemini text models that support generateContent."""
    available: set[str] = set()
    page_token = ""
    for _ in range(8):
        params: dict[str, Any] = {"key": api_key, "pageSize": 100}
        if page_token:
            params["pageToken"] = page_token
        try:
            response = await client.get(GEMINI_MODELS_URL, params=params)
        except httpx.HTTPError:
            return []
        if response.status_code >= 400:
            return []
        try:
            payload = response.json()
        except (ValueError, TypeError):
            return []
        models = payload.get("models", []) if isinstance(payload, dict) else []
        for model in models if isinstance(models, list) else []:
            if not isinstance(model, dict) or "generateContent" not in model.get("supportedGenerationMethods", []):
                continue
            identifiers = {
                str(model.get("baseModelId", "")).strip(),
                str(model.get("name", "")).removeprefix("models/").strip(),
            }
            available.update(identifier for identifier in identifiers if identifier in GEMINI_MODEL_PREFERENCE)
        page_token = str(payload.get("nextPageToken", "")).strip()
        if not page_token:
            break
    return [model for model in GEMINI_MODEL_PREFERENCE if model in available]


async def _generate_gemini(client: httpx.AsyncClient, prompt: str, api_key: str, video_language: str) -> dict[str, Any]:
    discovered = await _discover_gemini_models(client, api_key)
    models = discovered or list(GEMINI_STATIC_FALLBACK)
    attempted: list[str] = []
    last_status: int | None = None
    for model in models:
        attempted.append(model)
        try:
            response = await client.post(
                f"{GEMINI_MODELS_URL}/{model}:generateContent",
                params={"key": api_key},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.65, "responseMimeType": "application/json"},
                },
            )
        except httpx.HTTPError as exc:
            raise RuntimeError("تعذر الاتصال بخدمة Gemini. تحقق من الإنترنت وحاول مجدداً.") from exc
        if response.status_code >= 400:
            last_status = response.status_code
            continue
        try:
            payload = response.json()
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            last_status = response.status_code
            continue
        try:
            result = _parse_json(text, video_language)
        except (ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("أعاد Gemini استجابة غير صالحة؛ تحقق من لغة المحتوى ثم حاول مجدداً.") from exc
        result["model"] = model
        return result
    models_text = ", ".join(attempted[:5])
    suffix = f" (آخر HTTP {last_status})" if last_status else ""
    raise RuntimeError(
        f"لم ينجح أي نموذج Gemini متاح: {models_text}{suffix}. تحقق من المفتاح وحصة Free Tier، أو اختر Groq/الوضع التجريبي."
    )


async def generate_script(
    topic: str,
    provider: str,
    keys: ApiKeys,
    video_language: str = "en",
) -> dict[str, Any]:
    if provider == "demo":
        return demo_script(topic, video_language)
    prompt = _prompt(topic, video_language)
    timeout = httpx.Timeout(60.0, connect=15.0)
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
                            {"role": "system", "content": "Write accurate, engaging short documentary scripts in the requested language. Keep stock footage keywords in English."},
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
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise RuntimeError("تعذر قراءة استجابة Groq.") from exc
            result = _parse_json(text, video_language)
            result["model"] = GROQ_MODEL
            return result

        if provider == "gemini":
            if not keys.gemini.strip():
                raise ValueError("أدخل مفتاح Gemini في الإعدادات أو اختر الوضع التجريبي.")
            return await _generate_gemini(client, prompt, keys.gemini.strip(), video_language)

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
                if size > max_size or (quality == "large" and size == 0):
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
