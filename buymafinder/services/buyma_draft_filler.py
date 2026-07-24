from __future__ import annotations

import html
import json
import logging
import re
import unicodedata
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import BrowserContext, Locator, Page, TimeoutError as PlaywrightTimeoutError

from buymafinder.core.models import SizeStock, Source
from buymafinder.shops.antonia import parse_product_detail_html as _parse_antonia_product_detail_html
from buymafinder.shops.eleonora import parse_product_detail_html as _parse_eleonora_product_detail_html
from buymafinder.shops.flannels import parse_product_detail_html as _parse_flannels_product_detail_html
from buymafinder.shops.thedoublef import parse_product_detail_html as _parse_thedoublef_product_detail_html


BUYMA_NEW_LISTING_URL = "https://www.buyma.com/my/sell/new?tab=b"

# Per-supplier live stock refresh: shop_code, the shop's parse_product_detail_html,
# and a selector to wait for before reading the page (a signal that the
# size/stock markup has rendered). Add an entry here whenever a new source
# adapter needs to support the pre-draft stock re-check.
_SUPPLIER_REGISTRY: dict[str, tuple[str, Callable[..., object], str]] = {
    "eleonorabonucci.com": ("eleonora", _parse_eleonora_product_detail_html, "select option"),
    "www.eleonorabonucci.com": ("eleonora", _parse_eleonora_product_detail_html, "select option"),
    "antonia.it": ("antonia", _parse_antonia_product_detail_html, "body"),
    "www.antonia.it": ("antonia", _parse_antonia_product_detail_html, "body"),
    "flannels.com": ("flannels", _parse_flannels_product_detail_html, "body"),
    "www.flannels.com": ("flannels", _parse_flannels_product_detail_html, "body"),
    "thedoublef.com": ("thedoublef", _parse_thedoublef_product_detail_html, "product-main"),
    "www.thedoublef.com": ("thedoublef", _parse_thedoublef_product_detail_html, "product-main"),
}


class BuymaDraftError(RuntimeError):
    pass


class OutOfStockError(BuymaDraftError):
    """Raised when a product has zero purchasable stock; BUYMA won't save a draft for it."""


class ShippingNotAvailableError(BuymaDraftError):
    """Raised when none of the configured shipping methods exist on the form.

    Callers should catch this, skip the product, and continue with the rest.
    """


def load_listing_package(folder: Path) -> dict:
    path = folder / "listing_data.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BuymaDraftError(f"Cannot load listing package: {path}") from error
    required = ("source_url", "brand", "sku", "settings", "image_files")
    missing = [key for key in required if key not in payload]
    if missing:
        raise BuymaDraftError(f"Listing package is missing: {', '.join(missing)}")
    for name in payload["image_files"]:
        image = folder / name
        if not image.is_file():
            raise BuymaDraftError(f"Listing image is missing: {image}")
    return payload


def assert_safe_buyma_page(page: Page) -> None:
    parsed = urlsplit(page.url)
    if parsed.scheme != "https" or parsed.netloc not in {"buyma.com", "www.buyma.com"}:
        raise BuymaDraftError(f"Refusing to fill a non-BUYMA page: {page.url}")
    if parsed.path.rstrip("/") != "/my/sell/new":
        raise BuymaDraftError(f"Refusing to fill a page other than BUYMA new listing: {page.url}")


_DRAFT_EDIT_PATH = re.compile(r"^/my/sell/\d+/edit$")


def assert_safe_buyma_draft_edit_page(page: Page, draft_id: str) -> None:
    parsed = urlsplit(page.url)
    if parsed.scheme != "https" or parsed.netloc not in {"buyma.com", "www.buyma.com"}:
        raise BuymaDraftError(f"Refusing to edit a non-BUYMA page: {page.url}")
    expected_path = f"/my/sell/{draft_id}/edit"
    if parsed.path.rstrip("/") != expected_path:
        raise BuymaDraftError(f"Refusing to edit a page other than the intended draft: {page.url}")


def edit_buyma_draft_title(page: Page, draft_id: str, new_title: str) -> None:
    """Update only the 商品名 (title) field of an already-saved BUYMA draft.

    Deliberately separate from fill_buyma_draft(), which is hard-restricted
    by assert_safe_buyma_page() to /my/sell/new only: redrafting every
    existing listing from scratch to fix a title-format bug isn't practical
    at this batch's size, so this instead opens the draft's own /edit page
    and touches nothing but the title field.
    """
    page.goto(f"https://www.buyma.com/my/sell/{draft_id}/edit", wait_until="networkidle", timeout=60_000)
    assert_safe_buyma_draft_edit_page(page, draft_id)
    page.get_by_text("商品名", exact=True).first.wait_for(state="visible", timeout=20_000)
    _dismiss_blocking_overlays(page)
    _fill_near(page, "商品名", "input", new_title)
    assert_safe_buyma_draft_edit_page(page, draft_id)
    button = page.get_by_role("button", name="下書き保存する", exact=True)
    if button.count() == 0:
        button = page.get_by_role("button", name="下書き保存", exact=True)
    if button.count() == 0:
        raise BuymaDraftError("The draft-save button was not found; nothing was submitted")
    # The save button's own on-page state never changes on failure (no
    # visible error banner, no URL change either way) — confirmed live that
    # a rejected save (e.g. BUYMA's own title-length validation) only shows
    # up as a non-2xx response on this PUT, with the page otherwise looking
    # identical to a successful save. The response is the only reliable
    # signal, so it's captured directly instead of inferred from the DOM.
    with page.expect_response(
        lambda response: response.url.endswith(f"/rorapi/sell/products/{draft_id}") and response.request.method == "PUT",
        timeout=20_000,
    ) as response_info:
        button.first.click()
    response = response_info.value
    if not response.ok:
        try:
            body = response.text()
        except Exception:
            body = "<unreadable response body>"
        raise BuymaDraftError(f"BUYMA rejected the title update for draft {draft_id} ({response.status}): {body}")
    page.wait_for_timeout(500)
    assert_safe_buyma_draft_edit_page(page, draft_id)
    logging.info("BUYMA draft %s title updated.", draft_id)


def edit_buyma_draft_description(page: Page, draft_id: str, new_description: str) -> None:
    """Update only the 商品コメント (description) field of an already-saved BUYMA draft.

    Same rationale and safety approach as edit_buyma_draft_title().
    """
    page.goto(f"https://www.buyma.com/my/sell/{draft_id}/edit", wait_until="networkidle", timeout=60_000)
    assert_safe_buyma_draft_edit_page(page, draft_id)
    page.get_by_text("商品コメント", exact=True).first.wait_for(state="visible", timeout=20_000)
    _dismiss_blocking_overlays(page)
    _fill_near(page, "商品コメント", "textarea", new_description)
    assert_safe_buyma_draft_edit_page(page, draft_id)
    button = page.get_by_role("button", name="下書き保存する", exact=True)
    if button.count() == 0:
        button = page.get_by_role("button", name="下書き保存", exact=True)
    if button.count() == 0:
        raise BuymaDraftError("The draft-save button was not found; nothing was submitted")
    with page.expect_response(
        lambda response: response.url.endswith(f"/rorapi/sell/products/{draft_id}") and response.request.method == "PUT",
        timeout=20_000,
    ) as response_info:
        button.first.click()
    response = response_info.value
    if not response.ok:
        try:
            body = response.text()
        except Exception:
            body = "<unreadable response body>"
        raise BuymaDraftError(f"BUYMA rejected the description update for draft {draft_id} ({response.status}): {body}")
    page.wait_for_timeout(500)
    assert_safe_buyma_draft_edit_page(page, draft_id)
    logging.info("BUYMA draft %s description updated.", draft_id)


