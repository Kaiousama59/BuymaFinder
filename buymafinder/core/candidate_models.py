from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class CandidateSettings:
    preferred_brands: list[str]
    max_candidates: int = 20
    minimum_images: int = 2
    require_description: bool = True
    require_sizes: bool = True
    maximum_source_price: Decimal | None = None
    minimum_profit_margin: Decimal = Decimal("0.10")
    allowed_categories: list[str] | None = None

    def __post_init__(self) -> None:
        normalized = [brand.strip().casefold() for brand in self.preferred_brands]
        if not normalized or any(not brand for brand in normalized):
            raise ValueError("preferred_brands must contain at least one non-empty brand")
        if len(normalized) != len(set(normalized)):
            raise ValueError("preferred_brands must not contain duplicates")
        if self.max_candidates <= 0 or self.minimum_images <= 0:
            raise ValueError("candidate limits must be greater than zero")
        if self.maximum_source_price is not None and self.maximum_source_price <= 0:
            raise ValueError("maximum_source_price must be greater than zero")
        if self.minimum_profit_margin < 0 or self.minimum_profit_margin >= 1:
            raise ValueError("minimum_profit_margin must be between zero and one")
        if self.allowed_categories is not None and any(not category.strip() for category in self.allowed_categories):
            raise ValueError("allowed_categories must contain non-empty values")


@dataclass(frozen=True)
class ListingCandidate:
    brand_priority: int
    completeness_score: int
    brand: str
    name: str
    sku: str
    category: str
    source_price: Decimal
    currency: str
    available_sizes: list[str]
    image_count: int
    product_url: str
    purchase_cost_jpy: Decimal
    international_shipping_jpy: Decimal
    domestic_shipping_jpy: Decimal
    packing_cost_jpy: Decimal
    buyma_fee_jpy: Decimal
    total_estimated_cost_jpy: Decimal
    suggested_listing_price_jpy: Decimal
    expected_profit_jpy: Decimal
    expected_profit_margin: Decimal
