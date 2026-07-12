# Codex Development Rules

## Project goal

BuymaFinder automates product collection, analysis, profit calculation, change detection, and listing-draft preparation for BUYMA. Final listing and confirmation remain manual.

## Mandatory architecture rules

1. Keep `main.py` as orchestration only.
2. Put shop-specific selectors, parsing, and navigation in `buymafinder/shops/<shop>.py`.
3. Put shared browser, logging, storage, pricing, and export logic outside shop adapters.
4. A new shop must be addable without modifying existing shop adapters.
5. Never silently swallow errors. Log the shop, category, URL, and operation.
6. When collection returns zero products, save HTML and a screenshot to `debug/`.
7. Deduplicate products by normalized canonical URL and, when available, SKU.
8. Keep secrets and local settings in `.env`; never commit `.env`.
9. Do not automate BUYMA listing submission or bypass access controls.
10. Add or update tests whenever behavior changes.

## Data compatibility

Do not rename or remove exported fields without a migration note. Add new fields in a backward-compatible way.

## Shop adapter contract

Each adapter must implement:

- `collect_product_links(source, browser)`
- `collect_product_detail(product_url, browser)`
- `normalize_product(raw_product)`

Adapters should return shared model objects rather than shop-specific dictionaries.

## Validation before commit

Run:

```bash
python -m pytest
python -m compileall buymafinder main.py
```

Document tests that could not run and the reason.