def edit_buyma_draft_category(page: Page, draft_id: str, category_path: list[str]) -> None:
    """Update only the カテゴリ (category) field of an already-saved BUYMA draft.

    Same rationale and safety approach as edit_buyma_draft_title(): opens
    the draft's own /edit page and touches nothing but the category
    dropdowns, verifying success from the save PUT response rather than
    any on-page state (which looks identical whether the save succeeded).
    """
    page.goto(f"https://www.buyma.com/my/sell/{draft_id}/edit", wait_until="networkidle", timeout=60_000)
    assert_safe_buyma_draft_edit_page(page, draft_id)
    page.get_by_text("カテゴリ", exact=True).first.wait_for(state="visible", timeout=20_000)
    _dismiss_blocking_overlays(page)
    _select_category(page, category_path)
    assert_safe_buyma_draft_edit_page(page, draft_id)
    button = page.get_by_role("button", name="下書き保存する", exact=True)
    if button.count() == 0:
        button = page.get_by_role("button", name="下書き保存", exact=True)
    if button.count() == 0:
        raise BuymaDraftError("The draft-save button was not found; nothing was submitted")
    with page.expect_response(
        lambda response: response.url.endswith(f"/rorapi/sell/products/{draft_id}") and response.request.method == "PUT",
        timeout=20_000,
    ) as response_info:
        button.first.click()
    response = response_info.value
    if not response.ok:
        try:
            body = response.text()
        except Exception:
            body = "<unreadable response body>"
        raise BuymaDraftError(f"BUYMA rejected the category update for draft {draft_id} ({response.status}): {body}")
    page.wait_for_timeout(500)
    assert_safe_buyma_draft_edit_page(page, draft_id)
    logging.info("BUYMA draft %s category updated.", draft_id)


def edit_buyma_draft_color(page: Page, draft_id: str, color_family: str, color_name: str = "") -> None:
    """Update only the 色・サイズ (color) field of an already-saved BUYMA draft.

    Same rationale and safety approach as edit_buyma_draft_title(). Unlike
    edit_buyma_draft_category(), this doesn't touch the stock table, so it
    isn't expected to trip BUYMA's "incomplete stock selection" validation
    the way a category change can.
    """
    page.goto(f"https://www.buyma.com/my/sell/{draft_id}/edit", wait_until="networkidle", timeout=60_000)
    assert_safe_buyma_draft_edit_page(page, draft_id)
    page.get_by_text("色・サイズ", exact=True).first.wait_for(state="visible", timeout=20_000)
    _dismiss_blocking_overlays(page)
    _select_color(page, color_family, color_name)
    assert_safe_buyma_draft_edit_page(page, draft_id)
    button = page.get_by_role("button", name="下書き保存する", exact=True)
    if button.count() == 0:
        button = page.get_by_role("button", name="下書き保存", exact=True)
    if button.count() == 0:
        raise BuymaDraftError("The draft-save button was not found; nothing was submitted")
    with page.expect_response(
        lambda response: response.url.endswith(f"/rorapi/sell/products/{draft_id}") and response.request.method == "PUT",
        timeout=20_000,
    ) as response_info:
        button.first.click()
    response = response_info.value
    if not response.ok:
        try:
            body = response.text()
        except Exception:
            body = "<unreadable response body>"
        raise BuymaDraftError(f"BUYMA rejected the color update for draft {draft_id} ({response.status}): {body}")
    page.wait_for_timeout(500)
    assert_safe_buyma_draft_edit_page(page, draft_id)
    logging.info("BUYMA draft %s color updated.", draft_id)


def edit_buyma_draft_images(page: Page, draft_id: str, image_paths: list[str], *, remove_existing: bool = False) -> None:
    """Add images to an already-saved BUYMA draft, optionally clearing existing ones first.

    The edit page's own file input starts empty regardless of how many
    images are already uploaded (previously-saved images render as separate
    thumbnails, not as pre-populated files on the input), so set_input_files
    here only adds image_paths — it does not replace what's already there
    on its own, hence the explicit remove_existing step. Same save/verify
    approach as edit_buyma_draft_title().
    """
    if not image_paths:
        raise BuymaDraftError("No images were given to add")
    page.goto(f"https://www.buyma.com/my/sell/{draft_id}/edit", wait_until="networkidle", timeout=60_000)
    assert_safe_buyma_draft_edit_page(page, draft_id)
    page.get_by_text("商品画像", exact=True).first.wait_for(state="visible", timeout=20_000)
    _dismiss_blocking_overlays(page)
    if remove_existing:
        # Each delete icon triggers a native confirm() ("削除してよろしいで
        # すか？"); without a dialog handler, Playwright leaves it
        # unresolved and the click has no visible effect (confirmed live —
        # the icon looked clickable and the click "succeeded" with the
        # thumbnail still there afterward). page.once() (not page.on()) is
        # used so the handler self-removes after firing — this function
        # runs many times against the same shared page across a batch, and
        # a page.on() handler would keep stacking a new listener on every
        # call, each trying to accept an already-handled dialog and
        # throwing (confirmed live: this crashed a 100+ item batch partway
        # through with "Dialog.accept: Cannot accept dialog which is
        # already handled!").
        delete_icon = page.locator(".js-delete-icon")
        remaining = delete_icon.count()
        while remaining > 0:
            page.once("dialog", lambda dialog: dialog.accept())
            delete_icon.first.click(force=True)
            page.wait_for_timeout(500)
            new_remaining = delete_icon.count()
            if new_remaining >= remaining:
                raise BuymaDraftError(f"Could not remove existing images from draft {draft_id}: stuck at {remaining}")
            remaining = new_remaining
    upload = page.locator('input[type="file"]').first
    if upload.count() == 0:
        raise BuymaDraftError("BUYMA image upload input was not found")
    upload.set_input_files(image_paths)
    # Each image is uploaded to BUYMA's own storage individually once
    # selected; the save button isn't ready to submit until every one has
    # finished, which isn't signaled by any single network response, so a
    # settle wait is used instead (mirrors the same per-image delay already
    # relied on elsewhere in this form).
    page.wait_for_timeout(1500 * len(image_paths))
    assert_safe_buyma_draft_edit_page(page, draft_id)
    button = page.get_by_role("button", name="下書き保存する", exact=True)
    if button.count() == 0:
        button = page.get_by_role("button", name="下書き保存", exact=True)
    if button.count() == 0:
        raise BuymaDraftError("The draft-save button was not found; nothing was submitted")
    with page.expect_response(
        lambda response: response.url.endswith(f"/rorapi/sell/products/{draft_id}") and response.request.method == "PUT",
        timeout=20_000,
    ) as response_info:
        button.first.click()
    response = response_info.value
    if not response.ok:
        try:
            body = response.text()
        except Exception:
            body = "<unreadable response body>"
        raise BuymaDraftError(f"BUYMA rejected the image update for draft {draft_id} ({response.status}): {body}")
    page.wait_for_timeout(500)
    assert_safe_buyma_draft_edit_page(page, draft_id)
    logging.info("BUYMA draft %s images updated (+%d).", draft_id, len(image_paths))


