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
    has_ancestor_with_class,
    normalize_whitespace,
    offer_availability,
    overall_stock,
    parse_price,  # noqa: F401 - re-exported for callers that import it from this module
    prices_from_html,
)


PRODUCT_PATH = re.compile(r"^/en/[^/]+/(?:women|men)/.+/\d+/?$")
_LOGGER = logging.getLogger(__name__)


def parse_product_links(html: str, base_url: str) -> list[str]:
    """Extract and normalize Eleonora product links from category HTML."""
    parser = _EleonoraHTMLParser()
    parser.feed(html)
    return unique_normalized_urls(
        urljoin(base_url, href)
        for href in parser.links
        if PRODUCT_PATH.match(href.split("?", 1)[0])
    )


def parse_next_page_url(html: str, current_url: str) -> str | None:
    """Return the absolute URL of the next category page, or ``None`` on the last page.

    Eleonora paginates category listings with ``?start=N`` links that also carry a
    ``f=`` filter-state parameter. The next-page link is returned verbatim so the
    filter state is preserved during navigation.
    """
    current = urlsplit(current_url)
    current_query = dict(parse_qsl(current.query, keep_blank_values=True))
    try:
        current_start = int(current_query.get("start", "1"))
    except ValueError:
        current_start = 1

    parser = _EleonoraHTMLParser()
    parser.feed(html)
    for href in parser.links:
        absolute = urljoin(current_url, href)
        candidate = urlsplit(absolute)
        if candidate.path.rstrip("/") != current.path.rstrip("/"):
            continue
        candidate_query = dict(parse_qsl(candidate.query, keep_blank_values=True))
        try:
            candidate_start = int(candidate_query.get("start", ""))
        except ValueError:
            continue
        if candidate_start == current_start + 1:
            return absolute
    return None


def parse_product_detail_html(
    html: str,
    product_url: str,
    source: Source,
    collected_at: datetime | None = None,
    *,
    target_sku: str | None = None,
) -> Product:
    """Build a normalized shared Product from Eleonora product page HTML.

    ``target_sku`` is accepted for interface parity with shops that expose
    multiple priced variants per URL (e.g. flannels.com); eleonorabonucci.com
    has one product per URL, so it's unused here.
    """
    del target_sku
    parser = _EleonoraHTMLParser()
    parser.feed(html)
    metadata = parser.metadata
    product_root = parser.primary_product_node()
    product_url = normalize_url(metadata.get("canonical", product_url))
    json_product = find_product_json_ld(parser.scripts)
    title = first_present(
        json_product.get("name") if json_product else "",
        parser.first_text(tag="h2", root=product_root),
        metadata.get("og:title", ""),
        parser.first_text(tag="h1", root=product_root),
    )
    brand_value = json_product.get("brand") if json_product else ""
    brand = first_present(
        brand_value.get("name", "") if isinstance(brand_value, dict) else str(brand_value or ""),
        parser.first_text(tag="h1", root=product_root),
        parser.first_text(itemprop="brand", root=product_root),
        parser.first_text(class_name="brand", root=product_root),
    )
    description = first_present(
        json_product.get("description") if json_product else "",
        parser.first_text(itemprop="description", root=product_root),
        parser.first_text(class_name="product-details", root=product_root),
        metadata.get("description", ""),
    )
    sku = first_present(
        str(json_product.get("sku", "")) if json_product else "",
        parser.first_text(tag="h3", root=product_root),
        parser.first_text(itemprop="productID", root=product_root),
        parser.first_text(class_name="sku", root=product_root),
    )
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
        sku=normalize_whitespace(re.sub(r"^(?:SKU:|Style ID\s*)", "", sku, flags=re.IGNORECASE)),
        color=normalize_whitespace(
            first_present(
                parser.first_text(itemprop="color", root=product_root),
                parser.first_text(class_name="color", root=product_root),
            )
        ),
        sizes=sizes,
        description=normalize_whitespace(description),
        image_urls=image_urls,
        in_stock=in_stock,
        collected_at=collected_at or datetime.now(),
    )


class EleonoraAdapter(ShopAdapter):
    """Collect Eleonora Bonucci products with one shared browser context."""

    code = "eleonora"

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
                    page.locator(".product.sf-dress").first.wait_for(state="attached", timeout=20_000)
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
        for attempt in range(1, EleonoraAdapter.NAVIGATION_ATTEMPTS + 1):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=EleonoraAdapter.NAVIGATION_TIMEOUT_MS)
                page.wait_for_load_state("domcontentloaded")
                EleonoraAdapter._dismiss_privacy_dialog(page)
                return
            except Exception as error:
                last_error = error
                if attempt < EleonoraAdapter.NAVIGATION_ATTEMPTS:
                    _LOGGER.warning(
                        "Navigation attempt %d/%d failed, retrying: %s",
                        attempt,
                        EleonoraAdapter.NAVIGATION_ATTEMPTS,
                        url,
                    )
        assert last_error is not None
        raise last_error

    @staticmethod
    def _dismiss_privacy_dialog(page: Any) -> None:
        for selector in (
            "#iubenda-cs-accept-btn",
            "button:has-text('Accept')",
            "button:has-text('Allow all')",
        ):
            button = page.locator(selector)
            if button.count():
                try:
                    button.first.click(timeout=2_000)
                except Exception:
                    continue
                return


class _EleonoraHTMLParser(ShopHTMLParser):
    def price_texts(self, root: Node | None = None) -> list[str]:
        values = []
        for node in self.find_nodes(root or self.root, None, None, None):
            if node.tag not in {"del", "ins"} or not has_ancestor_with_class(node, "product-price"):
                continue
            value = normalize_whitespace(node.full_text())
            if value:
                values.append(value)
        return values or self.texts(class_name="product-price", root=root)

    def size_stocks(self, root: Node | None = None) -> list[SizeStock]:
        sizes = []
        for option in self.find_nodes(root or self.root, "option", None, None):
            size = normalize_whitespace(option.full_text() or option.attributes.get("value", ""))
            if not size or size.lower() in {"select size", "size", "- select -"}:
                continue
            in_stock = "disabled" not in option.attributes and "sold out" not in size.lower()
            sizes.append(SizeStock(size=size, in_stock=in_stock))
        return sizes

    def primary_product_node(self) -> Node:
        for node in self.find_nodes(self.root, "div", None, "single-product"):
            classes = node.attributes.get("class", "").split()
            if "shop-quick-view-ajax" not in classes:
                return node
        return self.root


def _image_urls(
    parser: _EleonoraHTMLParser,
    metadata: dict[str, str],
    json_product: dict[str, Any],
    base_url: str,
    product_root: Node,
) -> list[str]:
    urls = [metadata.get("og:image", "")]
    image_data = json_product.get("image", []) if json_product else []
    urls.extend(image_data if isinstance(image_data, list) else [image_data])
    for node in _EleonoraHTMLParser.find_nodes(product_root, "img", None, None):
        urls.append(node.attributes.get("src", "") or node.attributes.get("data-src", ""))
    usable = [urljoin(base_url, url) for url in urls if url and not url.startswith("data:")]
    return unique_normalized_urls(usable)
