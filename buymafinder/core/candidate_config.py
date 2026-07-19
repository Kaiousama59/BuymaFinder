from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from buymafinder.core.candidate_models import CandidateSettings


def load_candidate_settings(path: Path) -> CandidateSettings:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        maximum = payload.get("maximum_source_price")
        return CandidateSettings(
            preferred_brands=list(payload["preferred_brands"]),
            max_candidates=int(payload.get("max_candidates", 20)),
            minimum_images=int(payload.get("minimum_images", 2)),
            require_description=bool(payload.get("require_description", True)),
            require_sizes=bool(payload.get("require_sizes", True)),
            maximum_source_price=None if maximum in (None, "") else Decimal(str(maximum)),
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError, InvalidOperation) as error:
        raise ValueError(f"Invalid candidate configuration: {path}") from error