def edit_buyma_draft_stock(
    page: Page,
    draft_id: str,
    size_stocks: list[SizeStock],
    *,
    size_variation: bool,
    size_unit: str = "",
    purchasable_quantity: int,
    on_hand_quantity: int = 0,
) -> None:
    """Update only the 販売可否/在庫 (stock) field of an already-saved BUYMA draft.

    Reuses _fill_size_inventory() (the same per-size stock table logic
    fill_buyma_draft() uses for a brand-new listing) against the draft's own
    /edit page, since stock is the one field expected to go stale between
    when a listing was first drafted and whenever it's next reviewed.
    """
    page.goto(f"https://www.buyma.com/my/sell/{draft_id}/edit", wait_until="networkidle", timeout=60_000)
    assert_safe_buyma_draft_edit_page(page, draft_id)
    page.get_by_text("販売可否/在庫", exact=True).first.wait_for(state="visible", timeout=20_000)
    _dismiss_blocking_overlays(page)
    _fill_size_inventory(
        page,
        size_stocks,
        size_variation=size_variation,
        size_unit=size_unit,
        purchasable_quantity=purchasable_quantity,
        on_hand_quantity=on_hand_quantity,
    )
    assert_safe_buyma_draft_edit_page(page, draft_id)
    button = page.get_by_role("button", name="下書き保存する", exact=True)
    if button.count() == 0:
        button = page.get_by_role("button", name="下書き保存", exact=True)
    if button.count() == 0:
        raise BuymaDraftError("The draft-save button was not found; nothing was submitted")
    with page.expect_response(
        lambda response: response.url.endswith(f"/rorapi/sell/products/{draft_id}") and response.request.method == "PUT",
        timeout=20_000,
    ) as response_info:
        button.first.click()
    response = response_info.value
    if not response.ok:
        try:
            body = response.text()
        except Exception:
            body = "<unreadable response body>"
        raise BuymaDraftError(f"BUYMA rejected the stock update for draft {draft_id} ({response.status}): {body}")
    page.wait_for_timeout(500)
    assert_safe_buyma_draft_edit_page(page, draft_id)
    logging.info("BUYMA draft %s stock updated.", draft_id)


def list_buyma_draft_source_urls(page: Page) -> dict[str, str]:
    """Return {draft_id: source_url} for every current BUYMA draft.

    The draft list's own visible row text truncates each row's source URL
    (e.g. "...prod..."), but each row also carries a `data-shop-urls`
    attribute with the full, untruncated JSON (confirmed live) — that's
    what's parsed here rather than the rendered text, since matching drafts
    back to local packages needs an exact, complete source_url.
    """
    mapping: dict[str, str] = {}
    page_number = 1
    empty_pages = 0
    while empty_pages < 2:
        page.goto(
            f"https://www.buyma.com/my/sell/?status=draft&tab=b&limit=100&page={page_number}",
            wait_until="networkidle",
            timeout=60_000,
        )
        page.wait_for_timeout(1200)
        row_attributes = re.findall(r'data-shop-urls="([^"]*)"', page.content())
        new_count = 0
        for raw in row_attributes:
            try:
                shop_urls = json.loads(html.unescape(raw))
            except json.JSONDecodeError:
                continue
            for entry in shop_urls:
                draft_id = entry.get("syo_id")
                source_url = entry.get("url")
                if draft_id and source_url and str(draft_id) not in mapping:
                    mapping[str(draft_id)] = source_url
                    new_count += 1
        empty_pages = empty_pages + 1 if not row_attributes else 0
        page_number += 1
    return mapping


def fill_buyma_draft(
    page: Page,
    package_folder: Path,
    *,
    save_draft: bool,
) -> None:
    payload = load_listing_package(package_folder)
    settings = payload["settings"]
    assert_safe_buyma_page(page)
    _dismiss_blocking_overlays(page)
    size_stocks = _refresh_source_stock(
        page.context, payload, size_variation=settings.get("size_variation", True)
    )
    if not any(item.in_stock for item in size_stocks):
        # BUYMA's own draft-save button silently fails to navigate to a new
        # draft when every size (or the whole no-size product) is out of
        # stock, since there is nothing purchasable to list (confirmed live
        # on multiple completely sold-out products). Raised early, before
        # the rest of the form is filled in, so a batch run fails fast on
        # this instead of timing out at the very end.
        raise OutOfStockError(
            f"{payload['brand']} {payload['sku']} is completely out of stock; BUYMA will not save a draft for it"
        )

    images = [str((package_folder / name).resolve()) for name in payload["image_files"]]
    _upload_images(page, images)
    _fill_near(page, "商品名", "input", settings["japanese_title"])
    _fill_near(page, "商品コメント", "textarea", settings["japanese_description"])
    _select_category(page, settings["buyma_category_path"])
    _select_brand(page, payload["brand"])
    _select_color(page, settings["color_family"], settings.get("color_name", ""))
    _fill_sizes(
        page,
        [item.size for item in size_stocks],
        settings.get("size_notes", ""),
        size_variation=settings.get("size_variation", True),
        size_unit=settings.get("size_unit", "cm"),
        category_path=settings.get("buyma_category_path", []),
    )
    _select_shipping_methods(
        page,
        settings.get("shipping_methods") or [settings["shipping_method"]],
        settings.get("buyer_shipping_jpy"),
    )
    _fill_purchase_and_price(page, payload)
    _fill_purchase_deadline(page, settings.get("purchase_deadline_days", 90))
    _fill_near(page, "出品メモ", "textarea", settings.get("private_memo", ""), required=False)
    _fill_supplier_memo(page, payload["supplier"], payload["source_url"])
    _fill_size_inventory(
        page,
        size_stocks,
        size_variation=settings.get("size_variation", True),
        size_unit=settings.get("size_unit", "cm"),
        purchasable_quantity=settings.get("purchasable_quantity", 1),
        on_hand_quantity=settings.get("on_hand_quantity", 0),
    )

    if save_draft:
        assert_safe_buyma_page(page)
        button = page.get_by_role("button", name="下書き保存する", exact=True)
        if button.count() == 0:
            button = page.get_by_role("button", name="下書き保存", exact=True)
        if button.count() == 0:
            raise BuymaDraftError("The draft-save button was not found; nothing was submitted")
        button.first.click()
        try:
            page.wait_for_url(re.compile(r"/my/sell/\d+/edit"), timeout=15_000)
        except PlaywrightTimeoutError as error:
            # Confirmed live: the save button can silently no-op and leave
            # the page on /my/sell/new instead of navigating to the new
            # draft's /edit URL, with no visible error on the page. Without
            # this check that was logged as a successful save.
            raise BuymaDraftError(
                f"BUYMA draft-save did not navigate to a saved draft; still on {page.url}"
            ) from error
        page.wait_for_timeout(1500)
        logging.info("BUYMA draft-save button clicked. Current URL: %s", page.url)


