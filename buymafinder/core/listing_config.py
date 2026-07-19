from __future__ import annotations

import json
from pathlib import Path

from buymafinder.core.listing_models import ListingSettings


def load_listing_settings(path: Path) -> ListingSettings:
    """Load explicit, user-reviewed BUYMA draft values."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot load listing configuration: {path}") from error
    if not isinstance(data, dict):
        raise ValueError("Listing configuration must be a JSON object")
    try:
        return ListingSettings(**data)
    except TypeError as error:
        raise ValueError(f"Invalid listing configuration keys: {error}") from error
