from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True, slots=True)
class PricingSettings:
    """Validated production settings required for a BUYMA price calculation."""

    exchange_rates: dict[str, Decimal]
    target_profit_margin: Decimal
    exchange_rate_safety_margin: Decimal
    international_shipping_jpy: Decimal
    domestic_shipping_jpy: Decimal
    packing_cost_jpy: Decimal
    buyma_fee_rate: Decimal
    estimated_import_cost_rates: dict[str, Optional[Decimal]]
    listing_price_rounding_increment_jpy: int
    free_international_shipping_threshold_source: Optional[Decimal] = None


@dataclass(frozen=True, slots=True)
class PricingResult:
    """A pricing outcome that preserves absent values as ``None``."""

    pricing_status: str
    pricing_error: str = ""
    source_current_price: Optional[Decimal] = None
    source_currency: str = ""
    exchange_rate: Optional[Decimal] = None
    exchange_rate_safety_margin: Optional[Decimal] = None
    adjusted_exchange_rate: Optional[Decimal] = None
    purchase_cost_jpy: Optional[Decimal] = None
    international_shipping_jpy: Optional[Decimal] = None
    estimated_import_cost_jpy: Optional[Decimal] = None
    domestic_shipping_jpy: Optional[Decimal] = None
    packing_cost_jpy: Optional[Decimal] = None
    pre_buyma_cost_jpy: Optional[Decimal] = None
    buyma_fee_rate: Optional[Decimal] = None
    buyma_fee_jpy: Optional[Decimal] = None
    total_estimated_cost_jpy: Optional[Decimal] = None
    suggested_listing_price_jpy: Optional[Decimal] = None
    expected_profit_jpy: Optional[Decimal] = None
    expected_profit_margin: Optional[Decimal] = None

    @classmethod
    def not_priced(
        cls,
        status: str,
        error: str,
        source_current_price: Optional[Decimal],
        source_currency: str,
    ) -> PricingResult:
        """Create a non-priced result while retaining the product source price."""
        return cls(
            pricing_status=status,
            pricing_error=error,
            source_current_price=source_current_price,
            source_currency=source_currency,
        )
