from __future__ import annotations

import csv
from pathlib import Path

from buymafinder.core.models import Source


def load_enabled_sources(path: Path) -> list[Source]:
    """Load enabled scan sources from a CSV file."""
    with path.open(newline="", encoding="utf-8-sig") as source_file:
        reader = csv.DictReader(source_file)
        required_fields = {"shop_code", "shop_name", "target", "category", "list_url", "enabled"}
        if reader.fieldnames is None or not required_fields.issubset(reader.fieldnames):
            raise ValueError(f"Source CSV is missing required columns: {path}")

        sources = []
        for row_number, row in enumerate(reader, start=2):
            if not _is_enabled(row["enabled"] or ""):
                continue
            values = {field: (row[field] or "").strip() for field in required_fields - {"enabled"}}
            if not all(values.values()):
                raise ValueError(f"Source CSV has an empty required value on row {row_number}: {path}")
            sources.append(Source(**values))
    return sources


def _is_enabled(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no", ""}:
        return False
    raise ValueError(f"Invalid enabled value in source CSV: {value!r}")
