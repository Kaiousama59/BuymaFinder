from pathlib import Path

from buymafinder.core.models import Source
from buymafinder.shops.eleonora import EleonoraAdapter, parse_next_page_url


FIXTURES = Path(__file__).parent / "fixtures"
LIST_URL = "https://eleonorabonucci.com/en/women/sale/clothing"
SOURCE = Source(
    shop_code="eleonora",
    shop_name="Eleonora Bonucci",
    target="women",
    category="Clothing",
    list_url=LIST_URL,
)


class _FakeLocator:
    def __init__(self, present: bool) -> None:
        self._present = present

    @property
    def first(self) -> "_FakeLocator":
        return self

    def wait_for(self, state: str, timeout: int) -> None:
        if not self._present:
            raise TimeoutError("locator not attached")

    def count(self) -> int:
        return 0


class _FakePage:
    def __init__(self, pages: dict[str, str]) -> None:
        self._pages = pages
        self.url = ""
        self.visited: list[str] = []

    def goto(self, url: str, wait_until: str, timeout: int) -> None:
        if url not in self._pages:
            raise ValueError(f"Unexpected navigation: {url}")
        self.url = url
        self.visited.append(url)

    def wait_for_load_state(self, state: str) -> None:
        pass

    def locator(self, selector: str) -> _FakeLocator:
        if selector == ".product.sf-dress":
            return _FakeLocator("sf-dress" in self._pages[self.url])
        return _FakeLocator(False)

    def content(self) -> str:
        return self._pages[self.url]

    def close(self) -> None:
        pass


class _FakeBrowser:
    def __init__(self, page: _FakePage) -> None:
        self._page = page

    def new_page(self) -> _FakePage:
        return self._page


def test_parse_next_page_url_returns_link_with_incremented_start() -> None:
    html = (FIXTURES / "eleonora_category_page1.html").read_text(encoding="utf-8")

    next_url = parse_next_page_url(html, LIST_URL)

    assert next_url == f"{LIST_URL}?start=2&f=STATE"


def test_parse_next_page_url_returns_none_on_the_last_page() -> None:
    html = (FIXTURES / "eleonora_category_page2.html").read_text(encoding="utf-8")

    assert parse_next_page_url(html, f"{LIST_URL}?start=2&f=STATE") is None


def test_collect_product_links_follows_pagination_and_deduplicates() -> None:
    pages = {
        LIST_URL: (FIXTURES / "eleonora_category_page1.html").read_text(encoding="utf-8"),
        f"{LIST_URL}?start=2&f=STATE": (FIXTURES / "eleonora_category_page2.html").read_text(encoding="utf-8"),
    }
    page = _FakePage(pages)
    adapter = EleonoraAdapter(max_pages_per_source=10)

    links = adapter.collect_product_links(SOURCE, _FakeBrowser(page))

    assert links == [
        "https://eleonorabonucci.com/en/example/women/clothing/dresses/1001",
        "https://eleonorabonucci.com/en/example/women/clothing/dresses/1002",
        "https://eleonorabonucci.com/en/example/women/clothing/dresses/1003",
    ]
    assert page.visited == [LIST_URL, f"{LIST_URL}?start=2&f=STATE"]


def test_collect_product_links_respects_page_and_link_caps() -> None:
    pages = {
        LIST_URL: (FIXTURES / "eleonora_category_page1.html").read_text(encoding="utf-8"),
        f"{LIST_URL}?start=2&f=STATE": (FIXTURES / "eleonora_category_page2.html").read_text(encoding="utf-8"),
    }

    one_page_adapter = EleonoraAdapter(max_pages_per_source=1)
    page = _FakePage(dict(pages))
    assert len(one_page_adapter.collect_product_links(SOURCE, _FakeBrowser(page))) == 2
    assert page.visited == [LIST_URL]

    capped_adapter = EleonoraAdapter(max_pages_per_source=10, max_links_per_source=2)
    page = _FakePage(dict(pages))
    assert len(capped_adapter.collect_product_links(SOURCE, _FakeBrowser(page))) == 2
    assert page.visited == [LIST_URL]
