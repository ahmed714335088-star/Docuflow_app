"""Small, source-bounded fact profiles for reliable no-key demonstrations."""
from __future__ import annotations

import re
from typing import Any


GIZA_FACT_SOURCES = [
    {
        "title": "National Museums Scotland — casing stone from the Great Pyramid",
        "url": "https://www.nms.ac.uk/discover-catalogue/casing-stone-from-the-great-pyramid-of-giza",
        "supports": "The casing was fine white Tura limestone, not marble.",
    },
    {
        "title": "Smithsonian Magazine — Merer’s diary and pyramid logistics",
        "url": "https://www.smithsonianmag.com/history/ancient-egypt-shipping-mining-farming-economy-pyramids-180956619/",
        "supports": "Merer’s record describes a crew of about 200 and moving Tura limestone by boat to Giza.",
    },
    {
        "title": "Ancient Egypt Research Associates — Lost City",
        "url": "https://aeraweb.org/projects/lost-city/",
        "supports": "A Fourth Dynasty settlement near the Sphinx included workshops, bakeries and stores and served pyramid-complex operations.",
    },
]


GIZA_CONTEXT_EN = """Use only these bounded reference facts for the Giza-pyramid story: the Great Pyramid is associated with Khufu; the exterior casing was fine white Tura limestone, not marble; the surviving diary of inspector Merer records a crew of about 200 and transport of Tura limestone by boat to Giza; excavations at the Fourth Dynasty settlement near the Sphinx found work areas, bakeries and stores. The exact method used to raise every block is not established; label ramp proposals as hypotheses. Do not present secret chambers, aliens, an Orion design, supernatural energy, or a precise total workforce as established facts. Do not invent quotations, dates, measurements or sources. Narrow the story explicitly to Giza rather than implying all pyramids are identical."""

GIZA_CONTEXT_AR = """استخدم هذه الحقائق المحدودة فقط لقصة أهرامات الجيزة: يرتبط الهرم الأكبر بالملك خوفو؛ وكانت كسوته الخارجية من حجر جيري أبيض ناعم من طرة، لا من الرخام؛ وتسجل يوميات المفتش مرر فريقاً يقارب مئتي رجل ونقل الحجر الجيري من طرة إلى الجيزة بالقوارب؛ وكشفت حفريات مستوطنة الأسرة الرابعة قرب أبو الهول عن مناطق عمل ومخابز ومخازن. لا توجد طريقة واحدة محسومة تشرح رفع كل كتلة؛ لذلك تُعرض فرضيات المنحدرات كفرضيات. لا تعرض الغرف السرية أو الكائنات الفضائية أو محاذاة أوريون أو الطاقة الخارقة أو رقماً إجمالياً دقيقاً للعمال كحقائق ثابتة. لا تختلق اقتباسات أو تواريخ أو قياسات أو مصادر. حدّد القصة بأهرامات الجيزة ولا تعممها على كل أهرامات العالم."""


