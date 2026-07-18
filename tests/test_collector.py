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


class RecordingScanState:
    def __init__(self, skip_urls: set[str] | None = None) -> None:
        self.skip_urls = skip_urls or set()
        self.successes: list[tuple[str, int]] = []
        self.failures: list[tuple[str, str]] = []

    def should_skip(self, source: Source) -> bool:
        return source.list_url in self.skip_urls

    def record_success(self, source: Source, product_count: int) -> None:
        self.successes.append((source.category, product_count))

    def record_failure(self, source: Source, error: str) -> None:
        self.failures.append((source.category, error))


class FailingLinksAdapter(FakeAdapter):
    def collect_product_links(self, source: Source, browser: object) -> list[str]:
        raise RuntimeError("category page timed out")


def test_collect_products_enforces_the_per_source_limit() -> None:
    sources = [
        Source("eleonora", "Eleonora Bonucci", "women", "Clothing", "https://example.test/clothing"),
        Source("eleonora", "Eleonora Bonucci", "women", "Bags", "https://example.test/bags"),
    ]

    products = collect_products(
        sources, lambda _: FakeAdapter(), browser=object(), limit=100, per_source_limit=2
    )

    assert [product.category for product in products] == ["Clothing", "Clothing", "Bags", "Bags"]


def test_collect_products_records_and_skips_via_scan_state() -> None:
    sources = [
        Source("eleonora", "Eleonora Bonucci", "women", "Clothing", "https://example.test/clothing"),
        Source("eleonora", "Eleonora Bonucci", "women", "Bags", "https://example.test/bags"),
    ]
    scan_state = RecordingScanState(skip_urls={"https://example.test/clothing"})

    products = collect_products(
        sources, lambda _: FakeAdapter(), browser=object(), limit=100, scan_state=scan_state
    )

    assert [product.category for product in products] == ["Bags"] * 4
    assert scan_state.successes == [("Bags", 4)]
    assert scan_state.failures == []


def test_collect_products_records_link_collection_failures() -> None:
    sources = [
        Source("eleonora", "Eleonora Bonucci", "women", "Clothing", "https://example.test/clothing"),
    ]
    scan_state = RecordingScanState()

    products = collect_products(
        sources, lambda _: FailingLinksAdapter(), browser=object(), limit=100, scan_state=scan_state
    )

    assert products == []
    assert scan_state.successes == []
    assert len(scan_state.failures) == 1
    assert scan_state.failures[0][0] == "Clothing"


def test_collect_products_enforces_the_total_five_product_limit() -> None:
    sources = [
        Source("eleonora", "Eleonora Bonucci", "women", "Clothing", "https://example.test/clothing"),
        Source("eleonora", "Eleonora Bonucci", "women", "Bags", "https://example.test/bags"),
    ]

    products = collect_products(sources, lambda _: FakeAdapter(), browser=object(), limit=5)

    assert len(products) == 5
    assert [product.category for product in products] == ["Clothing", "Clothing", "Clothing", "Clothing", "Bags"]


def test_collect_products_seeds_deduplication_from_restored_products() -> None:
    sources = [
        Source("eleonora", "Eleonora Bonucci", "women", "Clothing", "https://example.test/clothing"),
    ]
    adapter = FakeAdapter()
    restored = [
        adapter.collect_product_detail("https://example.test/Clothing/0", sources[0], object()),
        adapter.collect_product_detail("https://example.test/Clothing/1", sources[0], object()),
    ]

    products = collect_products(
        sources,
        lambda _: adapter,
        browser=object(),
        limit=3,
        initial_products=restored,
    )

    assert len(products) == 3
    assert [product.name for product in products] == ["0", "1", "2"]


def test_collect_products_reports_each_new_product_to_the_sink() -> None:
    sources = [
        Source("eleonora", "Eleonora Bonucci", "women", "Clothing", "https://example.test/clothing"),
    ]
    saved: list[str] = []

    products = collect_products(
        sources,
        lambda _: FakeAdapter(),
        browser=object(),
        limit=2,
        product_sink=lambda product: saved.append(product.product_url),
    )

    assert saved == [product.product_url for product in products]
    assert len(saved) == 2
