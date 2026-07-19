from __future__ import annotations

import argparse
from pathlib import Path

from buymafinder.core.listing_config import load_listing_settings
from buymafinder.services.candidate_listing_preparer import (
    load_approved_candidate_rows,
    prepare_candidate_packages,
    write_package_queue,
)
from buymafinder.services.product_csv_loader import load_products


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare reviewed candidate products as individual BUYMA draft packages.")
    parser.add_argument("--products-csv", type=Path, default=Path("output/eleonora_products.csv"))
    parser.add_argument("--candidates-csv", type=Path, default=Path("output/listing_candidates.csv"))
    parser.add_argument("--config", type=Path, default=Path("config/listing.json"))
    parser.add_argument("--output-root", type=Path, default=Path.home() / "Desktop/BUYMA/ListingImages")
    parser.add_argument("--queue", type=Path, default=Path("output/prepared_candidate_queue.json"))
    parser.add_argument("--approve-all", action="store_true", help="Explicitly approve every row in the candidate CSV")
    parser.add_argument("--skip-images", action="store_true")
    args = parser.parse_args()

    rows = load_approved_candidate_rows(args.candidates_csv, approve_all=args.approve_all)
    products = load_products(args.products_csv)
    settings = load_listing_settings(args.config)
    folders = prepare_candidate_packages(
        products, rows, settings, args.output_root, download_images=not args.skip_images
    )
    write_package_queue(folders, args.queue)
    print(f"Prepared {len(folders)} candidate packages and queue: {args.queue}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
