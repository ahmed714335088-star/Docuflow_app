"""Fetch relevant visual assets from Pixabay or reusable Wikimedia Commons files."""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageOps

from app.visual_matching import choose_commons_page, rank_pixabay_hits, search_tokens


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


def _hit_identity(hit: dict[str, Any], url: str) -> str:
    return str(hit.get("id") or hit.get("pageURL") or url).strip()


def _pixabay_hits(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    hits = payload.get("hits")
    return [hit for hit in hits if isinstance(hit, dict)] if isinstance(hits, list) else []


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
    query = _anchored_query(query, visual_anchor)
    portrait = orientation == "portrait"
    params = {
        "key": key.strip(),
        "q": query,
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
        try:
            response = await client.get("https://pixabay.com/api/videos/", params=params)
        except httpx.HTTPError as exc:
            raise RuntimeError("تعذر الاتصال بـPixabay؛ تحقق من المفتاح والإنترنت.") from exc
        if response.status_code in (401, 403):
            raise RuntimeError("رفض Pixabay المفتاح. راجع مفتاح API في الإعدادات.")
        if response.status_code >= 400:
            raise RuntimeError(f"رفض Pixabay البحث (HTTP {response.status_code}).")
        try:
            hits = _pixabay_hits(response.json())
        except (ValueError, TypeError) as exc:
            raise RuntimeError("تعذر قراءة استجابة Pixabay.") from exc
        candidates = rank_pixabay_hits(hits, query, visual_anchor, used_ids, strict_tags=True)
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
    query = _anchored_query(query, visual_anchor)
    portrait = orientation == "portrait"
    params = {
        "key": key.strip(),
        "q": query,
        "image_type": "photo",
        "orientation": "vertical" if portrait else "horizontal",
        "safesearch": "true",
        "per_page": 30,
        "order": "popular",
        "min_width": 720 if portrait else 1280,
        "min_height": 1280 if portrait else 720,
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(55.0, connect=15.0), follow_redirects=True) as client:
        try:
            response = await client.get("https://pixabay.com/api/", params=params)
        except httpx.HTTPError as exc:
            raise RuntimeError("تعذر الاتصال بـPixabay للبحث عن الصور.") from exc
        if response.status_code in (401, 403):
            raise RuntimeError("رفض Pixabay المفتاح. راجع مفتاح API في الإعدادات.")
        if response.status_code >= 400:
            raise RuntimeError(f"رفض Pixabay بحث الصور (HTTP {response.status_code}).")
        try:
            hits = _pixabay_hits(response.json())
        except (ValueError, TypeError) as exc:
            raise RuntimeError("تعذر قراءة استجابة صور Pixabay.") from exc
        for hit in rank_pixabay_hits(hits, query, visual_anchor, used_ids):
            image_url = str(hit.get("largeImageURL") or hit.get("webformatURL") or "")
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
