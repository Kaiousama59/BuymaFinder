from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from buymafinder.services.buyma_draft_filler import (
    BuymaDraftError,
    edit_buyma_draft_title,
    list_buyma_draft_source_urls,
)
from buymafinder.services.candidate_listing_preparer import CandidatePreparationError, recompute_title
from buymafinder.services.listing_batch import discover_batch_items, load_completed_keys, save_completed_keys


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh the 商品名 title on already-saved BUYMA drafts in place.")
    parser.add_argument("package_root", type=Path, help="Folder containing prepared listing package folders")
    parser.add_argument("--save", action="store_true", help="Required safety flag: this writes to live BUYMA drafts")
    parser.add_argument("--limit", type=int, default=10, help="Maximum drafts to update in this run (default: 10)")
    parser.add_argument("--profile", type=Path, default=Path("data/buyma_browser"))
    parser.add_argument("--progress", type=Path, default=Path("data/buyma_title_edit_progress.json"))
    parser.add_argument("--delay-seconds", type=float, default=2.0, help="Pause between edits (default: 2)")
    args = parser.parse_args()
    if not args.save:
        parser.error("--save is required; this command writes to live BUYMA drafts")
    if args.limit <= 0:
        parser.error("--limit must be greater than zero")
    if args.delay_seconds < 0:
        parser.error("--delay-seconds cannot be negative")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    items = discover_batch_items(args.package_root)
    package_by_source_url = {item.source_url: item for item in items}
    completed = load_completed_keys(args.progress)

    args.profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(args.profile), channel="chrome", headless=False, viewport={"width": 1440, "height": 1000}
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            logging.info("Fetching current BUYMA draft list...")
            draft_source_urls = list_buyma_draft_source_urls(page)
            logging.info("Found %d BUYMA drafts.", len(draft_source_urls))

            pending: list[tuple[str, object, str]] = []
            for draft_id, source_url in draft_source_urls.items():
                if draft_id in completed:
                    continue
                item = package_by_source_url.get(source_url)
                if item is None:
                    continue
                payload = json.loads((item.package_folder / "listing_data.json").read_text(encoding="utf-8"))
                brand = str(payload["brand"]).strip()
                source_name = payload["source_name"]
                color_name = payload["settings"].get("color_name", "")
                try:
                    fresh_title = recompute_title(brand, source_name, color_name)
                except CandidatePreparationError as error:
                    logging.warning("Skipping draft %s (%s): %s", draft_id, item.package_folder, error)
                    continue
                pending.append((draft_id, item, fresh_title))
            pending = pending[: args.limit]

            if not pending:
                print("No pending draft titles to update.")
                return 0

            for index, (draft_id, item, fresh_title) in enumerate(pending, start=1):
                logging.info(
                    "Updating draft %d/%d: %s -> %s",
                    index,
                    len(pending),
                    draft_id,
                    fresh_title,
                )
                try:
                    edit_buyma_draft_title(page, draft_id, fresh_title)
                except BuymaDraftError as error:
                    logging.warning("Failed to update draft %s: %s", draft_id, error)
                else:
                    completed.add(draft_id)
                    save_completed_keys(args.progress, completed)
                if index < len(pending):
                    time.sleep(args.delay_seconds)
        except Exception:
            debug = Path("debug")
            debug.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(debug / "buyma_title_edit_failure.png"), full_page=True)
            (debug / "buyma_title_edit_failure.html").write_text(page.content(), encoding="utf-8")
            logging.exception("BUYMA title-edit batch stopped safely; completed updates remain recorded")
            raise
        finally:
            context.close()
    print(f"Updated up to {len(pending)} BUYMA draft titles.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
