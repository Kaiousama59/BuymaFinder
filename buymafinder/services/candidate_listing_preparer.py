from __future__ import annotations

import csv
import json
import re
import unicodedata
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from pathlib import Path

from buymafinder.core.listing_models import ListingSettings
from buymafinder.core.models import Product
from buymafinder.services.listing_preparer import prepare_listing_package


class CandidatePreparationError(ValueError):
    """Raised when a candidate cannot be converted without guessing listing data."""


def _w(*path: str) -> tuple[str, ...]:
    return ("レディースファッション", *path)


def _m(*path: str) -> tuple[str, ...]:
    return ("メンズファッション", *path)


# Bags default to the women's category regardless of which gendered source
# collection they were scraped from: bag styles (pouches, totes, clutches)
# sell better listed as women's on BUYMA even when the supplier files them
# under menswear. A target_override still wins if one is set explicitly.
_BAG_KEYWORDS = ("BAG", "TOTE", "CLUTCH", "POUCH", "VANITY", "DUFFLE", "DUFFEL", "BAGUETTE")


# Each rule is (keywords, japanese_type, women_category_path, men_category_path).
# The men's and women's BUYMA category trees use different level-2/level-3 names
# (e.g. women's "ブーツ" is its own top-level branch with subtypes; men's is a
# single "靴・ブーツ・サンダル > ブーツ" leaf), so the two paths are looked up
# independently rather than swapping just the top-level label. A men's path of
# None means no reviewed equivalent exists yet; classify_product raises rather
# than guessing one.
_PRODUCT_RULES = (
    # Footwear/bag product-type keywords are checked before the generic
    # material keywords below (e.g. "LEATHER") on purpose: a product like
    # "Black Leather Duchesse Pumps" contains "LEATHER" but is shoes, not a
    # leather jacket, so the more specific product-type match must win.
    (("PUMPS",), "パンプス", _w("靴・シューズ", "パンプス"), None),
    # "SLIDER" is flannels.com's term for slide sandals.
    (("FLIP-FLOP", "FLIP FLOP", "SANDAL", "MULE", "SLIDER"), "サンダル・ミュール", _w("靴・シューズ", "サンダル・ミュール"), _m("靴・ブーツ・サンダル", "サンダル")),
    # "TRAINERS" is the British-English term for sneakers, used by UK sites (e.g. flannels.com).
    (("SNEAKER", "TRAINER"), "スニーカー", _w("靴・シューズ", "スニーカー"), _m("靴・ブーツ・サンダル", "スニーカー")),
    (("SLIP-ON", "SLIP ON", "SLIPON"), "スリッポン", _w("靴・シューズ", "スリッポン"), None),
    (("RAIN BOOT",), "レインブーツ", _w("ブーツ", "レインブーツ"), _m("靴・ブーツ・サンダル", "ブーツ")),
    (("ANKLE BOOT",), "ショートブーツ・ブーティ", _w("ブーツ", "ショートブーツ・ブーティ"), _m("靴・ブーツ・サンダル", "ブーツ")),
    (("KNEE BOOT", "OVER-THE-KNEE", "OVER THE KNEE"), "ロングブーツ", _w("ブーツ", "ロングブーツ"), _m("靴・ブーツ・サンダル", "ブーツ")),
    (("BOOT",), "ブーツその他", _w("ブーツ", "ブーツその他"), _m("靴・ブーツ・サンダル", "ブーツ")),
    # Bare "OXFORD" deliberately excluded: it's a common cotton-weave fabric
    # name ("Oxford cotton" shirting), not just a shoe style, and a plain
    # substring match would misclassify a shirt as Oxford shoes (confirmed
    # live: a "Camicia...in Oxford di Cotone" shirt was misclassified this way).
    (("LOAFER", "OXFORD SHOE", "SCARPE OXFORD"), "ローファー・オックスフォード", _w("靴・シューズ", "ローファー・オックスフォード"), _m("靴・ブーツ・サンダル", "ドレスシューズ・革靴・ビジネスシューズ")),
    (("BALLET FLAT", "BALLERINA"), "バレエシューズ", _w("靴・シューズ", "バレエシューズ"), None),
    (("FLAT SHOE", "FLATS"), "フラットシューズ", _w("靴・シューズ", "フラットシューズ"), None),
    # "Duffle coat"/"duffel coat" is a garment (toggle-fastened wool coat),
    # not a duffle bag; checked before the generic DUFFLE/DUFFEL bag keyword
    # below so "Hooded Duffle Coat" doesn't misclassify as a bag.
    (("DUFFLE COAT", "DUFFEL COAT"), "コート", _w("アウター", "コート"), _m("アウター・ジャケット", "ピーコート")),
    (_BAG_KEYWORDS, "ショルダーバッグ・ポシェット", _w("バッグ・カバン", "ショルダーバッグ・ポシェット"), _m("バッグ・カバン", "バッグ・カバンその他")),
    (("CARD HOLDER", "CARDHOLDER"), "カードケース・名刺入れ", _w("財布・小物", "カードケース・名刺入れ"), _m("財布・雑貨", "カードケース・名刺入れ")),
    # Also checked before the generic LEATHER rule below: a leather keychain/
    # keyring's own material composition text would otherwise false-match as
    # a leather jacket (confirmed live: a "Double-label keychain in black
    # grained calf leather" was misclassified this way). Women's tree calls
    # this "キーホルダー・キーリング", men's calls it "キーケース・キーリング"
    # (confirmed live against BUYMA's own category picker, same verification
    # approach as the belt/trousers fixes above).
    (("KEYCHAIN", "KEYRING", "KEY RING"), "キーホルダー・キーリング", _w("財布・小物", "キーホルダー・キーリング"), _m("財布・雑貨", "キーケース・キーリング")),
    # Checked before the generic LEATHER rule below: a belt's own material
    # composition text ("100% leather") would otherwise false-match as a
    # leather jacket (confirmed live: a "Wide Leather Belt" was misclassified
    # this way).
    # Belt lives under "ファッション雑貨・小物" for both genders, not
    # "アクセサリー" (which is jewelry-only on both the men's and women's
    # trees); confirmed live against BUYMA's own category picker after a
    # men's belt draft failed with "option did not appear" (the earlier
    # "アクセサリー" path had never actually been verified against the
    # men's tree, only assumed to mirror women's).
    (("BELT", "CINTURA"), "ベルト", _w("ファッション雑貨・小物", "ベルト"), _m("ファッション雑貨・小物", "ベルト")),
    # Also checked before the generic LEATHER rule below, same reasoning as
    # BELT above: a pair of leather trousers/shorts/a leather skirt's own
    # material composition text ("Black Leather Trousers") would otherwise
    # false-match as a leather jacket (confirmed live: "Bottega Veneta Black
    # Leather Trousers" was misclassified this way).
    (("JEANS",), "デニム・ジーパン", _w("ボトムス", "デニム・ジーパン"), _m("パンツ・ボトムス", "デニム・ジーパン")),
    (("SHORTS",), "ショートパンツ", _w("ボトムス", "ショートパンツ"), _m("パンツ・ボトムス", "ハーフ・ショートパンツ")),
    (("TROUSER", "PANTS", "PANTALONE"), "パンツ", _w("ボトムス", "パンツ"), _m("パンツ・ボトムス", "パンツ・ボトムスその他")),
    (("SKIRT", "GONNA"), "スカート", _w("ボトムス", "スカート"), None),
    (("BIKER", "PELLE", "LEATHER"), "レザージャケット", _w("アウター", "レザージャケット・コート"), _m("アウター・ジャケット", "レザージャケット")),
    (("SHEARLING",), "ムートンコート", _w("アウター", "ムートン・ファーコート"), _m("アウター・ジャケット", "アウターその他")),
    (("PIUMINO SMANICATO", "GILET", "PUFFER VEST"), "ダウンベスト", _w("アウター", "ダウンベスト"), _m("アウター・ジャケット", "ダウンベスト")),
    (("TRENCH",), "トレンチコート", _w("アウター", "トレンチコート"), _m("アウター・ジャケット", "トレンチコート")),
    (("CABAN", "COAT", "PARKA"), "コート", _w("アウター", "コート"), _m("アウター・ジャケット", "ピーコート")),
    (("BLOUSON", "BOMBER"), "ブルゾン", _w("アウター", "ブルゾン"), _m("アウター・ジャケット", "ブルゾン")),
    (("DENIM JACKET",), "デニムジャケット", _w("アウター", "ジャケット"), _m("アウター・ジャケット", "デニムジャケット")),
    # "OVERSHIRT"/"OUTERSHIRT" must be checked before the plain SHIRT rule
    # below: both are heavier, jacket-like layers (not a dress shirt), but
    # naive substring matching would otherwise catch "SHIRT" inside them first.
    (("OVERSHIRT", "OUTERSHIRT"), "ジャケット", _w("アウター", "ジャケット"), _m("アウター・ジャケット", "ジャケットその他")),
    # "JKT" is flannels.com's abbreviation for jacket; "BLAZER" is a tailored
    # jacket, listed under the same general jacket category.
    (("GIACCA", "JACKET", "JKT", "BLAZER"), "ジャケット", _w("アウター", "ジャケット"), _m("アウター・ジャケット", "ジャケットその他")),
    (("COSTUME INTERO",), "ワンピース水着", _w("水着・ビーチグッズ", "ワンピース水着"), None),
    (("BIKINI",), "ビキニ", _w("水着・ビーチグッズ", "ビキニ"), None),
    (("LOGO CAP", "BASEBALL CAP", "TRUCKER CAP"), "キャップ", _w("帽子", "キャップ"), None),
    (("LOGO HAT",), "ハット", _w("帽子", "ハット"), None),
    (("MANTELLA", "CAPE", "PONCHO"), "ケープ", _w("アウター", "ポンチョ・ケープ"), None),
    (("EARRING",), "ピアス", _w("アクセサリー", "ピアス"), None),
    (("SUNGLASSES", "OCCHIALI DA SOLE"), "サングラス", _w("アイウェア", "サングラス"), _m("アイウェア", "サングラス")),
    (("WALLET", "PORTAFOGLIO"), "長財布", _w("財布・小物", "長財布"), _m("財布・雑貨", "長財布")),
    (("BODY",), "トップス", _w("トップス", "トップスその他"), None),
    (("ABITO", "DRESS"), "ワンピース", _w("ワンピース・オールインワン", "ワンピース"), None),
    # "CARDI" is flannels.com's abbreviation for cardigan.
    (("CARDIGAN", "CARDI"), "カーディガン", _w("トップス", "カーディガン"), _m("トップス", "カーディガン")),
    # "JUMPER" is British English for a knit sweater.
    (("MAGLIA", "SWEATER", "PULLOVER", "JUMPER"), "ニット・セーター", _w("トップス", "ニット・セーター"), _m("トップス", "ニット・セーター")),
    (("POLO",), "ポロシャツ", _w("トップス", "ポロシャツ"), _m("トップス", "ポロシャツ")),
    (("HOODIE", "FELPA CON CAPPUCCIO"), "パーカー", _w("トップス", "パーカー・フーディ"), _m("トップス", "パーカー・フーディ")),
    (("FELPA", "SWEATSHIRT"), "スウェット", _w("トップス", "スウェット・トレーナー"), _m("トップス", "スウェット・トレーナー")),
    (("T-SHIRT",), "Tシャツ", _w("トップス", "Tシャツ・カットソー"), _m("トップス", "Tシャツ・カットソー")),
    (("CAMICIA", "CAMICETTA", "BLUSA", "SHIRT"), "シャツ", _w("トップス", "ブラウス・シャツ"), _m("トップス", "シャツ")),
    (("TOP",), "トップス", _w("トップス", "トップスその他"), _m("トップス", "トップスその他")),
)

