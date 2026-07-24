from __future__ import annotations

import csv
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from buymafinder.core.listing_models import ListingSettings
from buymafinder.core.models import Product, SizeStock
from buymafinder.services.candidate_listing_preparer import (
    CandidatePreparationError,
    classify_product,
    listing_settings_for_candidate,
    load_approved_candidate_rows,
    prepare_candidate_packages,
    write_package_queue,
)


def _product(name: str = "CAMICIA IN COTONE") -> Product:
    return Product(
        shop_code="eleonora", shop_name="Eleonora Bonucci", target="women", category="Clothing",
        brand="AMI PARIS", name=name, product_url="https://eleonorabonucci.com/en/product/123",
        currency="EUR", regular_price=Decimal("100"), sku="SKU1", sizes=[SizeStock("S", True)],
        description="- 100% COTTON - MADE IN Italy", image_urls=[], in_stock=True,
        collected_at=datetime(2026, 7, 19),
    )


def _settings() -> ListingSettings:
    return ListingSettings(
        japanese_title="placeholder", japanese_description="placeholder", buyma_category_path=["x"],
        color_family="色指定なし", color_name="", listing_price_jpy=10000, buyer_shipping_jpy=300,
        shipping_method="日本郵便 - ゆうパック", arrival_days_min=14, arrival_days_max=21,
    )


def _candidate() -> dict[str, str]:
    return {
        "product_url": "https://eleonorabonucci.com/en/product/123", "sku": "SKU1",
        "suggested_listing_price_jpy": "30000", "expected_profit_jpy": "3100",
        "expected_profit_margin": "10.3333%",
    }


@pytest.mark.parametrize("name", [
    "CABAN REVERSIBILE", "TOP FLOREALE CON PAILLETTES", "GIACCA IN DENIM",
    "T-SHIRT IN COTONE A MANICA LUNGA", "FELPA A MANICA CORTA", "JEANS A GAMBA DRITTA",
    "CAMICIA IN SETA E COTONE", "ABITO MIDI CON DRAPPEGGIO", "MAGLIA CON LOGO IN LANA MERINO",
    "CARDIGAN IN LANA", "TRENCH DOPPIOPETTO", "BLOUSON IN PELLE", "GIACCA BIKER IN PELLE",
])
def test_classifies_reviewed_product_names(name: str) -> None:
    product_type, category = classify_product(name)
    assert product_type
    assert len(category) == 3


def test_unknown_product_name_stops_preparation() -> None:
    with pytest.raises(CandidatePreparationError, match="No reviewed BUYMA category rule"):
        classify_product("UNKNOWN ITEM")


def test_builds_product_specific_settings_without_guessing_color() -> None:
    settings = listing_settings_for_candidate(_product(), _candidate(), _settings())
    assert settings.japanese_title == "AMI PARIS コットン シャツ"
    assert settings.buyma_category_path == ["レディースファッション", "トップス", "ブラウス・シャツ"]
    assert settings.color_family == "色指定なし"
    assert settings.listing_price_jpy == 30000
    assert "仕入先URL: https://eleonorabonucci.com/en/product/123" in settings.private_memo
    assert "10.33%" in settings.private_memo
    assert "100% コットン" in settings.japanese_description


@pytest.mark.parametrize(
    ("name", "method", "shipping"),
    [
        ("T-SHIRT IN COTONE", "かんたんBUYMA便【匿名配送】 - ゆうパケット", 300),
        ("GIACCA IN DENIM", "かんたんBUYMA便【匿名配送】 - ゆうパック 60サイズ", 800),
        ("CABAN REVERSIBILE", "かんたんBUYMA便【匿名配送】 - ゆうパック 60サイズ", 800),
    ],
)
def test_uses_reviewed_shipping_class(name: str, method: str, shipping: int) -> None:
    settings = listing_settings_for_candidate(_product(name), _candidate(), _settings())
    assert settings.shipping_method == method
    assert settings.buyer_shipping_jpy == shipping


def test_explicit_approval_gate(tmp_path: Path) -> None:
    path = tmp_path / "candidates.csv"
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=["approved", "sku"])
        writer.writeheader()
        writer.writerow({"approved": "", "sku": "A"})
    with pytest.raises(CandidatePreparationError, match="No approved candidates"):
        load_approved_candidate_rows(path, approve_all=False)
    assert len(load_approved_candidate_rows(path, approve_all=True)) == 1


def test_prepares_package_and_writes_exact_queue(tmp_path: Path) -> None:
    folders = prepare_candidate_packages([_product()], [_candidate()], _settings(), tmp_path, download_images=False)
    queue = tmp_path / "queue.json"
    write_package_queue(folders, queue)
    payload = json.loads((folders[0] / "listing_data.json").read_text(encoding="utf-8"))
    assert payload["settings"]["listing_price_jpy"] == 30000
    assert json.loads(queue.read_text(encoding="utf-8"))["packages"] == [str(folders[0].resolve())]