def _refresh_source_stock(context: BrowserContext, payload: dict, *, size_variation: bool = True) -> list[SizeStock]:
    source_url = str(payload["source_url"])
    parsed = urlsplit(source_url)
    supplier = _SUPPLIER_REGISTRY.get(parsed.netloc) if parsed.scheme == "https" else None
    if supplier is None:
        raise BuymaDraftError(f"Live stock refresh is not supported for this supplier URL: {source_url}")
    shop_code, parse_detail_html, ready_selector = supplier
    source = Source(
        shop_code=shop_code,
        shop_name=str(payload["supplier"]),
        target="women",
        category="Clothing",
        list_url=source_url,
    )
    stock_page = context.new_page()
    try:
        # A page fetch occasionally returns before the size widget has
        # finished rendering (confirmed live: identical fetch/parse code
        # reliably found sizes when re-run standalone against the same
        # product moments later), so an empty result on a product that's
        # expected to have real sizes is retried a couple of times before
        # giving up, rather than immediately treating it as a dead page.
        attempts = 4 if size_variation else 1
        product = None
        for attempt in range(1, attempts + 1):
            stock_page.goto(source_url, wait_until="domcontentloaded", timeout=60_000)
            stock_page.locator(ready_selector).first.wait_for(state="attached", timeout=20_000)
            # Some shops build their size picker via client-side JS after
            # the initial DOM attaches (confirmed live on antonia.it: a
            # standalone fetch/parse of the same URL reliably found sizes,
            # but the batch's back-to-back page loads sometimes captured
            # content before that JS finished), so a short settle time is
            # given before reading the page content.
            stock_page.wait_for_timeout(1200)
            product = parse_detail_html(stock_page.content(), source_url, source, target_sku=str(payload["sku"]))
            if product.sizes or attempt == attempts:
                break
            logging.warning(
                "%s page returned no sizes on attempt %d/%d; retrying",
                payload["supplier"],
                attempt,
                attempts,
            )
            stock_page.wait_for_timeout(2000)
    except Exception as error:
        raise BuymaDraftError(
            f"Could not verify current sizes and stock on {payload['supplier']}; draft was not saved"
        ) from error
    finally:
        stock_page.close()
    if not product.sizes:
        if not size_variation:
            # No-size accessories (bags, wallets, belts, sunglasses) have no
            # real size list to verify; fall back to the product's own
            # top-level availability instead of treating an empty list as a
            # failed page load (confirmed live: a Gucci bag page legitimately
            # renders no size/variant selector at all).
            in_stock = bool(product.in_stock)
            logging.info(
                "Live source stock verified (no-size product): %s",
                "available" if in_stock else "sold out",
            )
            return [SizeStock(size="FREE", in_stock=in_stock)]
        raise BuymaDraftError(
            f"{payload['supplier']} returned no size stock; draft was not saved to avoid stale inventory"
        )
    logging.info(
        "Live source stock verified: %s",
        ", ".join(f"{item.size}={'available' if item.in_stock else 'sold out'}" for item in product.sizes),
    )
    return product.sizes


def wait_for_new_listing(page: Page, timeout_ms: int = 600_000) -> None:
    page.goto(BUYMA_NEW_LISTING_URL, wait_until="domcontentloaded", timeout=60_000)
    try:
        page.wait_for_url("**/my/sell/new**", timeout=timeout_ms)
        page.get_by_text("新規出品", exact=True).first.wait_for(timeout=timeout_ms)
    except PlaywrightTimeoutError as error:
        raise BuymaDraftError("BUYMA login or new-listing page was not ready within 10 minutes") from error
    assert_safe_buyma_page(page)


def _section(page: Page, title: str) -> Locator:
    heading = page.get_by_text(title, exact=True).first
    if heading.count() == 0:
        raise BuymaDraftError(f"BUYMA field was not found: {title}")
    return heading.locator("xpath=ancestor::*[self::section or self::div][.//input or .//textarea or .//*[@role='combobox']][1]")


def _fill_near(page: Page, title: str, selector: str, value: str, *, required: bool = True) -> None:
    if not value and not required:
        return
    try:
        field = _section(page, title).locator(selector).first
        field.wait_for(state="visible", timeout=5000)
        field.fill(value)
    except (BuymaDraftError, PlaywrightTimeoutError) as error:
        if required:
            raise BuymaDraftError(f"Could not fill BUYMA field: {title}") from error
        logging.warning("Optional BUYMA field was skipped: %s", title)


def _upload_images(page: Page, images: list[str]) -> None:
    if not images:
        raise BuymaDraftError("No listing images are available")
    upload = page.locator('input[type="file"]').first
    if upload.count() == 0:
        raise BuymaDraftError("BUYMA image upload input was not found")
    upload.set_input_files(images)


def _select_category(page: Page, path: list[str]) -> None:
    section = _section(page, "カテゴリ")
    for index, value in enumerate(path):
        control = _wait_for_combobox(page, section, index, value)
        if control.evaluate("element => element.tagName") == "SELECT":
            control.select_option(label=value)
        else:
            _open_react_select(page, control)
            option = _wait_for_react_option(page, control, value)
            _safe_click(page, option)
        page.wait_for_timeout(400)


def _wait_for_combobox(page: Page, section: Locator, index: int, value: str) -> Locator:
    controls = section.locator("select, [role='combobox']")
    for _ in range(40):
        if controls.count() > index:
            control = controls.nth(index)
            if control.is_visible():
                return control
        page.wait_for_timeout(250)
    raise BuymaDraftError(f"BUYMA category level {index + 1} did not appear before selecting: {value}")


def _wait_for_react_option(page: Page, control: Locator, value: str) -> Locator:
    # Scoped to the open dropdown's own option list: a page-wide text search
    # (the previous approach) can match unrelated same-text cells elsewhere
    # on the page (e.g. a different row's already-selected label), silently
    # clicking the wrong element while the select's actual value is left at
    # its default. Different BUYMA form widgets nest their menu under
    # different ancestor levels, so every "Select"-classed ancestor (closest
    # first) is tried until one actually contains the target option.
    wrappers = control.locator("xpath=ancestor::*[contains(@class, 'Select')]")
    for _ in range(40):
        count = wrappers.count()
        for wrapper_index in range(count):
            menu = wrappers.nth(wrapper_index).locator(".Select-menu, [role='listbox']")
            matches = menu.get_by_text(value, exact=True)
            for index in range(matches.count()):
                candidate = matches.nth(index)
                if candidate.is_visible():
                    return candidate
        page.wait_for_timeout(250)
    raise BuymaDraftError(f"BUYMA option did not appear: {value}")


def _open_react_select(page: Page, control: Locator) -> None:
    wrapper = control.locator("xpath=ancestor::*[contains(@class, 'Select-control')][1]")
    target = wrapper if wrapper.count() else control
    _safe_click(page, target)
    page.wait_for_timeout(300)
    if control.get_attribute("aria-expanded") != "true":
        control.focus()
        control.press("ArrowDown")
        page.wait_for_timeout(300)


def _select_brand(page: Page, brand: str) -> None:
    section = _section(page, "ブランド")
    field = section.locator("input").first
    field.fill(brand)
    page.wait_for_timeout(1200)
    exact_matches = page.get_by_text(brand, exact=True)
    for index in range(exact_matches.count()):
        candidate = exact_matches.nth(index)
        if candidate.is_visible():
            _safe_click(page, candidate)
            return
    # BUYMA often renders the registered Japanese name together with the
    # Latin brand name, so an exact text match is unavailable. The first
    # autocomplete result is selected only after searching the full brand.
    field.press("ArrowDown")
    field.press("Enter")
    page.wait_for_timeout(500)


