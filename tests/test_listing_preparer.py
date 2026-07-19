from __future__ import annotations

import csv
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from buymafinder.core.listing_models import ListingSettings
from buymafinder.core.models import Product, SizeStock
from buymafinder.services.listing_preparer import ImageDownloadError, download_product_images, prepare_listing_package, product_image_urls
from buymafinder.services.product_csv_loader import load_product_by_url


def settings() -> ListingSettings:
    return ListingSettings("AMI PARIS ロゴ タンクトップ ホワイト", "新品・正規品です。", ["レディースファッション", "トップス", "タンクトップ"], "ホワイト（白）系", "ホワイト", 32900, 300, "日本郵便 - ゆうパック", 14, 21)


def product() -> Product:
    return Product(shop_code="eleonora", shop_name="Eleonora Bonucci", target="women", category="Clothing", brand="AMI PARIS", name="Ami Paris top", product_url="https://eleonorabonucci.com/en/ami/women/clothing/tops/424047", currency="EUR", sale_price=Decimal("120"), sku="FTP811_JE0117100", sizes=[SizeStock("S", True), SizeStock("M", False)], image_urls=["https://images.eleonorabonucci.com/photo/424047/1/900/1200", "https://images.eleonorabonucci.com/photo/424047/1/900/1200?duplicate=yes", "https://images.eleonorabonucci.com/photo/999999/2/900/1200", "https://images.eleonorabonucci.com/photo/424047/3/900/1200?text=3"], collected_at=datetime(2026, 7, 19, 9, 0))


def test_product_image_urls_removes_duplicates_and_other_products() -> None:
    assert product_image_urls(product()) == ["https://images.eleonorabonucci.com/photo/424047/1/900/1200", "https://images.eleonorabonucci.com/photo/424047/3/900/1200"]


def test_prepare_listing_package_writes_reviewable_json(tmp_path: Path) -> None:
    folder = prepare_listing_package(product(), settings(), tmp_path, download_images=False)
    payload = json.loads((folder / "listing_data.json").read_text(encoding="utf-8"))
    assert folder == tmp_path / "Eleonora_Bonucci" / "AMI_PARIS" / "FTP811_JE0117100"
    assert payload["buyma_total_price_jpy"] == 33200
    assert payload["source_sizes"] == ["S"]
    assert payload["source_size_stock"] == {"S": True, "M": False}
    assert len(payload["image_urls"]) == 2


def test_downloader_does_not_overwrite_different_existing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("buymafinder.services.listing_preparer._download", lambda *_: (b"x" * 2048, "jpg"))
    (tmp_path / "01_main.jpg").write_bytes(b"different")
    with pytest.raises(ImageDownloadError, match="Refusing to overwrite"):
        download_product_images(["https://example.test/image"], tmp_path)


def test_load_product_by_url_rebuilds_exported_row(tmp_path: Path) -> None:
    path = tmp_path / "products.csv"
    fields = ["shop_code", "shop_name", "target", "category", "brand", "name", "product_url", "currency", "regular_price", "sale_price", "sku", "color", "sizes", "description", "image_urls", "in_stock", "collected_at"]
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerow(dict(zip(fields, ["eleonora", "Eleonora Bonucci", "women", "Clothing", "AMI PARIS", "Top", "https://example.test/product/1", "EUR", "150", "120", "SKU", "White", '[{"size":"S","in_stock":true}]', "Description", '["https://example.test/image.jpg"]', "true", "2026-07-19T09:00:00"])))
    loaded = load_product_by_url(path, "https://example.test/product/1/")
    assert loaded.sale_price == Decimal("120")
    assert loaded.sizes == [SizeStock("S", True)]


def test_listing_settings_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="arrival_days"):
        ListingSettings("title", "description", ["a"], "white", "white", 1, 0, "shipping", 21, 14)


def test_listing_settings_rejects_deadline_over_buyma_limit() -> None:
    with pytest.raises(ValueError, match="purchase_deadline_days"):
        ListingSettings("title", "description", ["a"], "white", "white", 1, 0, "shipping", 14, 21, purchase_deadline_days=91)


def test_listing_settings_requires_buying_region() -> None:
    with pytest.raises(ValueError, match="location"):
        ListingSettings(
            "title", "description", ["a"], "white", "white", 1, 0, "shipping", 14, 21,
            buying_region="",
        )