# Each tuple contains spoken narration, a distinct English image-search phrase,
# and an English image-generation direction. Every query retains the same anchor.
_GIZA_AR = [
    ("ما سرُّ بناء الهرم الأكبر؟", "Giza pyramids Egypt Great Pyramid plateau overview", "Wide, accurate documentary view of the Great Pyramid of Giza on its plateau; no fantasy details."),
    ("يرتفع الهرم الأكبر فوق الجيزة.", "Giza pyramids Egypt Great Pyramid aerial plateau", "Aerial documentary view of the Giza plateau and Great Pyramid in natural daylight."),
    ("شُيّد للملك المصري خوفو.", "Giza pyramids Egypt Khufu statue artifact", "Museum documentary close-up of an authentic small Khufu artifact, clearly an archaeological object."),
    ("قبل أكثر من أربعة آلاف وخمسمئة عام.", "Giza pyramids Egypt ancient Great Pyramid landscape", "Historically grounded view of the Great Pyramid, no invented machinery or inscriptions."),
    ("كسوته الأصلية حجر جيري أبيض.", "Giza pyramids Egypt limestone casing stone block", "A surviving smooth white limestone casing stone associated with the Great Pyramid."),
    ("نحتوه من محاجر طرة.", "Giza pyramids Egypt Tura limestone quarry stone", "Tura limestone quarry landscape and pale limestone rock, presented as a documented source material."),
    ("وثّق مرر نقلَ الحجر بالقوارب.", "Giza pyramids Egypt Merer diary papyrus", "Documentary archival view of the Diary of Merer papyrus or its museum display; no fabricated readable text."),
    ("رحلةٌ من طرة إلى الجيزة.", "Giza pyramids Egypt Nile boat limestone transport", "Documentary reconstruction of a cargo boat carrying limestone on the Nile toward Giza."),
    ("قاد مرر نحو مئتي رجل.", "Giza pyramids Egypt Merer team ancient workers", "Historically cautious illustration of an organized Egyptian transport crew; avoid claiming a total workforce."),
    ("لكنهم فريق نقل، لا الجميع.", "Giza pyramids Egypt workers settlement excavation", "Archaeological excavation at the settlement near the Giza pyramid complex."),
    ("قرب الموقع، كشفت الحفريات مستوطنة.", "Giza pyramids Egypt Fourth Dynasty settlement archaeology", "Excavated remains of a Fourth Dynasty settlement near the Giza plateau."),
    ("مخابز وورشاً ومخازن تدعم العمل.", "Giza pyramids Egypt bakery workshop settlement ruins", "Archaeological remains of bakeries, workshops or storage spaces in the workers settlement."),
    ("أما طريقة رفع الكتل، فتُناقش.", "Giza pyramids Egypt stone blocks construction archaeology", "Close documentary view of pyramid masonry that frames an open engineering question."),
    ("المنحدرات فرضيات، وليست حقيقة محسومة.", "Giza pyramids Egypt ramp theory archaeological diagram", "Clearly labeled conceptual reconstruction of a ramp hypothesis, not footage presented as fact."),
    ("والكسوة لم تكن من الرخام.", "Giza pyramids Egypt limestone casing museum fragment", "Museum display of a white limestone casing fragment associated with the Great Pyramid."),
    ("الأدلة تبرز تنظيماً بشرياً هائلاً.", "Giza pyramids Egypt plateau archaeological panorama", "Grounded final panorama of the Giza pyramids and plateau in warm natural light."),
    ("يحفظ سجلّ مرر حركة الإمداد.", "Giza pyramids Egypt Merer diary shipment record", "Archival documentary detail of the surviving Merer diary display, without fabricated readable writing."),
    ("وتكشف المستوطنة تفاصيل الحياة اليومية.", "Giza pyramids Egypt workers settlement daily life ruins", "Archaeological documentation of settlement remains near Giza, framed as evidence of organized site support."),
    ("لكن بعض خطوات البناء ما زالت مجهولة.", "Giza pyramids Egypt construction method evidence blocks", "Measured documentary view of pyramid masonry, emphasizing that the exact construction sequence is unresolved."),
    ("فالوثائق تضيق مساحة التخمين.", "Giza pyramids Egypt archaeological evidence plateau survey", "Careful archaeological survey of the Giza plateau, grounded and free of sensational claims."),
]

