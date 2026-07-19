from __future__ import annotations

import csv
import json
import re
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from pathlib import Path

from buymafinder.core.listing_models import ListingSettings
from buymafinder.core.models import Product
from buymafinder.services.listing_preparer import prepare_listing_package


class CandidatePreparationError(ValueError):
    """Raised when a candidate cannot be converted without guessing listing data."""


_PRODUCT_RULES = (
    (("BIKER", "PELLE", "LEATHER"), "レザージャケット", ("レディースファッション", "アウター", "レザージャケット・コート")),
    (("TRENCH",), "トレンチコート", ("レディースファッション", "アウター", "トレンチコート")),
    (("CABAN",), "コート", ("レディースファッション", "アウター", "コート")),
    (("BLOUSON",), "ブルゾン", ("レディースファッション", "アウター", "ブルゾン")),
    (("GIACCA",), "ジャケット", ("レディースファッション", "アウター", "ジャケット")),
    (("JEANS",), "デニム・ジーパン", ("レディースファッション", "ボトムス", "デニム・ジーパン")),
    (("ABITO",), "ワンピース", ("レディースファッション", "ワンピース・オールインワン", "ワンピース")),
    (("CARDIGAN",), "カーディガン", ("レディースファッション", "トップス", "カーディガン")),
    (("MAGLIA",), "ニット・セーター", ("レディースファッション", "トップス", "ニット・セーター")),
    (("FELPA",), "スウェット", ("レディースファッション", "トップス", "スウェット・トレーナー")),
    (("T-SHIRT",), "Tシャツ", ("レディースファッション", "トップス", "Tシャツ・カットソー")),
    (("CAMICIA",), "シャツ", ("レディースファッション", "トップス", "ブラウス・シャツ")),
    (("TOP",), "トップス", ("レディースファッション", "トップス", "トップスその他")),
)

_EASY_BUYMA_PACKET = ("かんたんBUYMA便【匿名配送】 - ゆうパケット", 280)
_EASY_BUYMA_60 = ("かんたんBUYMA便【匿名配送】 - ゆうパック 60サイズ", 800)
_EASY_BUYMA_80 = ("かんたんBUYMA便【匿名配送】 - ゆうパック 80サイズ", 950)
_EASY_BUYMA_100 = ("かんたんBUYMA便【匿名配送】 - ゆうパック 100サイズ", 1100)

# Multiple methods are checked on the form so the actual shipment can match
# the real parcel size. The first entry defines buyer_shipping_jpy.
_DEFAULT_SHIPPING_METHODS = (_EASY_BUYMA_PACKET, _EASY_BUYMA_60)
_COLOR_RULES = (
    (("NERO", "BLACK", "NOIR"), ("ブラック系", "ブラック")),
    (("BIANCO", "WHITE", "OFF-WHITE", "OFFWHITE", "AVORIO", "IVORY", "PANNA"), ("ホワイト系", "ホワイト")),
    (("GRIGIO", "GREY", "GRAY", "ANTRACITE"), ("グレー系", "グレー")),
    (("BEIGE", "SABBIA", "CREMA", "CAMMELLO", "CAMEL", "TAUPE", "ECRU"), ("ベージュ系", "ベージュ")),
    (("MARRONE", "BROWN", "CIOCCOLATO", "MORO", "CUOIO", "TABACCO", "COGNAC"), ("ブラウン系", "ブラウン")),
    (("NAVY", "BLU SCURO", "INDACO"), ("ネイビー系", "ネイビー")),
    (("BLU", "BLUE", "COBALTO", "AZZURRO", "CELESTE", "DENIM"), ("ブルー系", "ブルー")),
    (("VERDE", "GREEN", "MILITARE", "KHAKI", "KAKI", "OLIVA", "SALVIA"), ("グリーン系", "グリーン")),
    (("ROSSO", "RED", "BORDEAUX", "BURGUNDY", "VINACCIA"), ("レッド系", "レッド")),
    (("ROSA", "PINK", "FUCSIA", "MAGENTA"), ("ピンク系", "ピンク")),
    (("VIOLA", "PURPLE", "LILLA", "LAVANDA"), ("パープル系", "パープル")),
    (("GIALLO", "YELLOW", "SENAPE", "MOSTARDA"), ("イエロー系", "イエロー")),
    (("ARANCIONE", "ORANGE", "CORALLO"), ("オレンジ系", "オレンジ")),
    (("ARGENTO", "SILVER"), ("シルバー系", "シルバー")),
    (("ORO", "GOLD", "DORATO"), ("ゴールド系", "ゴールド")),
    (("MULTICOLOR", "MULTICOLOUR", "FANTASIA", "STAMPA", "FLOREALE", "PRINT"), ("マルチカラー", "マルチカラー")),
)

