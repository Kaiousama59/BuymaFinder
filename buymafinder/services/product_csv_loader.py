from __future__ import annotations

import csv
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from buymafinder.core.models import Product, SizeStock
from buymafinder.core.urls import normalize_url


def load_product_by_url(path: Path, product_url: str) -> Product:
    wanted = normalize_url(product_url)
    with path.open(newline="", encoding="utf-8") as input_file:
        for row in csv.DictReader(input_file):
            if normalize_url(row["product_url"].lstrip("'")) == wanted:
                return _product_from_row(row)
    raise ValueError(f"Product URL was not found in {path}: {product_url}")


def _product_from_row(row: dict[str, str]) -> Product:
    sizes = json.loads(row.get("sizes") or "[]")
    return Product(
        shop_code=row["shop_code"].lstrip("'"),
        shop_name=row["shop_name"].lstrip("'"),
        target=row["target"].lstrip("'"),
        category=row["category"].lstrip("'"),
        brand=row["brand"].lstrip("'"),
        name=row["name"].lstrip("'"),
        product_url=row["product_url"].lstrip("'"),
        currency=row["currency"].lstrip("'"),
        regular_price=_decimal(row.get("regular_price")),
        sale_price=_decimal(row.get("sale_price")),
        sku=row.get("sku", "").lstrip("'"),
        color=row.get("color", "").lstrip("'"),
        sizes=[SizeStock(str(item["size"]), item.get("in_stock")) for item in sizes],
        description=row.get("description", "").lstrip("'"),
        image_urls=list(json.loads(row.get("image_urls") or "[]")),
        in_stock=_boolean(row.get("in_stock")),
        collected_at=datetime.fromisoformat(row["collected_at"]),
    )


def _decimal(value: str | None) -> Decimal | None:
    return Decimal(value) if value else None


def _boolean(value: str | None) -> bool | None:
    return None if not value else value.lower() == "true"