_EASY_BUYMA_PACKET = ("かんたんBUYMA便【匿名配送】 - ゆうパケット", 300)
_EASY_BUYMA_60 = ("かんたんBUYMA便【匿名配送】 - ゆうパック 60サイズ", 800)
_EASY_BUYMA_80 = ("かんたんBUYMA便【匿名配送】 - ゆうパック 80サイズ", 950)
_EASY_BUYMA_100 = ("かんたんBUYMA便【匿名配送】 - ゆうパック 100サイズ", 1100)

# Multiple methods are checked on the form so the actual shipment can match
# the real parcel size. The first entry defines buyer_shipping_jpy.
_DEFAULT_SHIPPING_METHODS = (_EASY_BUYMA_PACKET, _EASY_BUYMA_60)

# Pants/jeans sizes are often given as a raw waist measurement (e.g. "24",
# "25") rather than an IT dress size, which BUYMA's reference-size dropdown
# has no direct equivalent for; this standard chart is appended to the size
# notes so buyers can self-convert instead of seeing no guidance at all.
_WAIST_SIZED_PRODUCT_TYPES = {"デニム・ジーパン", "パンツ", "ショートパンツ"}
# Product types with no real clothing-size concept; see the size_variation
# override in listing_settings_for_candidate().
# Accessory types with no real clothing/shoe size concept. flannels.com
# reuses its size-selector DOM slot for per-colorway swatch codes on these
# pages (confirmed for both sunglasses and bags), which would otherwise be
# mistaken for real sizes.
_NO_SIZE_PRODUCT_TYPES = {"サングラス", "ショルダーバッグ・ポシェット", "長財布", "ベルト", "カードケース・名刺入れ"}
_WAIST_SIZE_REFERENCE_TABLE = (
    "■海外パンツサイズ目安（ウエスト表記の場合）\n"
    "XXS：22-23 / XS：24-25 / S：26-27 / M：28-29 / "
    "L：30-31 / XL：32-33 / XXL：34-35 / XXXL：36-37"
)
_COLOR_RULES = (
    # "BLK" is flannels.com's abbreviation for black.
    (("NERO", "BLACK", "NOIR", "BLK"), ("ブラック", "ブラック")),
    # "EGGSHELL" is a pale off-white; "BLANC" is French for white; "WHT" is
    # flannels.com's abbreviation. Safe to include now that classify_color()
    # picks the earliest-occurring color word rather than the first matching
    # rule.
    # "CHALKY" is a pale, chalk-like off-white (confirmed against product photos).
    (("BIANCO", "WHITE", "OFF-WHITE", "OFFWHITE", "AVORIO", "IVORY", "PANNA", "EGGSHELL", "BLANC", "WHT", "CHALKY"), ("ホワイト", "ホワイト")),
    # "AVION" and "GRIS" (French for grey), and "CHARCOAL"/"SALT & PEPPER"
    # (mixed black-and-white flecked wool) are used by flannels.com brands.
    (("GRIGIO", "GREY", "GRAY", "ANTRACITE", "ANTHRACITE", "AVION", "GRIS", "CHARCOAL", "SALT & PEPPER", "SALT AND PEPPER"), ("グレー", "グレー")),
    # "CREME" is the French spelling, used by flannels.com brands.
    # "GINGER" (as in "Ginger/Ash") reads as a light tan, confirmed against
    # product photos.
    # "NATURALE" is Italian for "natural" (undyed fabric), used by Prada
    # descriptions for an off-white/ecru tone.
    (("BEIGE", "SABBIA", "CREMA", "CREME", "CAMMELLO", "CAMEL", "TAUPE", "ECRU", "GINGER", "NATURALE"), ("ベージュ", "ベージュ")),
    # "DUST" is Rick Owens' signature dark taupe-brown color (confirmed
    # against product photos, not a beige tone despite the name); "NOISETTE"
    # is French for hazelnut/brown. "CACAO" (cocoa) and "TURKISH COFFEE" are
    # both dark-brown colorway names (confirmed against product swatches).
    (("MARRONE", "BROWN", "CIOCCOLATO", "MORO", "CUOIO", "TABACCO", "COGNAC", "DUST", "NOISETTE", "CACAO", "TURKISH COFFEE"), ("ブラウン", "ブラウン")),
    # "ENCRE" is French for ink, a very dark navy/blue-black.
    (("NAVY", "BLU SCURO", "INDACO", "INDIGO", "ENCRE"), ("ネイビー", "ネイビー")),
    # "DENIM" deliberately excluded: it names a fabric, not a color, and a
    # product's name/description mentioning it (e.g. "Denim Jacket") doesn't
    # mean the item's actual color is blue (confirmed live: black and white
    # denim pieces were being misclassified as blue this way).
    # "BLEU" is French for blue.
    (("BLU", "BLUE", "BLEU", "COBALTO", "AZZURRO", "CELESTE"), ("ブルー", "ブルー")),
    # "LODEN" is a traditional dark olive-green wool color; "SAUGE" is French
    # for sage.
    (("VERDE", "GREEN", "MILITARE", "KHAKI", "KAKI", "OLIVA", "SALVIA", "LODEN", "SAUGE"), ("グリーン", "グリーン")),
    # "WINETASTING" is a dark wine/burgundy colorway name (confirmed against
    # product swatches).
    (("ROSSO", "RED", "BORDEAUX", "BURGUNDY", "VINACCIA", "WINETASTING"), ("レッド", "レッド")),
    (("ROSA", "PINK", "FUCSIA", "MAGENTA"), ("ピンク", "ピンク")),
    (("VIOLA", "PURPLE", "LILLA", "LAVANDA"), ("パープル", "パープル")),
    (("GIALLO", "YELLOW", "SENAPE", "MOSTARDA"), ("イエロー", "イエロー")),
    (("ARANCIONE", "ORANGE", "CORALLO"), ("オレンジ", "オレンジ")),
    (("ARGENTO", "SILVER"), ("シルバー", "シルバー")),
    (("ORO", "GOLD", "DORATO"), ("ゴールド", "ゴールド")),
    (("MULTICOLOR", "MULTICOLOUR", "FANTASIA", "STAMPA", "FLOREALE", "PRINT"), ("マルチカラー", "マルチカラー")),
)