def _select_color(page: Page, family: str, name: str) -> None:
    section = _section(page, "色・サイズ")
    controls = section.locator("select, [role='combobox']")
    if controls.count() == 0:
        logging.warning("Color control was not found; leaving color for review")
        return
    control = controls.first
    if control.evaluate("element => element.tagName") == "SELECT":
        _select_color_family_option(control, family)
    else:
        _open_react_select(page, control)
        # BUYMA labels families like "ブラック（黒）系"; match by prefix, not exact
        # text, so configured "ブラック" still finds the option.
        option = page.get_by_text(family, exact=True)
        if option.count() == 0:
            option = page.get_by_text(re.compile(rf"^{re.escape(family)}"))
        if option.count() == 0:
            raise BuymaDraftError(f"BUYMA color family option was not found: {family}")
        _safe_click(page, option.last)
    if name:
        text_inputs = section.locator("input[type='text']")
        if text_inputs.count():
            text_inputs.last.fill(name)


def _select_color_family_option(control: Locator, family: str) -> None:
    options = control.locator("option")
    for index in range(options.count()):
        option = options.nth(index)
        if option.inner_text().strip().startswith(family):
            value = option.get_attribute("value")
            control.select_option(value=value)
            return
    raise BuymaDraftError(f"BUYMA color family option was not found: {family}")


def _fill_sizes(
    page: Page,
    sizes: list[str],
    notes: str,
    *,
    size_variation: bool,
    size_unit: str,
    category_path: list[str],
) -> None:
    section = _section(page, "色・サイズ")
    tab = section.get_by_text("サイズ", exact=True)
    if tab.count():
        tab.first.click()
    comboboxes = section.locator("select:visible, [role='combobox']:visible")
    if comboboxes.count():
        _select_control_label(page, comboboxes.first, "バリエーションあり" if size_variation else "バリエーションなし")
    if not size_variation:
        # BUYMA auto-fills a single "FREE SIZE" row (with 参考日本サイズ already
        # set to 指定なし) as soon as バリエーションなし is selected; there is no
        # size input or reference dropdown to fill in ourselves.
        if notes:
            textareas = section.locator("textarea")
            if textareas.count():
                textareas.last.fill(notes)
        return
    for _ in range(20):
        if comboboxes.count() > 1:
            _select_control_label(page, comboboxes.nth(1), size_unit)
            break
        page.wait_for_timeout(250)
    for index, size in enumerate(sizes):
        inputs = section.locator("input[type='text']")
        if index >= inputs.count():
            add = section.get_by_text("新しいサイズを追加", exact=False)
            if add.count():
                add.first.click()
                inputs = section.locator("input[type='text']")
        if index < inputs.count():
            inputs.nth(index).fill(size)
            row = inputs.nth(index).locator("xpath=ancestor::*[self::tr or self::div][.//*[@role='combobox'] or .//select][1]")
            references = row.locator("select, [role='combobox']")
            if references.count():
                reference = references.last
                reference_label = _reference_size_label(size, category_path)
                if reference.evaluate("element => element.tagName") == "SELECT":
                    try:
                        reference.select_option(label=reference_label)
                    except PlaywrightTimeoutError:
                        logging.warning(
                            "BUYMA reference size %r was unavailable for source size %r; using unspecified",
                            reference_label,
                            size,
                        )
                        reference.select_option(label="指定なし")
                else:
                    _open_react_select(page, reference)
                    try:
                        option = _wait_for_react_option(page, reference, reference_label)
                    except BuymaDraftError:
                        logging.warning(
                            "BUYMA reference size %r was unavailable for source size %r; using unspecified",
                            reference_label,
                            size,
                        )
                        _open_react_select(page, reference)
                        option = _wait_for_react_option(page, reference, "指定なし")
                    _safe_click(page, option)
    if notes:
        textareas = section.locator("textarea")
        if textareas.count():
            textareas.last.fill(notes)


def _select_control_label(page: Page, control: Locator, value: str, *, scope: Locator | None = None) -> None:
    if control.evaluate("element => element.tagName") == "SELECT":
        options = control.locator("option")
        matching_value: str | None = None
        for index in range(options.count()):
            option = options.nth(index)
            if value in option.inner_text():
                matching_value = option.get_attribute("value")
                break
        if matching_value is None:
            raise BuymaDraftError(f"BUYMA location option did not appear: {value}")
        control.select_option(value=matching_value)
    else:
        _open_react_select(page, control)
        if scope is not None:
            # The menu renders as a descendant of the same row/container as
            # the control itself (confirmed for the per-size stock table),
            # so searching there is simpler and more reliable than walking
            # up an ancestor chain for a "Select"-classed wrapper.
            menu = scope.locator(".Select-menu, [role='listbox']")
            option = None
            for _ in range(40):
                matches = menu.get_by_text(value, exact=True)
                for index in range(matches.count()):
                    candidate = matches.nth(index)
                    if candidate.is_visible():
                        option = candidate
                        break
                if option is not None:
                    break
                page.wait_for_timeout(250)
            if option is None:
                raise BuymaDraftError(f"BUYMA option did not appear: {value}")
        else:
            option = _wait_for_react_option(page, control, value)
        _safe_click(page, option)
        page.wait_for_timeout(500)


# EU/IT foot-length size -> BUYMA's cm reference scale (approximate, shown as
# 参考 only; brands vary by up to half a size). Each threshold is the largest
# EU/IT size that still maps to the given cm label; sizes above the last
# threshold fall through to the scale's own "以上" ceiling.
_SHOE_SIZE_CM_WOMEN = (
    (34.5, "21cm以下"), (35.0, "22cm"), (35.5, "22.5cm"), (36.0, "23cm"),
    (36.5, "23.5cm"), (37.0, "24cm"), (37.5, "24.5cm"), (38.0, "24.5cm"),
    (38.5, "25cm"), (39.0, "25.5cm"), (39.5, "26cm"), (40.0, "26cm"),
    (40.5, "26.5cm"),
)
_SHOE_SIZE_CM_WOMEN_CEILING = "27cm以上"
_SHOE_SIZE_CM_MEN = (
    (38.5, "23cm以下"), (39.0, "23cm以下"), (39.5, "23.5cm"), (40.0, "24cm"),
    (40.5, "24.5cm"), (41.0, "25cm"), (41.5, "25.5cm"), (42.0, "26cm"),
    (42.5, "26.5cm"), (43.0, "27cm"), (43.5, "27.5cm"), (44.0, "28cm"),
    (44.5, "28.5cm"),
)
_SHOE_SIZE_CM_MEN_CEILING = "29cm以上"


def _shoe_size_to_cm_label(italian_size: float, *, is_men: bool) -> str:
    table = _SHOE_SIZE_CM_MEN if is_men else _SHOE_SIZE_CM_WOMEN
    ceiling = _SHOE_SIZE_CM_MEN_CEILING if is_men else _SHOE_SIZE_CM_WOMEN_CEILING
    for threshold, label in table:
        if italian_size <= threshold:
            return label
    return ceiling


_SPELLED_OUT_SIZE_WORDS = {
    "XXSMALL": "XXS",
    "XSMALL": "XS",
    "SMALL": "S",
    "MEDIUM": "M",
    "LARGE": "L",
    "XLARGE": "XL",
    "XXLARGE": "XXL",
    "XXXLARGE": "XXXL",
    # flannels.com uses digit-prefixed plus sizes ("2X Large", "3X Large")
    # rather than "XXLarge"/"XXXLarge".
    "2XLARGE": "XXL",
    "3XLARGE": "XXXL",
    "4XLARGE": "XXXL",
}

