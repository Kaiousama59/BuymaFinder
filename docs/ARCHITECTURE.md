# Architecture

## Processing flow

```text
source_urls.csv
    ↓
Source loader
    ↓
Shop registry
    ↓
Shop adapter
    ├─ collect product links
    └─ collect product detail
    ↓
Normalizer
    ↓
Pricing service
    ↓
SQLite repository
    ↓
Change detector
    ↓
Excel / CSV / JSON exporters
```

## Layers

### `buymafinder/core`

Shared data models, exceptions, configuration, logging, and interfaces. This layer must not import shop adapters.

### `buymafinder/shops`

One adapter per shop. Only this layer contains shop-specific URLs, selectors, page structure, cookies, pagination, and parsing rules.

### `buymafinder/services`

Application services such as pricing, change detection, translation/draft generation, and exports.

### `config`

Editable source URLs and operational settings. Users should be able to add or disable a category without editing Python.

### `data`

SQLite database and persistent scan state. Generated locally and excluded from Git.

### `output`, `logs`, `debug`

Generated artifacts. Keep only `.gitkeep` files in Git.

## Reliability rules

- Reuse one browser session where safe, but isolate recovery per shop.
- Retry a failed category without advancing its state.
- Apply explicit waits instead of relying only on fixed sleeps.
- Limit collection during development.
- Save debug evidence when selectors return zero items.
- Record scan start/end time and counts per shop/category.
- Do not mark a scan successful when collection fails.

## Initial milestone

The first milestone is not full automation. It is a reliable Eleonora Bonucci scan that returns up to 20 products per registered category and exports verifiable product URLs and prices.
