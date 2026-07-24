from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin, urlsplit

from buymafinder.core.models import Product, SizeStock, Source
from buymafinder.core.urls import normalize_url, unique_normalized_urls
from buymafinder.shops.base import ShopAdapter
from buymafinder.shops.common import (
    Node,
    ShopHTMLParser,
    first_present,
    normalize_whitespace,
    parse_price,
)


# Product pages are a single hyphenated slug ending in a 5-7 digit style code
# (e.g. "/gucci-mens-short-sleeve-polo-shirt-542145"), unlike category pages
# ("/gucci/women") or search/pagination URLs, which never end this way.
PRODUCT_PATH = re.compile(r"^/[a-z0-9]+(?:-[a-z0-9]+)*-\d{5,7}$")
_LOGGER = logging.getLogger(__name__)

# Colour variants are selected client-side via a "#colcode=" URL fragment,
# which buymafinder's shared URL normalization strips (fragments are never
# part of a canonical URL, matching every other shop adapter) and which
# doesn't survive a fresh page load anyway. That's fine: every colorway's
# own price/sku/color is already embedded server-side in the page's
# ProductGroup JSON-LD (see parse_product_detail_variants), so nothing is
# lost by always collecting links without the fragment.


def parse_product_links(html: str, base_url: str) -> list[str]:
    parser = _FlannelsHTMLParser()
    parser.feed(html)
    return unique_normalized_urls(
        urljoin(base_url, path)
        for path in (href.split("?", 1)[0].split("#", 1)[0] for href in parser.links)
        if PRODUCT_PATH.match(path) and "gift-card" not in path and "gift-voucher" not in path
    )


def parse_next_page_url(html: str, current_url: str) -> str | None:
    """Return the absolute URL of the next listing page, or ``None`` on the last page.

    flannels.com paginates category listings with a ``?dcp=N`` query parameter,
    surfaced as an ``aria-label`` containing "next" on the pagination control.
    """
    parser = _FlannelsHTMLParser()
    parser.feed(html)
    for node in parser.find_nodes(parser.root, "a", None, None):
        href = node.attributes.get("href")
        if not href:
            continue
        if "next" in node.attributes.get("aria-label", "").lower():
            return urljoin(current_url, href)
    return None


def parse_product_detail_html(
    html: str,
    product_url: str,
    source: Source,
    collected_at: datetime | None = None,
    *,
    target_sku: str | None = None,
) -> Product:
    """Build a normalized shared Product from a flannels.com product page.

    Some products expose several priced colorways under one URL (a
    schema.org ``ProductGroup`` with a ``hasVariant`` list, each with its own
    sku/color/price) rather than a single ``Product``. When ``target_sku`` is
    given and matches one of those variants, that variant's own price/color
    is used instead of the page's default (first) variant — used by the live
    stock-refresh check, which already knows which colorway was listed.
    """
    variants = parse_product_detail_variants(html, product_url, source, collected_at)
    if not variants:
        raise ValueError(f"No product data found on flannels.com page: {product_url}")
    if target_sku:
        for variant in variants:
            if variant.sku.strip().casefold() == target_sku.strip().casefold():
                return variant
    return variants[0]


def parse_product_detail_variants(
    html: str,
    product_url: str,
    source: Source,
    collected_at: datetime | None = None,
) -> list[Product]:
    """Build one Product per purchasable colorway on a flannels.com page.

    All variants' price/color/sku/availability come from a single static
    JSON-LD block already embedded in the page on first load — no extra
    navigation or clicking is needed even when a product has several
    differently-priced colorways.
    """
    parser = _FlannelsHTMLParser()
    parser.feed(html)
    json_ld = _find_product_or_group_json_ld(parser.scripts)
    brand_value = json_ld.get("brand") if json_ld else None
    brand = normalize_whitespace(brand_value.get("name", "") if isinstance(brand_value, dict) else str(brand_value or ""))
    title = normalize_whitespace(first_present(json_ld.get("name") if json_ld else "", parser.metadata.get("og:title", "")))
    group_description = str(json_ld.get("description", "")) if json_ld else ""
    sizes = parser.size_stocks()

    variant_dicts = _variant_offers(json_ld)
    if not variant_dicts:
        return []

    default_sku = normalize_whitespace(str(variant_dicts[0].get("sku", "")))
    # _image_urls() scans every <img> on the page, which (confirmed live)
    # also picks up the OTHER colorway's small swatch-selector icon sharing
    # the same amplience.net host — a "Wild Tiger T-Shirt" Black-SKU listing
    # ended up with one White-SKU image mixed into its otherwise-correct
    # gallery this way. Every real gallery/thumbnail image embeds
    # "{sku}_o" in its path, so that's used as a hard filter rather than
    # trusting the page's DOM structure (CSS-module class names are
    # hashed and not a stable thing to scope by).
    default_image_urls = _own_sku_images(_image_urls(parser), default_sku)
    products: list[Product] = []
    for index, variant in enumerate(variant_dicts):
        offer = variant.get("offers", {}) if isinstance(variant.get("offers"), dict) else {}
        sku = normalize_whitespace(str(variant.get("sku", "")))
        color = normalize_whitespace(str(variant.get("color", "")))
        currency, price = _offer_price(offer)
        description = _augment_description(group_description or str(offer.get("description", "")), color)
        availability = str(offer.get("availability", "")).lower()
        in_stock = "outofstock" not in availability if availability else (any(s.in_stock for s in sizes) if sizes else None)
        if index == 0:
            image_urls = default_image_urls
        else:
            # Non-default colorways aren't rendered in the initial HTML, so
            # their photo gallery is reconstructed by swapping the default
            # gallery's SKU-code prefix for this variant's own sku — the
            # same CDN naming/angle-suffix convention is shared across a
            # product's colorways (confirmed live against flannels.com).
            image_urls = _swap_image_sku(default_image_urls, default_sku, sku)
        products.append(
            Product(
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
                color=color,
                sizes=sizes,
                description=normalize_whitespace(description),
                image_urls=image_urls,
                in_stock=in_stock,
                collected_at=collected_at or datetime.now(),
            )
        )
    return products