# Matches a bare letter-size token anywhere in a compound size string (e.g.
# the "XS" in "8 (XS)", or the "S" in "S (46)"). Longest tokens first so
# "XXXL" isn't cut short by an earlier partial match of "XL".
_LETTER_SIZE_TOKEN = re.compile(r"\b(2XS|3XS|XXXL|XXL|XL|XXS|XS|[SML])\b")
_LETTER_SIZE_ALIASES = {"2XS": "XXS", "3XS": "XXS"}

# flannels.com shows footwear sizes as "UK (EU)", e.g. "7 (41)"; also used
# as a fallback for "EU (UK N)"-style compounds like "36 (UK 10)".
_PARENTHETICAL_NUMBER = re.compile(r"\((\d+(?:\.\d+)?)\)")
_LEADING_NUMBER = re.compile(r"^(\d+(?:\.\d+)?)")
# Waist size given in inches with a trailing "W" (e.g. "32W"). BUYMA's
# reference-size dropdown only offers XS以下/S/M/L/XL以上 (no separate XXS/XXL
# entries — see the XL/XXL/XXXL and XXS/XS collapses below), so these
# thresholds collapse the finer _WAIST_SIZE_REFERENCE_TABLE scale to match.
_WAIST_INCHES = re.compile(r"^(\d{2})W$")
_WAIST_SIZE_TABLE = ((25, "XS以下"), (27, "S"), (29, "M"), (31, "L"))
_WAIST_SIZE_CEILING = "XL以上"


def _reference_size_label(source_size: str, category_path: list[str] | None = None) -> str:
    normalized = source_size.strip().upper()
    # Some sites (e.g. flannels.com) spell sizes out in full ("Xsmall",
    # "Medium") instead of using the usual XS/S/M/L abbreviations.
    normalized = _SPELLED_OUT_SIZE_WORDS.get(normalized.replace(" ", "").replace("-", ""), normalized)
    # A letter-size token anywhere in the string is the brand's own explicit
    # guidance and takes priority over any numeric conversion table, since
    # numeric tables are only an approximation (e.g. "8 (XS)" or "S (46)").
    letter_match = _LETTER_SIZE_TOKEN.search(normalized)
    if letter_match:
        normalized = _LETTER_SIZE_ALIASES.get(letter_match.group(1), letter_match.group(1))
    else:
        waist_match = _WAIST_INCHES.match(normalized)
        parenthetical = _PARENTHETICAL_NUMBER.search(normalized)
        if waist_match:
            inches = int(waist_match.group(1))
            for threshold, label in _WAIST_SIZE_TABLE:
                if inches <= threshold:
                    return label
            return _WAIST_SIZE_CEILING
        if parenthetical:
            normalized = parenthetical.group(1)
        elif "(" in normalized:
            # A compound like "36 (UK 10)" where the parenthetical isn't a
            # bare number: fall back to the leading number outside it.
            leading = _LEADING_NUMBER.match(normalized)
            if leading:
                normalized = leading.group(1)
    if normalized in {"XL", "XXL", "XXXL"}:
        return "XL以上"
    if normalized in {"XXS", "XS"}:
        return "XS以下"
    # Source sizes are typically "IT"-prefixed (e.g. "IT38"); strip it before
    # testing for a numeric IT/EU size so the conversion below actually runs.
    numeric_text = normalized[2:] if normalized.startswith("IT") else normalized
    try:
        italian_size = float(numeric_text)
    except ValueError:
        return normalized
    is_men = bool(category_path) and category_path[0] == "メンズファッション"
    category = " / ".join(category_path or [])
    if any(term in category for term in ("靴", "シューズ", "ブーツ")):
        return _shoe_size_to_cm_label(italian_size, is_men=is_men)
    if not italian_size.is_integer():
        return "指定なし"
    whole_size = int(italian_size)
    # A bare number on a waist-sized garment (jeans/trousers/shorts) without
    # a "W" suffix (e.g. flannels.com's plain "30", "32") is a waist size in
    # inches, not an IT/EU dress size — those categories don't use the
    # 34-66 IT scale at all, so route it through the same waist table.
    if any(term in category for term in ("デニム", "ジーパン", "パンツ", "ショートパンツ")) and 22 <= whole_size <= 40:
        for threshold, label in _WAIST_SIZE_TABLE:
            if whole_size <= threshold:
                return label
        return _WAIST_SIZE_CEILING
    if is_men:
        # Standard menswear IT -> JP reference (brands vary; shown as 参考 only):
        # IT44 and below = XS, IT46 = S, IT48 = M, IT50 = L, IT52 and above = XL.
        if 40 <= whole_size <= 66 and whole_size % 2 == 0:
            if whole_size <= 44:
                return "XS以下"
            if whole_size == 46:
                return "S"
            if whole_size == 48:
                return "M"
            if whole_size == 50:
                return "L"
            return "XL以上"
        return "指定なし"
    # Standard womenswear IT -> JP reference (brands vary; shown as 参考 only):
    # IT38 and below = XS, IT40 = S, IT42 = M, IT44 = L, IT46 and above = XL.
    if 34 <= whole_size <= 60 and whole_size % 2 == 0:
        if whole_size <= 38:
            return "XS以下"
        if whole_size == 40:
            return "S"
        if whole_size == 42:
            return "M"
        if whole_size == 44:
            return "L"
        return "XL以上"
    # Designer numeric sizing (0-4, used by e.g. Zimmermann in place of IT/EU
    # sizes): 0 = XS, 1 = S, 2 = M, 3 = L, 4 and above = XL.
    if 0 <= whole_size <= 4:
        if whole_size == 0:
            return "XS以下"
        if whole_size == 1:
            return "S"
        if whole_size == 2:
            return "M"
        if whole_size == 3:
            return "L"
        return "XL以上"
    return "指定なし"


def _fill_purchase_and_price(page: Page, payload: dict) -> None:
    settings = payload["settings"]
    _check_label(page, "海外" if settings["purchasing_location"] == "overseas" else "国内", occurrence=0)
    _select_location_value(page, "買付地", settings.get("buying_region", "ヨーロッパ"), level=0)
    _select_location_value(page, "買付地", settings.get("buying_country", "イタリア"), level=1)
    _check_label(page, "国内" if settings["shipping_location"] == "domestic" else "海外", occurrence=-1)
    _select_location_value(page, "発送地", settings.get("shipping_prefecture", "神奈川県"), level=0)
    _fill_near(page, "商品価格", "input", str(settings["listing_price_jpy"]))
    if settings.get("duties_included"):
        checkbox = page.get_by_text("関税込み（購入者の関税負担なし）", exact=False)
        if checkbox.count():
            checkbox.first.click()


def _select_location_value(page: Page, section_title: str, value: str, *, level: int) -> None:
    section = _section(page, section_title)
    controls = section.locator("select, [role='combobox']")
    for _ in range(40):
        if controls.count() > level and controls.nth(level).is_visible():
            break
        page.wait_for_timeout(250)
    else:
        raise BuymaDraftError(f"BUYMA location selector did not appear: {section_title}")
    control = controls.nth(level)
    if control.evaluate("element => element.tagName") == "SELECT":
        options = control.locator("option")
        matching_value: str | None = None
        for index in range(options.count()):
            option = options.nth(index)
            if value in option.inner_text():
                matching_value = option.get_attribute("value")
                break
        if matching_value is None:
            raise BuymaDraftError(f"BUYMA location option did not appear: {value}")
        control.select_option(value=matching_value)
    else:
        _open_react_select(page, control)
        option = _wait_for_react_option(page, control, value)
        _safe_click(page, option)
        page.wait_for_timeout(500)


