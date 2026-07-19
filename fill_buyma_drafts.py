from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from buymafinder.services.buyma_draft_filler import fill_buyma_draft, wait_for_new_listing
from buymafinder.services.listing_batch import (
    discover_batch_items,
    discover_queued_batch_items,
    load_completed_keys,
    save_completed_keys,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fill and save multiple prepared BUYMA listing packages as drafts.")
    parser.add_argument("package_root", type=Path, help="Folder containing prepared listing package folders")
    parser.add_argument("--save-drafts", action="store_true", help="Required safety flag: click only BUYMA draft-save buttons")
    parser.add_argument("--limit", type=int, default=5, help="Maximum drafts to save in this run (default: 5)")
    parser.add_argument("--profile", type=Path, default=Path("data/buyma_browser"))
    parser.add_argument("--progress", type=Path, default=Path("data/buyma_batch_progress.json"))
    parser.add_argument("--queue", type=Path, help="Prepared package queue; excludes unrelated existing packages")
    parser.add_argument("--delay-seconds", type=float, default=3.0, help="Pause between drafts (default: 3)")
    args = parser.parse_args()
    if not args.save_drafts:
        parser.error("--save-drafts is required; this command never publishes listings")
    if args.limit <= 0:
        parser.error("--limit must be greater than zero")
    if args.delay_seconds < 0:
        parser.error("--delay-seconds cannot be negative")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    items = discover_queued_batch_items(args.queue) if args.queue else discover_batch_items(args.package_root)
    completed = load_completed_keys(args.progress)
    pending = [item for item in items if item.key not in completed][: args.limit]
    if not pending:
        print("No pending listing packages were found.")
        return 0

    args.profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(args.profile), channel="chrome", headless=False, viewport={"width": 1440, "height": 1000}
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            for index, item in enumerate(pending, start=1):
                logging.info(
                    "Preparing BUYMA draft %d/%d: %s %s (%s)",
                    index,
                    len(pending),
                    item.brand,
                    item.sku,
                    item.source_url,
                )
                wait_for_new_listing(page)
                fill_buyma_draft(page, item.package_folder, save_draft=True)
                completed.add(item.key)
                save_completed_keys(args.progress, completed)
                logging.info("Draft saved and progress recorded: %s", item.package_folder)
                if index < len(pending):
                    time.sleep(args.delay_seconds)
        except Exception:
            debug = Path("debug")
            debug.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(debug / "buyma_batch_failure.png"), full_page=True)
            (debug / "buyma_batch_failure.html").write_text(page.content(), encoding="utf-8")
            logging.exception("BUYMA draft batch stopped safely; completed drafts remain recorded")
            raise
        finally:
            context.close()
    print(f"Saved {len(pending)} BUYMA drafts. Public listing was not performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
