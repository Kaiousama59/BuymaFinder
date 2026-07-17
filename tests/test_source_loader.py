from pathlib import Path

from buymafinder.core.source_loader import load_enabled_sources


def test_load_enabled_sources_skips_disabled_rows(tmp_path: Path) -> None:
    source_csv = tmp_path / "sources.csv"
    source_csv.write_text(
        "shop_code,shop_name,target,category,list_url,enabled\n"
        "eleonora,Eleonora Bonucci,women,Clothing,https://example.test/clothing,1\n"
        "eleonora,Eleonora Bonucci,women,Bags,https://example.test/bags,0\n",
        encoding="utf-8",
    )

    sources = load_enabled_sources(source_csv)

    assert [source.category for source in sources] == ["Clothing"]
