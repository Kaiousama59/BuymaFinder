from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

from buymafinder.core.models import Product, SizeStock


def product_to_json(product: Product) -> str:
    """Serialize a collected product to JSON, excluding the pricing result.

    Pricing is recalculated at export time, so persisted products only contain
    source data.
    """
    return json.dumps(
        {
            "shop_code": product.shop_code,
            "shop_name": product.shop_name,
            "target": product.target,
            "category": product.category,
            "brand": product.brand,
            "name": product.name,
            "product_url": product.product_url,
            "currency": product.currency,
            "regular_price": None if product.regular_price is None else str(product.regular_price),
            "sale_price": None if product.sale_price is None else str(product.sale_price),
            "sku": product.sku,
            "color": product.color,
            "sizes": [{"size": size.size, "in_stock": size.in_stock} for size in product.sizes],
            "description": product.description,
            "image_urls": product.image_urls,
            "in_stock": product.in_stock,
            "collected_at": product.collected_at.isoformat(),
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )


def product_from_json(payload: str) -> Product:
    """Rebuild a product from its persisted JSON payload."""
    data = json.loads(payload)
    return Product(
        shop_code=data["shop_code"],
        shop_name=data["shop_name"],
        target=data["target"],
        category=data["category"],
        brand=data["brand"],
        name=data["name"],
        product_url=data["product_url"],
        currency=data["currency"],
        regular_price=None if data["regular_price"] is None else Decimal(data["regular_price"]),
        sale_price=None if data["sale_price"] is None else Decimal(data["sale_price"]),
        sku=data["sku"],
        color=data["color"],
        sizes=[SizeStock(size=item["size"], in_stock=item["in_stock"]) for item in data["sizes"]],
        description=data["description"],
        image_urls=list(data["image_urls"]),
        in_stock=data["in_stock"],
        collected_at=datetime.fromisoformat(data["collected_at"]),
    )
