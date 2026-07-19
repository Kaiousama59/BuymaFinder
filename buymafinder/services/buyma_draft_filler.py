from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import BrowserContext, Locator, Page, TimeoutError as PlaywrightTimeoutError


BUYMA_NEW_LISTING_URL = "https://www.buyma.com/my/sell/new?tab=b"


class BuymaDraftError(RuntimeError):
    pass


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

    images = [str((package_folder / name).resolve()) for name in payload["image_files"]]
    _upload_images(page, images)
    _fill_near(page, "商品名", "input", settings["japanese_title"])
    _fill_near(page, "商品コメント", "textarea", settings["japanese_description"])
    _select_category(page, settings["buyma_category_path"])
    _select_brand(page, payload["brand"])
    _select_color(page, settings["color_family"], settings.get("color_name", ""))
    _fill_sizes(page, payload.get("source_sizes", []), settings.get("size_notes", ""))
    _select_shipping(page, settings["shipping_method"])
    _fill_purchase_and_price(page, payload)
    _fill_near(page, "出品メモ", "textarea", settings.get("private_memo", ""), required=False)

    if save_draft:
        assert_safe_buyma_page(page)
        button = page.get_by_role("button", name="下書き保存する", exact=True)
        if button.count() == 0:
            button = page.get_by_role("button", name="下書き保存", exact=True)
        if button.count() == 0:
            raise BuymaDraftError("The draft-save button was not found; nothing was submitted")
        button.first.click()
        page.wait_for_timeout(1500)
        logging.info("BUYMA draft-save button clicked. Current URL: %s", page.url)


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
            option = _wait_for_react_option(page, value)
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


def _wait_for_react_option(page: Page, value: str) -> Locator:
    for _ in range(40):
        matches = page.get_by_text(value, exact=True)
        for index in range(matches.count()):
            candidate = matches.nth(index)
            if candidate.is_visible():
                return candidate
        page.wait_for_timeout(250)
    raise BuymaDraftError(f"BUYMA category option did not appear: {value}")


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
        control.select_option(label=family)
    else:
        _open_react_select(page, control)
        _safe_click(page, page.get_by_text(family, exact=True).last)
    if name:
        text_inputs = section.locator("input[type='text']")
        if text_inputs.count():
            text_inputs.last.fill(name)


def _fill_sizes(page: Page, sizes: list[str], notes: str) -> None:
    section = _section(page, "色・サイズ")
    tab = section.get_by_text("サイズ", exact=True)
    if tab.count():
        tab.first.click()
    for index, size in enumerate(sizes):
        inputs = section.locator("input[type='text']")
        if index >= inputs.count():
            add = section.get_by_text("新しいサイズを追加", exact=False)
            if add.count():
                add.first.click()
                inputs = section.locator("input[type='text']")
        if index < inputs.count():
            inputs.nth(index).fill(size)
    if notes:
        textareas = section.locator("textarea")
        if textareas.count():
            textareas.last.fill(notes)


def _fill_purchase_and_price(page: Page, payload: dict) -> None:
    settings = payload["settings"]
    _check_label(page, "海外" if settings["purchasing_location"] == "overseas" else "国内", occurrence=0)
    _fill_near(page, "買付先ショップ名", "input", payload["supplier"][:30], required=False)
    _check_label(page, "国内" if settings["shipping_location"] == "domestic" else "海外", occurrence=-1)
    _fill_near(page, "商品価格", "input", str(settings["listing_price_jpy"]))
    if settings.get("duties_included"):
        checkbox = page.get_by_text("関税込み（購入者の関税負担なし）", exact=False)
        if checkbox.count():
            checkbox.first.click()


def _select_shipping(page: Page, method: str) -> None:
    section = _section(page, "配送方法")
    option = section.get_by_text(method, exact=True)
    if option.count() == 0:
        raise BuymaDraftError(
            f"Saved BUYMA shipping method was not found: {method}. Add it once in BUYMA and run again."
        )
    row = option.first.locator("xpath=ancestor::*[self::tr or self::div][.//input[@type='checkbox']][1]")
    checkbox = row.locator("input[type='checkbox']")
    if checkbox.count():
        target = checkbox.first
        if not target.is_checked():
            if target.is_visible():
                target.check()
            else:
                target.evaluate("element => element.click()")
        if not target.is_checked():
            raise BuymaDraftError(f"BUYMA shipping method could not be selected: {method}")
    else:
        option.first.click()


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
