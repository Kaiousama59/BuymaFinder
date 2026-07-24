from __future__ import annotations

import argparse
from pathlib import Path

from buymafinder.core.listing_config import load_listing_settings
from buymafinder.services.candidate_listing_preparer import (
    load_approved_candidate_rows,
    load_color_overrides,
    load_description_translations,
    load_target_overrides,
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
    parser.add_argument(
        "--translations",
        type=Path,
        default=Path("output/description_translations.json"),
        help="SKU -> hand-translated Japanese product-detail text; used in place of the mechanical fallback",
    )
    parser.add_argument(
        "--color-overrides",
        type=Path,
        default=Path("output/color_overrides.json"),
        help="SKU -> [color_family, color_name] manual overrides; used in place of the keyword-matched color",
    )
    parser.add_argument(
        "--target-overrides",
        type=Path,
        default=Path("output/target_overrides.json"),
        help="SKU -> \"men\"/\"women\" manual overrides; used in place of the source collection's gender",
    )
    args = parser.parse_args()

    rows = load_approved_candidate_rows(args.candidates_csv, approve_all=args.approve_all)
    products = load_products(args.products_csv)
    settings = load_listing_settings(args.config)
    translations = load_description_translations(args.translations)
    color_overrides = load_color_overrides(args.color_overrides)
    target_overrides = load_target_overrides(args.target_overrides)
    folders = prepare_candidate_packages(
        products,
        rows,
        settings,
        args.output_root,
        download_images=not args.skip_images,
        description_translations=translations,
        color_overrides=color_overrides,
        target_overrides=target_overrides,
    )
    write_package_queue(folders, args.queue)
    print(f"Prepared {len(folders)} candidate packages and queue: {args.queue}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
