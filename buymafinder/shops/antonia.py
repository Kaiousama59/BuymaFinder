from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlsplit

from buymafinder.core.debug import save_empty_category_evidence
from buymafinder.core.models import Product, SizeStock, Source
from buymafinder.core.urls import normalize_url, unique_normalized_urls
from buymafinder.shops.base import ShopAdapter
from buymafinder.shops.common import (
    Node,
    ShopHTMLParser,
    find_product_json_ld,
    first_present,
    normalize_whitespace,
    offer_availability,
    overall_stock,
    prices_from_html,
)


PRODUCT_PATH = re.compile(r"^/en-us/products/[^/?#]+/?$")
_LOGGER = logging.getLogger(__name__)

# Confirmed against a live product page (2026-07-20): the "Details" accordion
# panel is <div class="product__meta__accordions__contents__content"
# data-accordion="N">, matched to its title by a shared data-accordion value,
# and each field is <p><strong>Label</strong>: value</p>.
_ACCORDION_TITLE_CLASS = "product__meta__accordions__titles__title"
_ACCORDION_CONTENT_CLASS = "product__meta__accordions__contents__content"

# Shopify variant pickers render size options as an <input type="radio"
# value="IT36" data-available="true"> paired with a <label ...
# data-available="true"><span>IT36</span></label> (confirmed live). Sold-out
# variants carry data-available="false"; disabled/aria-disabled/class-keyword
# checks are kept as a fallback for markup variants that weren't observed.
_SIZE_TOKEN = re.compile(r"^(?:IT)?\d{2}(?:\.\d)?$|^(?:XXS|XS|S|M|L|XL|XXL|XXXL|ONE SIZE|OS)$")
_SIZE_TAGS = {"button", "input", "label", "li"}
_UNAVAILABLE_CLASS_KEYWORDS = ("disabled", "sold-out", "sold_out", "soldout", "unavailable")


def parse_product_links(html: str, base_url: str) -> list[str]:
    """Extract and normalize antonia.it product links from collection HTML.

    Query strings (e.g. ``?variant=123`` preselecting a variant) are dropped
    before deduplication so the same product listed with different
    preselected variants collapses to a single canonical URL.
    """
    parser = _AntoniaHTMLParser()
    parser.feed(html)
    return unique_normalized_urls(
        urljoin(base_url, path)
        for path in (href.split("?", 1)[0] for href in parser.links)
        if PRODUCT_PATH.match(path)
    )


def parse_next_page_url(html: str, current_url: str) -> str | None:
    """Return the absolute URL of the next collection page, or ``None`` on the last page.

    antonia.it paginates Shopify collections with a ``?page=N`` query parameter.
    A ``rel="next"`` link is preferred when present (the standard Shopify
    pagination convention); otherwise the next page is inferred from a
    same-path link whose ``page`` value is one greater than the current page.
    """
    current = urlsplit(current_url)
    parser = _AntoniaHTMLParser()
    parser.feed(html)

    for node in parser.find_nodes(parser.root, "a", None, None):
        href = node.attributes.get("href")
        if not href:
            continue
        rel = node.attributes.get("rel", "").lower().split()
        if "next" in rel:
            return urljoin(current_url, href)

    current_query = dict(parse_qsl(current.query, keep_blank_values=True))
    try:
        current_page = int(current_query.get("page", "1"))
    except ValueError:
        current_page = 1

    for href in parser.links:
        absolute = urljoin(current_url, href)
        candidate = urlsplit(absolute)
        if candidate.path.rstrip("/") != current.path.rstrip("/"):
            continue
        candidate_query = dict(parse_qsl(candidate.query, keep_blank_values=True))
        try:
            candidate_page = int(candidate_query.get("page", ""))
        except ValueError:
            continue
        if candidate_page == current_page + 1:
            return absolute
    return None


