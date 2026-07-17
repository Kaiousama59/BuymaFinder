from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from buymafinder.core.pricing_models import PricingSettings


SUPPORTED_CURRENCIES = frozenset({"EUR"})
REQUIRED_GLOBAL_KEYS = (
    "exchange_rates.EUR",
    "target_profit_margin",
    "exchange_rate_safety_margin",
    "international_shipping_jpy",
    "domestic_shipping_jpy",
    "packing_cost_jpy",
    "buyma_fee_rate",
    "estimated_import_cost_rates",
    "listing_price_rounding_increment_jpy",
)


class PricingConfigurationError(ValueError):
    """Raised when production pricing settings are missing or invalid."""

    def __init__(self, missing_keys: list[str] | None = None, invalid_keys: list[str] | None = None) -> None:
        self.missing_keys = tuple(missing_keys or [])
        self.invalid_keys = tuple(invalid_keys or [])
        messages = []
        if self.missing_keys:
            messages.append(f"missing configuration keys: {', '.join(self.missing_keys)}")
        if self.invalid_keys:
            messages.append(f"invalid configuration keys: {', '.join(self.invalid_keys)}")
        super().__init__("; ".join(messages))


def load_pricing_settings(path: Path, source_categories: list[str]) -> PricingSettings:
    """Load and validate explicit production pricing settings from JSON."""
    if not path.exists():
        raise PricingConfigurationError(missing_keys=list(REQUIRED_GLOBAL_KEYS))
    try:
        raw_settings = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    except (OSError, json.JSONDecodeError) as error:
        raise PricingConfigurationError(invalid_keys=["pricing.json"]) from error
    if not isinstance(raw_settings, dict):
        raise PricingConfigurationError(invalid_keys=["pricing.json"])
    return _validate_settings(raw_settings, source_categories)


def _validate_settings(raw_settings: dict[str, Any], source_categories: list[str]) -> PricingSettings:
    missing_keys: list[str] = []
    invalid_keys: list[str] = []
    exchange_rates = _currency_rates(raw_settings, missing_keys, invalid_keys)
    target_profit_margin = _percentage(raw_settings, "target_profit_margin", missing_keys, invalid_keys)
    exchange_rate_safety_margin = _percentage(
        raw_settings, "exchange_rate_safety_margin", missing_keys, invalid_keys
    )
    international_shipping_jpy = _non_negative_amount(
        raw_settings, "international_shipping_jpy", missing_keys, invalid_keys
    )
    domestic_shipping_jpy = _non_negative_amount(raw_settings, "domestic_shipping_jpy", missing_keys, invalid_keys)
    packing_cost_jpy = _non_negative_amount(raw_settings, "packing_cost_jpy", missing_keys, invalid_keys)
    buyma_fee_rate = _percentage(raw_settings, "buyma_fee_rate", missing_keys, invalid_keys)
    import_cost_rates = _category_rates(raw_settings, source_categories, missing_keys, invalid_keys)
    rounding_increment = _rounding_increment(raw_settings, missing_keys, invalid_keys)

    if target_profit_margin is not None and buyma_fee_rate is not None:
        if buyma_fee_rate + target_profit_margin >= Decimal("1"):
            invalid_keys.append("buyma_fee_rate + target_profit_margin")
    if missing_keys or invalid_keys:
        raise PricingConfigurationError(missing_keys, invalid_keys)

    return PricingSettings(
        exchange_rates=exchange_rates,
        target_profit_margin=target_profit_margin,
        exchange_rate_safety_margin=exchange_rate_safety_margin,
        international_shipping_jpy=international_shipping_jpy,
        domestic_shipping_jpy=domestic_shipping_jpy,
        packing_cost_jpy=packing_cost_jpy,
        buyma_fee_rate=buyma_fee_rate,
        estimated_import_cost_rates=import_cost_rates,
        listing_price_rounding_increment_jpy=rounding_increment,
    )


def _currency_rates(
    raw_settings: dict[str, Any], missing_keys: list[str], invalid_keys: list[str]
) -> dict[str, Decimal]:
    raw_rates = raw_settings.get("exchange_rates")
    if not isinstance(raw_rates, dict):
        missing_keys.append("exchange_rates.EUR")
        return {}
    rates: dict[str, Decimal] = {}
    for currency, value in raw_rates.items():
        if currency not in SUPPORTED_CURRENCIES:
            invalid_keys.append(f"exchange_rates.{currency}")
            continue
        rate = _decimal(value)
        if value is None:
            missing_keys.append(f"exchange_rates.{currency}")
        elif rate is None:
            invalid_keys.append(f"exchange_rates.{currency}")
        elif rate <= 0:
            invalid_keys.append(f"exchange_rates.{currency}")
        else:
            rates[currency] = rate
    for currency in SUPPORTED_CURRENCIES:
        if currency not in raw_rates:
            missing_keys.append(f"exchange_rates.{currency}")
    return rates


def _percentage(
    raw_settings: dict[str, Any], key: str, missing_keys: list[str], invalid_keys: list[str]
) -> Decimal | None:
    value = _decimal(raw_settings.get(key))
    if key not in raw_settings or raw_settings[key] is None:
        missing_keys.append(key)
    elif value is None:
        invalid_keys.append(key)
    elif value < 0 or value >= 1:
        invalid_keys.append(key)
    return value


def _non_negative_amount(
    raw_settings: dict[str, Any], key: str, missing_keys: list[str], invalid_keys: list[str]
) -> Decimal | None:
    value = _decimal(raw_settings.get(key))
    if key not in raw_settings or raw_settings[key] is None:
        missing_keys.append(key)
    elif value is None:
        invalid_keys.append(key)
    elif value < 0:
        invalid_keys.append(key)
    return value


def _category_rates(
    raw_settings: dict[str, Any],
    source_categories: list[str],
    missing_keys: list[str],
    invalid_keys: list[str],
) -> dict[str, Decimal | None]:
    raw_rates = raw_settings.get("estimated_import_cost_rates")
    if not isinstance(raw_rates, dict):
        missing_keys.append("estimated_import_cost_rates")
        return {}
    normalized_categories = {normalize_category(category): category for category in source_categories}
    rates: dict[str, Decimal | None] = {}
    for category, value in raw_rates.items():
        normalized_category = normalize_category(category)
        if normalized_category not in normalized_categories:
            invalid_keys.append(f"estimated_import_cost_rates.{category}")
            continue
        if normalized_category in rates:
            invalid_keys.append(f"estimated_import_cost_rates.{category}")
            continue
        rate = _decimal(value)
        if value is not None and (rate is None or rate < 0 or rate >= 1):
            invalid_keys.append(f"estimated_import_cost_rates.{category}")
            continue
        rates[normalized_category] = rate
    return rates


def _rounding_increment(
    raw_settings: dict[str, Any], missing_keys: list[str], invalid_keys: list[str]
) -> int | None:
    value = raw_settings.get("listing_price_rounding_increment_jpy")
    if value is None:
        missing_keys.append("listing_price_rounding_increment_jpy")
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        invalid_keys.append("listing_price_rounding_increment_jpy")
        return None
    return value


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return decimal_value if decimal_value.is_finite() else None


def normalize_category(category: str) -> str:
    """Normalize category names for configuration matching."""
    return " ".join(category.split()).casefold()
