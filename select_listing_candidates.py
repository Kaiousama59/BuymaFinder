from __future__ import annotations

import argparse
from pathlib import Path

from buymafinder.core.candidate_config import load_candidate_settings
from buymafinder.services.candidate_selector import export_listing_candidates, select_listing_candidates
from buymafinder.services.product_csv_loader import load_products


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a review queue of complete products from preferred brands.")
    parser.add_argument("--products-csv", type=Path, default=Path("output/eleonora_products.csv"))
    parser.add_argument("--config", type=Path, default=Path("config/candidates.json"))
    parser.add_argument("--output", type=Path, default=Path("output/listing_candidates.csv"))
    args = parser.parse_args()

    products = load_products(args.products_csv)
    settings = load_candidate_settings(args.config)
    candidates = select_listing_candidates(products, settings)
    export_listing_candidates(candidates, args.output)
    print(f"Selected {len(candidates)} review candidates: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