def parse_product_detail_html(
    html: str,
    product_url: str,
    source: Source,
    collected_at: datetime | None = None,
) -> Product:
    """Build a normalized shared Product from an antonia.it product page."""
    parser = _AntoniaHTMLParser()
    parser.feed(html)
    metadata = parser.metadata
    product_root = parser.primary_product_node()
    product_url = normalize_url(metadata.get("canonical", product_url))
    json_product = find_product_json_ld(parser.scripts)

    title = first_present(
        json_product.get("name") if json_product else "",
        parser.first_text(tag="h1", root=product_root),
        metadata.get("og:title", ""),
    )
    brand_value = json_product.get("brand") if json_product else ""
    brand = first_present(
        brand_value.get("name", "") if isinstance(brand_value, dict) else str(brand_value or ""),
        parser.first_text(itemprop="brand", root=product_root),
        parser.brand_heading(root=product_root),
    )
    details = parser.detail_fields(root=product_root)
    sku = first_present(
        details.get("model", ""),
        str(json_product.get("sku", "")) if json_product else "",
        _offer_sku(json_product),
    )
    description = first_present(
        json_product.get("description") if json_product else "",
        metadata.get("description", ""),
    )
    description = _augment_description(description, details)

    price_values = parser.price_texts(root=product_root)
    regular_price, sale_price, currency = prices_from_html(json_product, price_values)
    image_urls = _image_urls(parser, metadata, json_product, product_url, product_root)
    sizes = parser.size_stocks(root=product_root)
    availability = offer_availability(json_product)
    in_stock = overall_stock(parser.metadata, sizes, product_root.full_text(), availability)
    return Product(
        shop_code=source.shop_code,
        shop_name=source.shop_name,
        target=source.target,
        category=source.category,
        brand=normalize_whitespace(brand),
        name=normalize_whitespace(title),
        product_url=product_url,
        currency=currency,
        regular_price=regular_price,
        sale_price=sale_price,
        sku=normalize_whitespace(sku),
        color="",
        sizes=sizes,
        description=normalize_whitespace(description),
        image_urls=image_urls,
        in_stock=in_stock,
        collected_at=collected_at or datetime.now(),
    )


class AntoniaAdapter(ShopAdapter):
    """Collect antonia.it products with one shared browser context."""

    code = "antonia"

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
                    page.locator("a[href*='/products/']").first.wait_for(state="attached", timeout=20_000)
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
            if not product_links:
                save_empty_category_evidence(page, source)
            return product_links
        finally:
            page.close()

    def collect_product_detail(self, product_url: str, source: Source, browser: Any) -> Product:
        page = browser.new_page()
        try:
            self._navigate(page, product_url)
            page.locator("body").wait_for(state="attached", timeout=20_000)
            return parse_product_detail_html(page.content(), page.url, source)
        finally:
            page.close()

    NAVIGATION_TIMEOUT_MS = 60_000
    NAVIGATION_ATTEMPTS = 2

    @staticmethod
    def _navigate(page: Any, url: str) -> None:
        last_error: Exception | None = None
        for attempt in range(1, AntoniaAdapter.NAVIGATION_ATTEMPTS + 1):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=AntoniaAdapter.NAVIGATION_TIMEOUT_MS)
                page.wait_for_load_state("domcontentloaded")
                AntoniaAdapter._dismiss_cookie_dialog(page)
                return
            except Exception as error:
                last_error = error
                if attempt < AntoniaAdapter.NAVIGATION_ATTEMPTS:
                    _LOGGER.warning(
                        "Navigation attempt %d/%d failed, retrying: %s",
                        attempt,
                        AntoniaAdapter.NAVIGATION_ATTEMPTS,
                        url,
                    )
        assert last_error is not None
        raise last_error

    @staticmethod
    def _dismiss_cookie_dialog(page: Any) -> None:
        for selector in (
            "#onetrust-accept-btn-handler",
            "button:has-text('Accept')",
            "button:has-text('Accept All')",
        ):
            button = page.locator(selector)
            if button.count():
                try:
                    button.first.click(timeout=2_000)
                except Exception:
                    continue
                return


