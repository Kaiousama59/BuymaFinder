from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from buymafinder.services.buyma_draft_filler import BuymaDraftError, load_listing_package


@dataclass(frozen=True)
class BatchItem:
    package_folder: Path
    key: str
    source_url: str
    brand: str
    sku: str


def discover_batch_items(root: Path) -> list[BatchItem]:
    if not root.is_dir():
        raise BuymaDraftError(f"Listing package root does not exist: {root}")
    items: list[BatchItem] = []
    seen: set[str] = set()
    for data_file in sorted(root.rglob("listing_data.json")):
        payload = load_listing_package(data_file.parent)
        source_url = str(payload["source_url"]).strip()
        sku = str(payload["sku"]).strip()
        key = listing_key(source_url, sku)
        if key in seen:
            raise BuymaDraftError(f"Duplicate listing package for {source_url} ({sku})")
        seen.add(key)
        items.append(
            BatchItem(
                package_folder=data_file.parent,
                key=key,
                source_url=source_url,
                brand=str(payload["brand"]).strip(),
                sku=sku,
            )
        )
    if not items:
        raise BuymaDraftError(f"No listing_data.json files found under: {root}")
    return items


def discover_queued_batch_items(path: Path) -> list[BatchItem]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        packages = payload["packages"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise BuymaDraftError(f"Cannot read prepared package queue: {path}") from error
    if not isinstance(packages, list) or not packages or any(not isinstance(item, str) for item in packages):
        raise BuymaDraftError(f"Invalid prepared package queue: {path}")
    items: list[BatchItem] = []
    seen: set[str] = set()
    for folder_name in packages:
        folder = Path(folder_name).expanduser()
        payload = load_listing_package(folder)
        source_url = str(payload["source_url"]).strip()
        sku = str(payload["sku"]).strip()
        key = listing_key(source_url, sku)
        if key in seen:
            raise BuymaDraftError(f"Duplicate queued listing package for {source_url} ({sku})")
        seen.add(key)
        items.append(BatchItem(folder, key, source_url, str(payload["brand"]).strip(), sku))
    return items


def listing_key(source_url: str, sku: str) -> str:
    identity = f"{source_url.strip()}\n{sku.strip()}".encode("utf-8")
    return hashlib.sha256(identity).hexdigest()


def load_completed_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BuymaDraftError(f"Cannot read batch progress: {path}") from error
    completed = payload.get("completed", [])
    if not isinstance(completed, list) or any(not isinstance(key, str) for key in completed):
        raise BuymaDraftError(f"Invalid batch progress format: {path}")
    return set(completed)


def save_completed_keys(path: Path, completed: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps({"completed": sorted(completed)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
