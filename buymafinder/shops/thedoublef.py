from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from buymafinder.core.models import Product, SizeStock, Source
from buymafinder.core.urls import normalize_url, unique_normalized_urls
from buymafinder.shops.base import ShopAdapter
from buymafinder.shops.common import (
    ShopHTMLParser,
    find_product_json_ld,
    normalize_whitespace,
    parse_price,
)

_LOGGER = logging.getLogger(__name__)

# thedoublef.com is a standard Shopify storefront: every collection exposes
# its product list as clean JSON at "{collection_url}/products.json", one
# page at a time (confirmed live: this is far more reliable than scraping
# product-card links out of the rendered HTML, and avoids the geo/cookie
# interstitials the normal page shows on first load).
_COLLECTION_PAGE_SIZE = 250

# A variant's own sku embeds its size as a trailing "_NNN-SIZE" segment
# (e.g. "S50BN0545M35693/S_MARGI-900_202-46" for size "46"), confirmed
# consistent across both clothing and accessories; stripping it gives a
# single size-independent identifier for the product itself.
_VARIANT_SIZE_SUFFIX = re.compile(r"_\d{3}-[^_/]+$")


def collection_json_url(list_url: str, page: int) -> str:
    base = list_url.rstrip("/")
    return f"{base}/products.json?limit={_COLLECTION_PAGE_SIZE}&page={page}"


def _is_products_json(json_text: str) -> bool:
    """True only for a genuine Shopify products.json body (even an empty one).

    Distinguishes "this collection page has zero products" from "the
    request failed and returned something else" (e.g. an HTML error page),
    which would otherwise both parse down to an empty product list.
    """
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        return False
    return isinstance(data, dict) and isinstance(data.get("products"), list)


