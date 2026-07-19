from __future__ import annotations

import argparse
import logging
from pathlib import Path

from playwright.sync_api import sync_playwright

from buymafinder.services.buyma_draft_filler import fill_buyma_draft, wait_for_new_listing


def main() -> int:
    parser = argparse.ArgumentParser(description="Fill one BUYMA new-listing form and optionally save it as a draft.")
    parser.add_argument("package", type=Path, help="Folder containing listing_data.json and downloaded images")
    parser.add_argument("--save-draft", action="store_true", help="Click only BUYMA's exact draft-save button after filling")
    parser.add_argument("--profile", type=Path, default=Path("data/buyma_browser"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    args.profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(args.profile), channel="chrome", headless=False, viewport={"width": 1440, "height": 1000}
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            wait_for_new_listing(page)
            fill_buyma_draft(page, args.package, save_draft=args.save_draft)
            if args.save_draft:
                print("BUYMA draft save was requested. Check the opened browser before closing it.")
            else:
                input("Form filled. Review it in Chrome, then press Enter here to close the browser: ")
        except Exception:
            debug = Path("debug")
            debug.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(debug / "buyma_draft_failure.png"), full_page=True)
            (debug / "buyma_draft_failure.html").write_text(page.content(), encoding="utf-8")
            logging.exception("BUYMA draft automation stopped safely; no public-listing button was clicked")
            raise
        finally:
            context.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