_SHIPPING_METHODS_BY_PRODUCT_TYPE = {
    "コート": (_EASY_BUYMA_80, _EASY_BUYMA_100),
    "トレンチコート": (_EASY_BUYMA_80, _EASY_BUYMA_100),
    "レザージャケット": (_EASY_BUYMA_80, _EASY_BUYMA_100),
    "ブルゾン": (_EASY_BUYMA_60, _EASY_BUYMA_80),
    "ジャケット": (_EASY_BUYMA_60, _EASY_BUYMA_80),
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


def prepare_candidate_packages(
    products: list[Product],
    candidate_rows: list[dict[str, str]],
    base_settings: ListingSettings,
    output_root: Path,
    *,
    download_images: bool = True,
) -> list[Path]:
    by_identity = {(product.product_url.strip(), product.sku.strip().casefold()): product for product in products}
    folders: list[Path] = []
    for row in candidate_rows:
        key = (row.get("product_url", "").strip(), row.get("sku", "").strip().casefold())
        product = by_identity.get(key)
        if product is None:
            raise CandidatePreparationError(f"Candidate was not found in product CSV: {row.get('sku', '')}")
        settings = listing_settings_for_candidate(product, row, base_settings)
        folders.append(prepare_listing_package(product, settings, output_root, download_images=download_images))
    return folders


def listing_settings_for_candidate(
    product: Product, candidate: dict[str, str], base: ListingSettings
) -> ListingSettings:
    product_type, category_path = classify_product(product.name)
    color_family, color_name = classify_color(product)
    listing_price = _positive_int(candidate, "suggested_listing_price_jpy")
    expected_profit = _positive_int(candidate, "expected_profit_jpy")
    expected_margin = _decimal(candidate, "expected_profit_margin")
    title = _title(product.brand, product_type, product.name)
    description = _description(product, product_type)
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
    size_notes = "仕入先で在庫確認できたサイズのみ買付可能として登録しています。注文前に在庫確認をお願いします。"
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
        description_source_url=product.product_url,
    )


def classify_product(source_name: str) -> tuple[str, tuple[str, str, str]]:
    normalized = " ".join(source_name.upper().split())
    for keywords, japanese_type, category in _PRODUCT_RULES:
        if any(keyword in normalized for keyword in keywords):
            return japanese_type, category
    raise CandidatePreparationError(f"No reviewed BUYMA category rule for product: {source_name}")


def classify_color(product: Product) -> tuple[str, str]:
    """Map the source color (or name/description keywords) to a BUYMA color.

    Raises CandidatePreparationError when no rule matches, so the owner can
    extend _COLOR_RULES instead of the tool guessing a wrong color.
    """
    for text in (product.color, product.name, product.description):
        normalized = " ".join((text or "").upper().split())
        if not normalized:
            continue
        for keywords, result in _COLOR_RULES:
            if any(keyword in normalized for keyword in keywords):
                return result
    raise CandidatePreparationError(
        f"No color rule matched product {product.sku}: color={product.color!r} name={product.name!r}"
    )


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


def _title(brand: str, product_type: str, source_name: str) -> str:
    features = []
    upper = source_name.upper()
    translations = (
        ("REVERSIBILE", "リバーシブル"), ("FLOREALE", "フローラル"), ("PAILLETTES", "スパンコール"),
        ("DENIM", "デニム"), ("LUNGA", "長袖"), ("CORTA", "半袖"), ("LOGO", "ロゴ"),
        ("LANA MERINO", "メリノウール"), ("LANA", "ウール"), ("SETA", "シルク"),
        ("COTONE", "コットン"), ("RIGHE", "ストライプ"), ("DRAPPEGGIO", "ドレープ"),
        ("DOPPIOPETTO", "ダブルブレスト"),
    )
    for source, japanese in translations:
        if source in upper and japanese not in features:
            features.append(japanese)
    title = " ".join([brand, *features[:2], product_type])
    if len(title) > 60:
        title = f"{brand} {product_type}"
    if len(title) > 60:
        raise CandidatePreparationError(f"Generated BUYMA title exceeds 60 characters: {title}")
    return title


def _description(product: Product, product_type: str) -> str:
    details = _clean_source_description(product.description)
    lines = [
        f"{product.brand}の{product_type}です。",
        "",
        "【商品詳細】",
        details,
        "",
        f"品番：{product.sku}",
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
