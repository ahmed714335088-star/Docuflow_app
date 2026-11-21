"""Metadata-based relevance and reuse checks for documentary visual assets.

These checks reduce unrelated stock results; they do not inspect image pixels or
prove that an asset depicts the narrated event.
"""
from __future__ import annotations

import re
from collections import Counter
from html import unescape
from typing import Any, cast


_STOPWORDS = {
    "a", "an", "and", "at", "by", "for", "from", "in", "into", "near",
    "of", "on", "or", "the", "to", "with", "wide", "close", "shot",
    "view", "photo", "image", "video", "footage", "cinematic", "documentary",
    "ancient", "great", "modern", "historic", "historical", "scene", "showing",
}
_SINGULARS = {
    "builders": "builder", "blocks": "block", "boats": "boat", "cities": "city",
    "pyramids": "pyramid", "stones": "stone", "workers": "worker",
}


def _meaningful_tokens(value: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", str(value or "").lower())
    return [
        _SINGULARS.get(token) or token
        for token in tokens
        if len(token) > 1 and token not in _STOPWORDS
    ]


def search_tokens(value: str) -> set[str]:
    """Return meaningful English-like terms from search text and source metadata."""
    return set(_meaningful_tokens(value))


def visual_relevance_score(metadata: str, visual_anchor: str, query: str) -> int | None:
    """Score a candidate only when both the documentary anchor and scene beat match."""
    anchor_tokens = search_tokens(visual_anchor)
    if not anchor_tokens:
        return None
    metadata_tokens = search_tokens(metadata)
    anchor_hits = anchor_tokens & metadata_tokens
    anchor_threshold = min(2, len(anchor_tokens))
    if len(anchor_hits) < anchor_threshold:
        return None

    query_counts = Counter(_meaningful_tokens(query))
    query_counts.subtract(Counter(_meaningful_tokens(visual_anchor)))
    scene_tokens = {token for token, count in query_counts.items() if count > 0}
    if not scene_tokens:
        return None
    scene_hits = scene_tokens & metadata_tokens
    if not scene_hits:
        return None
    return len(anchor_hits) * 100 + len(scene_hits) * 10 + min(len(metadata_tokens), 99)


def pixabay_video_tags_match(tags: str, visual_anchor: str, query: str) -> bool:
    """Require subject and at least two concrete scene terms in Pixabay's tags."""
    anchor_tokens = search_tokens(visual_anchor)
    tag_tokens = search_tokens(tags)
    query_counts = Counter(_meaningful_tokens(query))
    query_counts.subtract(Counter(_meaningful_tokens(visual_anchor)))
    scene_tokens = {token for token, count in query_counts.items() if count > 0}
    if not anchor_tokens or not scene_tokens:
        return False
    if len(anchor_tokens & tag_tokens) < min(2, len(anchor_tokens)):
        return False
    return len(scene_tokens & tag_tokens) >= min(2, len(scene_tokens))


def rank_pixabay_hits(
    hits: list[dict[str, Any]], query: str, visual_anchor: str, used_ids: set[str] | None = None,
    strict_tags: bool = False,
) -> list[dict[str, Any]]:
    """Return matching Pixabay candidates, best metadata match first, without used IDs."""
    used = used_ids or set()
    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for position, hit in enumerate(hits):
        if not isinstance(hit, dict):
            continue
        identity = str(hit.get("id") or hit.get("pageURL") or "").strip()
        if identity and identity in used:
            continue
        tags = str(hit.get("tags", ""))
        if strict_tags and not pixabay_video_tags_match(tags, visual_anchor, query):
            continue
        metadata = tags if strict_tags else " ".join(
            str(hit.get(field, "")) for field in ("tags", "title", "description", "pageURL")
        )
        score = visual_relevance_score(metadata, visual_anchor, query)
        if score is not None:
            ranked.append((score, -position, hit))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in ranked]


def clean_html(value: Any) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _valid_http_url(value: str) -> bool:
    match = re.match(r"^https?://[^/\s]+", str(value or "").strip(), flags=re.IGNORECASE)
    return bool(match)


def commons_license_is_reusable(short_name: str, license_url: str = "") -> bool:
    """Accept public domain, CC0, CC BY and CC BY-SA; reject unknown/restrictive terms."""
    name = re.sub(r"\s+", " ", str(short_name or "").strip()).upper()
    if not name:
        return False
    if name in {"CC0", "CC0 1.0", "PUBLIC DOMAIN", "PUBLIC DOMAIN MARK", "PDM"}:
        return True
    if re.fullmatch(r"CC BY(?:-SA)? (?:1\.0|2\.0|2\.5|3\.0|4\.0)", name):
        return True
    url = str(license_url or "").lower()
    return "creativecommons.org/publicdomain/zero/" in url or "creativecommons.org/publicdomain/mark/" in url


def choose_commons_page(
    pages: list[dict[str, Any]],
    query: str,
    visual_anchor: str,
    orientation: str,
    used_titles: set[str] | None = None,
) -> dict[str, Any] | None:
    """Choose a relevant, non-duplicate Commons image whose license is reusable."""
    used = used_titles or set()
    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for position, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        title = str(page.get("title") or "").strip()
        if not title or title in used:
            continue
        infos = page.get("imageinfo") or []
        info = infos[0] if isinstance(infos, list) and infos and isinstance(infos[0], dict) else {}
        raw_ext = info.get("extmetadata")
        ext = cast(dict[str, Any], raw_ext) if isinstance(raw_ext, dict) else {}

        def meta(key: str) -> str:
            value = ext.get(key, {})
            return clean_html(value.get("value", "") if isinstance(value, dict) else value)

        license_name = meta("LicenseShortName")
        license_url = meta("LicenseUrl")
        if not commons_license_is_reusable(license_name, license_url):
            continue
        if not _valid_http_url(license_url):
            continue
        width = int(info.get("width", 0) or 0)
        height = int(info.get("height", 0) or 0)
        if width <= 0 or height <= 0:
            continue
        if orientation == "portrait" and height <= width:
            continue
        if orientation != "portrait" and width < height:
            continue
        categories = page.get("categories") or []
        category_text = " ".join(
            str(item.get("title", "")) for item in categories if isinstance(item, dict)
        )
        description = meta("ImageDescription")
        object_name = meta("ObjectName")
        metadata = " ".join((title, description, object_name, category_text))
        score = visual_relevance_score(metadata, visual_anchor, query)
        if score is None:
            continue
        image_url = str(info.get("thumburl") or info.get("url") or "")
        if not image_url.startswith("https://"):
            continue
        artist = meta("Artist") or meta("Credit")
        page_url = str(info.get("descriptionurl") or "")
        if not page_url.startswith("https://"):
            continue
        if license_name.strip().upper().startswith("CC BY") and not artist:
            continue
        ranked.append((score, -position, {
            "id": title,
            "title": title,
            "image_url": image_url,
            "page_url": page_url,
            "artist": artist,
            "license": license_name,
            "license_url": license_url,
            "credit": meta("Credit"),
        }))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return ranked[0][2] if ranked else None