def _fill_purchase_deadline(page: Page, days: int) -> None:
    deadline = _purchase_deadline_date(date.today(), days)
    value = deadline.strftime("%Y/%m/%d")
    section = _section(page, "購入期限(日本時間)")
    field = section.locator("input:visible").last
    try:
        field.wait_for(state="visible", timeout=5_000)
        field.click()
        field.press("Meta+A")
        field.fill(value, timeout=5_000)
        field.press("Tab")
    except PlaywrightTimeoutError:
        pass
    if _normalized_date(field.input_value()) != _normalized_date(value):
        _select_calendar_date(page, field, deadline)
    if _normalized_date(field.input_value()) != _normalized_date(value):
        raise BuymaDraftError(
            f"BUYMA purchase deadline was not accepted: expected {value}, actual {field.input_value()!r}"
        )


def _normalized_date(value: str) -> str:
    return value.replace("-", "/").lstrip("0")


def _purchase_deadline_date(today: date, days: int) -> date:
    # BUYMA counts today as day one of its maximum 90-day purchase window.
    return today + timedelta(days=days - 1)


def _select_calendar_date(page: Page, field: Locator, deadline: date) -> None:
    field.click()
    page.wait_for_timeout(300)
    calendar_selectors = (
        ".react-datepicker:visible",
        ".ui-datepicker:visible",
        ".rdtPicker:visible",
        "[class*='calendar']:visible",
        "[class*='Calendar']:visible",
    )
    calendar: Locator | None = None
    for selector in calendar_selectors:
        candidates = page.locator(selector)
        if candidates.count():
            calendar = candidates.last
            break
    if calendar is None:
        raise BuymaDraftError("BUYMA purchase-deadline calendar did not appear")

    next_selectors = (
        "button[aria-label*='次']",
        "button[aria-label*='Next' i]",
        ".react-datepicker__navigation--next",
        ".ui-datepicker-next",
        ".rdtNext",
        "[class*='next' i]",
    )
    for _ in range(12):
        exact_date = page.locator(
            f'.react-datepicker__day[aria-label^="Choose {deadline.year}年{deadline.month}月{deadline.day}日"]'
        )
        if exact_date.count() and exact_date.first.is_visible():
            _safe_click(page, exact_date.first)
            page.wait_for_timeout(300)
            return
        for selector in calendar_selectors:
            candidates = page.locator(selector)
            if candidates.count() and candidates.last.is_visible():
                calendar = candidates.last
                break
        next_button: Locator | None = None
        for selector in next_selectors:
            matches = calendar.locator(selector)
            if matches.count() and matches.first.is_visible():
                next_button = matches.first
                break
        if next_button is None:
            raise BuymaDraftError("BUYMA calendar next-month button did not appear")
        disabled = next_button.is_disabled() or next_button.get_attribute("aria-disabled") == "true"
        classes = next_button.get_attribute("class") or ""
        if disabled or "disabled" in classes.lower():
            raise BuymaDraftError("BUYMA calendar stopped before the requested deadline month")
        _safe_click(page, next_button)
        page.wait_for_timeout(250)

    raise BuymaDraftError(f"BUYMA calendar had no selectable date for {deadline:%Y/%m/%d}")


def _fill_supplier_memo(page: Page, supplier: str, source_url: str) -> None:
    section = _section(page, "買付先メモ")
    fields = section.locator("input[type='text']:visible")
    if fields.count() < 2:
        raise BuymaDraftError("BUYMA supplier-memo name and URL fields did not appear")
    fields.nth(0).fill(supplier)
    fields.nth(1).fill(source_url)
    if fields.count() > 2:
        fields.nth(2).fill("仕入先の商品ページ")


def _stock_table_row(section: Locator, size: str, *, size_unit: str = "") -> Locator:
    # BUYMA concatenates the size unit directly onto the size in this
    # table's row label with no separator (e.g. size "L" + unit "cm" renders
    # as "Lcm", confirmed live) whenever a unit is set; the plain size is
    # tried first since most numeric/no-unit sizes render unchanged.
    candidates = [size]
    if size_unit and size_unit not in ("指定なし", ""):
        candidates.append(f"{size}{size_unit}")
    for candidate_text in candidates:
        label = section.get_by_text(candidate_text, exact=True)
        for index in range(label.count()):
            candidate = label.nth(index)
            if candidate.is_visible():
                return candidate.locator("xpath=ancestor::tr[1]")
    raise BuymaDraftError(f"BUYMA stock table row did not appear for size: {size}")


def _fill_size_inventory(
    page: Page,
    size_stocks: list[SizeStock],
    *,
    size_variation: bool,
    size_unit: str = "",
    purchasable_quantity: int,
    on_hand_quantity: int,
) -> None:
    marker = page.get_by_text("買付できる合計数量を入力", exact=False)
    try:
        marker.first.wait_for(state="visible", timeout=10_000)
    except PlaywrightTimeoutError as error:
        raise BuymaDraftError("BUYMA size inventory controls did not appear") from error
    section = marker.first.locator(
        "xpath=ancestor::*[self::section or self::div]"
        "[contains(., '手元に在庫あり合計数量')]"
        "[.//select or .//*[@role='combobox']][1]"
    )
    if size_variation:
        # Each size has its own <tr>, holding exactly one stock dropdown;
        # finding the row by its own size label (rather than indexing into
        # every combobox in the section, which also picks up an unrelated
        # "sell-stock-filter" control and made row/index alignment
        # unreliable) guarantees the right dropdown is set for the right size.
        for item in size_stocks:
            row = _stock_table_row(section, item.size, size_unit=size_unit)
            control = row.locator("select, [role='combobox']").first
            # The real "not in stock" option is labeled "在庫なし", not
            # "買付不可" (confirmed live; "買付不可" never appears as an
            # option at all).
            _select_control_label(page, control, "買付可" if item.in_stock else "在庫なし", scope=row)
    else:
        # No-size products (size_variation=False) get a single BUYMA-created
        # free-size row, not one row per source "size" entry. size_stocks
        # here can hold values with no real size meaning (e.g. sunglasses,
        # where flannels.com reuses the size-selector DOM slot for its
        # color-swatch codes); matching by that text against the single row
        # would fail or, worse, coincidentally match unrelated text. The
        # overall in-stock state is set on the one row that exists instead.
        rows = section.locator("tr.sell-stock-table__head-row, tr.sell-stock-table__rest-row")
        try:
            rows.first.wait_for(state="visible", timeout=10_000)
        except PlaywrightTimeoutError as error:
            raise BuymaDraftError("BUYMA free-size inventory row did not appear") from error
        row = rows.first
        control = row.locator("select, [role='combobox']").first
        available_now = any(item.in_stock for item in size_stocks)
        _select_control_label(page, control, "買付可" if available_now else "在庫なし", scope=row)

    available = any(item.in_stock for item in size_stocks)
    purchase_total = purchasable_quantity if available else 0
    purchase_input = marker.first.locator("xpath=following::input[1]")
    if purchase_input.count() == 0:
        raise BuymaDraftError("BUYMA purchasable-quantity input did not appear")
    if purchase_input.is_disabled():
        # BUYMA disables this field itself (and leaves it blank, not "0")
        # once every size is marked 在庫なし, since there is nothing left to
        # buy; confirmed live on a completely sold-out product. Trying to
        # fill a disabled field just times out, so only proceed if there was
        # nothing to set in the first place.
        if purchase_total != 0:
            raise BuymaDraftError(
                f"BUYMA purchasable-quantity input is disabled but {purchase_total} was expected"
            )
    else:
        purchase_input.fill(str(purchase_total))
        if purchase_input.input_value() != str(purchase_total):
            raise BuymaDraftError("BUYMA purchasable quantity was not accepted")

    if on_hand_quantity != 0:
        raise BuymaDraftError("Non-zero on-hand inventory is not supported by the BUYMA draft filler")
    logging.info(
        "BUYMA inventory filled: purchasable=%d; on-hand inventory remains at BUYMA's default %d",
        purchase_total,
        on_hand_quantity,
    )


