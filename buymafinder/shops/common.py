from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any

from buymafinder.core.models import SizeStock


PRICE_NUMBER = re.compile(r"[-+]?\d[\d.,\s]*")

_VOID_TAGS = {"meta", "link", "img", "input", "br", "hr"}


def normalize_whitespace(value: str) -> str:
    """Collapse whitespace in a user-visible value."""
    return " ".join(value.split())


def first_present(*values: object) -> str:
    """Return the first non-blank value, stringified."""
    return next((str(value) for value in values if value and str(value).strip()), "")


def parse_price(value: str) -> tuple[str, Decimal | None]:
    """Parse a storefront price string into its currency and decimal value."""
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
    elif "." in number:
        # A lone "." with exactly 3 trailing digits is a European thousands
        # separator (e.g. "1.805" meaning 1805), not a decimal point: retail
        # prices are never quoted to sub-cent precision, so this pattern is
        # unambiguous. Confirmed live: a jacket priced "€1.805" (1805 EUR)
        # was otherwise misparsed as 1.805 EUR, undervaluing it ~1000x.
        fraction = number.rsplit(".", 1)[1]
        if len(fraction) == 3:
            number = number.replace(".", "")
    try:
        return currency, Decimal(number)
    except InvalidOperation:
        return currency, None


@dataclass
class Node:
    tag: str
    attributes: dict[str, str]
    parent: "Node | None" = None
    text: list[str] = field(default_factory=list)
    children: list["Node"] = field(default_factory=list)

    def full_text(self) -> str:
        return "".join(self.text + [child.full_text() for child in self.children])


class ShopHTMLParser(HTMLParser):
    """Generic node-tree/link/metadata/JSON-LD collector shared by shop adapters."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("root", {})
        self.stack = [self.root]
        self.links: list[str] = []
        self.metadata: dict[str, str] = {}
        self.scripts: list[str] = []
        self._script_type: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value or "" for key, value in attrs}
        node = Node(tag.lower(), attributes, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag.lower() not in _VOID_TAGS:
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
        root: Node | None = None,
    ) -> str:
        matches = self.find_nodes(root or self.root, tag, itemprop, class_name)
        return normalize_whitespace(matches[0].full_text()) if matches else ""

    def texts(self, class_name: str, root: Node | None = None) -> list[str]:
        return [
            normalize_whitespace(node.full_text())
            for node in self.find_nodes(root or self.root, None, None, class_name)
            if normalize_whitespace(node.full_text())
        ]

    @staticmethod
    def find_nodes(
        node: Node,
        tag: str | None,
        itemprop: str | None,
        class_name: str | None,
    ) -> list[Node]:
        matches = []
        if (
            (tag is None or node.tag == tag)
            and (itemprop is None or node.attributes.get("itemprop", "").lower() == itemprop.lower())
            and (class_name is None or class_name.lower() in node.attributes.get("class", "").lower().split())
        ):
            matches.append(node)
        for child in node.children:
            matches.extend(ShopHTMLParser.find_nodes(child, tag, itemprop, class_name))
        return matches


def has_ancestor_with_class(node: Node, class_name: str) -> bool:
    current = node.parent
    while current is not None:
        if class_name in current.attributes.get("class", "").lower().split():
            return True
        current = current.parent
    return False


def find_product_json_ld(scripts: list[str]) -> dict[str, Any]:
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


def offer_availability(json_product: dict[str, Any]) -> str:
    offers = json_product.get("offers", {}) if json_product else {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    return str(offers.get("availability", "")).lower() if isinstance(offers, dict) else ""


def prices_from_html(
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


def overall_stock(
    metadata: dict[str, str],
    sizes: list[SizeStock],
    product_root_text: str,
    structured_availability: str,
) -> bool | None:
    availability = metadata.get("availability", "").lower()
    if availability:
        return "outofstock" not in availability
    page_text = product_root_text.lower()
    if "out of stock" in page_text or "sold out" in page_text:
        return False
    if sizes:
        return any(size.in_stock for size in sizes)
    if structured_availability:
        return "outofstock" not in structured_availability
    return None
