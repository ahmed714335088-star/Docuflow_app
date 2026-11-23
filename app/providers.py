from __future__ import annotations

import base64
import binascii
import io
import json
import re
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageOps

from app.factual_profiles import fact_sources_for_topic, giza_demo_script, is_giza_pyramid_topic, reference_context
from app.models import ApiKeys
from app.visual_matching import search_tokens

GROQ_MODEL = "llama-3.3-70b-versatile"
GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
CLAUDE_MODELS_URL = "https://api.anthropic.com/v1/models"
CLAUDE_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_VERSION = "2023-06-01"


class GeminiImageError(RuntimeError):
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


def demo_script(topic: str, video_language: str = "en", scene_count: int = 15) -> dict[str, Any]:
    if scene_count > 20:
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


def _extract_json_payload(text: str) -> dict[str, Any]:
    """Extract the first decodable JSON object, tolerating fences and surrounding prose."""
    cleaned = str(text or "").lstrip("\ufeff").strip()
    cleaned = re.sub(r"(?is)```(?:json)?\s*(.*?)\s*```", r"\1", cleaned)
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", cleaned):
        try:
            payload, _end = decoder.raw_decode(cleaned[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("لم تُرجع خدمة النص كائن JSON صالحاً.")


def _parse_json(text: str, video_language: str = "en", ratio: str = "16:9", scene_count: int = 12) -> dict[str, Any]:
    payload = _extract_json_payload(text)
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
        visual_type = str(scene.get("visual_type") or "").strip().lower()
        if visual_type not in {"ai_image", "stock_video"}:
            raise ValueError("يجب أن يحدد الكاتب visual_type لكل مشهد: ai_image أو stock_video.")
        if narration and (translation or video_language == "ar"):
            normalized.append({
                "narration": narration,
                "translation_ar": translation,
                "visual_anchor": visual_anchor,
                "visual_keywords": keywords,
                "image_prompt": image_prompt,
                "visual_type": visual_type,
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


def _prompt(
    topic: str, video_language: str = "en", ratio: str = "16:9", scene_count: int = 15,
    requested_seconds: int | None = None,
) -> str:
    frame = "9:16 vertical" if ratio == "9:16" else "16:9 horizontal"
    target_seconds = requested_seconds if requested_seconds is not None else round(scene_count * 3.08 + 0.22)
    if video_language == "ar":
        return f"""اكتب سيناريو وثائقياً سريعاً وجذاباً عن: {topic!r}، بالعربية الفصحى السليمة والطبيعية، لفيديو {frame} مدته قرابة {target_seconds} ثانية.
ابدأ بخطاف قوي في أول مشهدين، ثم قدّم سياقاً وأدلة وتحولاً وكشفاً وخاتمة. اجعل كل جملة دقيقة نحوياً وموجزة وسهلة النطق، وتجنب الترجمة الحرفية. ميّز الوقائع الموثقة عن الفرضيات: استخدم صياغة مثل «تشير التقارير التاريخية إلى…» للوقائع المنقولة، و«تطرح بعض الفرضيات…» للنظريات؛ لا تخترع أرقاماً أو مصادر أو أحداثاً، ولا تعرض فرضية على أنها حقيقة.
أعد بالضبط {scene_count} مشهداً. اجعل narration موجزاً وطبيعياً وبفكرة واحدة، دون حد زمني ثابت؛ سيُمدد المشهد إلى مدة الصوت المقاسة مضافاً إليها وقفة 0.4 ثانية. لا تسرّع الكلام ولا تحذف الكلمات لإجباره على مدة مسبقة. قسّم الحجة مشهداً مشهداً ولا تكرر المعلومة أو الصورة. في visual_keywords اكتب استعلاماً تصويرياً إنجليزياً محدداً، عادة 5–9 كلمات، يصف الشيء والفعل والبيئة في هذا المشهد (مثال: cargo ship storm Atlantic night؛ vintage airplane flying clouds archival؛ compass navigation map closeup؛ underwater methane bubbles seabed). ممنوع الكلمات العامة المفردة مثل fog أو ocean أو mystery. اجعل استعلام كل مشهد مختلفاً عن كل الاستعلامات السابقة. الصورة يجب أن تعرض ما يقوله narration فعلياً لا مجرد جو عام. أضف image_prompt إنجليزياً واقعياً مطابقاً للسرد.
اختر visual_type بقيمة ai_image للأحداث التاريخية والألغاز والمفاهيم العلمية والفرضيات والسرد الدرامي؛ أو stock_video فقط لفعل واقعي ملموس تلائمه لقطة مخزون مطابقة. لا تختر stock_video للمزاج أو اللقطات العامة. اجعل translation_ar مطابقة للسرد العربي حرفياً. أعد JSON فقط بالشكل: {{"title":"عنوان عربي موجز","hook":"خطاف عربي","visual_anchor":"stable English subject anchor","scenes":[{{"narration":"جملة عربية قصيرة دقيقة","translation_ar":"نفس الجملة","visual_type":"ai_image","visual_keywords":"specific English search query","image_prompt":"context-specific English documentary image prompt"}}]}}. لا تضف Markdown."""
    return f"""Create a factual, fast-paced, highly engaging documentary script about: {topic!r}, for {frame}, about {target_seconds} seconds long.
Open with a gripping two-scene hook, then build context, evidence, a turn, reveal and payoff. Do not fabricate statistics, sources, quotes, incidents or certainty; clearly distinguish documented facts from hypotheses. Keep every English sentence grammatically natural and fact-aware.
Return exactly {scene_count} scenes. Keep narration concise and natural without a fixed speech-time cap; the scene expands to measured voice duration plus a 0.4 second silent gap. Never force narration into a preset time window. Every scene needs a distinct factual beat and a different visual. For each scene provide a faithful fluent Arabic translation and a specific 5–9 word ENGLISH Pixabay query naming the subject, action and setting in the narration (e.g. cargo ship storm Atlantic night; vintage airplane flying clouds archival; compass navigation map closeup; underwater methane bubbles seabed). Never use vague one-word queries like fog, ocean, mystery. No repeated query, clip idea, or generic B-roll unrelated to the sentence. Also provide a separate, concrete English image prompt showing the actual described action.
Set visual_type to ai_image for historical events, mysteries, scientific concepts, hypotheses and dramatic storytelling; use stock_video only for concrete observable actions that an exact stock clip can show, never for generic B-roll or mood. Return ONLY valid JSON: {{"title":"short title","hook":"hook","visual_anchor":"stable English subject anchor","scenes":[{{"narration":"4-7 spoken English words","translation_ar":"accurate Arabic translation","visual_type":"ai_image","visual_keywords":"specific contextual English search query","image_prompt":"specific documentary visual for this narration"}}]}}. No markdown."""


def _strict_prompt(
    topic: str, video_language: str, ratio: str, scene_count: int, requested_seconds: int | None = None,
) -> str:
    """Prompt for one coherent, factual story with explicit scene-to-visual anchors."""
    frame = "vertical 9:16" if ratio == "9:16" else "horizontal 16:9"
    target_seconds = requested_seconds if requested_seconds is not None else round(scene_count * 3.08 + 0.22)
    context = reference_context(topic, video_language)
    if video_language == "ar":
        grounding = f"\n\nحقائق مرجعية مقيدة استخدمها لهذا الموضوع فقط:\n{context}" if context else "\n\nاستخدم الحقائق عالية الثقة فقط. احذف التفاصيل المختلف عليها أو صغها كاحتمال، ولا تخترع رقماً أو اقتباساً أو مرجعاً."
        return f"""اكتب فيلماً وثائقياً عربياً فصيحاً وطبيعياً عن {topic!r}، مدته نحو {target_seconds} ثانية وبصيغة {frame}.
اكتب قصة متماسكة لا قائمة حقائق: افتتح بسؤال بصري قوي، ثم عرّف الموضوع، وقدم أدلة ومشهداً إنسانياً، وميّز ما لم يُحسم، واختم باستنتاج واضح. استخدم أفعالاً وصوراً ملموسة، وتجنب العبارات الإنشائية والتهويل. لا تختلق إحصاءات أو اقتباسات أو مصادر. ميّز الوقائع عن الفرضيات بعبارات صريحة؛ الفرضية ليست مشهداً حقيقياً.
أعد بالضبط {scene_count} مشهداً. اجعل narration موجزاً وطبيعياً، لكن لا تحشره في حد زمني ثابت: سيُمدد المشهد تلقائياً إلى مدة الصوت الفعلية مع وقفة 0.4 ثانية. لا تسرّع الجمل ولا تحذف كلمات لإجبارها على مدة مسبقة. الترجمة العربية في translation_ar يجب أن تطابق narration حرفياً.
أعد visual_anchor واحداً ثابتاً من كلمتين إلى أربع كلمات باللغة الإنجليزية يحدد الموضوع الدقيق؛ لا تضع فيه المزاج أو نوع الصورة. يجب أن يبدأ كل visual_keywords بهذه المرساة نفسها، ثم يضيف 3–6 كلمات تصف الشيء أو الفعل أو المكان المذكور فعلاً في الجملة. اكتب وصفاً مختلفاً لكل مشهد. لا تستخدم كلمات عامة مثل fog أو ocean أو mystery أو Egypt وحدها، ولا تستخدم لقطة طبيعة أو مدينة لا صلة لها بالنص. لا تدّع أن تطابق الوسوم يثبت ما تعرضه بكسلات الصورة.
لكل مشهد اختر visual_type واحداً: ai_image للأحداث التاريخية والألغاز والمفاهيم العلمية والفرضيات والسرد الدرامي؛ أو stock_video فقط لفعل واقعي ملموس يمكن لمقطع مخزون مطابق عرضه فعلاً. لا تختر stock_video لمجرد الجو العام. إذا اخترت stock_video فاجعل استعلامه قابلاً للتحقق بوسوم دقيقة. وسم إعادة بناء الفرضية بأنها تصور لا دليل. image_prompt محدد ويطابق المرساة والسرد دون خيال أو كتابة مقروءة.
أخرج JSON صالحاً فقط بالشكل التالي: {{"title":"عنوان محدد","hook":"خطاف","visual_anchor":"stable English subject anchor","scenes":[{{"narration":"جملة عربية قصيرة","translation_ar":"الجملة نفسها","visual_type":"ai_image","visual_keywords":"same anchor plus concrete scene terms","image_prompt":"specific scene matching narration"}}]}}.{grounding}"""
    grounding = f"\n\nBounded source context for this topic; use only these facts:\n{context}" if context else "\n\nUse high-confidence facts only. Qualify disputed details or omit them; never invent statistics, quotations, incidents or sources."
    return f"""Write a fluent, factual, engaging documentary short about {topic!r}, about {target_seconds} seconds long in {frame}.
Tell one coherent story, not a list of trivia: open with a concrete visual question, establish the subject, build through evidence and one human/scientific detail, qualify what remains unknown, and end with a clear takeaway. Use vivid but restrained language. Do not invent statistics, quotations, incidents or sources. Clearly distinguish documented records from hypotheses.
Return exactly {scene_count} scenes. Keep narration concise and natural, but do not target a fixed speech duration: the scene will be extended to the measured voiceover duration plus a 0.4 second pause. Never omit words or force speech into a preset time window. Each scene must add a new beat. Provide fluent accurate Arabic translation in translation_ar.
Return one stable visual_anchor of 2–4 English words naming the exact documentary subject, not a mood. Every visual_keywords query must begin with that exact anchor, followed by 3–6 concrete words describing the visible object, action or place actually named in the narration. Make every scene query distinct. Do not use vague terms such as fog, ocean, mystery or Egypt alone and never substitute unrelated nature/city B-roll. Metadata matching is a relevance filter, not proof of pixel-level content.
For every scene set visual_type to exactly ai_image or stock_video. Use ai_image for historical events, mysteries, scientific concepts, hypotheses and dramatic storytelling. Use stock_video only for concrete observable real-world actions that matching footage can show, never for mood or generic B-roll. Pixabay tags will be checked and unrelated clips rejected. Label hypothetical reconstructions as conceptual, not evidence. image_prompt must match the subject and action and exclude fantasy or readable fake text.
Return ONLY valid JSON in this shape: {{"title":"specific title","hook":"hook","visual_anchor":"stable English subject anchor","scenes":[{{"narration":"4–7 spoken English words","translation_ar":"accurate Arabic translation","visual_type":"ai_image","visual_keywords":"same anchor plus concrete scene terms","image_prompt":"specific factual scene matching narration"}}]}}.{grounding}"""


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
    last_parse_error = ""
    total_attempts = 0
    for model in models:
        if total_attempts >= 8:
            break
        attempted.append(model)
        for repair_attempt in range(2):
            if total_attempts >= 8:
                break
            total_attempts += 1
            request_prompt = prompt
            if repair_attempt:
                request_prompt += (
                    "\n\nJSON RETRY: Regenerate the complete response. Return exactly one valid JSON object, "
                    f"with exactly {scene_count} valid scenes and every required field. Do not add markdown or prose."
                )
            try:
                response = await client.post(
                    f"{GEMINI_MODELS_URL}/{model}:generateContent",
                    params={"key": api_key},
                    json={
                        "contents": [{"parts": [{"text": request_prompt}]}],
                        "generationConfig": {
                            "temperature": 0.75,
                            "maxOutputTokens": min(24000, max(4000, scene_count * 150)),
                            "responseMimeType": "application/json",
                        },
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
                    break
                raise RuntimeError(f"رفضت Gemini صيغة الطلب أو صلاحية الوصول (HTTP {response.status_code}). راجع المفتاح وقيود المشروع.")
            try:
                payload = response.json()
                candidates = payload.get("candidates", []) if isinstance(payload, dict) else []
                parts = candidates[0].get("content", {}).get("parts", []) if candidates and isinstance(candidates[0], dict) else []
                text = "\n".join(str(part.get("text", "")) for part in parts if isinstance(part, dict) and part.get("text"))
                result = _parse_json(text, video_language, aspect_ratio, scene_count)
            except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
                last_parse_error = str(exc) or "استجابة فارغة أو غير مكتملة"
                if repair_attempt == 0:
                    continue
                break
            result["model"] = model
            return result
    models_text = ", ".join(attempted[:5])
    suffix = f" (آخر HTTP {last_status})" if last_status else ""
    if last_parse_error:
        raise RuntimeError(
            f"لم تُحلّل استجابة Gemini إلى JSON صالح بعد إعادة المحاولة وتجربة النماذج المتاحة. آخر خطأ: {last_parse_error}."
        )
    raise RuntimeError(
        f"لم ينجح أي نموذج Gemini نشط في قائمة حسابك: {models_text}{suffix}. تحقق من المفتاح أو الحصة، أو اختر Groq/Claude/الوضع التجريبي."
    )



def _gemini_image_model_sort(model_id: str) -> tuple[int, tuple[int, ...], str]:
    lower = model_id.lower()
    priority = 0 if "gemini-3.1-flash-image" in lower else 1 if "gemini-2.5-flash-image" in lower else 2 if "nano-banana" in lower else 3
    numbers = re.search(r"gemini-(\d+(?:\.\d+)*)", lower)
    version = tuple(int(piece) for piece in numbers.group(1).split(".")) if numbers else (0,)
    return priority, tuple(-piece for piece in version), lower


def _gemini_image_ids(payload: Any) -> list[str]:
    found: set[str] = set()
    models = payload.get("models", []) if isinstance(payload, dict) else []
    for item in models if isinstance(models, list) else []:
        if not isinstance(item, dict):
            continue
        methods = item.get("supportedGenerationMethods", [])
        if not isinstance(methods, list) or "generateContent" not in methods:
            continue
        for raw in (item.get("baseModelId", ""), str(item.get("name", "")).removeprefix("models/")):
            model_id = str(raw).strip()
            lower = model_id.lower()
            if model_id.startswith("gemini-") and ("image" in lower or "nano-banana" in lower):
                found.add(model_id)
    return sorted(found, key=_gemini_image_model_sort)


def _extract_gemini_image(payload: Any) -> bytes | None:
    if not isinstance(payload, dict):
        return None
    candidates = payload.get("candidates", [])
    for candidate in candidates if isinstance(candidates, list) else []:
        content = candidate.get("content", {}) if isinstance(candidate, dict) else {}
        parts = content.get("parts", []) if isinstance(content, dict) else []
        for part in parts if isinstance(parts, list) else []:
            if not isinstance(part, dict):
                continue
            inline = part.get("inlineData") or part.get("inline_data")
            if not isinstance(inline, dict):
                continue
            mime = str(inline.get("mimeType") or inline.get("mime_type") or "")
            encoded = inline.get("data")
            if not mime.lower().startswith("image/") or not isinstance(encoded, str):
                continue
            try:
                raw = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                continue
            if raw and len(raw) <= 24 * 1024 * 1024:
                return raw
    return None


async def generate_gemini_image(prompt: str, api_key: str, ratio: str, destination: Path) -> dict[str, Any]:
    """Generate and locally normalize one native Gemini image; never substitutes generic artwork."""
    if not api_key.strip():
        raise RuntimeError("توليد صورة المشهد يحتاج Gemini API Key صالحاً.")
    timeout = httpx.Timeout(180.0, connect=25.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            listing = await client.get(GEMINI_MODELS_URL, params={"key": api_key.strip(), "pageSize": 1000})
        except httpx.HTTPError as exc:
            raise RuntimeError("تعذر الاتصال بقائمة نماذج Gemini للصور.") from exc
        if listing.status_code in (401, 403):
            raise RuntimeError("رفض Gemini مفتاح الصور أو صلاحيات الحساب.")
        if listing.status_code >= 400:
            raise RuntimeError(f"تعذر اكتشاف نماذج صور Gemini (HTTP {listing.status_code}).")
        try:
            models = _gemini_image_ids(listing.json())
        except (ValueError, TypeError) as exc:
            raise RuntimeError("تعذر قراءة قائمة نماذج صور Gemini.") from exc
        if not models:
            raise RuntimeError("لا يعرض حساب Gemini نموذج صور generateContent نشطاً؛ لم يُستخدم نموذج نصي كبديل.")
        attempted: list[str] = []
        for model_id in models:
            attempted.append(model_id)
            image_prompt = (
                f"Create one photorealistic cinematic documentary still for this exact scene: {prompt[:1800]}. "
                f"Compose for {ratio}. Match the narrated subject and action, avoid unrelated scenery, "
                "do not invent factual details, and include no readable text, labels or watermark."
            )
            try:
                response = await client.post(
                    f"{GEMINI_MODELS_URL}/{model_id}:generateContent",
                    params={"key": api_key.strip()},
                    json={
                        "contents": [{"parts": [{"text": image_prompt}]}],
                        "generationConfig": {"responseModalities": ["IMAGE"]},
                    },
                )
            except httpx.HTTPError as exc:
                raise RuntimeError("تعذر الاتصال بتوليد صور Gemini.") from exc
            if response.status_code == 429:
                raise GeminiImageError("بلغ Gemini حد الطلبات/الحصة لتوليد الصور (HTTP 429).", 429)
            if response.status_code >= 400:
                if response.status_code in (400, 404, 500, 502, 503):
                    continue
                if response.status_code in (401, 403):
                    raise RuntimeError("رفض Gemini صلاحية توليد الصورة؛ تحقق من المفتاح والنموذج.")
                raise RuntimeError(f"فشل توليد صورة Gemini (HTTP {response.status_code}).")
            try:
                payload = response.json()
            except (ValueError, TypeError):
                continue
            raw = _extract_gemini_image(payload)
            if raw is None:
                continue
            try:
                with Image.open(io.BytesIO(raw)) as image:
                    size = (1280, 720) if ratio == "16:9" else (720, 1280)
                    ImageOps.fit(image.convert("RGB"), size, method=Image.Resampling.LANCZOS).save(
                        destination, format="JPEG", quality=92, optimize=True
                    )
            except Exception as exc:
                destination.unlink(missing_ok=True)
                raise RuntimeError("أعاد Gemini محتوى لا يمكن فكّه كصورة.") from exc
            return {"path": destination, "kind": "gemini_image", "model": model_id}
    raise RuntimeError("لم يُرجع أي نموذج صور Gemini نشطاً بيانات صورة inlineData: " + ", ".join(attempted[:5]))


async def _discover_claude_sonnet_models(client: httpx.AsyncClient, api_key: str) -> list[dict[str, Any]]:
    headers = {"x-api-key": api_key, "anthropic-version": CLAUDE_VERSION}
    models: list[dict[str, Any]] = []
    params: dict[str, Any] = {"limit": 100}
    for _ in range(5):
        try:
            response = await client.get(CLAUDE_MODELS_URL, headers=headers, params=params)
        except httpx.HTTPError as exc:
            raise RuntimeError("تعذر الاتصال بقائمة نماذج Claude.") from exc
        if response.status_code in (401, 403):
            raise RuntimeError("رفض Anthropic مفتاح Claude أو صلاحيات قائمة النماذج.")
        if response.status_code >= 400:
            raise RuntimeError(f"تعذر اكتشاف نماذج Claude (HTTP {response.status_code}).")
        try:
            payload = response.json()
        except (ValueError, TypeError) as exc:
            raise RuntimeError("تعذر قراءة قائمة نماذج Claude.") from exc
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and "sonnet" in str(row.get("id", "")).lower():
                models.append(row)
        if not isinstance(payload, dict) or not payload.get("has_more"):
            break
        next_id = payload.get("last_id")
        if not next_id:
            break
        params["after_id"] = str(next_id)
    def preference(item: dict[str, Any]) -> tuple[int, str]:
        model_id = str(item.get("id", "")).lower()
        return (0 if "claude-sonnet-4-6" in model_id else 1 if "claude-3-5-sonnet" in model_id else 2, model_id)
    unique = {str(item.get("id")): item for item in models if item.get("id")}
    return sorted(unique.values(), key=preference)


async def _generate_claude(
    client: httpx.AsyncClient, prompt: str, api_key: str, video_language: str, aspect_ratio: str, scene_count: int
) -> dict[str, Any]:
    if not api_key.strip():
        raise ValueError("أدخل Claude API Key في الإعدادات أو اختر مزوداً آخر.")
    models = await _discover_claude_sonnet_models(client, api_key.strip())
    if not models:
        raise RuntimeError("لم يعرض حساب Anthropic أي نموذج Claude Sonnet نشط في قائمة النماذج.")
    for model in models:
        model_id = str(model["id"])
        try:
            response = await client.post(
                CLAUDE_MESSAGES_URL,
                headers={"x-api-key": api_key.strip(), "anthropic-version": CLAUDE_VERSION, "content-type": "application/json"},
                json={
                    "model": model_id,
                    "max_tokens": min(24000, max(4000, scene_count * 150)),
                    "temperature": 0.75,
                    "system": "Return only valid JSON matching the user's requested schema. Do not add markdown.",
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
        except httpx.HTTPError as exc:
            raise RuntimeError("تعذر الاتصال برسائل Claude.") from exc
        if response.status_code in (401, 403):
            raise RuntimeError("رفض Anthropic مفتاح Claude أو صلاحية إنشاء النص.")
        if response.status_code == 429:
            raise RuntimeError("بلغ Claude حد الطلبات أو الحصة (HTTP 429). لم يُستخدم مزود بديل بصمت.")
        if response.status_code in (400, 404, 500, 502, 503):
            continue
        if response.status_code >= 400:
            raise RuntimeError(f"رفض Claude الطلب (HTTP {response.status_code}).")
        try:
            payload = response.json()
            parts = payload.get("content", [])
            text = "".join(str(part.get("text", "")) for part in parts if isinstance(part, dict) and part.get("type") == "text")
            if not text:
                raise ValueError("Claude response has no text block")
            result = _parse_json(text, video_language, aspect_ratio, scene_count)
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("لم تُرجع Claude سيناريو JSON صالحاً مطابقاً للمشاهد المطلوبة.") from exc
        result["model"] = model_id
        return result
    raise RuntimeError("تعذر استخدام نماذج Claude Sonnet النشطة في حسابك؛ تحقق من صلاحية Messages API.")


async def generate_script(
    topic: str,
    provider: str,
    keys: ApiKeys,
    video_language: str = "en",
    aspect_ratio: str = "16:9",
    scene_count: int = 15,
    requested_seconds: int | None = None,
) -> dict[str, Any]:
    if provider == "demo":
        if scene_count > 20:
            raise ValueError("وضع التجربة مصمم لمقطع دقيقة واحدة فقط؛ اختر Gemini أو Groq أو Claude للمدد الأطول.")
        return demo_script(topic, video_language, scene_count)
    prompt = _strict_prompt(topic, video_language, aspect_ratio, scene_count, requested_seconds)
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

        if provider == "claude":
            result = await _generate_claude(client, prompt, keys.claude, video_language, aspect_ratio, scene_count)
            result["fact_sources"] = fact_sources
            return result



    raise ValueError("مزود النص غير معروف.")
