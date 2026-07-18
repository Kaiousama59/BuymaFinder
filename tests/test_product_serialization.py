from datetime import datetime
from decimal import Decimal

from buymafinder.core.models import Product, SizeStock
from buymafinder.core.product_serialization import product_from_json, product_to_json


def test_product_serialization_round_trip_preserves_all_source_fields() -> None:
    product = Product(
        shop_code="eleonora",
        shop_name="Eleonora Bonucci",
        target="women",
        category="Bags",
        brand="Example",
        name="Example Bag",
        product_url="https://example.test/product",
        currency="EUR",
        regular_price=Decimal("1000.00"),
        sale_price=Decimal("800.50"),
        sku="EX-1",
        color="Black",
        sizes=[SizeStock("One Size", True), SizeStock("Mini", False)],
        description="A bag.",
        image_urls=["https://example.test/image"],
        in_stock=True,
        collected_at=datetime(2026, 1, 2, 3, 4, 5),
    )

    restored = product_from_json(product_to_json(product))

    assert restored == product


def test_product_serialization_preserves_absent_values() -> None:
    product = Product(
        shop_code="eleonora",
        shop_name="Eleonora Bonucci",
        target="women",
        category="Bags",
        brand="Example",
        name="Example Bag",
        product_url="https://example.test/product",
        currency="EUR",
    )

    restored = product_from_json(product_to_json(product))

    assert restored.regular_price is None
    assert restored.sale_price is None
    assert restored.in_stock is None
    assert restored.sizes == []
