from pathlib import Path

from buymafinder.core.models import Source
from buymafinder.services.scan_state import ScanStateRepository


CLOTHING = Source("eleonora", "Eleonora Bonucci", "women", "Clothing", "https://example.test/clothing")
BAGS = Source("eleonora", "Eleonora Bonucci", "women", "Bags", "https://example.test/bags")


def test_resume_skips_completed_sources_and_retries_failed_ones(tmp_path: Path) -> None:
    database = tmp_path / "state.db"

    first = ScanStateRepository(database)
    first.start_run()
    first.record_success(CLOTHING, 24)
    first.record_failure(BAGS, "collect_product_links: timeout")
    first.close()

    resumed = ScanStateRepository(database)
    resumed.start_run(resume=True)
    assert resumed.should_skip(CLOTHING) is True
    assert resumed.should_skip(BAGS) is False
    resumed.close()


def test_finished_runs_are_not_resumed(tmp_path: Path) -> None:
    database = tmp_path / "state.db"

    first = ScanStateRepository(database)
    first_run_id = first.start_run()
    first.record_success(CLOTHING, 24)
    first.finish_run()
    first.close()

    second = ScanStateRepository(database)
    second_run_id = second.start_run(resume=True)
    assert second_run_id != first_run_id
    assert second.should_skip(CLOTHING) is False
    second.close()


def test_recording_the_same_source_twice_updates_the_row(tmp_path: Path) -> None:
    repository = ScanStateRepository(tmp_path / "state.db")
    repository.start_run()
    repository.record_failure(CLOTHING, "temporary error")
    repository.record_success(CLOTHING, 10)

    assert repository.should_skip(CLOTHING) is True
    repository.close()


def test_products_are_persisted_and_restored_within_a_run(tmp_path: Path) -> None:
    from datetime import datetime
    from decimal import Decimal

    from buymafinder.core.models import Product

    database = tmp_path / "state.db"
    product = Product(
        shop_code="eleonora",
        shop_name="Eleonora Bonucci",
        target="women",
        category="Bags",
        brand="Example",
        name="Example Bag",
        product_url="https://example.test/product",
        currency="EUR",
        regular_price=Decimal("100"),
        collected_at=datetime(2026, 1, 2, 3, 4, 5),
    )

    first = ScanStateRepository(database)
    first.start_run()
    first.save_product(product)
    first.close()

    resumed = ScanStateRepository(database)
    resumed.start_run(resume=True)
    restored = resumed.load_products()
    resumed.close()

    assert restored == [product]


def test_products_from_finished_runs_are_not_restored(tmp_path: Path) -> None:
    from decimal import Decimal

    from buymafinder.core.models import Product

    database = tmp_path / "state.db"
    product = Product(
        shop_code="eleonora",
        shop_name="Eleonora Bonucci",
        target="women",
        category="Bags",
        brand="Example",
        name="Example Bag",
        product_url="https://example.test/product",
        currency="EUR",
        regular_price=Decimal("100"),
    )

    first = ScanStateRepository(database)
    first.start_run()
    first.save_product(product)
    first.finish_run()
    first.close()

    second = ScanStateRepository(database)
    second.start_run(resume=True)
    assert second.load_products() == []
    second.close()