def parse_collection_json(json_text: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        return []
    products = data.get("products") if isinstance(data, dict) else None
    return [item for item in products if isinstance(item, dict)] if isinstance(products, list) else []


def product_url_for_handle(handle: str) -> str:
    return f"https://www.thedoublef.com/en-us/products/{handle}"


def parse_product_detail_html(
    html: str,
    product_url: str,
    source: Source,
    collected_at: datetime | None = None,
    *,
    target_sku: str | None = None,
) -> Product:
    """Build a normalized shared Product from a thedoublef.com product page.

    ``target_sku`` is accepted for interface parity with shops that expose
    multiple priced variants per URL; thedoublef.com's variants (sizes) all
    share one price, so it's unused here.
    """
    del target_sku
    parser = _TheDoubleFHTMLParser()
    parser.feed(html)
    json_ld = find_product_json_ld(parser.scripts)

    title = normalize_whitespace(
        str(json_ld.get("name", "")) or parser.metadata.get("og:title", "")
    )
    brand_value = json_ld.get("brand") if json_ld else None
    brand = normalize_whitespace(
        brand_value.get("name", "") if isinstance(brand_value, dict) else str(brand_value or "")
    )
    description = normalize_whitespace(str(json_ld.get("description", "")) if json_ld else "")
    raw_sku = normalize_whitespace(str(json_ld.get("sku", "")) if json_ld else "")
    sku = _VARIANT_SIZE_SUFFIX.sub("", raw_sku) or raw_sku

    offer = json_ld.get("offers", {}) if json_ld else {}
    if isinstance(offer, list):
        offer = offer[0] if offer else {}
    if not isinstance(offer, dict):
        offer = {}
    currency = str(offer.get("priceCurrency") or "USD")
    price = None
    if offer.get("price") not in (None, ""):
        _, price = parse_price(str(offer["price"]))

    sizes = _size_stocks(parser)
    image_urls = _image_urls(html, json_ld)

    availability = str(offer.get("availability", "")).lower()
    in_stock = (
        "outofstock" not in availability
        if availability
        else (any(item.in_stock for item in sizes) if sizes else None)
    )

    return Product(
        shop_code=source.shop_code,
        shop_name=source.shop_name,
        target=source.target,
        category=source.category,
        brand=brand,
        name=title,
        product_url=normalize_url(product_url),
        currency=currency,
        regular_price=price,
        sale_price=None,
        sku=sku,
        color="",
        sizes=sizes,
        description=description,
        image_urls=image_urls,
        in_stock=in_stock,
        collected_at=collected_at or datetime.now(),
    )


class TheDoubleFAdapter(ShopAdapter):
    """Collect thedoublef.com products via its Shopify collection/product JSON."""

    code = "thedoublef"

    def __init__(self, max_pages_per_source: int = 10, max_links_per_source: int | None = None) -> None:
        if max_pages_per_source < 1:
            raise ValueError("Maximum pages per source must be at least one.")
        if max_links_per_source is not None and max_links_per_source < 1:
            raise ValueError("Maximum links per source must be at least one when set.")
        self.max_pages_per_source = max_pages_per_source
        self.max_links_per_source = max_links_per_source

    def collect_product_links(self, source: Source, browser: Any) -> Iterable[str]:
        page = browser.new_page()
        try:
            links: list[str] = []
            for page_number in range(1, self.max_pages_per_source + 1):
                url = collection_json_url(source.list_url, page_number)
                response = page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                body = page.inner_text("body")
                # A failed request (wrong URL, blocked, site down) can
                # render an HTML error page instead of JSON; that parses to
                # the same empty list a genuinely-exhausted last page would,
                # so it's indistinguishable from "no more products" unless
                # checked explicitly here. Only the first page raises: a
                # later page failing is treated the same as flannels.py
                # treats it — the source had at least some products, so
                # stop rather than losing everything already collected.
                ok_status = response is not None and response.ok
                valid_json = _is_products_json(body)
                if page_number == 1 and not (ok_status and valid_json):
                    raise RuntimeError(
                        f"thedoublef.com collection request did not return valid product JSON: {url} "
                        f"(status={response.status if response else 'no response'})"
                    )
                if not (ok_status and valid_json):
                    break
                items = parse_collection_json(body)
                links.extend(product_url_for_handle(item["handle"]) for item in items if item.get("handle"))
                if self.max_links_per_source is not None and len(links) >= self.max_links_per_source:
                    break
                if len(items) < _COLLECTION_PAGE_SIZE:
                    break
            links = unique_normalized_urls(links)
            if self.max_links_per_source is not None:
                links = links[: self.max_links_per_source]
            return links
        finally:
            page.close()

    def collect_product_detail(self, product_url: str, source: Source, browser: Any) -> Product:
        page = browser.new_page()
        try:
            page.goto(product_url, wait_until="domcontentloaded", timeout=60_000)
            page.locator("body").wait_for(state="attached", timeout=20_000)
            return parse_product_detail_html(page.content(), page.url, source)
        finally:
            page.close()


class _TheDoubleFHTMLParser(ShopHTMLParser):
    pass


def _size_stocks(parser: _TheDoubleFHTMLParser) -> list[SizeStock]:
    # Confirmed live markup: <product-main data-variants="[{&quot;id&quot;:
    # ...,&quot;title&quot;:&quot;46&quot;,&quot;available&quot;:true,...}]">
    # is a single custom element per product page, holding every size's own
    # live availability as clean JSON — no ambiguity with unrelated
    # "recently viewed"/recommendation widgets elsewhere on the page (unlike
    # the embedded Shopify analytics blob, which repeats similarly-shaped
    # data for many products at once).
    nodes = parser.find_nodes(parser.root, "product-main", None, None)
    if not nodes:
        return []
    raw = nodes[0].attributes.get("data-variants", "")
    try:
        variants = json.loads(raw)
    except json.JSONDecodeError:
        return []
    sizes: list[SizeStock] = []
    seen: set[str] = set()
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        title = normalize_whitespace(str(variant.get("title", "")))
        if not title or title in seen:
            continue
        seen.add(title)
        sizes.append(SizeStock(size=title, in_stock=bool(variant.get("available"))))
    return sizes


def _image_urls(html: str, json_ld: dict[str, Any]) -> list[str]:
    # The product's own gallery images share one filename stem with a
    # trailing single-letter angle suffix (e.g. "{stem}.a.jpg", "{stem}
    # .b.jpg", ... not always contiguous letters), confirmed live; the stem
    # is read from the JSON-LD's own primary image so this can't pick up an
    # unrelated product's photos from elsewhere on the page (e.g. "recently
    # viewed").
    primary_image = _primary_image_url(json_ld.get("image") if json_ld else None)
    if not primary_image:
        return []
    parsed = urlsplit(primary_image)
    match = re.search(r"/([^/]+)\.[a-z]\.jpg$", parsed.path)
    if not match:
        return [primary_image]
    stem = match.group(1)
    base_path = parsed.path.rsplit("/", 1)[0]
    pattern = re.compile(re.escape(stem) + r"\.([a-z])\.jpg")
    letters: list[str] = []
    for found in pattern.finditer(html):
        letter = found.group(1)
        if letter not in letters:
            letters.append(letter)
    if not letters:
        return [primary_image]
    version = next((value for key, value in parse_qsl(parsed.query) if key == "v"), "")
    version_suffix = f"?v={version}" if version else ""
    return unique_normalized_urls(
        f"https://{parsed.netloc}{base_path}/{stem}.{letter}.jpg{version_suffix}" for letter in letters
    )


def _primary_image_url(image: Any) -> str:
    """Normalize schema.org's ``image`` field to a single URL string.

    The spec allows a plain URL string, an ImageObject (``{"url": ...}``),
    or a list of either — thedoublef.com's own pages only showed a plain
    string, but nothing here guarantees every product page does.
    """
    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, dict):
        return str(image.get("url", ""))
    if isinstance(image, str):
        return image
    return ""
