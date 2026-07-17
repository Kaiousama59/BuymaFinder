from buymafinder.core.urls import unique_normalized_urls


def test_unique_normalized_urls_removes_tracking_fragments_and_duplicates() -> None:
    urls = unique_normalized_urls(
        [
            "http://EXAMPLE.test/product/?utm_source=mail#details",
            "https://example.test/product",
            "https://example.test/product?colour=blue&fbclid=value",
        ]
    )

    assert urls == ["https://example.test/product", "https://example.test/product?colour=blue"]
