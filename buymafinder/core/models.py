from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True, slots=True)
class Source:
    shop_code: str
    shop_name: str
    target: str
    category: str
    list_url: str
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class SizeStock:
    size: str
    in_stock: bool


@dataclass(slots=True)
class Product:
    shop_code: str
    shop_name: str
    target: str
    category: str
    brand: str
    name: str
    product_url: str
    currency: str
    regular_price: Optional[Decimal] = None
    sale_price: Optional[Decimal] = None
    sku: str = ""
    color: str = ""
    sizes: list[SizeStock] = field(default_factory=list)
    description: str = ""
    image_urls: list[str] = field(default_factory=list)
    in_stock: Optional[bool] = None
    collected_at: datetime = field(default_factory=datetime.now)

    @property
    def current_price(self) -> Optional[Decimal]:
        return self.sale_price if self.sale_price is not None else self.regular_price