# Small, light items ship in a envelope-style packet; bags, shoes, and
# outerwear are bulkier, so all three box sizes are offered and BUYMA (or
# the seller at fulfillment time) picks whichever actually fits.
_SHIPPING_60_80_100 = (_EASY_BUYMA_60, _EASY_BUYMA_80, _EASY_BUYMA_100)
_SHOE_PRODUCT_TYPES = (
    "パンプス", "サンダル・ミュール", "スニーカー", "スリッポン", "レインブーツ",
    "ショートブーツ・ブーティ", "ロングブーツ", "ブーツその他",
    "ローファー・オックスフォード", "バレエシューズ", "フラットシューズ",
)
_BAG_PRODUCT_TYPES = ("ショルダーバッグ・ポシェット", "カードケース・名刺入れ")
_OUTER_PRODUCT_TYPES = (
    "レザージャケット", "ムートンコート", "トレンチコート", "コート",
    "ブルゾン", "デニムジャケット", "ジャケット",
)
_SHIPPING_METHODS_BY_PRODUCT_TYPE = {
    "Tシャツ": (_EASY_BUYMA_PACKET,),
    **{name: _SHIPPING_60_80_100 for name in (*_SHOE_PRODUCT_TYPES, *_BAG_PRODUCT_TYPES, *_OUTER_PRODUCT_TYPES)},
    "ワンピース": (_EASY_BUYMA_60, _EASY_BUYMA_80),
}


