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
