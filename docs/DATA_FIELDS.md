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
| exchange_rate | decimal | Currency conversion rate |
| purchase_jpy | integer | Purchase cost converted to JPY |
| overseas_shipping_jpy | integer | International shipping estimate |
| duty_tax_jpy | integer | Duty/tax estimate |
| domestic_shipping_jpy | integer | Domestic shipping estimate |
| packing_jpy | integer | Packing cost |
| buyma_fee_jpy | integer | BUYMA fee estimate |
| total_cost_jpy | integer | Total estimated cost |
| sale_price_jpy | integer | Suggested listing price |
| expected_profit_jpy | integer | Expected profit |
| expected_margin | decimal | Profit divided by sale price |

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
