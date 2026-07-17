from datetime import datetime
from decimal import Decimal
from pathlib import Path

from buymafinder.core.models import Source
from buymafinder.shops.eleonora import parse_price, parse_product_detail_html, parse_product_links


FIXTURES = Path(__file__).parent / "fixtures"
SOURCE = Source(
    shop_code="eleonora",
    shop_name="Eleonora Bonucci",
    target="women",
    category="Clothing",
    list_url="https://eleonorabonucci.com/en/women/new-collection/clothing/",
)


def test_parse_price_supports_grouped_and_decimal_euro_prices() -> None:
    assert parse_price("€ 1,234.50") == ("EUR", Decimal("1234.50"))
    assert parse_price("EUR 1.234,50") == ("EUR", Decimal("1234.50"))


def test_parse_product_links_returns_unique_product_urls() -> None:
    links = parse_product_links(
        (FIXTURES / "eleonora_category.html").read_text(encoding="utf-8"),
        SOURCE.list_url,
    )

    assert links == [
        "https://eleonorabonucci.com/en/example/women/clothing/dresses/1001",
        "https://eleonorabonucci.com/en/example/women/clothing/dresses/1002",
    ]


def test_parse_product_detail_uses_structured_data_and_dom_fallbacks() -> None:
    product = parse_product_detail_html(
        (FIXTURES / "eleonora_product.html").read_text(encoding="utf-8"),
        "https://eleonorabonucci.com/en/example/women/clothing/dresses/1001",
        SOURCE,
        collected_at=datetime(2026, 1, 2, 3, 4, 5),
    )

    assert product.brand == "Example Brand"
    assert product.name == "Example Dress"
    assert product.regular_price == Decimal("2000.00")
    assert product.sale_price == Decimal("1800.00")
    assert product.sku == "EX-1001"
    assert product.color == "Midnight Blue"
    assert [(size.size, size.in_stock) for size in product.sizes] == [("38", True), ("40", False)]
    assert product.in_stock is True
    assert len(product.image_urls) == 2


def test_parse_product_detail_uses_structured_stock_without_sizes() -> None:
    html = (FIXTURES / "eleonora_product.html").read_text(encoding="utf-8")
    html = html.replace(
        '<select id="size">\n      <option>Select size</option>\n      <option>- select -</option>\n      <option value="38"> 38 </option>\n      <option value="40" disabled>40</option>\n    </select>',
        "",
    )

    product = parse_product_detail_html(html, "https://eleonorabonucci.com/en/example/women/clothing/dresses/1001", SOURCE)

    assert product.sizes == []
    assert product.in_stock is True