class _AntoniaHTMLParser(ShopHTMLParser):
    def price_texts(self, root: Node | None = None) -> list[str]:
        # Confirmed live markup: the current price is a single
        # <span id="ProductPrice">, and an original/compare-at price (when a
        # sale is active) is <span id="ComparePrice"> (empty otherwise). Both
        # ids are unique per page, so this avoids picking up prices from
        # "recently viewed"/recommendation widgets elsewhere on the page.
        root = root or self.root
        compare_price = ""
        current_price = ""
        for node in self.find_nodes(root, None, None, None):
            node_id = node.attributes.get("id", "")
            if node_id == "ComparePrice":
                compare_price = normalize_whitespace(node.full_text())
            elif node_id == "ProductPrice":
                current_price = normalize_whitespace(node.full_text())
        return [value for value in (compare_price, current_price) if value]

    def size_stocks(self, root: Node | None = None) -> list[SizeStock]:
        root = root or self.root
        sizes: list[SizeStock] = []
        seen: set[str] = set()
        for node in self.find_nodes(root, None, None, None):
            if node.tag not in _SIZE_TAGS:
                continue
            value = normalize_whitespace(
                node.attributes.get("value") or node.attributes.get("data-value") or node.full_text()
            )
            if not value or not _SIZE_TOKEN.match(value.upper()) or value in seen:
                continue
            seen.add(value)
            sizes.append(SizeStock(size=value, in_stock=not _looks_unavailable(node)))
        return sizes

    def brand_heading(self, root: Node | None = None) -> str:
        # Fallback only: on the live theme observed, the brand is not shown as
        # DOM text anywhere (only in the JSON-LD block), so this path is
        # currently unexercised. Kept for themes/products where it might be.
        root = root or self.root
        for node in self.find_nodes(root, None, None, None):
            if node.tag not in {"a", "h2"}:
                continue
            text = normalize_whitespace(node.full_text())
            letters = sum(1 for character in text if character.isalpha())
            if text and text == text.upper() and letters >= 3 and len(text) <= 40:
                return text
        return ""

    def detail_fields(self, root: Node | None = None) -> dict[str, str]:
        root = root or self.root
        accordion_id = None
        for title in self.find_nodes(root, None, None, _ACCORDION_TITLE_CLASS):
            if normalize_whitespace(title.full_text()).lower() == "details":
                accordion_id = title.attributes.get("data-accordion")
                break
        fields: dict[str, str] = {}
        if accordion_id is None:
            return fields
        for content in self.find_nodes(root, None, None, _ACCORDION_CONTENT_CLASS):
            if content.attributes.get("data-accordion") != accordion_id:
                continue
            for paragraph in self.find_nodes(content, "p", None, None):
                labels = self.find_nodes(paragraph, "strong", None, None)
                if not labels:
                    continue
                label = normalize_whitespace(labels[0].full_text()).rstrip(":").lower()
                # Direct text only (not full_text()): the label's own text
                # lives on the <strong> child, so the paragraph's direct text
                # is exactly the value that follows it.
                value = normalize_whitespace("".join(paragraph.text)).lstrip(":").strip()
                if label and value:
                    fields[label] = value
            break
        return fields

    def primary_product_node(self) -> Node:
        # Confirmed live markup: <div itemscope class="product-template" ...>
        # wraps the title/price/accordions/size form and excludes the
        # "recently viewed"/recommendations widget that follows it.
        for node in self.find_nodes(self.root, None, None, "product-template"):
            return node
        for node in self.find_nodes(self.root, None, None, None):
            if "product" in node.attributes.get("itemtype", "").lower():
                return node
        return self.root


def _looks_unavailable(node: Node) -> bool:
    data_available = node.attributes.get("data-available")
    if data_available is not None:
        return data_available.strip().lower() == "false"
    if "disabled" in node.attributes:
        return True
    if node.attributes.get("aria-disabled", "").lower() == "true":
        return True
    class_attr = node.attributes.get("class", "").lower()
    return any(keyword in class_attr for keyword in _UNAVAILABLE_CLASS_KEYWORDS)


def _offer_sku(json_product: dict[str, Any]) -> str:
    # Falls back to the first variant offer's SKU (Shopify puts a per-variant
    # SKU on each Offer rather than a top-level Product.sku when using
    # AggregateOffer-style variant listings). The trailing size suffix (e.g.
    # "18981129-36") is stripped to get a single style-level identifier.
    offers = json_product.get("offers", {}) if json_product else {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    sku = str(offers.get("sku", "")) if isinstance(offers, dict) else ""
    return re.sub(r"-\d+$", "", sku)


def _augment_description(description: str, details: dict[str, str]) -> str:
    extra = []
    composition = details.get("composition", "")
    if composition:
        extra.append(f"Composition: {composition}")
    made_in = details.get("made in", "")
    if made_in:
        extra.append(f"Made in: {made_in}")
    season = details.get("season", "")
    if season:
        extra.append(f"Season: {season}")
    if not extra:
        return description
    return " ".join([description, *extra]) if description else " ".join(extra)


def _image_urls(
    parser: _AntoniaHTMLParser,
    metadata: dict[str, str],
    json_product: dict[str, Any],
    base_url: str,
    product_root: Node,
) -> list[str]:
    urls = [metadata.get("og:image", "")]
    image_data = json_product.get("image", []) if json_product else []
    urls.extend(image_data if isinstance(image_data, list) else [image_data])
    # Confirmed live markup: `.product-template` also contains a "recently
    # viewed"/recommendations grid with its own <img> tags for OTHER
    # products, so <img> scanning must be scoped to `.product-gallery`
    # specifically (the current product's own media), not the wider
    # product_root.
    gallery_nodes = _AntoniaHTMLParser.find_nodes(product_root, None, None, "product-gallery")
    scope = gallery_nodes[0] if gallery_nodes else product_root
    for node in _AntoniaHTMLParser.find_nodes(scope, "img", None, None):
        urls.append(node.attributes.get("src", "") or node.attributes.get("data-src", ""))
    usable = [urljoin(base_url, url) for url in urls if url and not url.startswith("data:")]
    return unique_normalized_urls(usable)
