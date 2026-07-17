import csv
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from buymafinder.core.models import Product, SizeStock
from buymafinder.core.pricing_models import PricingResult
from buymafinder.services.csv_exporter import export_products_csv


def test_export_products_csv_serializes_lists_as_stable_json(tmp_path: Path) -> None:
    output = tmp_path / "products.csv"
    product = Product(
        shop_code="eleonora",
        shop_name="Eleonora Bonucci",
        target="women",
        category="Bags",
        brand="Example",
        name="Example Bag",
        product_url="https://example.test/product",
        currency="EUR",
        regular_price=Decimal("1000.00"),
        sizes=[SizeStock("One Size", True)],
        image_urls=["https://example.test/image"],
        in_stock=True,
        collected_at=datetime(2026, 1, 2, 3, 4, 5),
    )

    assert export_products_csv([product], output) == 1
    with output.open(newline="", encoding="utf-8") as output_file:
        row = next(csv.DictReader(output_file))
    assert json.loads(row["sizes"]) == [{"size": "One Size", "in_stock": True}]
    assert json.loads(row["image_urls"]) == ["https://example.test/image"]
    assert row["regular_price"] == "1000.00"
    assert "pricing_status" in row
    assert "suggested_listing_price_jpy" in row


def test_export_products_csv_escapes_spreadsheet_formulas(tmp_path: Path) -> None:
    output = tmp_path / "products.csv"
    product = Product(
        shop_code="eleonora",
        shop_name="Eleonora Bonucci",
        target="women",
        category="Bags",
        brand="Example",
        name="=HYPERLINK(\"https://example.test\")",
        product_url="https://example.test/product",
        currency="EUR",
    )

    export_products_csv([product], output)

    with output.open(newline="", encoding="utf-8") as output_file:
        row = next(csv.DictReader(output_file))
    assert row["name"] == "'=HYPERLINK(\"https://example.test\")"


def test_export_products_csv_serializes_pricing_and_keeps_formula_protection(tmp_path: Path) -> None:
    output = tmp_path / "products.csv"
    product = Product(
        shop_code="eleonora",
        shop_name="Eleonora Bonucci",
        target="women",
        category="Bags",
        brand="Example",
        name="Example Bag",
        product_url="https://example.test/product",
        currency="EUR",
        pricing=PricingResult(
            pricing_status="priced",
            pricing_error="=unsafe",
            source_current_price=Decimal("10.50"),
            suggested_listing_price_jpy=Decimal("2000"),
            expected_profit_margin=Decimal("0.125"),
        ),
    )

    export_products_csv([product], output)

    with output.open(newline="", encoding="utf-8") as output_file:
        row = next(csv.DictReader(output_file))
    assert row["pricing_status"] == "priced"
    assert row["pricing_error"] == "'=unsafe"
    assert row["source_current_price"] == "10.50"
    assert row["suggested_listing_price_jpy"] == "2000"
