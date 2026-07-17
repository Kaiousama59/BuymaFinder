from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from buymafinder.core.models import Product


PRODUCT_COLUMNS = (
    "shop_code",
    "shop_name",
    "target",
    "category",
    "brand",
    "name",
    "product_url",
    "currency",
    "regular_price",
    "sale_price",
    "sku",
    "color",
    "sizes",
    "description",
    "image_urls",
    "in_stock",
    "collected_at",
    "pricing_status",
    "pricing_error",
    "source_current_price",
    "source_currency",
    "exchange_rate",
    "exchange_rate_safety_margin",
    "adjusted_exchange_rate",
    "purchase_cost_jpy",
    "international_shipping_jpy",
    "estimated_import_cost_jpy",
    "domestic_shipping_jpy",
    "packing_cost_jpy",
    "pre_buyma_cost_jpy",
    "buyma_fee_rate",
    "buyma_fee_jpy",
    "total_estimated_cost_jpy",
    "suggested_listing_price_jpy",
    "expected_profit_jpy",
    "expected_profit_margin",
)


def export_products_csv(products: Iterable[Product], path: Path) -> int:
    """Write normalized products to CSV and return the written row count."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=PRODUCT_COLUMNS)
        writer.writeheader()
        for product in products:
            writer.writerow(_serialize_product(product))
            count += 1
    return count


def _serialize_product(product: Product) -> dict[str, str]:
    pricing = product.pricing
    return {
        "shop_code": _csv_safe(product.shop_code),
        "shop_name": _csv_safe(product.shop_name),
        "target": _csv_safe(product.target),
        "category": _csv_safe(product.category),
        "brand": _csv_safe(product.brand),
        "name": _csv_safe(product.name),
        "product_url": _csv_safe(product.product_url),
        "currency": _csv_safe(product.currency),
        "regular_price": _format_decimal(product.regular_price),
        "sale_price": _format_decimal(product.sale_price),
        "sku": _csv_safe(product.sku),
        "color": _csv_safe(product.color),
        "sizes": json.dumps(
            [{"size": size_stock.size, "in_stock": size_stock.in_stock} for size_stock in product.sizes],
            ensure_ascii=True,
            separators=(",", ":"),
        ),
        "description": _csv_safe(product.description),
        "image_urls": json.dumps(product.image_urls, ensure_ascii=True, separators=(",", ":")),
        "in_stock": "" if product.in_stock is None else str(product.in_stock).lower(),
        "collected_at": product.collected_at.isoformat(),
        "pricing_status": _csv_safe(pricing.pricing_status),
        "pricing_error": _csv_safe(pricing.pricing_error),
        "source_current_price": _format_decimal(pricing.source_current_price),
        "source_currency": _csv_safe(pricing.source_currency),
        "exchange_rate": _format_decimal(pricing.exchange_rate),
        "exchange_rate_safety_margin": _format_decimal(pricing.exchange_rate_safety_margin),
        "adjusted_exchange_rate": _format_decimal(pricing.adjusted_exchange_rate),
        "purchase_cost_jpy": _format_decimal(pricing.purchase_cost_jpy),
        "international_shipping_jpy": _format_decimal(pricing.international_shipping_jpy),
        "estimated_import_cost_jpy": _format_decimal(pricing.estimated_import_cost_jpy),
        "domestic_shipping_jpy": _format_decimal(pricing.domestic_shipping_jpy),
        "packing_cost_jpy": _format_decimal(pricing.packing_cost_jpy),
        "pre_buyma_cost_jpy": _format_decimal(pricing.pre_buyma_cost_jpy),
        "buyma_fee_rate": _format_decimal(pricing.buyma_fee_rate),
        "buyma_fee_jpy": _format_decimal(pricing.buyma_fee_jpy),
        "total_estimated_cost_jpy": _format_decimal(pricing.total_estimated_cost_jpy),
        "suggested_listing_price_jpy": _format_decimal(pricing.suggested_listing_price_jpy),
        "expected_profit_jpy": _format_decimal(pricing.expected_profit_jpy),
        "expected_profit_margin": _format_decimal(pricing.expected_profit_margin),
    }


def _format_decimal(value: Decimal | None) -> str:
    return "" if value is None else format(value, "f")


def _csv_safe(value: str) -> str:
    return f"'{value}" if value.startswith(("=", "+", "-", "@")) else value
