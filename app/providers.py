from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from PIL import Image, ImageOps

from app.models import ApiKeys

GROQ_MODEL = "llama-3.3-70b-versatile"
GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
POLLINATIONS_BASE = "https://gen.pollinations.ai"


class PollinationsError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


_DEMO_EN = [
    ("One question about {topic} can reveal an unexpected story.", "قد يكشف سؤال واحد عن {topic} حكايةً غير متوقعة.", "cinematic question mystery documentary", "Photorealistic cinematic documentary, an intriguing unanswered question shown through a striking real-world visual, high-detail 8K-style texture, dramatic natural light, no text, wide composition."),
    ("The first answer often seems perfectly obvious.", "تبدو الإجابة الأولى غالباً بديهية تماماً.", "obvious answer familiar everyday scene", "Photorealistic documentary close-up of a familiar everyday scene that seems ordinary at first glance, subtle cinematic tension, no text."),
    ("Then a small detail changes everything.", "ثم تظهر تفصيلة صغيرة تقلب كل شيء.", "hidden detail macro discovery", "Extreme macro documentary shot revealing one unexpected detail, shallow depth of field, cinematic lighting, no text."),
    ("Follow it, and the familiar picture begins to shift.", "نتتبعها، فتبدأ الصورة المألوفة بالتغيّر.", "clue trail shifting perspective", "Cinematic documentary camera tracking toward a revealing clue, perspective shifting from shadow into light, no text."),
    ("Patterns appear where no one expected them.", "وتظهر أنماط لم يتوقعها أحد.", "patterns nature science aerial", "Photorealistic aerial documentary view of surprising patterns emerging across a vast landscape, high detail, no text."),
    ("Each clue opens a deeper mystery.", "كل دليل يفتح باباً لغزٍ أعمق.", "deep mystery archive evidence", "Moody documentary shot of carefully preserved evidence and an unfolding mystery, realistic textures, no text."),
    ("But one piece still refuses to fit.", "لكن قطعة واحدة ما زالت خارج الصورة.", "missing piece contradiction dramatic", "A cinematic documentary visual suggesting a missing piece and a contradiction, isolated object under a beam of light, no text."),
    ("Researchers test the evidence against the story.", "يفحص الباحثون الأدلة ويقارنونها بالرواية.", "researcher laboratory evidence testing", "Researchers examining real evidence in a modern laboratory, documentary handheld camera feel, natural skin and materials, no text."),
    ("A new discovery turns the timeline around.", "ثم يقلب اكتشاف جديد ترتيب الأحداث.", "new discovery history timeline", "Cinematic reconstruction of a newly uncovered historical artifact changing a timeline, photorealistic, no text."),
    ("What looked simple now connects distant worlds.", "ما بدا بسيطاً بات يربط عوالم بعيدة.", "connected distant worlds earth night", "Epic documentary shot connecting distant places across Earth at night, lights and atmosphere, wide cinematic frame, no text."),
    ("The truth was hiding in overlooked details.", "كانت الحقيقة مختبئة في تفاصيل تم تجاهلها.", "overlooked detail reveal close up", "Slow cinematic documentary reveal of overlooked details in an object or landscape, subtle suspense, no text."),
    ("And the next question is even more surprising.", "أما السؤال التالي، فربما يكون أكثر إدهاشاً.", "surprising question horizon documentary", "Hopeful yet suspenseful wide documentary ending at a dramatic horizon, opening a new question, photorealistic, no text."),
]

