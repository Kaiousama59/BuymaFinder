from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from buymafinder.core.models import Source


def save_empty_category_evidence(page: Any, source: Source, directory: Path = Path("debug")) -> None:
    """Save browser evidence for a category that yielded no product links."""
    directory.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^a-z0-9]+", "_", f"{source.shop_code}_{source.category}".lower()).strip("_")
    (directory / f"{stem}.html").write_text(page.content(), encoding="utf-8")
    page.screenshot(path=str(directory / f"{stem}.png"), full_page=True)
