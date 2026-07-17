import json
from pathlib import Path

import pytest

from buymafinder.core.pricing_config import PricingConfigurationError, load_pricing_settings


FIXTURES = Path(__file__).parent / "fixtures"
CATEGORIES = ["Clothing", "Footwear", "Accessories", "Bags"]


def _write_configuration(tmp_path: Path, updates: dict) -> Path:
    configuration = json.loads((FIXTURES / "pricing_valid.json").read_text(encoding="utf-8"))
    configuration.update(updates)
    path = tmp_path / "pricing.json"
    path.write_text(json.dumps(configuration), encoding="utf-8")
    return path


def test_missing_pricing_configuration_reports_all_required_keys(tmp_path: Path) -> None:
    with pytest.raises(PricingConfigurationError) as error:
        load_pricing_settings(tmp_path / "pricing.json", CATEGORIES)

    assert "exchange_rates.EUR" in str(error.value)
    assert "listing_price_rounding_increment_jpy" in str(error.value)


def test_incomplete_pricing_configuration_reports_missing_keys(tmp_path: Path) -> None:
    path = _write_configuration(tmp_path, {"buyma_fee_rate": None})

    with pytest.raises(PricingConfigurationError) as error:
        load_pricing_settings(path, CATEGORIES)

    assert "buyma_fee_rate" in str(error.value)


@pytest.mark.parametrize(
    ("updates", "invalid_key"),
    [
        ({"exchange_rates": {"EUR": 0}}, "exchange_rates.EUR"),
        ({"international_shipping_jpy": -1}, "international_shipping_jpy"),
        ({"buyma_fee_rate": 1}, "buyma_fee_rate"),
        ({"target_profit_margin": -0.01}, "target_profit_margin"),
        ({"exchange_rate_safety_margin": 1}, "exchange_rate_safety_margin"),
        ({"target_profit_margin": "NaN"}, "target_profit_margin"),
        ({"estimated_import_cost_rates": {"Unknown": 0.1}}, "estimated_import_cost_rates.Unknown"),
        ({"exchange_rates": {"EUR": 100, "USD": 1}}, "exchange_rates.USD"),
        ({"listing_price_rounding_increment_jpy": 1.5}, "listing_price_rounding_increment_jpy"),
    ],
)
def test_invalid_pricing_configuration_is_rejected(
    tmp_path: Path, updates: dict, invalid_key: str
) -> None:
    path = _write_configuration(tmp_path, updates)

    with pytest.raises(PricingConfigurationError) as error:
        load_pricing_settings(path, CATEGORIES)

    assert invalid_key in str(error.value)


def test_category_rate_names_are_normalized_against_source_categories(tmp_path: Path) -> None:
    path = _write_configuration(tmp_path, {"estimated_import_cost_rates": {" clothing ": 0.1}})

    settings = load_pricing_settings(path, CATEGORIES)

    assert str(settings.estimated_import_cost_rates["clothing"]) == "0.1"