def load_approved_candidate_rows(path: Path, *, approve_all: bool) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8-sig") as input_file:
            rows = list(csv.DictReader(input_file))
    except OSError as error:
        raise CandidatePreparationError(f"Cannot read candidate CSV: {path}") from error
    if not rows:
        raise CandidatePreparationError(f"Candidate CSV contains no products: {path}")
    approved = rows if approve_all else [row for row in rows if row.get("approved", "").strip().casefold() in {"yes", "true", "1", "y"}]
    if not approved:
        raise CandidatePreparationError("No approved candidates; mark approved=yes or pass --approve-all")
    return approved


def load_description_translations(path: Path) -> dict[str, str]:
    """Load SKU -> hand-translated Japanese product-detail text, if the file exists."""
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CandidatePreparationError(f"Cannot read description translations: {path}") from error
    if not isinstance(payload, dict):
        raise CandidatePreparationError(f"Description translations must be a SKU -> text object: {path}")
    return {str(sku): str(text) for sku, text in payload.items()}


def load_color_overrides(path: Path) -> dict[str, tuple[str, str]]:
    """Load SKU -> (color_family, color_name) manual overrides, if the file exists."""
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CandidatePreparationError(f"Cannot read color overrides: {path}") from error
    if not isinstance(payload, dict):
        raise CandidatePreparationError(f"Color overrides must be a SKU -> [family, name] object: {path}")
    overrides: dict[str, tuple[str, str]] = {}
    for sku, value in payload.items():
        if not isinstance(value, list) or len(value) != 2:
            raise CandidatePreparationError(f"Color override for {sku} must be [color_family, color_name]: {path}")
        overrides[str(sku)] = (str(value[0]), str(value[1]))
    return overrides


def load_target_overrides(path: Path) -> dict[str, str]:
    """Load SKU -> "men"/"women" manual gender overrides, if the file exists."""
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CandidatePreparationError(f"Cannot read target overrides: {path}") from error
    if not isinstance(payload, dict):
        raise CandidatePreparationError(f"Target overrides must be a SKU -> \"men\"/\"women\" object: {path}")
    overrides: dict[str, str] = {}
    for sku, value in payload.items():
        normalized = str(value).strip().casefold()
        if normalized not in {"men", "women"}:
            raise CandidatePreparationError(f"Target override for {sku} must be \"men\" or \"women\": {path}")
        overrides[str(sku)] = normalized
    return overrides


def prepare_candidate_packages(
    products: list[Product],
    candidate_rows: list[dict[str, str]],
    base_settings: ListingSettings,
    output_root: Path,
    *,
    download_images: bool = True,
    description_translations: dict[str, str] | None = None,
    color_overrides: dict[str, tuple[str, str]] | None = None,
    target_overrides: dict[str, str] | None = None,
) -> list[Path]:
    by_identity = {(product.product_url.strip(), product.sku.strip().casefold()): product for product in products}
    translations = description_translations or {}
    colors = color_overrides or {}
    targets = target_overrides or {}
    folders: list[Path] = []
    skipped: list[tuple[str, str]] = []
    for row in candidate_rows:
        key = (row.get("product_url", "").strip(), row.get("sku", "").strip().casefold())
        product = by_identity.get(key)
        if product is None:
            raise CandidatePreparationError(f"Candidate was not found in product CSV: {row.get('sku', '')}")
        try:
            settings = listing_settings_for_candidate(
                product,
                row,
                base_settings,
                translated_details=translations.get(product.sku),
                color_override=colors.get(product.sku),
                target_override=targets.get(product.sku),
            )
        except CandidatePreparationError as error:
            skipped.append((product.sku, str(error)))
            continue
        folders.append(prepare_listing_package(product, settings, output_root, download_images=download_images))
    if skipped:
        import logging

        for sku, reason in skipped:
            logging.warning("Skipped candidate %s: %s", sku, reason)
        logging.warning(
            "Skipped %d of %d candidates; handle them manually or extend the rules.",
            len(skipped),
            len(candidate_rows),
        )
    if not folders:
        raise CandidatePreparationError("Every candidate was skipped; nothing to prepare.")
    return folders


# BUYMA's "買付地" (purchasing location) fields must match the actual
# storefront the item was bought from, not a single hardcoded default —
# eleonora/antonia are Italian sites, flannels.com is British.
_BUYING_LOCATION_BY_SHOP = {
    "eleonora": ("ヨーロッパ", "イタリア"),
    "antonia": ("ヨーロッパ", "イタリア"),
    "flannels": ("ヨーロッパ", "イギリス"),
}


def listing_settings_for_candidate(
    product: Product,
    candidate: dict[str, str],
    base: ListingSettings,
    *,
    translated_details: str | None = None,
    color_override: tuple[str, str] | None = None,
    target_override: str | None = None,
) -> ListingSettings:
    effective_target = target_override or _default_target(product)
    product_type, category_path = classify_product(product.name, effective_target)
    if color_override is not None:
        color_family, color_name = color_override
    else:
        try:
            color_family, color_name = classify_color(product)
        except CandidatePreparationError:
            import logging

            logging.warning(
                "No color rule matched %s; using 色指定なし (set the color manually if BUYMA requires one)",
                product.sku,
            )
            color_family, color_name = "色指定なし", ""
    listing_price = _positive_int(candidate, "suggested_listing_price_jpy")
    expected_profit = _positive_int(candidate, "expected_profit_jpy")
    expected_margin = _decimal(candidate, "expected_profit_margin")
    title = _title(product.brand, product_type, product.name, color_name)
    description = _description(product, product_type, translated_details=translated_details)
    shipping_options = _SHIPPING_METHODS_BY_PRODUCT_TYPE.get(product_type, _DEFAULT_SHIPPING_METHODS)
    shipping_method, buyer_shipping = shipping_options[0]
    shipping_methods = [option[0] for option in shipping_options]
    source_price = product.current_price
    private_memo = (
        f"仕入先: {product.shop_name}\n"
        f"仕入先URL: {product.product_url}\n"
        f"仕入価格: {product.currency} {format(source_price, 'f') if source_price is not None else '不明'}\n"
        f"想定利益: {expected_profit}円\n"
        f"想定利益率: {format(expected_margin, '.2%')}"
    )
    size_notes = (
        "現地の正規取扱店で在庫を確認できたサイズのみ掲載しております。"
        "人気サイズは早期に売り切れる場合がございますので、"
        "ご注文前に必ず在庫確認をお願いいたします。"
    )
    if product_type in _WAIST_SIZED_PRODUCT_TYPES:
        size_notes = f"{size_notes}\n\n{_WAIST_SIZE_REFERENCE_TABLE}"
    # Eyewear has no real clothing sizes; flannels.com's "size" swatch slot
    # is reused for colorway codes on some sunglasses listings (confirmed
    # live: scraped "sizes" like "75733403" are actually per-color SKUs),
    # so force no size variation rather than showing those as fake sizes.
    size_variation = product_type not in _NO_SIZE_PRODUCT_TYPES and not _is_single_free_size(product.sizes)
    buying_region, buying_country = _BUYING_LOCATION_BY_SHOP.get(
        product.shop_code, (base.buying_region, base.buying_country)
    )
    return replace(
        base,
        japanese_title=title,
        japanese_description=description,
        buyma_category_path=list(category_path),
        color_family=color_family,
        color_name=color_name,
        listing_price_jpy=listing_price,
        buyer_shipping_jpy=buyer_shipping,
        shipping_method=shipping_method,
        shipping_methods=shipping_methods,
        private_memo=private_memo,
        size_notes=size_notes,
        size_unit="指定なし",
        size_variation=size_variation,
        description_source_url=product.product_url,
        buying_region=buying_region,
        buying_country=buying_country,
    )


