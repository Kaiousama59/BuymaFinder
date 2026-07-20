from __future__ import annotations

import argparse
import logging
from pathlib import Path

from playwright.sync_api import sync_playwright

from buymafinder.core.source_loader import load_enabled_sources
from buymafinder.services.collector import collect_products
from buymafinder.services.csv_exporter import export_products_csv
from buymafinder.services.pricing import apply_pricing
from buymafinder.services.scan_state import ScanStateRepository
from buymafinder.shops.antonia import AntoniaAdapter
from buymafinder.shops.base import ShopAdapter
from buymafinder.shops.eleonora import EleonoraAdapter


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


def main() -> int:
    """Run the configured product collection."""
    parser = argparse.ArgumentParser(description="Collect Eleonora Bonucci product data.")
    parser.add_argument("--headed", action="store_true", help="Show the browser window.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum total products to collect.")
    parser.add_argument(
        "--per-category-limit",
        type=int,
        default=None,
        help="Maximum products to collect from a single source category.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=10,
        help="Maximum listing pages to visit per source category.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Continue the latest unfinished run, restoring collected products "
        "and skipping completed categories.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/buymafinder.db"),
        help="SQLite database path for scan state.",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    sources = load_enabled_sources(Path("config/source_urls.csv"))
    per_source_link_cap = args.per_category_limit if args.per_category_limit is not None else args.limit
    adapters = {
        "eleonora": EleonoraAdapter(
            max_pages_per_source=args.max_pages,
            max_links_per_source=per_source_link_cap,
        ),
        "antonia": AntoniaAdapter(
            max_pages_per_source=args.max_pages,
            max_links_per_source=per_source_link_cap,
        ),
    }

    def adapter_factory(shop_code: str) -> ShopAdapter:
        try:
            return adapters[shop_code]
        except KeyError as error:
            raise ValueError(f"No adapter is registered for shop code: {shop_code}") from error

    scan_state = ScanStateRepository(args.database)
    scan_state.start_run(resume=args.resume)
    restored_products = scan_state.load_products() if args.resume else []

    products = list(restored_products)
    interrupted = False
    playwright = None
    browser = None
    context = None
    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=not args.headed)
        context = browser.new_context(
            ignore_https_errors=True,
            user_agent=USER_AGENT,
            locale="en-US",
        )
        products = collect_products(
            sources,
            adapter_factory,
            context,
            limit=args.limit,
            per_source_limit=args.per_category_limit,
            scan_state=scan_state,
            product_sink=scan_state.save_product,
            initial_products=restored_products,
        )
    except KeyboardInterrupt:
        interrupted = True
        logging.warning(
            "Interrupted by user; collected products are saved. "
            "Run again with --resume to continue."
        )
    finally:
        for closeable in (context, browser):
            if closeable is not None:
                try:
                    closeable.close()
                except Exception:
                    pass
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass

    if interrupted:
        scan_state.close()
        return 130

    try:
        if not products:
            logging.error("No products were collected; preserving any existing CSV export.")
            return 1

        apply_pricing(products, Path("config/pricing.json"), [source.category for source in sources])
        row_count = export_products_csv(products, Path("output/eleonora_products.csv"))
        logging.info("Exported %s products to output/eleonora_products.csv", row_count)
        scan_state.finish_run()
        return 0
    finally:
        scan_state.close()


if __name__ == "__main__":
    raise SystemExit(main())
