from __future__ import annotations

import argparse
from pathlib import Path

from buymafinder.core.listing_config import load_listing_settings
from buymafinder.services.listing_preparer import prepare_listing_package
from buymafinder.services.product_csv_loader import load_product_by_url


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare one reviewed product for a BUYMA draft.")
    parser.add_argument("--products-csv", type=Path, default=Path("output/eleonora_products.csv"))
    parser.add_argument("--product-url", required=True)
    parser.add_argument("--config", type=Path, default=Path("config/listing.json"))
    parser.add_argument("--output-root", type=Path, default=Path.home() / "Desktop/BUYMA/ListingImages")
    parser.add_argument("--skip-images", action="store_true")
    args = parser.parse_args()

    product = load_product_by_url(args.products_csv, args.product_url)
    settings = load_listing_settings(args.config)
    folder = prepare_listing_package(product, settings, args.output_root, download_images=not args.skip_images)
    print(f"Prepared listing package: {folder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
