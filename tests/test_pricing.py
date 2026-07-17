from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from buymafinder.core.models import Product
from buymafinder.core.pricing_config import load_pricing_settings
from buymafinder.services.pricing import PricingEngine, apply_pricing


FIXTURES = Path(__file__).parent / "fixtures"
CATEGORIES = ["Clothing", "Footwear", "Accessories", "Bags"]


def _settings():
    return load_pricing_settings(FIXTURES / "pricing_valid.json", CATEGORIES)


def _product(
    category: str = "Clothing",
    currency: str = "EUR",
    regular_price: Decimal | None = Decimal("100"),
    sale_price: Decimal | None = None,
) -> Product:
    return Product(
        shop_code="test",
        shop_name="Test Shop",
        target="women",
        category=category,
        brand="Test",
        name="Test Product",
        product_url="https://example.test/product",
        currency=currency,
        regular_price=regular_price,
        sale_price=sale_price,
    )


def test_pricing_uses_sale_price_and_exchange_rate_safety_margin() -> None:
    result = PricingEngine(_settings()).price_product(_product(regular_price=Decimal("100"), sale_price=Decimal("80")))

    assert result.pricing_status == "priced"
    assert result.source_current_price == Decimal("80")
    assert result.adjusted_exchange_rate == Decimal("103.00")
    assert result.purchase_cost_jpy == Decimal("8240")


def test_pricing_uses_regular_price_when_sale_price_is_unavailable() -> None:
    result = PricingEngine(_settings()).price_product(_product(regular_price=Decimal("100")))

    assert result.pricing_status == "priced"
    assert result.source_current_price == Decimal("100")
    assert result.purchase_cost_jpy == Decimal("10300")


def test_pricing_includes_each_cost_once_and_uses_category_import_rate() -> None:
    clothing = PricingEngine(_settings()).price_product(_product("Clothing"))
    footwear = PricingEngine(_settings()).price_product(_product("Footwear"))

    assert clothing.estimated_import_cost_jpy == Decimal("1050")
    assert footwear.estimated_import_cost_jpy == Decimal("2100")
    assert clothing.pre_buyma_cost_jpy == Decimal("11700")
    assert clothing.pre_buyma_cost_jpy == (
        clothing.purchase_cost_jpy
        + clothing.international_shipping_jpy
        + clothing.estimated_import_cost_jpy
        + clothing.domestic_shipping_jpy
        + clothing.packing_cost_jpy
    )


def test_pricing_rounds_listing_up_and_recalculates_fee_from_listing_price() -> None:
    result = PricingEngine(_settings()).price_product(_product())

    assert result.suggested_listing_price_jpy == Decimal("14700")
    assert result.buyma_fee_jpy == Decimal("1470")
    assert result.total_estimated_cost_jpy == Decimal("13170")
    assert result.expected_profit_jpy == Decimal("1530")
    assert result.expected_profit_margin == Decimal("1530") / Decimal("14700")
    assert result.expected_profit_margin >= Decimal("0.10")


def test_pricing_marks_missing_category_rate_without_discarding_product() -> None:
    settings = _settings()
    settings = replace(settings, estimated_import_cost_rates={"clothing": Decimal("0.1")})
    result = PricingEngine(settings).price_product(_product("Bags"))

    assert result.pricing_status == "category_rate_required"
    assert result.suggested_listing_price_jpy is None


def test_pricing_marks_unsupported_currency_and_missing_price() -> None:
    engine = PricingEngine(_settings())

    assert engine.price_product(_product(currency="USD")).pricing_status == "unsupported_currency"
    assert engine.price_product(_product(regular_price=None)).pricing_status == "missing_source_price"


def test_pricing_loop_has_a_finite_safety_guard() -> None:
    settings = replace(
        _settings(),
        exchange_rates={"EUR": Decimal("1")},
        target_profit_margin=Decimal("0.01"),
        exchange_rate_safety_margin=Decimal("0"),
        international_shipping_jpy=Decimal("0"),
        domestic_shipping_jpy=Decimal("0"),
        packing_cost_jpy=Decimal("0"),
        buyma_fee_rate=Decimal("0.01"),
        estimated_import_cost_rates={"clothing": Decimal("0")},
        listing_price_rounding_increment_jpy=1,
    )
    result = PricingEngine(settings, maximum_listing_iterations=1).price_product(_product(regular_price=Decimal("1")))

    assert result.pricing_status == "pricing_loop_safety_guard"


def test_one_unpriceable_product_does_not_stop_other_products() -> None:
    products = [_product(currency="USD"), _product()]

    apply_pricing(products, FIXTURES / "pricing_valid.json", CATEGORIES)

    assert [product.pricing.pricing_status for product in products] == ["unsupported_currency", "priced"]


def test_missing_configuration_marks_every_product_and_logs_one_warning(tmp_path: Path, caplog) -> None:
    products = [_product(), _product("Bags")]

    apply_pricing(products, tmp_path / "pricing.json", CATEGORIES)

    assert [product.pricing.pricing_status for product in products] == [
        "configuration_required",
        "configuration_required",
    ]
    assert sum("Pricing skipped:" in message for message in caplog.messages) == 1
