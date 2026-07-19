from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ListingSettings:
    japanese_title: str
    japanese_description: str
    buyma_category_path: list[str]
    color_family: str
    color_name: str
    listing_price_jpy: int
    buyer_shipping_jpy: int
    shipping_method: str
    arrival_days_min: int
    arrival_days_max: int
    purchasing_location: str = "overseas"
    shipping_location: str = "domestic"
    duties_included: bool = True
    purchasable_quantity: int = 1
    on_hand_quantity: int = 0
    private_memo: str = ""
    size_notes: str = ""
    purchase_deadline_days: int = 90
    buying_country: str = "イタリア"
    shipping_prefecture: str = "神奈川県"
    size_variation: bool = True
    size_unit: str = "cm"
    description_source_url: str = ""

    def __post_init__(self) -> None:
        required_text = {
            "japanese_title": self.japanese_title,
            "japanese_description": self.japanese_description,
            "color_family": self.color_family,
            "shipping_method": self.shipping_method,
        }
        invalid = [key for key, value in required_text.items() if not value.strip()]
        if len(self.japanese_title) > 60:
            invalid.append("japanese_title")
        if not self.buyma_category_path or any(not part.strip() for part in self.buyma_category_path):
            invalid.append("buyma_category_path")
        if self.listing_price_jpy <= 0 or self.buyer_shipping_jpy < 0:
            invalid.append("price")
        if not (0 < self.arrival_days_min <= self.arrival_days_max):
            invalid.append("arrival_days")
        if self.purchasable_quantity <= 0 or self.on_hand_quantity < 0:
            invalid.append("quantity")
        if not (1 <= self.purchase_deadline_days <= 90):
            invalid.append("purchase_deadline_days")
        if not self.buying_country.strip() or not self.shipping_prefecture.strip():
            invalid.append("location")
        if invalid:
            raise ValueError(f"Invalid listing settings: {', '.join(sorted(set(invalid)))}")


@dataclass
class ListingDraft:
    supplier: str
    source_url: str
    brand: str
    sku: str
    source_name: str
    source_currency: str
    source_price: str
    source_description: str
    source_sizes: list[str]
    source_size_stock: dict[str, bool]
    settings: ListingSettings
    image_urls: list[str] = field(default_factory=list)
    image_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["buyma_total_price_jpy"] = self.settings.listing_price_jpy + self.settings.buyer_shipping_jpy
        return result

    def write_json(self, path: Path) -> None:
        import json

        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
