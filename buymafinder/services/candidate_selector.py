from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from buymafinder.core.candidate_models import CandidateSettings, ListingCandidate
from buymafinder.core.models import Product


CANDIDATE_FIELDS = (
    "approved",
    "selection_status",
    "brand_priority",
    "completeness_score",
    "brand",
    "name",
    "sku",
    "category",
    "source_price",
    "currency",
    "available_sizes",
    "image_count",
    "product_url",
)


def select_listing_candidates(products: list[Product], settings: CandidateSettings) -> list[ListingCandidate]:
    brand_priority = {brand.casefold(): index + 1 for index, brand in enumerate(settings.preferred_brands)}
    candidates: list[ListingCandidate] = []
    seen: set[tuple[str, str]] = set()
    for product in products:
        priority = brand_priority.get(product.brand.casefold())
        price = product.current_price
        available_sizes = [item.size for item in product.sizes if item.in_stock]
        identity = (product.product_url, product.sku)
        if priority is None or product.in_stock is False or price is None or identity in seen:
            continue
        if settings.maximum_source_price is not None and price > settings.maximum_source_price:
            continue
        if len(product.image_urls) < settings.minimum_images:
            continue
        if settings.require_description and not product.description.strip():
            continue
        if settings.require_sizes and not available_sizes:
            continue
        seen.add(identity)
        completeness = (
            min(len(product.image_urls), 5)
            + (2 if product.description.strip() else 0)
            + (2 if available_sizes else 0)
            + (1 if product.color.strip() else 0)
            + (1 if product.sku.strip() else 0)
        )
        candidates.append(
            ListingCandidate(
                brand_priority=priority,
                completeness_score=completeness,
                brand=product.brand,
                name=product.name,
                sku=product.sku,
                category=product.category,
                source_price=price,
                currency=product.currency,
                available_sizes=available_sizes,
                image_count=len(product.image_urls),
                product_url=product.product_url,
            )
        )
    candidates.sort(key=lambda item: (item.brand_priority, -item.completeness_score, item.source_price, item.sku))
    return candidates[: settings.max_candidates]


def export_listing_candidates(candidates: list[ListingCandidate], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8-sig") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=CANDIDATE_FIELDS)
        writer.writeheader()
        for candidate in candidates:
            writer.writerow(
                {
                    "approved": "",
                    "selection_status": "review_required",
                    "brand_priority": candidate.brand_priority,
                    "completeness_score": candidate.completeness_score,
                    "brand": candidate.brand,
                    "name": candidate.name,
                    "sku": candidate.sku,
                    "category": candidate.category,
                    "source_price": format(candidate.source_price, "f"),
                    "currency": candidate.currency,
                    "available_sizes": " | ".join(candidate.available_sizes),
                    "image_count": candidate.image_count,
                    "product_url": candidate.product_url,
                }
            )
    temporary.replace(path)
