from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Iterable

from buymafinder.core.models import Product, Source


class ShopAdapter(ABC):
    """Contract implemented by every shop integration."""

    code: str

    @abstractmethod
    def collect_product_links(self, source: Source, browser: Any) -> Iterable[str]:
        """Return canonical product URLs found in a category page."""
        raise NotImplementedError

    @abstractmethod
    def collect_product_detail(self, product_url: str, source: Source, browser: Any) -> Product:
        """Collect and normalize one product detail page."""
        raise NotImplementedError

    def normalize_product(self, product: Product) -> Product:
        """Apply common cleanup after shop-specific parsing."""
        product.product_url = product.product_url.split("#", 1)[0]
        product.brand = " ".join(product.brand.split())
        product.name = " ".join(product.name.split())
        return product
