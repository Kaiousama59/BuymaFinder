from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path

from buymafinder.core.candidate_models import CandidateSettings, ListingCandidate
from buymafinder.core.models import Product
from buymafinder.core.urls import normalize_url


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
    "purchase_cost_jpy",
    "international_shipping_jpy",
    "domestic_shipping_jpy",
    "packing_cost_jpy",
    "buyma_fee_jpy",
    "total_estimated_cost_jpy",
    "suggested_listing_price_jpy",
    "expected_profit_jpy",
    "expected_profit_margin",
    "product_url",
)


def select_listing_candidates(
    products: list[Product],
    settings: CandidateSettings,
    *,
    excluded_identities: set[tuple[str, str]] | None = None,
) -> list[ListingCandidate]:
    brand_priority = {brand.casefold(): index + 1 for index, brand in enumerate(settings.preferred_brands)}
    allowed_categories = (
        None if settings.allowed_categories is None else {item.strip().casefold() for item in settings.allowed_categories}
    )
    excluded = excluded_identities or set()
    candidates: list[ListingCandidate] = []
    seen: set[tuple[str, str]] = set()
    for product in products:
        priority = brand_priority.get(product.brand.casefold())
        price = product.current_price
        available_sizes = [item.size for item in product.sizes if item.in_stock]
        identity = product_identity(product.product_url, product.sku)
        pricing = product.pricing
        if (
            priority is None
            or (allowed_categories is not None and product.category.strip().casefold() not in allowed_categories)
            or product.in_stock is False
            or price is None
            or identity in seen
            or identity in excluded
            or pricing.pricing_status != "priced"
            or pricing.expected_profit_margin is None
            or pricing.expected_profit_margin < settings.minimum_profit_margin
        ):
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
                purchase_cost_jpy=pricing.purchase_cost_jpy,
                international_shipping_jpy=pricing.international_shipping_jpy,
                domestic_shipping_jpy=pricing.domestic_shipping_jpy,
                packing_cost_jpy=pricing.packing_cost_jpy,
                buyma_fee_jpy=pricing.buyma_fee_jpy,
                total_estimated_cost_jpy=pricing.total_estimated_cost_jpy,
                suggested_listing_price_jpy=pricing.suggested_listing_price_jpy,
                expected_profit_jpy=pricing.expected_profit_jpy,
                expected_profit_margin=pricing.expected_profit_margin,
            )
        )
    candidates.sort(key=lambda item: (item.brand_priority, -item.completeness_score, item.source_price, item.sku))
    return candidates[: settings.max_candidates]


def product_identity(product_url: str, sku: str) -> tuple[str, str]:
    return normalize_url(product_url), sku.strip().casefold()


def load_existing_listing_identities(root: Path) -> set[tuple[str, str]]:
    if not root.exists():
        return set()
    if not root.is_dir():
        raise ValueError(f"Listing package root is not a directory: {root}")
    identities: set[tuple[str, str]] = set()
    for path in root.rglob("listing_data.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            identities.add(product_identity(str(payload["source_url"]), str(payload.get("sku", ""))))
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise ValueError(f"Invalid existing listing package: {path}") from error
    return identities


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
                    "selection_status": "price_verified_review_required",
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
                    "purchase_cost_jpy": format(candidate.purchase_cost_jpy, "f"),
                    "international_shipping_jpy": format(candidate.international_shipping_jpy, "f"),
                    "domestic_shipping_jpy": format(candidate.domestic_shipping_jpy, "f"),
                    "packing_cost_jpy": format(candidate.packing_cost_jpy, "f"),
                    "buyma_fee_jpy": format(candidate.buyma_fee_jpy, "f"),
                    "total_estimated_cost_jpy": format(candidate.total_estimated_cost_jpy, "f"),
                    "suggested_listing_price_jpy": format(candidate.suggested_listing_price_jpy, "f"),
                    "expected_profit_jpy": format(candidate.expected_profit_jpy, "f"),
                    "expected_profit_margin": format(candidate.expected_profit_margin, ".4%"),
                    "product_url": candidate.product_url,
                }
            )
    temporary.replace(path)