class FlannelsAdapter(ShopAdapter):
    """Collect flannels.com products with one shared browser context."""

    code = "flannels"

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
            self._navigate(page, source.list_url)
            product_links: list[str] = []
            for page_number in range(1, self.max_pages_per_source + 1):
                try:
                    page.locator("a[href*='-']").first.wait_for(state="attached", timeout=20_000)
                except Exception:
                    if page_number == 1:
                        raise
                    break
                html = page.content()
                product_links.extend(parse_product_links(html, page.url))
                if self.max_links_per_source is not None and len(product_links) >= self.max_links_per_source:
                    break
                if page_number == self.max_pages_per_source:
                    break
                next_page_url = parse_next_page_url(html, page.url)
                if next_page_url is None:
                    break
                self._navigate(page, next_page_url)
            product_links = unique_normalized_urls(product_links)
            if self.max_links_per_source is not None:
                product_links = product_links[: self.max_links_per_source]
            return product_links
        finally:
            page.close()

    def collect_product_detail(self, product_url: str, source: Source, browser: Any) -> Product:
        return self.collect_product_variants(product_url, source, browser)[0]

    def collect_product_variants(self, product_url: str, source: Source, browser: Any) -> list[Product]:
        page = browser.new_page()
        try:
            self._navigate(page, product_url)
            page.locator("body").wait_for(state="attached", timeout=20_000)
            return parse_product_detail_variants(page.content(), page.url, source)
        finally:
            page.close()

    NAVIGATION_TIMEOUT_MS = 60_000
    NAVIGATION_ATTEMPTS = 2

    @staticmethod
    def _navigate(page: Any, url: str) -> None:
        last_error: Exception | None = None
        for attempt in range(1, FlannelsAdapter.NAVIGATION_ATTEMPTS + 1):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=FlannelsAdapter.NAVIGATION_TIMEOUT_MS)
                page.wait_for_load_state("domcontentloaded")
                FlannelsAdapter._dismiss_cookie_dialog(page)
                return
            except Exception as error:
                last_error = error
                if attempt < FlannelsAdapter.NAVIGATION_ATTEMPTS:
                    _LOGGER.warning(
                        "Navigation attempt %d/%d failed, retrying: %s",
                        attempt,
                        FlannelsAdapter.NAVIGATION_ATTEMPTS,
                        url,
                    )
        assert last_error is not None
        raise last_error

    @staticmethod
    def _dismiss_cookie_dialog(page: Any) -> None:
        button = page.get_by_text("Reject all", exact=True)
        if button.count():
            try:
                button.first.click(timeout=2_000)
            except Exception:
                pass


class _FlannelsHTMLParser(ShopHTMLParser):
    def size_stocks(self, root: Node | None = None) -> list[SizeStock]:
        # Confirmed live markup: size options are <button data-testid="swatch-
        # button-enabled" value="S">, scoped inside a
        # data-testid="variant-selector-items" container so colour swatches
        # (which use the same Swatch_* CSS classes) aren't picked up too.
        root = root or self.root
        containers = self._find_by_data_testid(root, "variant-selector-items")
        scope = containers[0] if containers else root
        sizes: list[SizeStock] = []
        seen: set[str] = set()
        for node in self._find_by_data_testid_prefix(scope, "swatch-button-"):
            value = normalize_whitespace(node.attributes.get("value", "") or node.full_text())
            if not value or value in seen:
                continue
            seen.add(value)
            enabled = node.attributes.get("data-testid") == "swatch-button-enabled"
            sizes.append(SizeStock(size=value, in_stock=enabled))
        return sizes

    @staticmethod
    def _find_by_data_testid(node: Node, value: str) -> list[Node]:
        matches = []
        if node.attributes.get("data-testid") == value:
            matches.append(node)
        for child in node.children:
            matches.extend(_FlannelsHTMLParser._find_by_data_testid(child, value))
        return matches

    @staticmethod
    def _find_by_data_testid_prefix(node: Node, prefix: str) -> list[Node]:
        matches = []
        if node.attributes.get("data-testid", "").startswith(prefix):
            matches.append(node)
        for child in node.children:
            matches.extend(_FlannelsHTMLParser._find_by_data_testid_prefix(child, prefix))
        return matches


