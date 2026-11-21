from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from PIL import Image, ImageOps

from app.factual_profiles import fact_sources_for_topic, giza_demo_script, is_giza_pyramid_topic, reference_context
from app.models import ApiKeys
from app.visual_matching import search_tokens
from app.visual_sources import fetch_commons_image, fetch_pixabay_image, fetch_pixabay_video

GROQ_MODEL = "llama-3.3-70b-versatile"
GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
POLLINATIONS_BASE = "https://gen.pollinations.ai"


class PollinationsError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


_DEMO_EN = [
    ("One question begins this story.", "قد يفتح سؤال واحد باب الحكاية.", "cinematic question mystery documentary", "Photorealistic cinematic documentary, an intriguing unanswered question shown through a striking real-world visual, high-detail 8K-style texture, dramatic natural light, no text, wide composition."),
    ("The first answer seems obvious.", "تبدو الإجابة الأولى غالباً بديهية تماماً.", "obvious answer familiar everyday scene", "Photorealistic documentary close-up of a familiar everyday scene that seems ordinary at first glance, subtle cinematic tension, no text."),
    ("Then one detail changes everything.", "ثم تظهر تفصيلة صغيرة تقلب كل شيء.", "hidden detail macro discovery", "Extreme macro documentary shot revealing one unexpected detail, shallow depth of field, cinematic lighting, no text."),
    ("Follow the clue; the picture shifts.", "نتتبعها، فتبدأ الصورة المألوفة بالتغيّر.", "clue trail shifting perspective", "Cinematic documentary camera tracking toward a revealing clue, perspective shifting from shadow into light, no text."),
    ("Unexpected patterns begin to appear.", "وتظهر أنماط لم يتوقعها أحد.", "patterns nature science aerial", "Photorealistic aerial documentary view of surprising patterns emerging across a vast landscape, high detail, no text."),
    ("Each clue opens another mystery.", "كل دليل يفتح باباً لغزٍ أعمق.", "deep mystery archive evidence", "Moody documentary shot of carefully preserved evidence and an unfolding mystery, realistic textures, no text."),
    ("One piece still refuses to fit.", "لكن قطعة واحدة ما زالت خارج الصورة.", "missing piece contradiction dramatic", "A cinematic documentary visual suggesting a missing piece and a contradiction, isolated object under a beam of light, no text."),
    ("Researchers test the account against evidence.", "يفحص الباحثون الأدلة ويقارنونها بالرواية.", "researcher laboratory evidence testing", "Researchers examining real evidence in a modern laboratory, documentary handheld camera feel, natural skin and materials, no text."),
    ("A discovery rewrites the timeline.", "ثم يقلب اكتشاف جديد ترتيب الأحداث.", "new discovery history timeline", "Cinematic reconstruction of a newly uncovered historical artifact changing a timeline, photorealistic, no text."),
    ("Simple clues connect distant worlds.", "ما بدا بسيطاً بات يربط عوالم بعيدة.", "connected distant worlds earth night", "Epic documentary shot connecting distant places across Earth at night, lights and atmosphere, wide cinematic frame, no text."),
    ("Truth hides in overlooked details.", "كانت الحقيقة مختبئة في تفاصيل تم تجاهلها.", "overlooked detail reveal close up", "Slow cinematic documentary reveal of overlooked details in an object or landscape, subtle suspense, no text."),
    ("But the next question changes everything.", "أما السؤال التالي، فربما يكون أكثر إدهاشاً.", "surprising question horizon documentary", "Hopeful yet suspenseful wide documentary ending at a dramatic horizon, opening a new question, photorealistic, no text."),
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


def demo_script(topic: str, video_language: str = "en", scene_count: int = 15) -> dict[str, Any]:
    if scene_count > 16:
        raise ValueError("وضع التجربة يدعم دقيقة واحدة فقط؛ اختر مزود نص لمدد الفيديو الأطول.")
    if is_giza_pyramid_topic(topic):
        return giza_demo_script(topic, video_language, scene_count)
    raise ValueError("وضع التجربة الموثق متاح حالياً لأهرامات الجيزة فقط؛ لبقية الموضوعات اختر Gemini أو Groq.")
    scenes: list[dict[str, str]] = []
    if video_language == "ar":
        rows = list(_DEMO_AR[:scene_count])
        extras = [
            ("ثم نختبر هذه الرواية على ضوء الأدلة.", "documentary evidence comparison archive records", "Documentary close-up of records compared side by side under a desk lamp, no readable text."),
            ("وتكشف المقارنة ما غاب عن النظرة الأولى.", "researcher comparing historical evidence timeline", "Researcher comparing historical evidence on a timeline, cinematic documentary detail, no legible labels."),
            ("لكن السؤال الأهم يبقى مفتوحاً.", "open question horizon documentary ending", "Reflective cinematic documentary ending with a real-world horizon and an unresolved question, no text."),
            ("ولا بد من اختبار الدليل الأخير.", "final evidence test documentary archive", "Final documentary close-up of evidence being checked against an archival timeline, cinematic light, no readable labels."),
        ]
        rows.extend(extras[: max(0, scene_count - len(rows))])
        for narration_template, keywords, image_prompt in rows:
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
            "model": f"demo-{scene_count}-scenes",
        }

    rows_en = list(_DEMO_EN[:scene_count])
    extras_en = [
        ("Now we test the account.", "ثم نختبر هذه الرواية على ضوء الأدلة.", "documentary evidence comparison archive records", "Documentary close-up of records compared side by side under a desk lamp, no readable text."),
        ("Evidence reveals what we missed.", "وتكشف المقارنة ما غاب عن النظرة الأولى.", "researcher comparing historical evidence timeline", "Researcher comparing historical evidence on a timeline, cinematic documentary detail, no legible labels."),
        ("The key question remains open.", "لكن السؤال الأهم يبقى مفتوحاً.", "open question horizon documentary ending", "Reflective cinematic documentary ending with a real-world horizon and an unresolved question, no text."),
        ("The final clue needs testing.", "ولا بد من اختبار الدليل الأخير.", "final evidence test documentary archive", "Final documentary close-up of evidence being checked against an archival timeline, cinematic light, no readable labels."),
    ]
    rows_en.extend(extras_en[: max(0, scene_count - len(rows_en))])
    for narration_template, translation_template, keywords, image_prompt in rows_en:
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
        "model": f"demo-{scene_count}-scenes",
    }