_DEMO_AR = [
    ("وراء «{topic}» سؤال صغير.", "behind topic question mystery documentary", "Photorealistic cinematic documentary, an intriguing unanswered question shown through a striking real-world visual, high-detail 8K-style texture, dramatic natural light, no text, wide composition."),
    ("الإجابة الأولى تبدو سهلة ومألوفة.", "easy familiar answer everyday scene", "Photorealistic documentary close-up of a familiar everyday scene that seems ordinary at first glance, subtle cinematic tension, no text."),
    ("لكن تفصيلة واحدة تغيّر اتجاه القصة.", "small detail changes everything macro", "Extreme macro documentary shot revealing one unexpected detail, shallow depth of field, cinematic lighting, no text."),
    ("نتتبعها، فتبدأ الصورة المألوفة بالتغيّر.", "follow clue shifting perspective", "Cinematic documentary camera tracking toward a revealing clue, perspective shifting from shadow into light, no text."),
    ("وتظهر أنماط لم يتوقعها أحد.", "patterns nature science aerial", "Photorealistic aerial documentary view of surprising patterns emerging across a vast landscape, high detail, no text."),
    ("كل دليل يفتح باباً إلى لغز أعمق.", "deep mystery archive evidence", "Moody documentary shot of carefully preserved evidence and an unfolding mystery, realistic textures, no text."),
    ("لكن قطعة واحدة ما زالت خارج الصورة.", "missing piece contradiction dramatic", "A cinematic documentary visual suggesting a missing piece and a contradiction, isolated object under a beam of light, no text."),
    ("يفحص الباحثون الأدلة ويقارنونها بالرواية.", "researcher laboratory evidence testing", "Researchers examining real evidence in a modern laboratory, documentary handheld camera feel, natural skin and materials, no text."),
    ("ثم يقلب اكتشاف جديد ترتيب الأحداث.", "new discovery history timeline", "Cinematic reconstruction of a newly uncovered historical artifact changing a timeline, photorealistic, no text."),
    ("ما بدا بسيطاً بات يربط عوالم بعيدة.", "connected distant worlds earth night", "Epic documentary shot connecting distant places across Earth at night, lights and atmosphere, wide cinematic frame, no text."),
    ("كانت الحقيقة مختبئة في تفاصيل تم تجاهلها.", "overlooked detail reveal close up", "Slow cinematic documentary reveal of overlooked details in an object or landscape, subtle suspense, no text."),
    ("أما السؤال التالي، فربما يكون أكثر إدهاشاً.", "surprising question horizon documentary", "Hopeful yet suspenseful wide documentary ending at a dramatic horizon, opening a new question, photorealistic, no text."),
]


