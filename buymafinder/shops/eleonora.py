from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlsplit

from buymafinder.core.debug import save_empty_category_evidence
from buymafinder.core.models import Product, SizeStock, Source
from buymafinder.core.urls import normalize_url, unique_normalized_urls
from buymafinder.shops.base import ShopAdapter


PRODUCT_PATH = re.compile(r"^/en/[^/]+/(?:women|men)/.+/\d+/?$")
PRICE_NUMBER = re.compile(r"[-+]?\d[\d.,\s]*")
_LOGGER = logging.getLogger(__name__)


def normalize_whitespace(value: str) -> str:
    """Collapse whitespace in a user-visible value."""
    return " ".join(value.split())


def parse_price(value: str) -> tuple[str, Decimal | None]:
    """Parse an Eleonora price string into its currency and decimal value."""
    normalized = normalize_whitespace(value)
    currency = "EUR"
    if "£" in normalized or "GBP" in normalized.upper():
        currency = "GBP"
    elif "$" in normalized or "USD" in normalized.upper():
        currency = "USD"
    elif "¥" in normalized or "JPY" in normalized.upper():
        currency = "JPY"
    match = PRICE_NUMBER.search(normalized)
    if match is None:
        return currency, None
    number = match.group().replace(" ", "")
    if "," in number and "." in number:
        number = number.replace(",", "") if number.rfind(".") > number.rfind(",") else number.replace(".", "").replace(",", ".")
    elif "," in number:
        fraction = number.rsplit(",", 1)[1]
        number = number.replace(",", ".") if len(fraction) in {1, 2} else number.replace(",", "")
    try:
        return currency, Decimal(number)
    except InvalidOperation:
        return currency, None


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
) -> Product:
    """Build a normalized shared Product from Eleonora product page HTML."""
    parser = _EleonoraHTMLParser()
    parser.feed(html)
    metadata = parser.metadata
    product_root = parser.primary_product_node()
    product_url = normalize_url(metadata.get("canonical", product_url))
    json_product = _find_product_json_ld(parser.scripts)
    title = _first_present(
        json_product.get("name") if json_product else "",
        parser.first_text(tag="h2", root=product_root),
        metadata.get("og:title", ""),
        parser.first_text(tag="h1", root=product_root),
    )
    brand_value = json_product.get("brand") if json_product else ""
    brand = _first_present(
        brand_value.get("name", "") if isinstance(brand_value, dict) else str(brand_value or ""),
        parser.first_text(tag="h1", root=product_root),
        parser.first_text(itemprop="brand", root=product_root),
        parser.first_text(class_name="brand", root=product_root),
    )
    description = _first_present(
        json_product.get("description") if json_product else "",
        parser.first_text(itemprop="description", root=product_root),
        parser.first_text(class_name="product-details", root=product_root),
        metadata.get("description", ""),
    )
    sku = _first_present(
        str(json_product.get("sku", "")) if json_product else "",
        parser.first_text(tag="h3", root=product_root),
        parser.first_text(itemprop="productID", root=product_root),
        parser.first_text(class_name="sku", root=product_root),
    )
    price_values = parser.price_texts(root=product_root)
    regular_price, sale_price, currency = _prices_from_html(json_product, price_values)
    image_urls = _image_urls(parser, metadata, json_product, product_url, product_root)
    sizes = parser.size_stocks(root=product_root)
    availability = _offer_availability(json_product)
    in_stock = _overall_stock(parser, sizes, product_root, availability)
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
            _first_present(
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


@dataclass
class _Node:
    tag: str
    attributes: dict[str, str]
    parent: _Node | None = None
    text: list[str] = field(default_factory=list)
    children: list[_Node] = field(default_factory=list)

    def full_text(self) -> str:
        return "".join(self.text + [child.full_text() for child in self.children])


class _EleonoraHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {})
        self.stack = [self.root]
        self.links: list[str] = []
        self.metadata: dict[str, str] = {}
        self.scripts: list[str] = []
        self._script_type: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value or "" for key, value in attrs}
        node = _Node(tag.lower(), attributes, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag.lower() not in {"meta", "link", "img", "input", "br", "hr"}:
            self.stack.append(node)
        if tag.lower() == "a" and attributes.get("href"):
            self.links.append(attributes["href"])
        if tag.lower() == "meta":
            key = attributes.get("property") or attributes.get("name") or attributes.get("itemprop")
            if key and attributes.get("content"):
                self.metadata[key.lower()] = attributes["content"]
        if tag.lower() == "link" and attributes.get("rel") == "canonical" and attributes.get("href"):
            self.metadata["canonical"] = attributes["href"]
        if tag.lower() == "script":
            self._script_type = attributes.get("type", "").lower()

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script":
            self._script_type = None
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag.lower():
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        self.stack[-1].text.append(data)
        if self._script_type == "application/ld+json":
            self.scripts.append(data)

    def first_text(
        self,
        tag: str | None = None,
        itemprop: str | None = None,
        class_name: str | None = None,
        root: _Node | None = None,
    ) -> str:
        matches = self._find_nodes(root or self.root, tag, itemprop, class_name)
        return normalize_whitespace(matches[0].full_text()) if matches else ""

    def texts(self, class_name: str, root: _Node | None = None) -> list[str]:
        return [
            normalize_whitespace(node.full_text())
            for node in self._find_nodes(root or self.root, None, None, class_name)
            if normalize_whitespace(node.full_text())
        ]

    def price_texts(self, root: _Node | None = None) -> list[str]:
        values = []
        for node in self._find_nodes(root or self.root, None, None, None):
            if node.tag not in {"del", "ins"} or not _has_ancestor_with_class(node, "product-price"):
                continue
            value = normalize_whitespace(node.full_text())
            if value:
                values.append(value)
        return values or self.texts(class_name="product-price", root=root)

    def size_stocks(self, root: _Node | None = None) -> list[SizeStock]:
        sizes = []
        for option in self._find_nodes(root or self.root, "option", None, None):
            size = normalize_whitespace(option.full_text() or option.attributes.get("value", ""))
            if not size or size.lower() in {"select size", "size", "- select -"}:
                continue
            in_stock = "disabled" not in option.attributes and "sold out" not in size.lower()
            sizes.append(SizeStock(size=size, in_stock=in_stock))
        return sizes

    def primary_product_node(self) -> _Node:
        for node in self._find_nodes(self.root, "div", None, "single-product"):
            classes = node.attributes.get("class", "").split()
            if "shop-quick-view-ajax" not in classes:
                return node
        return self.root

    @staticmethod
    def _find_nodes(
        node: _Node,
        tag: str | None,
        itemprop: str | None,
        class_name: str | None,
    ) -> list[_Node]:
        matches = []
        if (
            (tag is None or node.tag == tag)
            and (itemprop is None or node.attributes.get("itemprop", "").lower() == itemprop.lower())
            and (class_name is None or class_name.lower() in node.attributes.get("class", "").lower().split())
        ):
            matches.append(node)
        for child in node.children:
            matches.extend(_EleonoraHTMLParser._find_nodes(child, tag, itemprop, class_name))
        return matches


def _find_product_json_ld(scripts: list[str]) -> dict[str, Any]:
    for script in scripts:
        try:
            data = json.loads(script)
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for candidate in candidates:
            product_type = candidate.get("@type") if isinstance(candidate, dict) else None
            if isinstance(candidate, dict) and (
                product_type == "Product" or isinstance(product_type, list) and "Product" in product_type
            ):
                return candidate
    return {}


def _prices_from_html(
    json_product: dict[str, Any],
    price_values: list[str],
) -> tuple[Decimal | None, Decimal | None, str]:
    offers = json_product.get("offers", {}) if json_product else {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    parsed = [parse_price(value) for value in price_values]
    amounts = [(currency, amount) for currency, amount in parsed if amount is not None]
    if len(amounts) > 1:
        return amounts[0][1], amounts[-1][1], amounts[-1][0]
    if amounts:
        return amounts[0][1], None, amounts[0][0]
    if isinstance(offers, dict) and offers.get("price") is not None:
        currency = str(offers.get("priceCurrency", "EUR"))
        _, current_price = parse_price(str(offers["price"]))
        return current_price, None, currency
    return None, None, "EUR"


def _image_urls(
    parser: _EleonoraHTMLParser,
    metadata: dict[str, str],
    json_product: dict[str, Any],
    base_url: str,
    product_root: _Node,
) -> list[str]:
    urls = [metadata.get("og:image", "")]
    image_data = json_product.get("image", []) if json_product else []
    urls.extend(image_data if isinstance(image_data, list) else [image_data])
    for node in _EleonoraHTMLParser._find_nodes(product_root, "img", None, None):
        urls.append(node.attributes.get("src", "") or node.attributes.get("data-src", ""))
    usable = [urljoin(base_url, url) for url in urls if url and not url.startswith("data:")]
    return unique_normalized_urls(usable)


def _overall_stock(
    parser: _EleonoraHTMLParser,
    sizes: list[SizeStock],
    product_root: _Node,
    structured_availability: str,
) -> bool | None:
    availability = parser.metadata.get("availability", "").lower()
    if availability:
        return "outofstock" not in availability
    page_text = product_root.full_text().lower()
    if "out of stock" in page_text or "sold out" in page_text:
        return False
    if sizes:
        return any(size.in_stock for size in sizes)
    if structured_availability:
        return "outofstock" not in structured_availability
    return None


def _offer_availability(json_product: dict[str, Any]) -> str:
    offers = json_product.get("offers", {}) if json_product else {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    return str(offers.get("availability", "")).lower() if isinstance(offers, dict) else ""


def _first_present(*values: object) -> str:
    return next((str(value) for value in values if value and str(value).strip()), "")


def _has_ancestor_with_class(node: _Node, class_name: str) -> bool:
    current = node.parent
    while current is not None:
        if class_name in current.attributes.get("class", "").lower().split():
            return True
        current = current.parent
    return False