def _select_shipping_methods(
    page: Page,
    methods: list[str],
    buyer_shipping_jpy: int | None = None,
) -> None:
    """Check every configured shipping method that exists on the form.

    BUYMA allows multiple shipping methods per listing, so all configured
    methods found on the form are checked. Methods missing from the form are
    logged and skipped. Raises ShippingNotAvailableError only when none of
    the configured methods exist, so callers can skip the product.
    """
    section = _section(page, "配送方法")
    checkboxes = section.locator("input[type='checkbox']")
    try:
        checkboxes.first.wait_for(state="attached", timeout=15_000)
    except Exception:
        raise ShippingNotAvailableError(
            "The shipping section rendered no checkboxes within 15s."
        )

    selected: list[str] = []
    missing: list[str] = []
    for method_number, method in enumerate(methods):
        price = buyer_shipping_jpy if method_number == 0 else None
        if _try_select_shipping(page, section, method, price):
            selected.append(method)
        else:
            missing.append(method)

    if missing:
        logging.warning(
            "Shipping methods not shown on this form and skipped: %s", missing
        )
    if not selected:
        available = _available_shipping_texts(section)
        raise ShippingNotAvailableError(
            "None of the configured shipping methods were found. "
            f"Tried: {methods}. Methods displayed on this form: {available}"
        )
    logging.info("Selected shipping methods: %s", selected)


def _try_select_shipping(
    page: Page,
    section: Locator,
    method: str,
    buyer_shipping_jpy: int | None,
) -> bool:
    # Matched against each checkbox's own (closest) row only - matching against
    # every ancestor level (the previous approach) let a broader shared
    # container (e.g. the whole shipping table) match for unrelated
    # checkboxes too, since it contains every row's text, which produced
    # multiple ambiguous "candidates" for methods like ゆうパケット and
    # silently failed a listing that had exactly one configured method.
    checkboxes = section.locator("input[type='checkbox']")
    target: Locator | None = None
    method_candidates: list[tuple[Locator, str]] = []
    for checkbox_index in range(checkboxes.count()):
        checkbox = checkboxes.nth(checkbox_index)
        row = checkbox.locator("xpath=ancestor::*[self::tr or self::div][1]")
        if row.count() == 0:
            continue
        text = row.first.inner_text()
        if not _shipping_method_matches(text, method):
            continue
        method_candidates.append((checkbox, text))
        if buyer_shipping_jpy is not None and _contains_yen_price(text, buyer_shipping_jpy):
            target = checkbox
            break
    if target is None and len(method_candidates) == 1:
        target, displayed_text = method_candidates[0]
        logging.warning(
            "BUYMA did not display configured shipping price ¥%s; selected the sole matching method: %s",
            buyer_shipping_jpy,
            " ".join(displayed_text.split()),
        )
    if target is None:
        return False
    if not target.is_checked():
        if target.is_visible():
            target.check()
        else:
            target.evaluate("element => element.click()")
    if not target.is_checked():
        raise BuymaDraftError(f"BUYMA shipping method could not be selected: {method}")
    return True


def _available_shipping_texts(section: Locator) -> list[str]:
    """Return the visible shipping method labels for diagnostics."""
    texts: list[str] = []
    checkboxes = section.locator("input[type='checkbox']")
    for checkbox_index in range(checkboxes.count()):
        checkbox = checkboxes.nth(checkbox_index)
        row = checkbox.locator("xpath=ancestor::*[self::tr or self::div][1]")
        try:
            text = " ".join(row.inner_text().split())
        except Exception:
            continue
        if text and text not in texts:
            texts.append(text[:120])
    return texts


def _shipping_method_matches(display_text: str, configured_method: str) -> bool:
    displayed = _normalized_shipping_text(display_text)
    configured = _normalized_shipping_text(configured_method)
    if configured in displayed:
        return True
    if "ゆうパケット" in configured:
        return "ゆうパケット" in displayed
    if "ゆうパック" in configured:
        if "ゆうパック" not in displayed:
            return False
        for size in ("60サイズ", "80サイズ", "100サイズ", "120サイズ"):
            if size in configured:
                return size in displayed
    return False


def _normalized_shipping_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    return "".join(normalized.split()).replace("−", "-").replace("–", "-").replace("—", "-")


def _contains_yen_price(text: str, amount: int) -> bool:
    normalized = unicodedata.normalize("NFKC", text).replace(",", "")
    return re.search(rf"(?<!\d){amount}(?!\d)", normalized) is not None


def _check_label(page: Page, label: str, occurrence: int) -> None:
    matches = page.get_by_text(label, exact=True)
    if matches.count() == 0:
        raise BuymaDraftError(f"BUYMA option was not found: {label}")
    target = matches.nth(occurrence if occurrence >= 0 else matches.count() - 1)
    _safe_click(page, target)


def _safe_click(page: Page, locator: Locator) -> None:
    """Click a known form control despite BUYMA's decorative inner layers."""
    _dismiss_blocking_overlays(page)
    try:
        locator.click(timeout=3_000)
    except PlaywrightTimeoutError:
        _dismiss_blocking_overlays(page, force=True)
        try:
            locator.wait_for(state="attached", timeout=3_000)
            locator.evaluate("element => element.click()")
        except PlaywrightTimeoutError as retry_error:
            raise BuymaDraftError("BUYMA form control remained blocked after a targeted retry") from retry_error


def _dismiss_blocking_overlays(page: Page, *, force: bool = False) -> None:
    overlay = page.locator("#driver-page-overlay")
    if overlay.count() == 0:
        return
    page.keyboard.press("Escape")
    close_selectors = (
        ".driver-popover-close-btn",
        ".driver-close-btn",
        "button:has-text('スキップ')",
        "button:has-text('閉じる')",
    )
    for selector in close_selectors:
        button = page.locator(selector)
        if button.count() and button.first.is_visible():
            button.first.click(force=True)
            page.wait_for_timeout(300)
            break
    if overlay.count() and overlay.first.is_visible():
        # This is only BUYMA's guided-tour layer, not an authentication or
        # security control. Removing it restores the underlying form.
        page.evaluate(
            """() => {
                document.querySelector('#driver-page-overlay')?.remove();
                document.querySelectorAll('.driver-popover, .driver-popover-wrapper').forEach(node => node.remove());
                document.documentElement.style.overflow = '';
                document.body.style.overflow = '';
            }"""
        )
        page.wait_for_timeout(200)