def _is_single_free_size(sizes: list[object]) -> bool:
    if len(sizes) != 1:
        return False
    size_text = str(getattr(sizes[0], "size", "")).strip().casefold()
    return size_text in {"one size", "onesize", "free", "free size", "freesize", "os"}


def _default_target(product: Product) -> str:
    # Bags sell better listed as women's on BUYMA regardless of which
    # gendered source collection they were scraped from; everything else
    # (shoes, clothing, ...) follows the supplier's own classification.
    normalized = " ".join(product.name.upper().split())
    if any(keyword in normalized for keyword in _BAG_KEYWORDS):
        return "women"
    return product.target


def classify_product(source_name: str, target: str = "women") -> tuple[str, tuple[str, ...]]:
    normalized = " ".join(source_name.upper().split())
    is_men = target.strip().casefold() == "men"
    for keywords, japanese_type, women_category, men_category in _PRODUCT_RULES:
        # Word-boundary match: a plain substring search would let a short
        # keyword like "PELLE" (Italian for leather) false-positive inside
        # an unrelated word (e.g. "Water-Repellent"), which is common in
        # English product names (confirmed live: an Ami Paris "Water-
        # Repellent...Overshirt" was misclassified as a leather jacket). An
        # optional trailing "S" is allowed before the closing boundary so a
        # singular keyword (e.g. "SNEAKER") still matches its common plural
        # product-listing form ("Sneakers") instead of falling through to a
        # wrong, more generic rule.
        if not any(re.search(rf"\b{re.escape(keyword)}S?\b", normalized) for keyword in keywords):
            continue
        if is_men:
            if men_category is None:
                raise CandidatePreparationError(
                    f"No reviewed men's BUYMA category rule for product: {source_name}"
                )
            return japanese_type, men_category
        return japanese_type, women_category
    raise CandidatePreparationError(f"No reviewed BUYMA category rule for product: {source_name}")


_COLOUR_SUFFIX = re.compile(r"COLOU?R\s*:\s*(.+)$")


def classify_color(product: Product) -> tuple[str, str]:
    """Map the source color (or name/description keywords) to a BUYMA color.

    Multi-color source strings (e.g. "Purple/Grey/Blk") list their primary
    color first, so matches are picked by *earliest position in the text*
    rather than by _COLOR_RULES's own ordering — otherwise a color rule that
    merely happens to sit earlier in the list (e.g. grey before purple) can
    win over the color the source actually lists first.

    An explicit "Colour: X" suffix (common in flannels.com descriptions) is
    checked before the rest of the description: incidental color words
    describing a detail earlier in the text (e.g. "White stitching at the
    back", "iconic Green and Red Web design") would otherwise outrank the
    garment's actual, authoritative stated color.

    If the product's own color field or a "Colour: X" suffix is present but
    its value isn't in _COLOR_RULES's vocabulary yet (e.g. a name like
    "Soothing Sea"), this stops there rather than falling through to scan
    the free-text description/name — otherwise an incidental color word
    elsewhere in the text (again, a stitching or trim detail, not the
    garment's own color) would win by default, which is worse than admitting
    the color isn't recognized.

    Raises CandidatePreparationError when no rule matches, so the owner can
    extend _COLOR_RULES instead of the tool guessing a wrong color.
    """
    colour_suffix_match = _COLOUR_SUFFIX.search((product.description or "").upper())
    colour_suffix = colour_suffix_match.group(1) if colour_suffix_match else ""
    authoritative_texts = [t for t in (product.color, colour_suffix) if (t or "").strip()]
    fallback_texts = [product.name, product.description]
    texts = authoritative_texts if authoritative_texts else fallback_texts
    for text in texts:
        normalized = " ".join((text or "").upper().split())
        if not normalized:
            continue
        best_index: int | None = None
        best_result: tuple[str, str] | None = None
        for keywords, result in _COLOR_RULES:
            for keyword in keywords:
                # Word-boundary match: a plain substring search would let a
                # short color keyword like "RED" false-positive inside an
                # unrelated word (e.g. "stRUCTUREd", "covERED", "tailoRED"),
                # which is common in English product descriptions. The
                # trailing boundary only requires "not followed by another
                # letter" (not a strict \b) since some sites glue a numeric
                # colorway code directly onto the color name with no space
                # (e.g. "Black1000"), which \b would otherwise reject.
                match = re.search(rf"\b{re.escape(keyword)}(?![A-Z])", normalized)
                if match is not None and (best_index is None or match.start() < best_index):
                    best_index = match.start()
                    best_result = result
        if best_result is not None:
            family, name = best_result
            modifier = _adjacent_shade_modifier(normalized, best_index)
            return family, f"{modifier}{name}" if modifier else name
    raise CandidatePreparationError(
        f"No color rule matched product {product.sku}: color={product.color!r} name={product.name!r}"
    )


# A qualifier word immediately next to the matched color keyword (e.g.
# "Dark" in "Dark Green", "Mil" in "Verde Mil") is folded into color_name
# only — color_family stays the plain base color, since that's the only
# vocabulary BUYMA's own family dropdown accepts. Without this, genuinely
# different shades collapse to an identical color_name with no way to tell
# them apart (confirmed live: five distinct Stone Island T-shirt colorways —
# Military Green, Light Green, Dark Green, Olive Green, Sage — all showed as
# identical "グリーン" listings). Single-letter "L"/"O" (this site's own
# abbreviation for Light/Olive, e.g. "L Green", "O Green") are included
# despite the false-positive risk of a bare single letter, since the
# adjacency check only ever fires right next to an already-matched color
# keyword within this narrowly-scoped authoritative color text (never
# free-roaming description prose) — confirmed live these are exactly what
# this site uses them for.
_COLOR_SHADE_MODIFIERS = (
    ("DARK", "ダーク"), ("DK", "ダーク"), ("DEEP", "ディープ"),
    ("LIGHT", "ライト"), ("L", "ライト"), ("PALE", "ペール"), ("BRIGHT", "ブライト"),
    ("MILITARY", "ミリタリー"), ("MIL", "ミリタリー"), ("OLIVE", "オリーブ"), ("O", "オリーブ"),
    ("SAGE", "セージ"), ("SKY", "スカイ"), ("SLATE", "スレート"),
    ("ASH", "アッシュ"), ("COAL", "コール"), ("IVORY", "アイボリー"),
)


