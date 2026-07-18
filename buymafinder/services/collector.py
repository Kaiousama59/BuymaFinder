from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Any, Optional, Protocol

from buymafinder.core.models import Product, Source
from buymafinder.core.urls import unique_normalized_urls
from buymafinder.shops.base import ShopAdapter


LOGGER = logging.getLogger(__name__)


class ScanState(Protocol):
    """Minimal contract the collector needs for resumable scans."""

    def should_skip(self, source: Source) -> bool: ...

    def record_success(self, source: Source, product_count: int) -> None: ...

    def record_failure(self, source: Source, error: str) -> None: ...


def collect_products(
    sources: Iterable[Source],
    adapter_factory: Callable[[str], ShopAdapter],
    browser: Any,
    limit: int = 5,
    per_source_limit: Optional[int] = None,
    scan_state: Optional[ScanState] = None,
    product_sink: Optional[Callable[[Product], None]] = None,
    initial_products: Optional[Iterable[Product]] = None,
) -> list[Product]:
    """Collect no more than ``limit`` unique products across all sources.

    ``per_source_limit`` caps how many products a single source may contribute.
    ``scan_state`` records per-source outcomes and skips sources already
    completed in a resumed run. ``product_sink`` is called once per newly
    collected product so callers can persist progress. ``initial_products``
    are products restored from an interrupted run; they seed deduplication,
    count toward ``limit``, and are included in the returned list.
    """
    if limit < 1:
        raise ValueError("Collection limit must be at least one.")
    if per_source_limit is not None and per_source_limit < 1:
        raise ValueError("Per-source limit must be at least one when set.")

    products: list[Product] = list(initial_products or [])
    seen_urls: set[str] = {product.product_url for product in products}
    seen_skus: set[str] = {
        product.sku.casefold().strip() for product in products if product.sku.strip()
    }
    if products:
        LOGGER.info("Restored %d previously collected products.", len(products))

    total_success = len(products)
    total_failure = 0

    for source in sources:
        if len(products) >= limit:
            break
        if scan_state is not None and scan_state.should_skip(source):
            LOGGER.info(
                "Skipping completed source: shop=%s category=%s url=%s",
                source.shop_code,
                source.category,
                source.list_url,
            )
            continue
        adapter = adapter_factory(source.shop_code)
        try:
            links = unique_normalized_urls(adapter.collect_product_links(source, browser))
        except Exception as error:
            LOGGER.exception(
                "Collection error: shop=%s category=%s url=%s operation=collect_product_links",
                source.shop_code,
                source.category,
                source.list_url,
            )
            if scan_state is not None:
                scan_state.record_failure(source, f"collect_product_links: {error}")
            continue

        LOGGER.info(
            "Source %s/%s: %d candidate product links found.",
            source.shop_code,
            source.category,
            len(links),
        )
        source_product_count = 0
        source_failed_details = 0
        for link_number, product_url in enumerate(links, start=1):
            if len(products) >= limit:
                break
            if per_source_limit is not None and source_product_count >= per_source_limit:
                break
            if product_url in seen_urls:
                continue
            LOGGER.info(
                "Collecting product %d/%d in %s (total collected %d/%d): %s",
                link_number,
                len(links),
                source.category,
                len(products),
                limit,
                product_url,
            )
            try:
                product = adapter.normalize_product(adapter.collect_product_detail(product_url, source, browser))
            except Exception:
                LOGGER.exception(
                    "Collection error: shop=%s category=%s url=%s operation=collect_product_detail",
                    source.shop_code,
                    source.category,
                    product_url,
                )
                source_failed_details += 1
                total_failure += 1
                continue

            normalized_url = product.product_url
            normalized_sku = product.sku.casefold().strip()
            if normalized_url in seen_urls or (normalized_sku and normalized_sku in seen_skus):
                continue
            seen_urls.add(normalized_url)
            if normalized_sku:
                seen_skus.add(normalized_sku)
            products.append(product)
            source_product_count += 1
            total_success += 1
            if product_sink is not None:
                product_sink(product)

        LOGGER.info(
            "Source %s/%s finished: %d collected, %d failed.",
            source.shop_code,
            source.category,
            source_product_count,
            source_failed_details,
        )
        if scan_state is not None:
            if source_product_count == 0 and source_failed_details > 0:
                scan_state.record_failure(
                    source,
                    f"collect_product_detail failed for all {source_failed_details} attempted products",
                )
            else:
                scan_state.record_success(source, source_product_count)

    LOGGER.info("Collection finished: %d products collected, %d failures.", total_success, total_failure)
    return products
