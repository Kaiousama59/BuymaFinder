import csv
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from buymafinder.core.models import Product, SizeStock
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