def _parse_json(text: str, video_language: str = "en", ratio: str = "16:9", scene_count: int = 12) -> dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    first, last = text.find("{"), text.rfind("}")
    if first < 0 or last <= first:
        raise ValueError("لم تُرجع خدمة النص بنية JSON صالحة.")
    payload = json.loads(text[first : last + 1])
    raw_scenes = payload.get("scenes")
    if not isinstance(raw_scenes, list) or not raw_scenes:
        raise ValueError("استجابة خدمة النص لا تحتوي مشاهد.")
    first_scene = raw_scenes[0] if isinstance(raw_scenes[0], dict) else {}
    visual_anchor = _english_text(payload.get("visual_anchor") or first_scene.get("visual_anchor"), 100)
    anchor_tokens = search_tokens(visual_anchor)
    if not anchor_tokens:
        raise ValueError("لم تُرجع خدمة النص مرساة بصرية إنجليزية محددة لموضوع الفيلم.")
    normalized: list[dict[str, str]] = []
    for scene in raw_scenes[:scene_count]:
        if not isinstance(scene, dict):
            continue
        narration = str(scene.get("narration") or scene.get("narration_en") or "").strip()
        translation = str(scene.get("translation_ar", "")).strip()
        if video_language == "ar":
            translation = narration
        keywords = _english_text(scene.get("visual_keywords"), 180)
        if not keywords:
            raise ValueError("أحد المشاهد لا يحتوي استعلام بحث بصرياً إنجليزياً محدداً.")
        if not anchor_tokens.issubset(search_tokens(keywords)):
            keywords = f"{visual_anchor} {keywords}"[:180]
        if not (search_tokens(keywords) - anchor_tokens):
            raise ValueError("يجب أن يضيف استعلام المشهد فعلاً أو تفصيلاً بصرياً إلى مرساة الموضوع.")
        image_prompt = _english_text(scene.get("image_prompt"), 900)
        if not image_prompt:
            raise ValueError("أحد المشاهد لا يحتوي وصفاً بصرياً إنجليزياً محدداً.")
        if not anchor_tokens.issubset(search_tokens(image_prompt)):
            image_prompt = f"{visual_anchor}. {image_prompt}"[:900]
        if narration and (translation or video_language == "ar"):
            normalized.append({
                "narration": narration,
                "translation_ar": translation,
                "visual_anchor": visual_anchor,
                "visual_keywords": keywords,
                "image_prompt": image_prompt,
            })
    if not normalized:
        raise ValueError("تعذر العثور على مشاهد صالحة في استجابة الخدمة.")
    if len(normalized) != scene_count:
        raise ValueError(f"أعاد مزود النص {len(normalized)} مشهداً، بينما تتطلب المدة {scene_count} مشهداً. أعد المحاولة.")
    default_title = "فيلم وثائقي قصير" if video_language == "ar" else "Documentary short"
    return {
        "title": str(payload.get("title") or default_title).strip()[:160],
        "hook": str(payload.get("hook") or "").strip()[:400],
        "visual_anchor": visual_anchor,
        "scenes": normalized,
        "demo": False,
    }