def _find_product_or_group_json_ld(scripts: list[str]) -> dict[str, Any]:
    """Find the page's schema.org Product or ProductGroup JSON-LD block.

    A ProductGroup (used for multi-colorway products) needs handling
    common.py's find_product_json_ld doesn't provide: it only recognizes
    ``@type == "Product"``.
    """
    for script in scripts:
        try:
            data = json.loads(script)
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get("@type") in ("Product", "ProductGroup"):
                return candidate
    return {}


def _primary_offer(json_product: dict[str, Any]) -> dict[str, Any]:
    """Unwrap the single real Offer nested inside flannels.com's AggregateOffer."""
    offers = json_product.get("offers", {}) if json_product else {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if not isinstance(offers, dict):
        return {}
    nested = offers.get("offers")
    if isinstance(nested, list) and nested and isinstance(nested[0], dict):
        return nested[0]
    return offers if offers.get("price") is not None else {}


def _variant_offers(json_ld: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one dict per purchasable colorway, each with "sku"/"color"/"offers".

    A ``ProductGroup`` already lists its variants with this shape in
    ``hasVariant``. A plain single-colorway ``Product`` is wrapped into an
    equivalent one-item list so callers never need to branch on page type.
    """
    if not json_ld:
        return []
    if json_ld.get("@type") == "ProductGroup":
        variants = json_ld.get("hasVariant", [])
        return [v for v in variants if isinstance(v, dict)]
    offer = _primary_offer(json_ld)
    item = offer.get("itemOffered", {}) if isinstance(offer.get("itemOffered"), dict) else {}
    sku = first_present(str(item.get("sku", "")), str(json_ld.get("sku", "")))
    # "color" is usually a top-level field on the Product itself, not nested
    # under itemOffered (which flannels.com's single-colorway pages don't
    # populate at all) — but check itemOffered first in case a page does.
    color = first_present(str(item.get("color", "")), str(json_ld.get("color", "")))
    return [{"sku": sku, "color": color, "offers": offer}]


def _own_sku_images(image_urls: list[str], sku: str) -> list[str]:
    """Keep only images whose CDN path embeds this exact SKU.

    Falls back to the unfiltered list if the SKU is missing or the filter
    would drop every image, rather than risk leaving a variant with no
    photos at all over a stricter-than-necessary filter.
    """
    if not sku:
        return image_urls
    filtered = [url for url in image_urls if f"{sku}_o" in urlsplit(url).path]
    return filtered if filtered else image_urls


def _swap_image_sku(image_urls: list[str], old_sku: str, new_sku: str) -> list[str]:
    """Rewrite a default colorway's image URLs to another colorway's own images.

    flannels.com's CDN paths are "{sku}_o", "{sku}_o_a1", "{sku}_o_a2", ...
    (same angle-suffix convention across a product's colorways, confirmed
    live), so swapping just the leading sku segment yields that colorway's
    real photos without visiting the page again.
    """
    if not old_sku or not new_sku or old_sku == new_sku:
        return list(image_urls)
    swapped = []
    for url in image_urls:
        parsed = urlsplit(url)
        if not parsed.path.startswith(f"/i/frasersdev/{old_sku}"):
            continue
        new_path = parsed.path.replace(f"/i/frasersdev/{old_sku}", f"/i/frasersdev/{new_sku}", 1)
        swapped.append(urlsplit(url)._replace(path=new_path, query="").geturl())
    return swapped


def _offer_price(offer: dict[str, Any]) -> tuple[str, Decimal | None]:
    currency = str(offer.get("priceCurrency", "GBP")) or "GBP"
    raw_price = offer.get("price")
    if raw_price in (None, ""):
        return currency, None
    _, price = parse_price(str(raw_price))
    return currency, price


def _augment_description(description: str, color: str) -> str:
    description = description.strip()
    if color and color.lower() not in description.lower():
        return f"{description}\nColour: {color}" if description else f"Colour: {color}"
    return description


def _image_urls(parser: "_FlannelsHTMLParser") -> list[str]:
    seen_paths: set[str] = set()
    urls: list[str] = []
    for node in parser.find_nodes(parser.root, "img", None, None):
        src = node.attributes.get("src", "")
        if "amplience.net" not in src:
            continue
        path = urlsplit(src).path
        if path in seen_paths:
            continue
        seen_paths.add(path)
        urls.append(src)
    return unique_normalized_urls(urls) if urls else []
