"""Fetch relevant visual assets from Pixabay or reusable Wikimedia Commons files."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageOps

from app.visual_matching import choose_commons_page, rank_pixabay_hits, search_tokens

_PIXABAY_CACHE_TTL = 24 * 60 * 60
_PIXABAY_MIN_REQUEST_GAP = 0.72
_PIXABAY_CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "cache" / "pixabay"
_pixabay_rate_lock = asyncio.Lock()
_pixabay_next_request_at = 0.0


def _clean_query(value: str, limit: int = 140) -> str:
    text = re.sub(r"[^A-Za-z0-9\s,;:.'!?()/-]", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip(" ,;.-")[:limit]


def _anchored_query(query: str, visual_anchor: str) -> str:
    anchor = _clean_query(visual_anchor, 80)
    scene = _clean_query(query, 180)
    if not search_tokens(anchor) or not search_tokens(scene):
        raise ValueError("لا يمكن البحث المرئي دون مرساة موضوع واستعلام مشهد إنجليزيين محددين.")
    if not search_tokens(anchor).issubset(search_tokens(scene)):
        scene = f"{anchor} {scene}"
    return scene[:180]


def _pixabay_search_query(query: str, visual_anchor: str) -> str:
    """Reduce detailed scene terms to exactly two concrete English search terms."""
    anchored = _anchored_query(query, visual_anchor)
    words = re.findall(r"[a-z0-9]+", anchored.lower())
    anchor_words = re.findall(r"[a-z0-9]+", _clean_query(visual_anchor, 80).lower())
    anchor_set = set(anchor_words)
    scene_words = list(dict.fromkeys(word for word in words if word not in anchor_set and len(word) > 1))
    if len(scene_words) >= 2:
        return " ".join(scene_words[:2])
    if len(scene_words) == 1:
        anchor_candidates = [word for word in anchor_words if word != scene_words[0]]
        if anchor_candidates:
            return f"{anchor_candidates[-1]} {scene_words[0]}"
    candidates = list(dict.fromkeys(words))
    if len(candidates) < 2:
        raise ValueError("يحتاج بحث Pixabay إلى كلمتين إنجليزيتين واضحتين على الأقل.")
    return " ".join(candidates[:2])


def _hit_identity(hit: dict[str, Any], url: str) -> str:
    return str(hit.get("id") or hit.get("pageURL") or url).strip()


def _pixabay_hits(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    hits = payload.get("hits")
    return [hit for hit in hits if isinstance(hit, dict)] if isinstance(hits, list) else []


async def _pace_pixabay_request() -> None:
    global _pixabay_next_request_at
    async with _pixabay_rate_lock:
        delay = _pixabay_next_request_at - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        _pixabay_next_request_at = time.monotonic() + _PIXABAY_MIN_REQUEST_GAP


async def _pixabay_search(client: httpx.AsyncClient, endpoint: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Search Pixabay with a 24-hour result cache and API-key rate pacing."""
    key_fingerprint = hashlib.sha256(str(params.get("key", "")).encode("utf-8")).hexdigest()
    cache_identity = {
        "endpoint": endpoint,
        "key": key_fingerprint,
        "params": {name: value for name, value in params.items() if name != "key"},
    }
    cache_name = hashlib.sha256(json.dumps(cache_identity, sort_keys=True).encode("utf-8")).hexdigest() + ".json"
    cache_path = _PIXABAY_CACHE_DIR / cache_name
    now = time.time()
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if now - float(cached.get("cached_at", 0)) < _PIXABAY_CACHE_TTL:
            return _pixabay_hits({"hits": cached.get("hits", [])})
    except (OSError, ValueError, TypeError, AttributeError):
        pass

    await _pace_pixabay_request()
    try:
        response = await client.get(endpoint, params=params)
    except httpx.HTTPError as exc:
        raise RuntimeError("تعذر الاتصال بـPixabay؛ تحقق من المفتاح والإنترنت.") from exc
    if response.status_code in (401, 403):
        raise RuntimeError("رفض Pixabay المفتاح. راجع مفتاح API في الإعدادات.")
    if response.status_code == 429:
        reset = str(response.headers.get("X-RateLimit-Reset", "")).strip()
        wait = f" انتظر {reset} ثانية تقريباً." if reset.isdigit() else " انتظر حتى تجدد الحد ثم أعد المحاولة."
        raise RuntimeError("بلغ مفتاح Pixabay حد الطلبات (HTTP 429)." + wait)
    if response.status_code >= 400:
        raise RuntimeError(f"رفض Pixabay البحث (HTTP {response.status_code}).")
    try:
        hits = _pixabay_hits(response.json())
    except (ValueError, TypeError) as exc:
        raise RuntimeError("تعذر قراءة استجابة Pixabay.") from exc
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"cached_at": now, "hits": hits}), encoding="utf-8")
        temporary.replace(cache_path)
    except OSError:
        pass
    return hits