def _prompt(topic: str, video_language: str = "en", ratio: str = "16:9", scene_count: int = 15) -> str:
    frame = "9:16 vertical" if ratio == "9:16" else "16:9 horizontal"
    target_seconds = round(scene_count * 3.78 + 0.22)
    if video_language == "ar":
        return f"""اكتب سيناريو وثائقياً سريعاً وجذاباً عن: {topic!r}، بالعربية الفصحى السليمة والطبيعية، لفيديو {frame} مدته قرابة {target_seconds} ثانية.
ابدأ بخطاف قوي في أول مشهدين، ثم قدّم سياقاً وأدلة وتحولاً وكشفاً وخاتمة. اجعل كل جملة دقيقة نحوياً وموجزة وسهلة النطق، وتجنب الترجمة الحرفية. ميّز الوقائع الموثقة عن الفرضيات: استخدم صياغة مثل «تشير التقارير التاريخية إلى…» للوقائع المنقولة، و«تطرح بعض الفرضيات…» للنظريات؛ لا تخترع أرقاماً أو مصادر أو أحداثاً، ولا تعرض فرضية على أنها حقيقة.
أعد بالضبط {scene_count} مشهداً. اجعل narration لكل مشهد 4–7 كلمات عربية منطوقة، مناسبة لـ3–4 ثوانٍ بسرعة طبيعية، وبفكرة واحدة. قسّم الحجة مشهداً مشهداً ولا تكرر المعلومة أو الصورة. في visual_keywords اكتب استعلاماً تصويرياً إنجليزياً محدداً، عادة 5–9 كلمات، يصف الشيء والفعل والبيئة في هذا المشهد (مثال: cargo ship storm Atlantic night؛ vintage airplane flying clouds archival؛ compass navigation map closeup؛ underwater methane bubbles seabed). ممنوع الكلمات العامة المفردة مثل fog أو ocean أو mystery. اجعل استعلام كل مشهد مختلفاً عن كل الاستعلامات السابقة. الصورة يجب أن تعرض ما يقوله narration فعلياً لا مجرد جو عام. أضف image_prompt إنجليزياً واقعياً مطابقاً للسرد.
اجعل translation_ar مطابقة للسرد العربي حرفياً. أعد JSON فقط بالشكل: {{"title":"عنوان عربي موجز","hook":"خطاف عربي","scenes":[{{"narration":"جملة عربية قصيرة دقيقة","translation_ar":"نفس الجملة","visual_keywords":"specific English search query","image_prompt":"context-specific English documentary image prompt"}}]}}. لا تضف Markdown."""
    return f"""Create a factual, fast-paced, highly engaging documentary script about: {topic!r}, for {frame}, about {target_seconds} seconds long.
Open with a gripping two-scene hook, then build context, evidence, a turn, reveal and payoff. Do not fabricate statistics, sources, quotes, incidents or certainty; clearly distinguish documented facts from hypotheses. Keep every English sentence grammatically natural and fact-aware.
Return exactly {scene_count} scenes. Each spoken narration must contain 4–7 short English words and fit a 3–4 second voiceover at natural pace. Every scene needs a distinct factual beat and a different visual. For each scene provide a faithful fluent Arabic translation and a specific 5–9 word ENGLISH Pixabay query naming the subject, action and setting in the narration (e.g. cargo ship storm Atlantic night; vintage airplane flying clouds archival; compass navigation map closeup; underwater methane bubbles seabed). Never use vague one-word queries like fog, ocean, mystery. No repeated query, clip idea, or generic B-roll unrelated to the sentence. Also provide a separate, concrete English image prompt showing the actual described action.
Return ONLY valid JSON: {{"title":"short title","hook":"hook","scenes":[{{"narration":"4-7 spoken English words","translation_ar":"accurate Arabic translation","visual_keywords":"specific contextual English search query","image_prompt":"specific documentary visual for this narration"}}]}}. No markdown."""


