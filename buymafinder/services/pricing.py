from __future__ import annotations

import logging
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Iterable

from buymafinder.core.models import Product
from buymafinder.core.pricing_config import (
    PricingConfigurationError,
    load_pricing_settings,
    normalize_category,
)
from buymafinder.core.pricing_models import PricingResult, PricingSettings


LOGGER = logging.getLogger(__name__)
MAXIMUM_LISTING_ITERATIONS = 10_000


class PricingEngine:
    """Calculate verified BUYMA listing prices from validated shared settings."""

    def __init__(self, settings: PricingSettings, maximum_listing_iterations: int = MAXIMUM_LISTING_ITERATIONS) -> None:
        if maximum_listing_iterations < 1:
            raise ValueError("Maximum listing iterations must be at least one.")
        self.settings = settings
        self.maximum_listing_iterations = maximum_listing_iterations

    def price_product(self, product: Product) -> PricingResult:
        """Return a verified pricing outcome without changing source product values."""
        source_price = product.current_price
        source_currency = product.currency.strip().upper()
        if source_price is None or source_price <= 0:
            return PricingResult.not_priced(
                "missing_source_price", "Product has no usable current price.", source_price, source_currency
            )
        if source_currency not in self.settings.exchange_rates:
            return PricingResult.not_priced(
                "unsupported_currency",
                f"Unsupported source currency: {source_currency or 'empty'}.",
                source_price,
                source_currency,
            )
        import_rate = self.settings.estimated_import_cost_rates.get(normalize_category(product.category))
        if import_rate is None:
            return PricingResult.not_priced(
                "category_rate_required",
                f"Missing estimated import cost rate for category: {product.category}.",
                source_price,
                source_currency,
            )

        exchange_rate = self.settings.exchange_rates[source_currency]
        adjusted_exchange_rate = exchange_rate * (Decimal("1") + self.settings.exchange_rate_safety_margin)
        purchase_cost_jpy = _round_up_jpy(source_price * adjusted_exchange_rate)
        international_shipping_jpy = self.settings.international_shipping_jpy
        taxable_base_jpy = purchase_cost_jpy + international_shipping_jpy
        estimated_import_cost_jpy = _round_up_jpy(taxable_base_jpy * import_rate)
        pre_buyma_cost_jpy = (
            purchase_cost_jpy
            + international_shipping_jpy
            + estimated_import_cost_jpy
            + self.settings.domestic_shipping_jpy
            + self.settings.packing_cost_jpy
        )
        suggested_listing_price_jpy = _round_up_to_increment(
            pre_buyma_cost_jpy
            / (Decimal("1") - self.settings.buyma_fee_rate - self.settings.target_profit_margin),
            self.settings.listing_price_rounding_increment_jpy,
        )

        for _ in range(self.maximum_listing_iterations):
            buyma_fee_jpy = _round_up_jpy(suggested_listing_price_jpy * self.settings.buyma_fee_rate)
            total_estimated_cost_jpy = pre_buyma_cost_jpy + buyma_fee_jpy
            expected_profit_jpy = suggested_listing_price_jpy - total_estimated_cost_jpy
            expected_profit_margin = expected_profit_jpy / suggested_listing_price_jpy
            if expected_profit_margin >= self.settings.target_profit_margin:
                return PricingResult(
                    pricing_status="priced",
                    source_current_price=source_price,
                    source_currency=source_currency,
                    exchange_rate=exchange_rate,
                    exchange_rate_safety_margin=self.settings.exchange_rate_safety_margin,
                    adjusted_exchange_rate=adjusted_exchange_rate,
                    purchase_cost_jpy=purchase_cost_jpy,
                    international_shipping_jpy=international_shipping_jpy,
                    estimated_import_cost_jpy=estimated_import_cost_jpy,
                    domestic_shipping_jpy=self.settings.domestic_shipping_jpy,
                    packing_cost_jpy=self.settings.packing_cost_jpy,
                    pre_buyma_cost_jpy=pre_buyma_cost_jpy,
                    buyma_fee_rate=self.settings.buyma_fee_rate,
                    buyma_fee_jpy=buyma_fee_jpy,
                    total_estimated_cost_jpy=total_estimated_cost_jpy,
                    suggested_listing_price_jpy=suggested_listing_price_jpy,
                    expected_profit_jpy=expected_profit_jpy,
                    expected_profit_margin=expected_profit_margin,
                )
            suggested_listing_price_jpy += self.settings.listing_price_rounding_increment_jpy

        return PricingResult.not_priced(
            "pricing_loop_safety_guard",
            "Pricing did not reach the target margin within the safety limit.",
            source_price,
            source_currency,
        )


def apply_pricing(
    products: Iterable[Product],
    configuration_path: Path,
    source_categories: list[str],
) -> None:
    """Apply pricing to products, or mark every product when configuration is unavailable."""
    products = list(products)
    try:
        settings = load_pricing_settings(configuration_path, source_categories)
    except PricingConfigurationError as error:
        LOGGER.warning("Pricing skipped: %s", error)
        for product in products:
            product.pricing = PricingResult.not_priced(
                "configuration_required", str(error), product.current_price, product.currency.strip().upper()
            )
        return

    engine = PricingEngine(settings)
    for product in products:
        product.pricing = engine.price_product(product)


def _round_up_jpy(value: Decimal) -> Decimal:
    """Round a JPY payable cost upward to the nearest whole yen."""
    return value.to_integral_value(rounding=ROUND_CEILING)


def _round_up_to_increment(value: Decimal, increment: int) -> Decimal:
    """Round a listing price upward to the configured whole-yen increment."""
    increment_decimal = Decimal(increment)
    return (value / increment_decimal).to_integral_value(rounding=ROUND_CEILING) * increment_decimal
