from __future__ import annotations

import argparse
import json
import logging
import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from buymafinder.services.buyma_draft_filler import (
    BuymaDraftError,
    edit_buyma_draft_images,
    list_buyma_draft_source_urls,
)
from buymafinder.services.listing_batch import discover_batch_items, load_completed_keys, save_completed_keys


def main() -> int:
    parser = argparse.ArgumentParser(description="Add missing images (e.g. footer guide images) to already-saved BUYMA drafts.")
    parser.add_argument("package_root", type=Path, help="Folder containing prepared listing package folders")
    parser.add_argument("--save", action="store_true", help="Required safety flag: this writes to live BUYMA drafts")
    parser.add_argument("--limit", type=int, default=10, help="Maximum drafts to update in this run (default: 10)")
    parser.add_argument("--profile", type=Path, default=Path("data/buyma_browser"))
    parser.add_argument("--progress", type=Path, default=Path("data/buyma_footer_image_progress.json"))
    parser.add_argument("--delay-seconds", type=float, default=2.0, help="Pause between edits (default: 2)")
    args = parser.parse_args()
    if not args.save:
        parser.error("--save is required; this command writes to live BUYMA drafts")
    if args.limit <= 0:
        parser.error("--limit must be greater than zero")

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

            updated = 0
            checked = 0
            for draft_id, source_url in draft_source_urls.items():
                if updated >= args.limit:
                    break
                if draft_id in completed:
                    continue
                item = package_by_source_url.get(source_url)
                if item is None:
                    continue
                payload = json.loads((item.package_folder / "listing_data.json").read_text(encoding="utf-8"))
                expected_images = payload["image_files"]
                checked += 1

                page.goto(f"https://www.buyma.com/my/sell/{draft_id}/edit", wait_until="networkidle", timeout=60_000)
                page.wait_for_timeout(1000)
                body_text = page.inner_text("body")
                match = re.search(r"残り(\d+)枚", body_text)
                if match is None:
                    logging.warning("Skipping draft %s: could not read current image count", draft_id)
                    completed.add(draft_id)
                    save_completed_keys(args.progress, completed)
                    continue
                current_count = 20 - int(match.group(1))
                missing = expected_images[current_count:]
                if not missing:
                    completed.add(draft_id)
                    save_completed_keys(args.progress, completed)
                    continue

                missing_paths = [str((item.package_folder / name).resolve()) for name in missing]
                logging.info(
                    "Updating draft %d: %s -> adding %d image(s): %s",
                    updated + 1,
                    draft_id,
                    len(missing_paths),
                    ", ".join(missing),
                )
                try:
                    edit_buyma_draft_images(page, draft_id, missing_paths)
                except BuymaDraftError as error:
                    logging.warning("Failed to update draft %s: %s", draft_id, error)
                else:
                    completed.add(draft_id)
                    save_completed_keys(args.progress, completed)
                    updated += 1
                if updated < args.limit:
                    time.sleep(args.delay_seconds)

            logging.info("Checked %d drafts with local matches; updated %d.", checked, updated)
        except Exception:
            debug = Path("debug")
            debug.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(debug / "buyma_footer_image_failure.png"), full_page=True)
            (debug / "buyma_footer_image_failure.html").write_text(page.content(), encoding="utf-8")
            logging.exception("BUYMA footer-image batch stopped safely; completed updates remain recorded")
            raise
        finally:
            context.close()
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