def _english_text(value: Any, limit: int = 500) -> str:
    text = re.sub(r"[^A-Za-z0-9\s,;:.'!?()/-]", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip(" ,;.-")[:limit]


def _english_visual_keywords(value: Any) -> str:
    return _english_text(value, 180) or "cinematic documentary, science, stock footage"


def _fallback_image_prompt(keywords: str, ratio: str = "16:9") -> str:
    frame = "vertical 9:16" if ratio == "9:16" else "wide 16:9"
    return (
        f"Photorealistic cinematic documentary image, {frame} composition, {keywords}, "
        "rich natural detail, suspenseful light, 8K-style texture, no words, no captions, no watermark."
    )


def demo_script(topic: str, video_language: str = "en") -> dict[str, Any]:
    scenes: list[dict[str, str]] = []
    if video_language == "ar":
        for index, (narration_template, keywords, image_prompt) in enumerate(_DEMO_AR, start=1):
            narration = narration_template.replace("{topic}", f"{topic}")
            scenes.append({
                "narration": narration,
                "translation_ar": narration,
                "visual_keywords": keywords,
                "image_prompt": image_prompt,
            })
        return {
            "title": f"الحكاية الخفية وراء {topic}",
            "hook": f"وراء «{topic}» سؤال صغير يقود إلى حكاية أكبر.",
            "scenes": scenes,
            "demo": True,
            "model": "demo-12-scenes",
        }

    for narration_template, translation_template, keywords, image_prompt in _DEMO_EN:
        scenes.append({
            "narration": narration_template.replace("{topic}", topic),
            "translation_ar": translation_template.replace("{topic}", topic),
            "visual_keywords": keywords,
            "image_prompt": image_prompt,
        })
    return {
        "title": f"A documentary mystery: {topic}",
        "hook": f"One question about {topic} can reveal an unexpected story.",
        "scenes": scenes,
        "demo": True,
        "model": "demo-12-scenes",
    }


def _parse_json(text: str, video_language: str = "en", ratio: str = "16:9") -> dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    first, last = text.find("{"), text.rfind("}")
    if first < 0 or last <= first:
        raise ValueError("لم تُرجع خدمة النص بنية JSON صالحة.")
    payload = json.loads(text[first : last + 1])
    raw_scenes = payload.get("scenes")
    if not isinstance(raw_scenes, list) or not raw_scenes:
        raise ValueError("استجابة خدمة النص لا تحتوي مشاهد.")
    normalized: list[dict[str, str]] = []
    for scene in raw_scenes[:18]:
        if not isinstance(scene, dict):
            continue
        narration = str(scene.get("narration") or scene.get("narration_en") or "").strip()
        translation = str(scene.get("translation_ar", "")).strip()
        if video_language == "ar":
            translation = narration
        keywords = _english_visual_keywords(scene.get("visual_keywords"))
        image_prompt = _english_text(scene.get("image_prompt"), 900) or _fallback_image_prompt(keywords, ratio)
        if narration and (translation or video_language == "ar"):
            normalized.append({
                "narration": narration,
                "translation_ar": translation,
                "visual_keywords": keywords,
                "image_prompt": image_prompt,
            })
    if not normalized:
        raise ValueError("تعذر العثور على مشاهد صالحة في استجابة الخدمة.")
    if len(normalized) < 8:
        raise ValueError("أعاد مزود النص أقل من 8 مشاهد قصيرة. أعد المحاولة أو استخدم الوضع التجريبي.")
    default_title = "فيلم وثائقي قصير" if video_language == "ar" else "Documentary short"
    return {
        "title": str(payload.get("title") or default_title).strip()[:160],
        "hook": str(payload.get("hook") or "").strip()[:400],
        "scenes": normalized,
        "demo": False,
    }


def _prompt(topic: str, video_language: str = "en", ratio: str = "16:9") -> str:
    frame = "9:16 vertical" if ratio == "9:16" else "16:9 horizontal"
    if video_language == "ar":
        return f"""اكتب نصاً وثائقياً سريع الإيقاع ومشوّقاً عن: {topic!r}، بلغة عربية فصحى سهلة، لفيديو {frame}.
ابدأ بخطاف يثير سؤالاً، ثم صعّد الفضول عبر معلومة/دليل/تحول، واختم بكشف مُرضٍ وسؤال يفتح باباً جديداً. لا تقلّد عبارات قناة بعينها، ولا تخترع حقائق.
أعد 12 مشهداً مرقماً في مصفوفة JSON. كل narration جملة منطوقة عربية قصيرة بين 6 و12 كلمة تقريباً، لتستغرق قرابة 3–5 ثوانٍ بصوت الراوي. اجعل كل مشهد لقطة بصرية جديدة ومختلفة عن سابقه. في كل مشهد اكتب translation_ar مطابقاً للسرد العربي (سيُعرض حرفياً كترجمة)، وvisual_keywords من 4–8 كلمات بحث قصيرة بالإنجليزية لـPixabay، وimage_prompt وصفي بالإنجليزية لصورة وثائقية واقعية بطابع سينمائي 8K-style من دون نص مكتوب. اجعل توزيع الأدوار متدرجاً: خطاف، سياق، أدلة، انعطاف، كشف، خاتمة.
أعد JSON صالحاً فقط بالشكل: {{"title":"عنوان عربي قصير","hook":"خطاف عربي","scenes":[{{"narration":"جملة قصيرة","translation_ar":"نفس الجملة","visual_keywords":"English stock-footage keywords","image_prompt":"English photorealistic cinematic image prompt"}}]}}. لا تضف Markdown."""
    return f"""Create a fast-paced, highly engaging and factual cinematic documentary short about: {topic!r}, for {frame}.
Build a strong curiosity hook, escalating evidence, one surprising but well-supported turn, a satisfying reveal, and a final open question. Keep the pacing exciting without copying any named channel or inventing facts.
Return exactly 12 numbered scene objects in a JSON array. Each spoken narration must be one short natural English sentence of about 6–12 words, designed to last roughly 3–5 seconds in voiceover. Every scene must introduce a distinct visual beat. For each scene, provide a faithful fluent Modern Standard Arabic translation, 4–8 short English Pixabay search keywords, and a separate descriptive English image_prompt for a photorealistic cinematic documentary still with 8K-style detail, no text or watermark. The story arc should move through hook, context, clues, reversal, reveal, and payoff.
Return ONLY valid JSON in this exact shape: {{"title":"short English title","hook":"one strong question-driven hook","scenes":[{{"narration":"short 6-12 word sentence","translation_ar":"faithful Arabic translation of this scene","visual_keywords":"4-8 English stock-footage search keywords","image_prompt":"English photorealistic cinematic image prompt"}}]}}. No markdown."""


def _gemini_candidate_sort(model_id: str) -> tuple[int, tuple[int, ...], int, str]:
    lower = model_id.lower()
    is_flash = "flash" in lower
    is_lite = "lite" in lower
    is_preview = any(tag in lower for tag in ("preview", "exp", "experimental"))
    numbers = re.search(r"gemini-(\d+(?:\.\d+)*)", lower)
    version = tuple(int(piece) for piece in numbers.group(1).split(".")) if numbers else (0,)
    return (0 if is_flash and not is_lite else 1 if is_flash else 2, tuple(-n for n in version), int(is_preview), lower)


async def _discover_gemini_models(client: httpx.AsyncClient, api_key: str) -> list[str]:
    """Return only model IDs that this API key currently lists for generateContent."""
    available: set[str] = set()
    page_token = ""
    for _ in range(8):
        params: dict[str, Any] = {"key": api_key, "pageSize": 1000}
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
            if not isinstance(model, dict):
                continue
            methods = model.get("supportedGenerationMethods", [])
            if not isinstance(methods, list) or "generateContent" not in methods:
                continue
            identifiers = [
                str(model.get("baseModelId", "")).strip(),
                str(model.get("name", "")).removeprefix("models/").strip(),
            ]
            for model_id in identifiers:
                low = model_id.lower()
                if not model_id.startswith("gemini-"):
                    continue
                if any(part in low for part in ("pro", "embedding", "image", "audio", "tts", "live", "transcrib", "veo")):
                    continue
                if model_id:
                    available.add(model_id)
        page_token = str(payload.get("nextPageToken", "")).strip() if isinstance(payload, dict) else ""
        if not page_token:
            break
    return sorted(available, key=_gemini_candidate_sort)


async def _generate_gemini(
    client: httpx.AsyncClient, prompt: str, api_key: str, video_language: str, aspect_ratio: str
) -> dict[str, Any]:
    models = await _discover_gemini_models(client, api_key)
    if not models:
        raise RuntimeError(
            "لم تُرجع Gemini أي نموذج generateContent صالحاً لهذا المفتاح. تحقق من المفتاح والاتصال، ثم راجع قائمة النماذج في Google AI Studio."
        )
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
                    "generationConfig": {"temperature": 0.75, "maxOutputTokens": 9000},
                },
            )
        except httpx.HTTPError as exc:
            raise RuntimeError("تعذر الاتصال بخدمة Gemini. تحقق من الإنترنت وحاول مجدداً.") from exc
        if response.status_code >= 400:
            last_status = response.status_code
            if response.status_code in (401, 403):
                raise RuntimeError(f"رفضت Gemini المفتاح أو صلاحية النموذج (HTTP {response.status_code}).")
            try:
                api_error = response.json().get("error", {})
                error_text = " ".join((str(api_error.get("status", "")), str(api_error.get("message", "")))).lower()
            except (ValueError, AttributeError, TypeError):
                error_text = ""
            model_not_found = "not_found" in error_text or "model not found" in error_text or "unknown model" in error_text
            if response.status_code in (404, 429, 500, 502, 503) or (response.status_code == 400 and model_not_found):
                continue
            raise RuntimeError(f"رفضت Gemini صيغة الطلب أو صلاحية الوصول (HTTP {response.status_code}). راجع المفتاح وقيود المشروع.")
        try:
            payload = response.json()
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError, ValueError):
            last_status = response.status_code
            continue
        try:
            result = _parse_json(text, video_language, aspect_ratio)
        except (ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("أعاد Gemini نصاً لا يطابق بنية المشاهد القصيرة. أعد المحاولة أو استخدم وضعاً آخر.") from exc
        result["model"] = model
        return result
    models_text = ", ".join(attempted[:5])
    suffix = f" (آخر HTTP {last_status})" if last_status else ""
    raise RuntimeError(
        f"لم ينجح أي نموذج Gemini نشط في قائمة حسابك: {models_text}{suffix}. تحقق من المفتاح أو الحصة، أو اختر Groq/Pollinations/الوضع التجريبي."
    )


def _catalog_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "models", "items"):
            items = payload.get(key)
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
    return []


