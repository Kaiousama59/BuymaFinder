from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Any

from buymafinder.core.models import Product, Source
from buymafinder.core.urls import unique_normalized_urls
from buymafinder.shops.base import ShopAdapter


LOGGER = logging.getLogger(__name__)


def collect_products(
    sources: Iterable[Source],
    adapter_factory: Callable[[str], ShopAdapter],
    browser: Any,
    limit: int = 5,
) -> list[Product]:
    """Collect no more than ``limit`` unique products across all sources."""
    if limit < 1:
        raise ValueError("Collection limit must be at least one.")

    products: list[Product] = []
    seen_urls: set[str] = set()
    seen_skus: set[str] = set()

    for source in sources:
        if len(products) >= limit:
            break
        adapter = adapter_factory(source.shop_code)
        try:
            links = unique_normalized_urls(adapter.collect_product_links(source, browser))
        except Exception:
            LOGGER.exception(
                "Collection error: shop=%s category=%s url=%s operation=collect_product_links",
                source.shop_code,
                source.category,
                source.list_url,
            )
            continue

        for product_url in links:
            if len(products) >= limit:
                break
            if product_url in seen_urls:
                continue
            try:
                product = adapter.normalize_product(adapter.collect_product_detail(product_url, source, browser))
            except Exception:
                LOGGER.exception(
                    "Collection error: shop=%s category=%s url=%s operation=collect_product_detail",
                    source.shop_code,
                    source.category,
                    product_url,
                )
                continue

            normalized_url = product.product_url
            normalized_sku = product.sku.casefold().strip()
            if normalized_url in seen_urls or (normalized_sku and normalized_sku in seen_skus):
                continue
            seen_urls.add(normalized_url)
            if normalized_sku:
                seen_skus.add(normalized_sku)
            products.append(product)
    return products
