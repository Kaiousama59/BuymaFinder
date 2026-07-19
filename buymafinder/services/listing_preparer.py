from __future__ import annotations

import hashlib
import os
import re
import time
import urllib.request
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit

from buymafinder.core.listing_models import ListingDraft, ListingSettings
from buymafinder.core.models import Product


class ImageDownloadError(RuntimeError):
    pass


def prepare_listing_package(
    product: Product,
    settings: ListingSettings,
    output_root: Path,
    *,
    download_images: bool = True,
) -> Path:
    folder = output_root / _safe(product.shop_name) / _safe(product.brand) / _safe(product.sku or _product_id(product.product_url))
    folder.mkdir(parents=True, exist_ok=True)
    image_urls = product_image_urls(product)
    image_files = download_product_images(image_urls, folder) if download_images else []
    price = product.sale_price if product.sale_price is not None else product.regular_price
    draft = ListingDraft(
        supplier=product.shop_name,
        source_url=product.product_url,
        brand=product.brand,
        sku=product.sku,
        source_name=product.name,
        source_currency=product.currency,
        source_price="" if price is None else format(price, "f"),
        source_description=product.description,
        source_sizes=[item.size for item in product.sizes if item.in_stock is not False],
        source_size_stock={item.size: item.in_stock for item in product.sizes},
        settings=settings,
        image_urls=image_urls,
        image_files=image_files,
    )
    draft.write_json(folder / "listing_data.json")
    return folder


def product_image_urls(product: Product) -> list[str]:
    """Keep only images belonging to this product and remove query duplicates."""
    product_id = _product_id(product.product_url)
    seen: set[str] = set()
    result: list[str] = []
    for raw_url in product.image_urls:
        parsed = urlsplit(raw_url)
        if product_id and f"/photo/{product_id}/" not in parsed.path:
            continue
        normalized = urlunsplit(("https", parsed.netloc.lower(), parsed.path, "", ""))
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def download_product_images(urls: list[str], folder: Path, retries: int = 3) -> list[str]:
    labels = ("main", "model", "back")
    hashes: set[str] = set()
    files: list[str] = []
    for url in urls:
        data, extension = _download(url, retries)
        digest = hashlib.sha256(data).hexdigest()
        if digest in hashes:
            continue
        hashes.add(digest)
        index = len(files) + 1
        label = labels[index - 1] if index <= len(labels) else "image"
        target = folder / f"{index:02d}_{label}.{extension}"
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ImageDownloadError(f"Refusing to overwrite a different file: {target}")
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(data)
        os.replace(temporary, target)
        files.append(target.name)
    return files


def _download(url: str, retries: int) -> tuple[bytes, str]:
    last_error: Exception | None = None
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 BuymaFinder/1.0"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read()
                content_type = response.headers.get_content_type()
            extension = {
                "image/jpeg": "jpg",
                "image/pjpeg": "jpg",
                "image/jpg": "jpg",
                "image/png": "png",
                "image/webp": "webp",
            }.get(content_type)
            if extension is None or len(data) < 1024:
                raise ImageDownloadError(f"Invalid image response from {url}")
            return data, extension
        except (HTTPError, URLError, TimeoutError, OSError, ImageDownloadError) as error:
            last_error = error
            if attempt + 1 < retries:
                time.sleep(2**attempt)
    raise ImageDownloadError(f"Image download failed after {retries} attempts: {url}") from last_error


def _product_id(url: str) -> str:
    match = re.search(r"/(\d+)(?:/)?(?:\?|$)", url)
    return match.group(1) if match else ""


def _safe(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._")
    if not cleaned:
        raise ValueError(f"Cannot create a folder name from {value!r}")
    return cleaned
