# Data Fields

## Source

| Field | Type | Meaning |
|---|---|---|
| shop_code | string | Stable adapter identifier |
| shop_name | string | Display name |
| target | string | women, men, kids, etc. |
| category | string | Source category |
| list_url | string | Category/list page URL |
| enabled | boolean | Whether the source is scanned |

## Product

| Field | Type | Meaning |
|---|---|---|
| shop_code | string | Source shop adapter |
| shop_name | string | Shop display name |
| target | string | women, men, etc. |
| category | string | Source category |
| brand | string | Brand name |
| name | string | Product name |
| product_url | string | Canonical product URL |
| sku | string | Shop or manufacturer product code |
| currency | string | ISO currency code |
| regular_price | decimal/null | Original price |
| sale_price | decimal/null | Current selling price |
| color | string | Color description |
| sizes | list | Size and stock information |
| description | string | Source description |
| image_urls | list | Product image URLs |
| in_stock | boolean/null | Overall stock state |
| collected_at | datetime | Collection timestamp |

## Pricing result

| Field | Type | Meaning |
|---|---|---|
| pricing_status | string | `priced` or the reason pricing was skipped |
| pricing_error | string | Concise error explaining skipped pricing |
| source_current_price | decimal/null | Sale price when available, otherwise regular price |
| source_currency | string | Source price ISO currency code |
| exchange_rate | decimal/null | Configured source-currency-to-JPY conversion rate |
| exchange_rate_safety_margin | decimal/null | Configured exchange-rate safety margin |
| adjusted_exchange_rate | decimal/null | Exchange rate multiplied by one plus the safety margin |
| purchase_cost_jpy | integer/null | Current source price converted with the adjusted exchange rate and rounded upward |
| international_shipping_jpy | integer/null | Configured international shipping cost |
| estimated_import_cost_jpy | integer/null | Estimated import cost calculated from purchase cost plus international shipping |
| domestic_shipping_jpy | integer/null | Configured domestic shipping cost |
| packing_cost_jpy | integer/null | Configured packing cost |
| pre_buyma_cost_jpy | integer/null | Purchase, shipping, import, domestic shipping, and packing costs |
| buyma_fee_rate | decimal/null | Configured BUYMA fee rate |
| buyma_fee_jpy | integer/null | BUYMA fee calculated from the suggested listing price |
| total_estimated_cost_jpy | integer/null | Pre-BUYMA cost plus BUYMA fee |
| suggested_listing_price_jpy | integer/null | Suggested listing price rounded upward to the configured increment |
| expected_profit_jpy | integer/null | Suggested listing price minus total estimated cost |
| expected_profit_margin | decimal/null | Expected profit divided by suggested listing price |

The estimated import cost taxable base is the upward-rounded purchase cost in JPY plus the configured international shipping cost. The configured category rate is applied to that base and rounded upward to the nearest whole JPY.

## Pricing configuration

`config/pricing.example.json` lists every supported key. Copy it to `config/pricing.json` manually, then replace every `null` value with a verified production value. `exchange_rates` maps each supported source currency to its JPY exchange rate. `target_profit_margin` and `exchange_rate_safety_margin` are the confirmed business settings. `international_shipping_jpy`, `domestic_shipping_jpy`, and `packing_cost_jpy` are fixed JPY costs. `buyma_fee_rate` is charged against the listing price, and `listing_price_rounding_increment_jpy` controls upward listing-price rounding. `exchange_rates` currently supports only `EUR`. All percentage values must be between zero inclusive and one exclusive, and `buyma_fee_rate + target_profit_margin` must be less than one. Fixed JPY costs must be zero or greater, while `listing_price_rounding_increment_jpy` must be a positive integer.

`estimated_import_cost_rates` uses normalized source category names. Its keys must be `Clothing`, `Footwear`, `Accessories`, and `Bags` for the configured sources. A missing category rate skips pricing only for products in that category.

## Change event

Allowed values:

- `new`
- `price_down`
- `price_up`
- `restocked`
- `sold_out`
- `size_added`
- `size_removed`
- `unchanged`
