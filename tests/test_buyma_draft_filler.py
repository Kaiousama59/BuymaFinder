from __future__ import annotations

import json
from pathlib import Path

import pytest

from buymafinder.services.buyma_draft_filler import BuymaDraftError, assert_safe_buyma_page, load_listing_package


class FakePage:
    def __init__(self, url: str) -> None:
        self.url = url


@pytest.mark.parametrize("url", ["https://www.buyma.com/my/sell/new?tab=b", "https://buyma.com/my/sell/new"])
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