def _adjacent_shade_modifier(text: str, color_index: int) -> str:
    words = list(re.finditer(r"\S+", text))
    color_word_index = next(
        (i for i, word in enumerate(words) if word.start() <= color_index < word.end()), None
    )
    if color_word_index is None:
        return ""
    neighbors = []
    if color_word_index > 0:
        neighbors.append(words[color_word_index - 1].group())
    if color_word_index + 1 < len(words):
        neighbors.append(words[color_word_index + 1].group())
    for neighbor in neighbors:
        for keyword, katakana in _COLOR_SHADE_MODIFIERS:
            if neighbor == keyword:
                return katakana
    return ""


def write_package_queue(folders: list[Path], path: Path) -> None:
    if len(folders) != len(set(folders)):
        raise CandidatePreparationError("Prepared package queue contains duplicates")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps({"packages": [str(folder.resolve()) for folder in folders]}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


#  A second brand name joined with a bare "X"/"x" (e.g. "Rick Owens x
# Moncler Flight Parka") signals a collaboration piece; buyers specifically
# search for the collaborating brand, so it needs to appear in the title
# even though `brand` (from the source site) only names the primary house.
_COLLAB_BRAND_PATTERN = re.compile(r"(?<!\w)[xX](?!\w) +([A-Z][a-z]+)")


def recompute_title(brand: str, source_name: str, color_name: str = "") -> str:
    """Recompute a package's 商品名 from scratch using the current _title()
    logic, given only the fields already stored in an existing package's
    listing_data.json — used to refresh already-drafted listings' titles
    after a _title() formatting fix, without redrafting the whole listing.
    """
    product_type, _ = classify_product(source_name)
    return _title(brand, product_type, source_name, color_name)


def _title(brand: str, product_type: str, source_name: str, color_name: str = "") -> str:
    features = []
    upper = source_name.upper()
    # Matched against the original-case source_name (not `upper`) so the
    # capitalized-word boundary reliably isolates a single collaborating
    # brand name, e.g. "Rick Owens x Moncler Flight Parka" -> "Moncler".
    collab_match = _COLLAB_BRAND_PATTERN.search(source_name)
    if collab_match and collab_match.group(1).upper() != brand.upper():
        features.append(f"{collab_match.group(1)}コラボ")
    translations = (
        ("REVERSIBILE", "リバーシブル"), ("FLOREALE", "フローラル"), ("PAILLETTES", "スパンコール"),
        ("DENIM", "デニム"), ("LUNGA", "長袖"), ("CORTA", "半袖"), ("LOGO", "ロゴ"),
        ("LANA MERINO", "メリノウール"), ("LANA", "ウール"), ("SETA", "シルク"),
        ("COTONE", "コットン"), ("RIGHE", "ストライプ"), ("DRAPPEGGIO", "ドレープ"),
        ("DOPPIOPETTO", "ダブルブレスト"),
        # English-language equivalents, needed for English-language source
        # sites (e.g. flannels.com) where the Italian keywords above never match.
        ("REVERSIBLE", "リバーシブル"), ("FLORAL", "フローラル"), ("SEQUIN", "スパンコール"),
        ("LONG SLEEVE", "長袖"), ("SHORT SLEEVE", "半袖"), ("MERINO WOOL", "メリノウール"),
        ("WOOL", "ウール"), ("SILK", "シルク"), ("COTTON", "コットン"), ("STRIPE", "ストライプ"),
        ("DOUBLE BREASTED", "ダブルブレスト"), ("DOWN", "ダウン"), ("QUILTED", "キルティング"),
        ("LINEN", "リネン"),
    )
    matched_feature_keywords = []
    for source, japanese in translations:
        # "DOWN" is a plain substring of "BUTTON-DOWN" (a shirt collar
        # style, unrelated to down insulation); confirmed live this
        # mislabeled an AMI PARIS Oxford cotton shirt as down-filled.
        if source == "DOWN" and re.search(r"BUTTON[\s-]?DOWN", upper):
            continue
        if source in upper and japanese not in features:
            features.append(japanese)
            matched_feature_keywords.append(source)
    descriptor = _model_descriptor(source_name, product_type, matched_feature_keywords, brand)
    descriptor_candidates = _descriptor_fit_candidates(descriptor)

    def build(*, descriptor_text: str, with_features: int, with_color: bool) -> str:
        parts = [brand]
        if with_color and color_name:
            parts.append(color_name)
        parts.extend(features[:with_features])
        if descriptor_text:
            parts.append(descriptor_text)
        parts.append(product_type)
        return " ".join(parts)

    # Descriptor candidates are tried from most to least complete at each
    # feature-count tier, so a title that's merely a little too long trims
    # the descriptor word-by-word (keeping the model code/product-line name
    # as long as possible) rather than jumping straight to dropping it
    # entirely, which used to lose e.g. a sunglasses model number ("SL M95")
    # or a bag's own line name ("Flamenco") even though only a throwaway
    # word like "Oversized" or "Medium" needed to go.
    # Color is tried before dropping it, but not at any cost: a long brand
    # name plus a long category name (e.g. "Saint Laurent" + "ショルダーバ
    # ッグ・ポシェット") can leave no room for any descriptor at all
    # alongside color, even though color is already captured as its own
    # structured field on BUYMA (not just this text) — so dropping color
    # here to keep the descriptor (e.g. "モノグラム") loses less real
    # information than dropping the descriptor to keep color would.
    non_empty_descriptor_candidates = [candidate for candidate in descriptor_candidates if candidate]
    stages = []
    for with_features in (2, 1, 0):
        # Every non-empty descriptor length is tried with color first
        # (longest to shortest) before color is dropped at all — trimming
        # the descriptor down to its shortest meaningful form is preferred
        # over losing color, but keeping *some* descriptor (even just a
        # model code or line name) still wins over keeping color once the
        # descriptor would otherwise be lost entirely.
        for descriptor_text in non_empty_descriptor_candidates:
            stages.append((descriptor_text, with_features, True))
        for descriptor_text in non_empty_descriptor_candidates:
            stages.append((descriptor_text, with_features, False))
        stages.append(("", with_features, True))
    stages.append(("", 0, False))
    for descriptor_text, with_features, with_color in stages:
        title = build(descriptor_text=descriptor_text, with_features=with_features, with_color=with_color)
        if _buyma_title_length(title) <= 60:
            return title
    raise CandidatePreparationError(f"Generated BUYMA title exceeds 60 characters: {title}")


def _descriptor_fit_candidates(descriptor: str) -> list[str]:
    """Progressively shorter versions of a descriptor, most complete first.

    Already-translated size words (e.g. "ミディアム" from "Medium") are
    dropped before anything else: they're pure magnitude info, while the
    remaining words are usually the actual product-line/model name buyers
    search by (confirmed live: LOEWE's "Medium Flamenco" needs "Flamenco"
    kept over "Medium" when both don't fit; Saint Laurent's "SL M95
    Oversized Cat-Eye" needs the "SL M95" model code kept over the rest).
    """
    if not descriptor:
        return [""]
    words = descriptor.split(" ")
    size_word_values = set(_SIZE_WORD_TRANSLATIONS.values())
    core_words = [word for word in words if word not in size_word_values]

    candidates = [" ".join(words)]
    if core_words != words:
        candidates.append(" ".join(core_words))
    for cut in range(len(core_words) - 1, 0, -1):
        candidates.append(" ".join(core_words[:cut]))
    candidates.append("")

    seen: set[str] = set()
    unique_candidates = []
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            unique_candidates.append(candidate)
    return unique_candidates


def _buyma_title_length(text: str) -> int:
    """BUYMA's own 商品名 limit is "全角30文字・半角60文字以内" — full-width
    (Japanese) characters count as 2, half-width (Roman/digits) count as 1,
    both capped by the same 60 budget. A plain len() check undercounts any
    title mixing Japanese and English (confirmed live: a 46-character title
    with both scripts was rejected by BUYMA's own save endpoint with a 422
    despite being well under 60 by character count alone).
    """
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _model_descriptor(source_name: str, product_type: str, matched_feature_keywords: list[str], brand: str) -> str:
    """Pull out a product's own model/size words (e.g. "Medium Flamenco" from
    "Brown Medium Flamenco Bag") once its color and category are already
    known, so recognizable product-line names aren't dropped from the title.

    Model names (proper nouns, e.g. "Flamenco", "Amazona") are kept in their
    original Roman-letter form — there's no translation API in this
    pipeline, and guessing a katakana transliteration risks being wrong in a
    way plain English wouldn't be. Common size words are translated via a
    small, stable, hand-maintained list instead of guessed.
    """
    remaining = source_name
    # Some source names repeat the brand and a gender possessive inline
    # (e.g. "Kenzo Men's Seigaiha Straight Leg Jeans"); left in, these would
    # duplicate the brand already leading the title (confirmed live:
    # "Kenzo ... Kenzo Men's Seigaiha Straight Leg デニム・ジーパン").
    if brand:
        # Some source names repeat the brand within their own title
        # (confirmed live: "Saint Laurent Saint Laurent SL 549 Mens
        # Rectangle Sunglasses"), so every occurrence is stripped, not just
        # the first.
        matched = True
        while matched:
            remaining, matched = _strip_keyword(remaining, brand)
    for possessive in (
        "MEN'S", "WOMEN'S", "MAN'S", "WOMAN'S",
        "MEN’S", "WOMEN’S", "MAN’S", "WOMAN’S",  # curly apostrophe
        "MENS", "WOMENS",
    ):
        remaining, _ = _strip_keyword(remaining, possessive)
    # Strip whichever color keyword is positioned earliest in source_name,
    # matching classify_color()'s own "earliest occurrence wins" rule —
    # picking a match by _COLOR_RULES's declaration order instead (e.g.
    # always preferring "brown" over "khaki") could strip a different color
    # word than the one actually used, leaving the real one behind
    # (confirmed live: "Khaki and brown gingham jacket" is classified
    # "khaki", but rule order would have stripped "brown" instead).
    best_index: int | None = None
    best_keyword: str | None = None
    for keywords, _ in _COLOR_RULES:
        for keyword in keywords:
            match = re.search(rf"\b{re.escape(keyword)}(?![A-Za-z])", remaining, re.IGNORECASE)
            if match is not None and (best_index is None or match.start() < best_index):
                best_index = match.start()
                best_keyword = keyword
    if best_keyword is not None:
        remaining, _ = _strip_keyword(remaining, best_keyword)
    # Every _PRODUCT_RULES keyword (not just the one that decided
    # product_type) is generic category vocabulary, redundant once the
    # category itself is shown as product_type — e.g. a "Shearling Jacket"
    # classifies as ムートンコート via SHEARLING, but "Jacket" is left
    # dangling unless JACKET (from a different rule) is stripped too.
    # Plural forms ("Trainers", "Sandals") are allowed here to mirror
    # classify_product()'s own optional trailing "S", and every matching
    # keyword is stripped rather than stopping at the first hit, since a
    # name can repeat more than one from the same rule (e.g. "Tote Bag").
    for keywords, *_ in _PRODUCT_RULES:
        for keyword in keywords:
            remaining, _ = _strip_keyword(remaining, keyword, allow_plural=True)
    # Also strip whatever _title()'s own fabric/style keyword scan already
    # translated into `features` (e.g. "Logo", "Cotton"), so it isn't
    # duplicated here in its untranslated form.
    for keyword in matched_feature_keywords:
        remaining, _ = _strip_keyword(remaining, keyword)
    remaining = " ".join(remaining.split())
    if not remaining:
        return ""
    words = []
    for word in remaining.split(" "):
        # A stripped keyword can leave stray punctuation glued to its
        # neighbor (e.g. "T-Shirts, 3-Pack" -> ", 3-Pack" once "T-Shirts"
        # is removed); trimmed here rather than preserved as junk.
        cleaned = word.strip(",-.:;")
        if not cleaned or cleaned.upper() in _DESCRIPTOR_STOPWORDS:
            continue
        translated = _SIZE_WORD_TRANSLATIONS.get(cleaned.upper()) or _DESCRIPTOR_WORD_TRANSLATIONS.get(cleaned.upper())
        words.append(translated or _strip_diacritics(cleaned))
    return " ".join(words)


def _strip_diacritics(word: str) -> str:
    """Flatten accented Latin letters (from Italian/French source names,
    e.g. "LAVALLIÈRE", "DÉLAVÉ") to plain ASCII.

    BUYMA's own 商品名 save endpoint rejects accented characters outright
    (confirmed live: a 422 "商品名に不正な文字「É」が含まれています" for an
    otherwise-valid title), so any leftover word carrying one would fail to
    save rather than just look slightly off.
    """
    normalized = unicodedata.normalize("NFKD", word)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


# Connector words left behind once the color/category/feature words around
# them are stripped out (e.g. "CAMICIA IN COTONE" -> "IN" after "CAMICIA"
# and "COTONE" are both removed elsewhere); dropped so they don't show up
# as meaningless leftovers in the title.
_DESCRIPTOR_STOPWORDS = {
    "IN", "WITH", "AND", "OF", "FOR", "A", "THE",
    "DI", "CON", "DA", "PER", "E", "IL", "LA", "LO", "GLI", "LE", "UN", "UNA",
}


# Generic material/construction words with one standard, unambiguous
# katakana form (confirmed with the seller) — distinct from
# _SIZE_WORD_TRANSLATIONS, whose entries are also treated as the first
# thing dropped when a title runs long; these describe the actual item and
# should stay even under space pressure. Brand-specific line/motif names
# (Andiamo, Marmont, Tabi, Cassandre, Compass, ...) are deliberately left
# untranslated as proper nouns.
_DESCRIPTOR_WORD_TRANSLATIONS = {
    "HORSEBIT": "ホースビット", "MONOGRAM": "モノグラム", "SUEDE": "スエード",
    "CANVAS": "キャンバス", "CHAIN": "チェーン", "CROCHET": "クロシェ",
    "RUBBER": "ラバー", "TWILL": "ツイル", "LACE": "レース", "ZIP": "ジップ",
    "CREWNECK": "クルーネック", "PUZZLE": "パズル", "FOLD": "フォールド",
    "FEATHERLIGHT": "フェザーライト", "AMAZONA": "アマゾナ", "FLAMENCO": "フラメンコ",
    "BALLET": "バレット", "RUNNER": "ランナー", "EDGE": "エッジ",
}


def _strip_keyword(text: str, keyword: str, *, allow_plural: bool = False) -> tuple[str, bool]:
    plural_suffix = "S?" if allow_plural else ""
    match = re.search(rf"\b{re.escape(keyword)}{plural_suffix}(?![A-Za-z])", text, re.IGNORECASE)
    if not match:
        return text, False
    start, end = match.start(), match.end()
    # Hyphenated compounds glue directly onto the matched word (e.g.
    # "Cotton-Blend", "Logo-Print"); consuming the hyphen too avoids
    # leaving a dangling "-Blend"/"-Print" behind in the title.
    if end < len(text) and text[end] == "-":
        end += 1
    elif start > 0 and text[start - 1] == "-":
        start -= 1
    return (text[:start] + text[end:]).strip(), True


_SIZE_WORD_TRANSLATIONS = {
    "MICRO": "マイクロ", "NANO": "ナノ", "MINI": "ミニ",
    "SMALL": "スモール", "MEDIUM": "ミディアム", "LARGE": "ラージ",
    "OVERSIZED": "オーバーサイズ", "OVERSIZE": "オーバーサイズ",
}


def _description(product: Product, product_type: str, *, translated_details: str | None = None) -> str:
    details = translated_details if translated_details else _clean_source_description(product.description)
    lines = [
        f"{product.brand}の{product_type}です。",
        "",
        "【商品詳細】",
        details,
        "",
        "海外正規取扱店から買い付ける新品・正規品です。",
        "在庫は常に変動するため、ご注文前に在庫確認のお問い合わせをお願いいたします。",
    ]
    result = "\n".join(lines)
    if len(result) > 3000:
        raise CandidatePreparationError(f"Generated BUYMA description exceeds 3000 characters: {product.sku}")
    return result


def _clean_source_description(value: str) -> str:
    text = value.strip().lstrip("'").strip()
    # BUYMA's own 商品コメント field rejects "•" outright (confirmed live:
    # a 422-equivalent on-page error, "商品コメントに不正な文字「•」が含ま
    # れています", for a thedoublef.com description using it as a bullet
    # separator with no surrounding whitespace, e.g. "suede• Color: White•
    # ..."); converted to the same newline-plus-nakaguro bullet style
    # already used for " - " below, rather than just stripped, so each
    # point stays readable on its own line.
    text = re.sub(r"\s*•\s*", "\n・", text)
    replacements = {
        "MADE IN Italy": "イタリア製", "MADE IN Turkey": "トルコ製", "Cotton": "コットン",
        "COTTON": "コットン", "POLYAMIDE": "ポリアミド", "ELASTANE": "エラスタン",
        "VIRGIN WOOL": "バージンウール", "WOOL": "ウール", "LAMB LEATHER": "ラムレザー",
        "Silk": "シルク", "Cupro": "キュプラ",
    }
    for source, replacement in replacements.items():
        text = text.replace(source, replacement)
    text = re.sub(
        r"LA MODELLA [ÈE] ALTA\s+(\d+)\s*CM E INDOSSA LA TAGLIA\s+([^\s,;]+)(?:\s+IT)?",
        r"モデル身長\1cm、着用サイズ\2 IT", text, flags=re.IGNORECASE,
    )
    text = re.sub(
        r"IL MODELLO [ÈE] ALTO\s+(\d+)\s*CM E INDOSSA LA TAGLIA\s+([^\s,;]+)(?:\s+IT)?",
        r"モデル身長\1cm、着用サイズ\2 IT", text, flags=re.IGNORECASE,
    )
    text = re.sub(r"\bnazione\s+madeIn\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bTessuto Primario\b", "主素材", text, flags=re.IGNORECASE)
    text = re.sub(r"\bTessuto Secondario\b", "副素材", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+-\s+", "\n・", text)
    text = re.sub(r"[ \t]{2,}", " ", text).strip(" ,;\n")
    return text or "素材・仕様の詳細は仕入先商品ページをご確認ください。"


def _positive_int(row: dict[str, str], key: str) -> int:
    try:
        value = int(Decimal(row[key]))
    except (KeyError, InvalidOperation, ValueError) as error:
        raise CandidatePreparationError(f"Candidate has invalid {key}: {row.get(key, '')}") from error
    if value <= 0:
        raise CandidatePreparationError(f"Candidate has non-positive {key}: {value}")
    return value


def _decimal(row: dict[str, str], key: str) -> Decimal:
    raw = row.get(key, "").strip().removesuffix("%")
    try:
        value = Decimal(raw)
    except InvalidOperation as error:
        raise CandidatePreparationError(f"Candidate has invalid {key}: {row.get(key, '')}") from error
    return value / 100 if row.get(key, "").strip().endswith("%") else value
