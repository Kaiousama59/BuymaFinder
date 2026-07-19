from __future__ import annotations

import json
from pathlib import Path

import pytest

from datetime import date

from buymafinder.services.buyma_draft_filler import BuymaDraftError, _contains_yen_price, _normalized_date, _purchase_deadline_date, _reference_size_label, _shipping_method_matches, assert_safe_buyma_page, load_listing_package


class FakePage:
    def __init__(self, url: str) -> None:
        self.url = url


@pytest.mark.parametrize("url", [
    "https://www.buyma.com/my/sell/new?tab=b",
    "https://www.buyma.com/my/sell/new/?tab=b",
    "https://buyma.com/my/sell/new",
])
def test_safe_page_accepts_only_buyma_new_listing(url: str) -> None:
    assert_safe_buyma_page(FakePage(url))  # type: ignore[arg-type]


@pytest.mark.parametrize("url", ["https://example.test/my/sell/new", "https://www.buyma.com/my/sell", "http://www.buyma.com/my/sell/new"])
def test_safe_page_rejects_other_destinations(url: str) -> None:
    with pytest.raises(BuymaDraftError, match="Refusing"):
        assert_safe_buyma_page(FakePage(url))  # type: ignore[arg-type]


def test_package_loader_requires_downloaded_images(tmp_path: Path) -> None:
    (tmp_path / "listing_data.json").write_text(json.dumps({
        "source_url": "https://example.test/product", "brand": "Brand", "sku": "SKU",
        "settings": {}, "image_files": ["01_main.jpg"],
    }), encoding="utf-8")
    with pytest.raises(BuymaDraftError, match="image is missing"):
        load_listing_package(tmp_path)


@pytest.mark.parametrize(("source", "expected"), [("S", "S"), ("M", "M"), ("L", "L"), ("XL", "XL以上"), ("XS", "XS以下")])
def test_reference_size_uses_buyma_boundary_labels(source: str, expected: str) -> None:
    assert _reference_size_label(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [("38", "XS以下"), ("40", "S"), ("42", "M"), ("44", "L"), ("46", "XL以上"), ("48", "XL以上")],
)
def test_reference_size_maps_italian_clothing_sizes(source: str, expected: str) -> None:
    assert _reference_size_label(source, ["レディースファッション", "トップス"]) == expected


def test_reference_size_does_not_treat_european_shoe_size_as_clothing() -> None:
    assert _reference_size_label("40", ["レディースファッション", "靴・シューズ"]) == "指定なし"


def test_date_comparison_accepts_browser_date_separator() -> None:
    assert _normalized_date("2026-10-17") == _normalized_date("2026/10/17")


def test_buyma_deadline_counts_today_as_day_one() -> None:
    assert _purchase_deadline_date(date(2026, 7, 19), 90) == date(2026, 10, 16)


@pytest.mark.parametrize("text", ["¥800", "¥ 800", "800円", "送料 ¥1,250"])
def test_shipping_price_match_ignores_display_format(text: str) -> None:
    expected = 1250 if "1,250" in text else 800
    assert _contains_yen_price(text, expected)


@pytest.mark.parametrize(
    ("display_text", "configured_method"),
    [
        ("かんたんBUYMA便 【匿名配送】\nゆうパケット", "かんたんBUYMA便【匿名配送】 - ゆうパケット"),
        ("日本郵便 - ゆうパック 60サイズ", "かんたんBUYMA便【匿名配送】 - ゆうパック 60サイズ"),
        ("かんたんBUYMA便\nゆうパック 80サイズ", "かんたんBUYMA便【匿名配送】 - ゆうパック 80サイズ"),
    ],
)
def test_shipping_method_match_tolerates_buyma_display_format(
    display_text: str,
    configured_method: str,
) -> None:
    assert _shipping_method_matches(display_text, configured_method)


def test_shipping_method_match_does_not_confuse_yu_pack_sizes() -> None:
    assert not _shipping_method_matches(
        "日本郵便 - ゆうパック 80サイズ",
        "かんたんBUYMA便【匿名配送】 - ゆうパック 60サイズ",
    )