_GIZA_EN = [
    ("How was the Great Pyramid built?", "Giza pyramids Egypt Great Pyramid plateau overview", "Wide, accurate documentary view of the Great Pyramid of Giza on its plateau; no fantasy details."),
    ("The Great Pyramid rises over Giza.", "Giza pyramids Egypt Great Pyramid aerial plateau", "Aerial documentary view of the Giza plateau and Great Pyramid in natural daylight."),
    ("It was built for King Khufu.", "Giza pyramids Egypt Khufu statue artifact", "Museum documentary close-up of an authentic small Khufu artifact, clearly an archaeological object."),
    ("More than forty-five centuries ago.", "Giza pyramids Egypt ancient Great Pyramid landscape", "Historically grounded view of the Great Pyramid, no invented machinery or inscriptions."),
    ("Its casing was fine white limestone.", "Giza pyramids Egypt limestone casing stone block", "A surviving smooth white limestone casing stone associated with the Great Pyramid."),
    ("The stone came from Tura quarries.", "Giza pyramids Egypt Tura limestone quarry stone", "Tura limestone quarry landscape and pale limestone rock, presented as a documented source material."),
    ("Merer recorded the stone shipments.", "Giza pyramids Egypt Merer diary papyrus", "Documentary archival view of the Diary of Merer papyrus or its museum display; no fabricated readable text."),
    ("Boats carried limestone toward Giza.", "Giza pyramids Egypt Nile boat limestone transport", "Documentary reconstruction of a cargo boat carrying limestone on the Nile toward Giza."),
    ("Merer led about two hundred men.", "Giza pyramids Egypt Merer team ancient workers", "Historically cautious illustration of an organized Egyptian transport crew; avoid claiming a total workforce."),
    ("That crew was not every builder.", "Giza pyramids Egypt workers settlement excavation", "Archaeological excavation at the settlement near the Giza pyramid complex."),
    ("Excavations found a nearby settlement.", "Giza pyramids Egypt Fourth Dynasty settlement archaeology", "Excavated remains of a Fourth Dynasty settlement near the Giza plateau."),
    ("Bakeries and workshops supported the work.", "Giza pyramids Egypt bakery workshop settlement ruins", "Archaeological remains of bakeries, workshops or storage spaces in the workers settlement."),
    ("How blocks were raised remains debated.", "Giza pyramids Egypt stone blocks construction archaeology", "Close documentary view of pyramid masonry that frames an open engineering question."),
    ("Ramp ideas remain hypotheses, not proof.", "Giza pyramids Egypt ramp theory archaeological diagram", "Clearly labeled conceptual reconstruction of a ramp hypothesis, not footage presented as fact."),
    ("The original casing was not marble.", "Giza pyramids Egypt limestone casing museum fragment", "Museum display of a white limestone casing fragment associated with the Great Pyramid."),
    ("The evidence points to organized human work.", "Giza pyramids Egypt plateau archaeological panorama", "Grounded final panorama of the Giza pyramids and plateau in warm natural light."),
    ("Merer's diary preserves a supply record.", "Giza pyramids Egypt Merer diary shipment record", "Archival documentary detail of the surviving Merer diary display, without fabricated readable writing."),
    ("Nearby remains reveal daily support work.", "Giza pyramids Egypt workers settlement daily life ruins", "Archaeological documentation of settlement remains near Giza, framed as evidence of organized site support."),
    ("Some construction steps remain unresolved.", "Giza pyramids Egypt construction method evidence blocks", "Measured documentary view of pyramid masonry, emphasizing that the exact construction sequence is unresolved."),
    ("Evidence narrows the room for guesswork.", "Giza pyramids Egypt archaeological evidence plateau survey", "Careful archaeological survey of the Giza plateau, grounded and free of sensational claims."),
]


def is_giza_pyramid_topic(topic: str) -> bool:
    """Recognize Giza/Egyptian pyramids while avoiding Maya/Aztec/other traditions."""
    value = str(topic or "").lower().translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه", "ى": "ي"}))
    excluded = ("maya", "mayan", "المايا", "aztec", "aztecs", "الازتك", "mexico", "guatemala", "inca", "incan", "nubian", "sudan", "سودان", "نوبه")
    if any(re.search(rf"\b{re.escape(term)}\w*\b", value) for term in excluded):
        return False
    explicit_giza = any(word in value for word in ("giza", "جيزه", "الجيزه", "خوفو", "cheops", "egypt", "مصر", "مصري"))
    return explicit_giza


def fact_sources_for_topic(topic: str) -> list[dict[str, str]]:
    return [dict(source) for source in GIZA_FACT_SOURCES] if is_giza_pyramid_topic(topic) else []


def reference_context(topic: str, language: str) -> str:
    if not is_giza_pyramid_topic(topic):
        return ""
    return GIZA_CONTEXT_AR if language == "ar" else GIZA_CONTEXT_EN


def giza_demo_script(topic: str, language: str, scene_count: int) -> dict[str, Any]:
    rows = _GIZA_AR if language == "ar" else _GIZA_EN
    scenes = [
        {
            "narration": narration,
            "translation_ar": narration if language == "ar" else _GIZA_AR[index][0],
            "visual_anchor": "Giza pyramids Egypt",
            "visual_keywords": query,
            "image_prompt": prompt,
            "visual_type": "ai_image",
        }
        for index, (narration, query, prompt) in enumerate(rows[:scene_count])
    ]
    if len(scenes) != scene_count:
        raise ValueError("عينة الأهرامات الموثقة متاحة لمشهد الدقيقة الواحدة فقط.")
    return {
        "title": "أهرامات الجيزة: ما تقوله الأدلة" if language == "ar" else "Giza Pyramids: What the Evidence Shows",
        "hook": scenes[0]["narration"],
        "scenes": scenes,
        "fact_sources": fact_sources_for_topic(topic),
        "demo": True,
        "model": f"curated-giza-demo-{len(scenes)}-scenes",
    }
