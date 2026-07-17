from __future__ import annotations

import argparse
import logging
from pathlib import Path

from playwright.sync_api import sync_playwright

from buymafinder.core.source_loader import load_enabled_sources
from buymafinder.services.collector import collect_products
from buymafinder.services.csv_exporter import export_products_csv
from buymafinder.services.pricing import apply_pricing
from buymafinder.shops.eleonora import EleonoraAdapter


def main() -> int:
    """Run the configured product collection milestone."""
    parser = argparse.ArgumentParser(description="Collect Eleonora Bonucci product data.")
    parser.add_argument("--headed", action="store_true", help="Show the browser window.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    sources = load_enabled_sources(Path("config/source_urls.csv"))
    adapters = {"eleonora": EleonoraAdapter()}

    def adapter_factory(shop_code: str) -> EleonoraAdapter:
        try:
            return adapters[shop_code]
        except KeyError as error:
            raise ValueError(f"No adapter is registered for shop code: {shop_code}") from error

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headed)
        context = browser.new_context(ignore_https_errors=True)
        try:
            products = collect_products(sources, adapter_factory, context, limit=5)
        finally:
            context.close()
            browser.close()

    if not products:
        logging.error("No products were collected; preserving any existing CSV export.")
        return 1

    apply_pricing(products, Path("config/pricing.json"), [source.category for source in sources])
    row_count = export_products_csv(products, Path("output/eleonora_products.csv"))
    logging.info("Exported %s products to output/eleonora_products.csv", row_count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())