def _selected_video_variant(hit: dict[str, Any], portrait: bool, max_size: int) -> tuple[str, int] | None:
    variants = hit.get("videos", {})
    if not isinstance(variants, dict):
        return None
    candidates: list[tuple[int, str]] = []
    for quality in ("large", "medium", "small", "tiny"):
        item = variants.get(quality, {})
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", ""))
        width = int(item.get("width", 0) or 0)
        height = int(item.get("height", 0) or 0)
        size = int(item.get("size", 0) or 0)
        if not url.startswith("https://") or width <= 0 or height <= 0:
            continue
        if size > max_size or (quality == "large" and size == 0):
            continue
        if portrait and (height <= width or height < 720):
            continue
        if not portrait and (width < height or width < 1280 or height < 720):
            continue
        candidates.append((width * height, url))
    if not candidates:
        return None
    area, url = max(candidates, key=lambda item: item[0])
    return url, area


async def fetch_pixabay_video(
    query: str,
    key: str,
    orientation: str,
    destination: Path,
    used_ids: set[str] | None = None,
    visual_anchor: str = "",
) -> dict[str, Any] | None:
    """Download the best metadata-matched, unused Pixabay video, or return None."""
    if not key.strip():
        return None
    detailed_query = _anchored_query(query, visual_anchor)
    search_query = _pixabay_search_query(detailed_query, visual_anchor)
    portrait = orientation == "portrait"
    params = {
        "key": key.strip(),
        "q": search_query,
        "per_page": 30,
        "video_type": "film",
        "safesearch": "true",
        "order": "popular",
        "min_width": 720 if portrait else 1280,
        "min_height": 1280 if portrait else 720,
    }
    max_size = 90 * 1024 * 1024
    timeout = httpx.Timeout(60.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        hits = await _pixabay_search(client, "https://pixabay.com/api/videos/", params)
        candidates = rank_pixabay_hits(hits, detailed_query, visual_anchor, used_ids, strict_tags=True)
        for hit in candidates:
            choice = _selected_video_variant(hit, portrait, max_size)
            if not choice:
                continue
            selected_url, _area = choice
            identity = _hit_identity(hit, selected_url)
            if used_ids is not None and identity in used_ids:
                continue
            if used_ids is not None:
                used_ids.add(identity)
            destination.parent.mkdir(parents=True, exist_ok=True)
            total = 0
            try:
                async with client.stream("GET", selected_url) as download:
                    download.raise_for_status()
                    with destination.open("wb") as output:
                        async for chunk in download.aiter_bytes(256 * 1024):
                            total += len(chunk)
                            if total > max_size:
                                raise RuntimeError("تجاوز مقطع Pixabay حد الحجم الآمن (90 MB).")
                            output.write(chunk)
            except httpx.HTTPError as exc:
                if used_ids is not None:
                    used_ids.discard(identity)
                destination.unlink(missing_ok=True)
                raise RuntimeError("تعذر تنزيل المقطع المرئي من Pixabay.") from exc
            except RuntimeError:
                if used_ids is not None:
                    used_ids.discard(identity)
                destination.unlink(missing_ok=True)
                raise
            if destination.is_file() and destination.stat().st_size > 50_000:
                return {
                    "path": destination,
                    "kind": "pixabay_video",
                    "source_id": identity,
                    "page_url": str(hit.get("pageURL", "")),
                    "tags": str(hit.get("tags", "")),
                }
            if used_ids is not None:
                used_ids.discard(identity)
            destination.unlink(missing_ok=True)
    return None


async def fetch_pixabay_image(
    query: str,
    key: str,
    orientation: str,
    destination: Path,
    used_ids: set[str] | None = None,
    visual_anchor: str = "",
) -> dict[str, Any] | None:
    """Download a metadata-matched, unused Pixabay still for Ken-Burns motion."""
    if not key.strip():
        return None
    detailed_query = _anchored_query(query, visual_anchor)
    search_query = _pixabay_search_query(detailed_query, visual_anchor)
    portrait = orientation == "portrait"
    params = {
        "key": key.strip(),
        "q": search_query,
        "image_type": "photo",
        "orientation": "vertical" if portrait else "horizontal",
        "safesearch": "true",
        "per_page": 30,
        "order": "popular",
        "min_width": 720 if portrait else 1280,
        "min_height": 1280 if portrait else 720,
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(55.0, connect=15.0), follow_redirects=True) as client:
        hits = await _pixabay_search(client, "https://pixabay.com/api/", params)
        for hit in rank_pixabay_hits(hits, detailed_query, visual_anchor, used_ids, strict_tags=True):
            image_url = str(hit.get("fullHDURL") or hit.get("largeImageURL") or "")
            width = int(hit.get("imageWidth", 0) or 0)
            height = int(hit.get("imageHeight", 0) or 0)
            if not image_url.startswith("https://") or width < 720 or height < 720:
                continue
            if portrait and height <= width:
                continue
            if not portrait and width < height:
                continue
            identity = _hit_identity(hit, image_url)
            if used_ids is not None and identity in used_ids:
                continue
            if used_ids is not None:
                used_ids.add(identity)
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                image_response = await client.get(image_url)
                image_response.raise_for_status()
                if len(image_response.content) > 24 * 1024 * 1024:
                    raise RuntimeError("تجاوزت صورة Pixabay الحد الآمن (24 MB).")
                with Image.open(io.BytesIO(image_response.content)) as image:
                    size = (720, 1280) if portrait else (1280, 720)
                    ImageOps.fit(image.convert("RGB"), size, method=Image.Resampling.LANCZOS).save(
                        destination, format="JPEG", quality=91, optimize=True
                    )
            except httpx.HTTPError as exc:
                if used_ids is not None:
                    used_ids.discard(identity)
                destination.unlink(missing_ok=True)
                raise RuntimeError("تعذر تنزيل صورة Pixabay.") from exc
            except RuntimeError:
                if used_ids is not None:
                    used_ids.discard(identity)
                destination.unlink(missing_ok=True)
                raise
            except Exception as exc:
                if used_ids is not None:
                    used_ids.discard(identity)
                destination.unlink(missing_ok=True)
                raise RuntimeError("تعذر فك صورة Pixabay المستلمة.") from exc
            if destination.is_file() and destination.stat().st_size > 20_000:
                return {
                    "path": destination,
                    "kind": "pixabay_image",
                    "source_id": identity,
                    "page_url": str(hit.get("pageURL", "")),
                    "tags": str(hit.get("tags", "")),
                }
            if used_ids is not None:
                used_ids.discard(identity)
            destination.unlink(missing_ok=True)
    return None


async def fetch_commons_image(
    query: str,
    visual_anchor: str,
    orientation: str,
    destination: Path,
    used_titles: set[str] | None = None,
) -> dict[str, Any] | None:
    """Find, attribute and download a scene-matched Commons image under a reusable license."""
    query = _anchored_query(query, visual_anchor)
    params = {
        "action": "query",
        "format": "json",
        "formatversion": 2,
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": 6,
        "gsrlimit": 20,
        "prop": "imageinfo|categories",
        "iiprop": "url|size|extmetadata",
        "iiextmetadatafilter": "Artist|Credit|ImageDescription|ObjectName|LicenseShortName|LicenseUrl",
        "iiurlwidth": 1280,
        "cllimit": 10,
    }
    headers = {"User-Agent": "DocuFlow/1.0 (open-source documentary video tool)"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(35.0, connect=15.0), follow_redirects=True, headers=headers) as client:
        try:
            response = await client.get("https://commons.wikimedia.org/w/api.php", params=params)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise RuntimeError("تعذر البحث في Wikimedia Commons؛ أعد المحاولة أو تحقق من الاتصال.") from exc
        except ValueError as exc:
            raise RuntimeError("تعذر قراءة نتائج Wikimedia Commons.") from exc
        pages = payload.get("query", {}).get("pages", []) if isinstance(payload, dict) else []
        if not isinstance(pages, list):
            pages = []
        selected = choose_commons_page(pages, query, visual_anchor, orientation, used_titles)
        if not selected:
            return None
        identity = str(selected["id"])
        if used_titles is not None:
            used_titles.add(identity)
        try:
            image_response = await client.get(str(selected["image_url"]))
            image_response.raise_for_status()
            if len(image_response.content) > 30 * 1024 * 1024:
                raise RuntimeError("تجاوز ملف Wikimedia Commons الحد الآمن (30 MB).")
            with Image.open(io.BytesIO(image_response.content)) as image:
                size = (720, 1280) if orientation == "portrait" else (1280, 720)
                ImageOps.fit(image.convert("RGB"), size, method=Image.Resampling.LANCZOS).save(
                    destination, format="JPEG", quality=91, optimize=True
                )
        except httpx.HTTPError as exc:
            if used_titles is not None:
                used_titles.discard(identity)
            destination.unlink(missing_ok=True)
            raise RuntimeError("تعذر تنزيل الصورة المختارة من Wikimedia Commons.") from exc
        except RuntimeError:
            if used_titles is not None:
                used_titles.discard(identity)
            destination.unlink(missing_ok=True)
            raise
        except Exception as exc:
            if used_titles is not None:
                used_titles.discard(identity)
            destination.unlink(missing_ok=True)
            raise RuntimeError("تعذر فك صورة Wikimedia Commons المستلمة.") from exc
        if not destination.is_file() or destination.stat().st_size < 20_000:
            if used_titles is not None:
                used_titles.discard(identity)
            destination.unlink(missing_ok=True)
            return None
        return {
            **selected,
            "path": destination,
            "kind": "commons_image",
        }
