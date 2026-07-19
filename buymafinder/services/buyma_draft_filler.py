from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import BrowserContext, Locator, Page, TimeoutError as PlaywrightTimeoutError

from buymafinder.core.models import SizeStock, Source
from buymafinder.shops.eleonora import parse_product_detail_html


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
    size_stocks = _refresh_source_stock(page.context, payload)

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
    )
    _select_shipping(page, settings["shipping_method"])
    _fill_purchase_and_price(page, payload)
    _fill_purchase_deadline(page, settings.get("purchase_deadline_days", 90))
    _fill_near(page, "出品メモ", "textarea", settings.get("private_memo", ""), required=False)
    _fill_supplier_memo(page, payload["supplier"], payload["source_url"])
    _fill_size_inventory(
        page,
        size_stocks,
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
        page.wait_for_timeout(1500)
        logging.info("BUYMA draft-save button clicked. Current URL: %s", page.url)


def _refresh_source_stock(context: BrowserContext, payload: dict) -> list[SizeStock]:
    source_url = str(payload["source_url"])
    parsed = urlsplit(source_url)
    if parsed.scheme != "https" or parsed.netloc not in {
        "eleonorabonucci.com",
        "www.eleonorabonucci.com",
    }:
        raise BuymaDraftError(f"Live stock refresh is not supported for this supplier URL: {source_url}")
    source = Source(
        shop_code="eleonora",
        shop_name=str(payload["supplier"]),
        target="women",
        category="Clothing",
        list_url=source_url,
    )
    stock_page = context.new_page()
    try:
        stock_page.goto(source_url, wait_until="domcontentloaded", timeout=60_000)
        stock_page.locator("select option").first.wait_for(state="attached", timeout=20_000)
        product = parse_product_detail_html(stock_page.content(), source_url, source)
    except Exception as error:
        raise BuymaDraftError(
            "Could not verify current sizes and stock on Eleonora Bonucci; draft was not saved"
        ) from error
    finally:
        stock_page.close()
    if not product.sizes:
        raise BuymaDraftError(
            "Eleonora Bonucci returned no size stock; draft was not saved to avoid stale inventory"
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
        control.select_option(label=family)
    else:
        _open_react_select(page, control)
        _safe_click(page, page.get_by_text(family, exact=True).last)
    if name:
        text_inputs = section.locator("input[type='text']")
        if text_inputs.count():
            text_inputs.last.fill(name)


def _fill_sizes(
    page: Page,
    sizes: list[str],
    notes: str,
    *,
    size_variation: bool,
    size_unit: str,
) -> None:
    section = _section(page, "色・サイズ")
    tab = section.get_by_text("サイズ", exact=True)
    if tab.count():
        tab.first.click()
    comboboxes = section.locator("select:visible, [role='combobox']:visible")
    if comboboxes.count():
        _select_control_label(page, comboboxes.first, "バリエーションあり" if size_variation else "バリエーションなし")
    if size_variation:
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
                reference_label = _reference_size_label(size)
                if reference.evaluate("element => element.tagName") == "SELECT":
                    reference.select_option(label=reference_label)
                else:
                    _open_react_select(page, reference)
                    _safe_click(page, _wait_for_react_option(page, reference_label))
    if notes:
        textareas = section.locator("textarea")
        if textareas.count():
            textareas.last.fill(notes)


def _select_control_label(page: Page, control: Locator, value: str) -> None:
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
        visible = page.get_by_text(value, exact=True)
        for index in range(visible.count()):
            candidate = visible.nth(index)
            if candidate.is_visible():
                _safe_click(page, candidate)
                return
        search = control.locator("input")
        if search.count():
            search.first.fill(value)
            search.first.press("ArrowDown")
            search.first.press("Enter")
        else:
            control.focus()
            page.keyboard.type(value)
            page.keyboard.press("ArrowDown")
            page.keyboard.press("Enter")
        page.wait_for_timeout(500)


def _reference_size_label(source_size: str) -> str:
    normalized = source_size.strip().upper()
    if normalized in {"XL", "XXL", "XXXL"}:
        return "XL以上"
    if normalized in {"XXS", "XS"}:
        return "XS以下"
    return normalized


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
        visible = page.get_by_text(value, exact=True)
        for index in range(visible.count()):
            candidate = visible.nth(index)
            if candidate.is_visible():
                _safe_click(page, candidate)
                return
        search = control.locator("input")
        if search.count():
            search.first.fill(value)
            search.first.press("ArrowDown")
            search.first.press("Enter")
        else:
            control.focus()
            page.keyboard.type(value)
            page.keyboard.press("ArrowDown")
            page.keyboard.press("Enter")
        page.wait_for_timeout(500)


def _fill_purchase_deadline(page: Page, days: int) -> None:
    deadline = date.today() + timedelta(days=days)
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
        _select_latest_calendar_date(page, field)
    if _normalized_date(field.input_value()) != _normalized_date(value):
        raise BuymaDraftError(f"BUYMA purchase deadline was not accepted: {value}")


def _normalized_date(value: str) -> str:
    return value.replace("-", "/").lstrip("0")


def _select_latest_calendar_date(page: Page, field: Locator) -> None:
    field.click()
    page.wait_for_timeout(300)
    calendar_selectors = (
        ".react-datepicker:visible",
        ".ui-datepicker:visible",
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
    )
    for _ in range(6):
        next_button: Locator | None = None
        for selector in next_selectors:
            matches = calendar.locator(selector)
            if matches.count() and matches.first.is_visible():
                next_button = matches.first
                break
        if next_button is None:
            break
        disabled = next_button.is_disabled() or next_button.get_attribute("aria-disabled") == "true"
        classes = next_button.get_attribute("class") or ""
        if disabled or "disabled" in classes.lower():
            break
        _safe_click(page, next_button)
        page.wait_for_timeout(250)

    day_selectors = (
        ".react-datepicker__day:not(.react-datepicker__day--disabled):not(.react-datepicker__day--outside-month)",
        "td:not(.ui-datepicker-unselectable) a",
        "[role='gridcell']:not([aria-disabled='true']) button",
        "button[data-date]:not([disabled])",
    )
    for selector in day_selectors:
        days = calendar.locator(selector)
        visible_days = [days.nth(index) for index in range(days.count()) if days.nth(index).is_visible()]
        if visible_days:
            _safe_click(page, visible_days[-1])
            page.wait_for_timeout(300)
            return
    raise BuymaDraftError("BUYMA calendar had no selectable purchase-deadline date")


def _fill_supplier_memo(page: Page, supplier: str, source_url: str) -> None:
    button = page.get_by_text("買付先メモを設定", exact=True)
    if button.count() == 0:
        raise BuymaDraftError("BUYMA supplier-memo button did not appear")
    _safe_click(page, button.first)
    page.wait_for_timeout(400)
    dialog = page.get_by_role("dialog")
    if dialog.count() == 0:
        dialog = page.locator(".modal:visible, [class*='modal']:visible")
    try:
        dialog.last.wait_for(state="visible", timeout=5_000)
    except PlaywrightTimeoutError as error:
        raise BuymaDraftError("BUYMA supplier-memo dialog did not appear") from error
    modal = dialog.last
    memo_fields = modal.locator("textarea:visible, input[type='text']:visible")
    link_fields = modal.locator("input[type='url']:visible, input[placeholder*='URL' i]:visible, input[placeholder*='リンク']:visible")
    if memo_fields.count() == 0:
        raise BuymaDraftError("BUYMA supplier-memo text field did not appear")
    memo_fields.first.fill(f"仕入先サイト: {supplier}")
    if link_fields.count():
        link_fields.first.fill(source_url)
    elif memo_fields.count() > 1:
        memo_fields.nth(1).fill(source_url)
    else:
        raise BuymaDraftError("BUYMA supplier-memo link field did not appear")
    save = modal.get_by_role("button", name="設定する", exact=True)
    if save.count() == 0:
        save = modal.get_by_role("button", name="保存", exact=True)
    if save.count() == 0:
        raise BuymaDraftError("BUYMA supplier-memo save button did not appear")
    _safe_click(page, save.first)


def _fill_size_inventory(
    page: Page,
    size_stocks: list[SizeStock],
    *,
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
    controls = section.locator("select:visible, [role='combobox']:visible")
    if controls.count() < len(size_stocks):
        raise BuymaDraftError(
            f"BUYMA showed {controls.count()} inventory rows for {len(size_stocks)} source sizes"
        )
    for index, item in enumerate(size_stocks):
        _select_control_label(page, controls.nth(index), "買付可" if item.in_stock else "買付不可")

    available = any(item.in_stock for item in size_stocks)
    purchase_total = purchasable_quantity if available else 0
    purchase_input = marker.first.locator("xpath=following::input[1]")
    if purchase_input.count() == 0:
        raise BuymaDraftError("BUYMA purchasable-quantity input did not appear")
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
