from __future__ import annotations

import json
from pathlib import Path

import pytest

from buymafinder.services.buyma_draft_filler import BuymaDraftError
from buymafinder.services.listing_batch import discover_batch_items, load_completed_keys, save_completed_keys


def _package(root: Path, name: str, source_url: str, sku: str) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "01_main.jpg").write_bytes(b"image")
    (folder / "listing_data.json").write_text(
        json.dumps(
            {
                "source_url": source_url,
                "brand": "AMI PARIS",
                "sku": sku,
                "settings": {},
                "image_files": ["01_main.jpg"],
            }
        ),
        encoding="utf-8",
    )
    return folder


def test_discovers_packages_in_stable_order(tmp_path: Path) -> None:
    _package(tmp_path, "b", "https://example.com/2", "SKU2")
    _package(tmp_path, "a", "https://example.com/1", "SKU1")

    items = discover_batch_items(tmp_path)

    assert [item.sku for item in items] == ["SKU1", "SKU2"]


def test_rejects_duplicate_product_identity(tmp_path: Path) -> None:
    _package(tmp_path, "a", "https://example.com/1", "SKU1")
    _package(tmp_path, "b", "https://example.com/1", "SKU1")

    with pytest.raises(BuymaDraftError, match="Duplicate listing package"):
        discover_batch_items(tmp_path)


def test_progress_round_trip_is_atomic(tmp_path: Path) -> None:
    path = tmp_path / "state" / "progress.json"

    save_completed_keys(path, {"b", "a"})

    assert load_completed_keys(path) == {"a", "b"}
    assert json.loads(path.read_text(encoding="utf-8"))["completed"] == ["a", "b"]


def test_rejects_invalid_progress(tmp_path: Path) -> None:
    path = tmp_path / "progress.json"
    path.write_text('{"completed": "not-a-list"}', encoding="utf-8")

    with pytest.raises(BuymaDraftError, match="Invalid batch progress"):
        load_completed_keys(path)