def _catalog_price(item: dict[str, Any], key: str) -> float:
    pricing = item.get("pricing")
    if not isinstance(pricing, dict):
        return 1e9
    try:
        return max(0.0, float(pricing.get(key, 1e9)))
    except (TypeError, ValueError):
        return 1e9


async def _pollinations_models(
    client: httpx.AsyncClient, category: str, endpoint: str
) -> list[tuple[str, dict[str, Any]]]:
    try:
        response = await client.get(f"{POLLINATIONS_BASE}/{category}/models")
        response.raise_for_status()
        items = _catalog_list(response.json())
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise PollinationsError("تعذر قراءة كتالوج Pollinations الحالي؛ حاول مجدداً لاحقاً.") from exc

    price_field = "completionTextTokens" if category == "text" else "completionImageTokens"
    eligible: list[tuple[str, dict[str, Any]]] = []
    for item in items:
        model_id = str(item.get("name") or item.get("id") or "").strip()
        endpoints = item.get("supported_endpoints", [])
        outputs = item.get("output_modalities", [])
        health = item.get("health", {})
        if not model_id or endpoint not in endpoints:
            continue
        if category == "text" and "text" not in outputs:
            continue
        if category == "image" and "image" not in outputs:
            continue
        if item.get("paid_only") is True or item.get("community") is True:
            continue
        if isinstance(health, dict) and health.get("status") == "unhealthy":
            continue
        eligible.append((model_id, item))
    eligible.sort(key=lambda pair: (
        _catalog_price(pair[1], price_field),
        -(float(pair[1].get("health", {}).get("success_rate", 0) or 0) if isinstance(pair[1].get("health"), dict) else 0),
        pair[0],
    ))
    return eligible


