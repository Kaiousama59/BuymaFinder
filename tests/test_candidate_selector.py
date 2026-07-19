from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from buymafinder.core.candidate_models import CandidateSettings
from buymafinder.core.models import Product, SizeStock
from buymafinder.services.candidate_selector import (
    export_listing_candidates,
    load_existing_listing_identities,
    product_identity,
    select_listing_candidates,
)


def product(brand: str, sku: str, *, price: str = "100", images: int = 3, description: str = "Details") -> Product:
    return Product(
        shop_code="eleonora",
        shop_name="Eleonora Bonucci",
        target="women",
        category="Clothing",
        brand=brand,
        name=f"{brand} product",
        product_url=f"https://eleonorabonucci.com/en/product/{sku}",
        currency="EUR",
        regular_price=Decimal(price),
        sku=sku,
        color="Black",
        sizes=[SizeStock("S", True), SizeStock("M", False)],
        description=description,
        image_urls=[f"https://example.test/{index}.jpg" for index in range(images)],
        in_stock=True,
        collected_at=datetime(2026, 7, 19),
    )


def test_filters_and_orders_by_explicit_brand_priority() -> None:
    settings = CandidateSettings(preferred_brands=["AMI PARIS", "BALENCIAGA"], minimum_images=2)

    result = select_listing_candidates(
        [product("BALENCIAGA", "B"), product("UNKNOWN", "X"), product("AMI PARIS", "A", price="200")],
        settings,
    )

    assert [item.sku for item in result] == ["A", "B"]


def test_rejects_incomplete_and_over_budget_products() -> None:
    settings = CandidateSettings(preferred_brands=["AMI PARIS"], maximum_source_price=Decimal("150"))

    result = select_listing_candidates(
        [product("AMI PARIS", "NO_IMAGES", images=1), product("AMI PARIS", "NO_DESC", description=""), product("AMI PARIS", "HIGH", price="151")],
        settings,
    )

    assert result == []


def test_export_requires_review_and_contains_no_invented_profit(tmp_path: Path) -> None:
    settings = CandidateSettings(preferred_brands=["AMI PARIS"])
    path = tmp_path / "candidates.csv"

    export_listing_candidates(select_listing_candidates([product("AMI PARIS", "A")], settings), path)

    with path.open(encoding="utf-8-sig", newline="") as input_file:
        row = next(csv.DictReader(input_file))
    assert row["approved"] == ""
    assert row["selection_status"] == "review_required"
    assert "profit" not in row


def test_excludes_existing_listing_package(tmp_path: Path) -> None:
    existing = tmp_path / "brand" / "sku"
    existing.mkdir(parents=True)
    (existing / "listing_data.json").write_text(
        '{"source_url": "https://eleonorabonucci.com/en/product/A", "sku": "A"}',
        encoding="utf-8",
    )
    settings = CandidateSettings(preferred_brands=["AMI PARIS"])

    result = select_listing_candidates(
        [product("AMI PARIS", "A"), product("AMI PARIS", "B")],
        settings,
        excluded_identities=load_existing_listing_identities(tmp_path),
    )

    assert [item.sku for item in result] == ["B"]
    assert product_identity("https://eleonorabonucci.com/en/product/A", "a") in load_existing_listing_identities(tmp_path)