def _strict_prompt(topic: str, video_language: str, ratio: str, scene_count: int) -> str:
    """Prompt for one coherent, factual story with explicit scene-to-visual anchors."""
    frame = "vertical 9:16" if ratio == "9:16" else "horizontal 16:9"
    target_seconds = round(scene_count * 3.78 + 0.22)
    context = reference_context(topic, video_language)
    if video_language == "ar":
        grounding = f"\n\nحقائق مرجعية مقيدة استخدمها لهذا الموضوع فقط:\n{context}" if context else "\n\nاستخدم الحقائق عالية الثقة فقط. احذف التفاصيل المختلف عليها أو صغها كاحتمال، ولا تخترع رقماً أو اقتباساً أو مرجعاً."
        return f"""اكتب فيلماً وثائقياً عربياً فصيحاً وطبيعياً عن {topic!r}، مدته نحو {target_seconds} ثانية وبصيغة {frame}.
اكتب قصة متماسكة لا قائمة حقائق: افتتح بسؤال بصري قوي، ثم عرّف الموضوع، وقدم أدلة ومشهداً إنسانياً، وميّز ما لم يُحسم، واختم باستنتاج واضح. استخدم أفعالاً وصوراً ملموسة، وتجنب العبارات الإنشائية والتهويل. لا تختلق إحصاءات أو اقتباسات أو مصادر. ميّز الوقائع عن الفرضيات بعبارات صريحة؛ الفرضية ليست مشهداً حقيقياً.
أعد بالضبط {scene_count} مشهداً. اجعل كل narration من 4 إلى 7 كلمات عربية منطوقة، بفكرة واحدة تلائم 3–4 ثوانٍ، وتأكد أن الجملة سليمة وطبيعية عند قراءتها بصوت مرتفع. الترجمة العربية في translation_ar يجب أن تطابق narration حرفياً.
أعد visual_anchor واحداً ثابتاً من كلمتين إلى أربع كلمات باللغة الإنجليزية يحدد الموضوع الدقيق؛ لا تضع فيه المزاج أو نوع الصورة. يجب أن يبدأ كل visual_keywords بهذه المرساة نفسها، ثم يضيف 3–6 كلمات تصف الشيء أو الفعل أو المكان المذكور فعلاً في الجملة. اكتب وصفاً مختلفاً لكل مشهد. لا تستخدم كلمات عامة مثل fog أو ocean أو mystery أو Egypt وحدها، ولا تستخدم لقطة طبيعة أو مدينة لا صلة لها بالنص. لا تدّع أن تطابق الوسوم يثبت ما تعرضه بكسلات الصورة.
لكل فرضية، لا تعرض رسماً أو إعادة تمثيل إلا إذا وُسمت بوضوح كتصور افتراضي. اجعل image_prompt إنجليزياً محدداً، يذكر المرساة والموضوع والفعل، ولا يضيف عناصر خيالية أو نصاً مقروءاً.
أخرج JSON صالحاً فقط بالشكل التالي، مع استبدال نصوص المثال بقيم حقيقية لكل موضوع: {{"title":"عنوان محدد","hook":"خطاف","visual_anchor":"stable English subject anchor","scenes":[{{"narration":"جملة عربية من 4–7 كلمات","translation_ar":"الجملة نفسها","visual_keywords":"same anchor plus concrete scene terms","image_prompt":"specific, factual scene matching the narration"}}]}}.{grounding}"""
    grounding = f"\n\nBounded source context for this topic; use only these facts:\n{context}" if context else "\n\nUse high-confidence facts only. Qualify disputed details or omit them; never invent statistics, quotations, incidents or sources."
    return f"""Write a fluent, factual, engaging documentary short about {topic!r}, about {target_seconds} seconds long in {frame}.
Tell one coherent story, not a list of trivia: open with a concrete visual question, establish the subject, build through evidence and one human/scientific detail, qualify what remains unknown, and end with a clear takeaway. Use vivid but restrained language. Do not invent statistics, quotations, incidents or sources. Clearly distinguish documented records from hypotheses.
Return exactly {scene_count} scenes. Each spoken narration must contain 4–7 natural English words and fit 3–4 seconds at normal pace. Each scene must add a new beat. Provide fluent accurate Arabic translation in translation_ar.
Return one stable visual_anchor of 2–4 English words naming the exact documentary subject, not a mood. Every visual_keywords query must begin with that exact anchor, followed by 3–6 concrete words describing the visible object, action or place actually named in the narration. Make every scene query distinct. Do not use vague terms such as fog, ocean, mystery or Egypt alone and never substitute unrelated nature/city B-roll. Metadata matching is a relevance filter, not proof of pixel-level content.
If narration describes a hypothesis, label any drawing or reconstruction as conceptual, not as evidence. Each English image_prompt must name the same anchor and the actual subject/action, and exclude fantasy details or readable fake text.
Return ONLY valid JSON in this shape, replacing examples with topic-specific values: {{"title":"specific title","hook":"hook","visual_anchor":"stable English subject anchor","scenes":[{{"narration":"4–7 spoken English words","translation_ar":"accurate Arabic translation","visual_keywords":"same anchor plus concrete scene terms","image_prompt":"specific factual visual matching the narration"}}]}}.{grounding}"""


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
    client: httpx.AsyncClient, prompt: str, api_key: str, video_language: str, aspect_ratio: str, scene_count: int
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
                    "generationConfig": {"temperature": 0.75, "maxOutputTokens": min(24000, max(4000, scene_count * 150))},
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
            result = _parse_json(text, video_language, aspect_ratio, scene_count)
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
    client: httpx.AsyncClient, prompt: str, api_key: str, video_language: str, aspect_ratio: str, scene_count: int
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
            body["max_tokens"] = min(24000, max(4000, scene_count * 150))
        elif "max_completion_tokens" in supported:
            body["max_completion_tokens"] = min(24000, max(4000, scene_count * 150))
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
            result = _parse_json(str(text), video_language, aspect_ratio, scene_count)
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
    scene_count: int = 15,
) -> dict[str, Any]:
    if provider == "demo":
        if scene_count > 16:
            raise ValueError("وضع التجربة مصمم لمقطع دقيقة واحدة فقط؛ اختر Gemini أو Groq أو Pollinations للمدد الأطول.")
        return demo_script(topic, video_language, scene_count)
    prompt = _strict_prompt(topic, video_language, aspect_ratio, scene_count)
    fact_sources = fact_sources_for_topic(topic)
    timeout = httpx.Timeout(300.0, connect=30.0)
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
                        "max_tokens": min(24000, max(4000, scene_count * 150)),
                        "response_format": {"type": "json_object"},
                        "messages": [
                            {"role": "system", "content": f"Write accurate, engaging documentary scripts with exactly {scene_count} short scenes in the requested language. Keep narration per scene short, and every visual keyword and image prompt in English."},
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
            result = _parse_json(text, video_language, aspect_ratio, scene_count)
            result["model"] = GROQ_MODEL
            result["fact_sources"] = fact_sources
            return result

        if provider == "gemini":
            if not keys.gemini.strip():
                raise ValueError("أدخل مفتاح Gemini في الإعدادات أو اختر الوضع التجريبي.")
            result = await _generate_gemini(client, prompt, keys.gemini.strip(), video_language, aspect_ratio, scene_count)
            result["fact_sources"] = fact_sources
            return result

        if provider == "pollinations":
            result = await _generate_pollinations(client, prompt, keys.pollinations, video_language, aspect_ratio, scene_count)
            result["fact_sources"] = fact_sources
            return result

    raise ValueError("مزود النص غير معروف.")



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