async def _generate_pollinations(
    client: httpx.AsyncClient, prompt: str, api_key: str, video_language: str, aspect_ratio: str
) -> dict[str, Any]:
    if not api_key.strip():
        raise ValueError("أدخل مفتاح Pollinations؛ خدمة توليد النص الحالية تتطلب مفتاحاً ورصيد Pollen.")
    models = await _pollinations_models(client, "text", "/v1/chat/completions")
    if not models:
        raise PollinationsError("لا توجد نماذج نص Pollinations متاحة في الكتالوج الحالي.")
    errors: list[int] = []
    for model_id, item in models[:4]:
        supported = set(item.get("supported_parameters", []))
        body: dict[str, Any] = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": "Return only valid JSON. Follow every language and scene-length instruction exactly."},
                {"role": "user", "content": prompt},
            ],
        }
        if "temperature" in supported:
            body["temperature"] = 0.75
        if "max_tokens" in supported:
            body["max_tokens"] = 9000
        elif "max_completion_tokens" in supported:
            body["max_completion_tokens"] = 9000
        if "response_format" in supported:
            body["response_format"] = {"type": "json_object"}
        try:
            response = await client.post(
                f"{POLLINATIONS_BASE}/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key.strip()}"},
                json=body,
            )
        except httpx.HTTPError as exc:
            raise PollinationsError("تعذر الاتصال بخدمة Pollinations.") from exc
        if response.status_code >= 400:
            errors.append(response.status_code)
            if response.status_code == 401:
                raise PollinationsError("رفض Pollinations المفتاح؛ تحقق من API Key.", 401)
            if response.status_code == 402:
                raise PollinationsError("رصيد Pollen غير كافٍ أو تجاوز المفتاح حد الإنفاق.", 402)
            if response.status_code == 403:
                raise PollinationsError("المفتاح لا يملك صلاحية للنموذج المختار.", 403)
            continue
        try:
            payload = response.json()
            text = payload["choices"][0]["message"]["content"]
            if isinstance(text, list):
                text = "".join(str(part.get("text", "")) for part in text if isinstance(part, dict))
            result = _parse_json(str(text), video_language, aspect_ratio)
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PollinationsError("لم تُرجع Pollinations سيناريو JSON صالحاً.") from exc
        result["model"] = model_id
        result["estimated_text_price_pollen_per_token"] = _catalog_price(item, "completionTextTokens")
        return result
    last_status = errors[-1] if errors else None
    raise PollinationsError(
        f"لم ينجح أي نموذج نص Pollinations متاح (HTTP {last_status or 'غير معروف'}). تحقق من المفتاح والرصيد وصلاحيات النماذج.",
        last_status,
    )


