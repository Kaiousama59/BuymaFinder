from decimal import Decimal

from buymafinder.core.models import Product, Source
from buymafinder.services.collector import collect_products
from buymafinder.shops.base import ShopAdapter


class FakeAdapter(ShopAdapter):
    code = "eleonora"

    def collect_product_links(self, source: Source, browser: object) -> list[str]:
        return [f"https://example.test/{source.category}/{number}" for number in range(4)]

    def collect_product_detail(self, product_url: str, source: Source, browser: object) -> Product:
        return Product(
            shop_code=source.shop_code,
            shop_name=source.shop_name,
            target=source.target,
            category=source.category,
            brand="Example",
            name=product_url.rsplit("/", 1)[-1],
            product_url=product_url,
            currency="EUR",
            regular_price=Decimal("1"),
        )


def test_collect_products_enforces_the_total_five_product_limit() -> None:
    sources = [
        Source("eleonora", "Eleonora Bonucci", "women", "Clothing", "https://example.test/clothing"),
        Source("eleonora", "Eleonora Bonucci", "women", "Bags", "https://example.test/bags"),
    ]

    products = collect_products(sources, lambda _: FakeAdapter(), browser=object(), limit=5)

    assert len(products) == 5
    assert [product.category for product in products] == ["Clothing", "Clothing", "Clothing", "Clothing", "Bags"]