async def generate_script(
    topic: str,
    provider: str,
    keys: ApiKeys,
    video_language: str = "en",
    aspect_ratio: str = "16:9",
) -> dict[str, Any]:
    if provider == "demo":
        return demo_script(topic, video_language)
    prompt = _prompt(topic, video_language, aspect_ratio)
    timeout = httpx.Timeout(100.0, connect=20.0)
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
                        "temperature": 0.75,
                        "max_tokens": 9000,
                        "response_format": {"type": "json_object"},
                        "messages": [
                            {"role": "system", "content": "Write accurate, engaging 12-scene documentary scripts in the requested language. Keep each narration micro-scene short and every visual keyword and image prompt in English."},
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
            result = _parse_json(text, video_language, aspect_ratio)
            result["model"] = GROQ_MODEL
            return result

        if provider == "gemini":
            if not keys.gemini.strip():
                raise ValueError("أدخل مفتاح Gemini في الإعدادات أو اختر الوضع التجريبي.")
            return await _generate_gemini(client, prompt, keys.gemini.strip(), video_language, aspect_ratio)

        if provider == "pollinations":
            return await _generate_pollinations(client, prompt, keys.pollinations, video_language, aspect_ratio)

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


async def fetch_pixabay_image(query: str, key: str, orientation: str, destination: Path) -> bool:
    """Fetch an inexpensive Pixabay still as a moving Ken-Burns fallback."""
    if not key.strip():
        return False
    portrait = orientation == "portrait"
    params = {
        "key": key.strip(),
        "q": " ".join(query.split())[:100] or "cinematic documentary",
        "image_type": "photo",
        "orientation": "vertical" if portrait else "horizontal",
        "safesearch": "true",
        "per_page": 20,
        "order": "popular",
        "min_width": 720 if portrait else 1280,
        "min_height": 1280 if portrait else 720,
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(55.0, connect=15.0), follow_redirects=True) as client:
        try:
            response = await client.get("https://pixabay.com/api/", params=params)
        except httpx.HTTPError as exc:
            raise RuntimeError("تعذر الاتصال بـPixabay للبحث عن صورة بديلة.") from exc
        if response.status_code in (401, 403):
            raise RuntimeError("رفض Pixabay المفتاح. راجع مفتاح API في الإعدادات.")
        if response.status_code >= 400:
            raise RuntimeError(f"رفض Pixabay بحث الصور (HTTP {response.status_code}).")
        try:
            hits = response.json().get("hits", [])
        except (ValueError, AttributeError, TypeError) as exc:
            raise RuntimeError("تعذر قراءة استجابة صور Pixabay.") from exc
        if not isinstance(hits, list) or not hits:
            return False
        selected_url = ""
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            image_url = str(hit.get("largeImageURL") or hit.get("webformatURL") or "")
            width = int(hit.get("imageWidth", 0) or 0)
            height = int(hit.get("imageHeight", 0) or 0)
            if not image_url.startswith("https://") or width <= 0 or height <= 0:
                continue
            if portrait and height <= width:
                continue
            if not portrait and width < height:
                continue
            selected_url = image_url
            break
        if not selected_url:
            return False
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            image_response = await client.get(selected_url)
            image_response.raise_for_status()
            if len(image_response.content) > 24 * 1024 * 1024:
                return False
            with Image.open(io.BytesIO(image_response.content)) as image:
                size = (720, 1280) if portrait else (1280, 720)
                image = ImageOps.fit(image.convert("RGB"), size, method=Image.Resampling.LANCZOS)
                image.save(destination, format="JPEG", quality=91, optimize=True)
        except httpx.HTTPError as exc:
            destination.unlink(missing_ok=True)
            raise RuntimeError("تعذر تنزيل صورة Pixabay البديلة.") from exc
        except Exception as exc:
            destination.unlink(missing_ok=True)
            raise RuntimeError("تعذر فك صورة Pixabay المستلمة.") from exc
    return destination.is_file() and destination.stat().st_size > 20_000


async def select_pollinations_image_model() -> tuple[str, dict[str, Any]]:
    timeout = httpx.Timeout(35.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        models = await _pollinations_models(client, "image", "/image/{prompt}")
    if not models:
        raise PollinationsError("لا يوجد نموذج صورة Pollinations متاح حالياً.")
    return models[0]


async def fetch_pollinations_image(
    prompt: str,
    key: str,
    ratio: str,
    destination: Path,
    seed: int,
    model_choice: tuple[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Generate one opt-in Pollinations image; requires a secret key and may spend Pollen."""
    if not key.strip():
        raise PollinationsError("توليد الصور عبر Pollinations يحتاج مفتاح API.")
    timeout = httpx.Timeout(180.0, connect=20.0)
    if model_choice is None:
        model_choice = await select_pollinations_image_model()
    model_id, model_info = model_choice
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        width, height = (1280, 720) if ratio == "16:9" else (720, 1280)
        encoded_prompt = quote(_english_text(prompt, 1100) or _fallback_image_prompt("documentary scene", ratio), safe="")
        url = f"{POLLINATIONS_BASE}/image/{encoded_prompt}"
        try:
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {key.strip()}"},
                params={"model": model_id, "width": width, "height": height, "seed": seed, "safe": "true"},
            )
        except httpx.HTTPError as exc:
            raise PollinationsError("تعذر الاتصال بتوليد الصور في Pollinations.") from exc
        if response.status_code >= 400:
            messages = {
                401: "رفض Pollinations المفتاح؛ تحقق من API Key.",
                402: "رصيد Pollen غير كافٍ أو تجاوز المفتاح حد الإنفاق.",
                403: "المفتاح لا يملك صلاحية لنموذج الصور.",
                429: "وصل Pollinations إلى حد الطلبات؛ استخدم الخلفية المتحركة المحلية أو أعد المحاولة لاحقاً.",
            }
            raise PollinationsError(messages.get(response.status_code, f"فشل توليد صورة Pollinations (HTTP {response.status_code})."), response.status_code)
        if not response.headers.get("content-type", "").lower().startswith("image/"):
            raise PollinationsError("لم تُرجع Pollinations ملف صورة صالحاً.")
        if len(response.content) > 24 * 1024 * 1024:
            raise PollinationsError("تجاوزت صورة Pollinations الحد الآمن (24 MB).")
        try:
            with Image.open(io.BytesIO(response.content)) as image:
                image = ImageOps.fit(image.convert("RGB"), (width, height), method=Image.Resampling.LANCZOS)
                destination.parent.mkdir(parents=True, exist_ok=True)
                image.save(destination, format="JPEG", quality=92, optimize=True)
        except Exception as exc:
            destination.unlink(missing_ok=True)
            raise PollinationsError("تعذر فك صورة Pollinations المستلمة.") from exc
        return {
            "path": destination,
            "model": model_id,
            "estimated_pollen_per_image": _catalog_price(model_info, "completionImageTokens"),
        }
