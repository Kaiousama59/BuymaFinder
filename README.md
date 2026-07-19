# BuymaFinder

海外ショップの商品情報を収集し、BUYMA出品前の作業を半自動化するPythonプロジェクトです。

## 目的

商品一覧・商品詳細の取得、利益計算、差分検知、出品用データ作成までを自動化し、最終確認とBUYMAへの出品操作だけを人が行います。

## 基本方針

- 出品操作は自動化しない
- 利用規約とサイト負荷に配慮する
- ショップ固有処理は `buymafinder/shops/` に分離する
- 共通処理はショップ固有コードから切り離す
- 最低利益率は10%
- 新しいショップを追加しても既存ショップを壊さない構成を優先する

## 最初の対応ショップ

Eleonora Bonucci

対象カテゴリ:

- Women Clothing
- Women Footwear
- Women Accessories
- Women Bags

## 予定する処理

1. `config/source_urls.csv` から巡回先を読み込む
2. カテゴリ一覧から商品URLを収集する
3. 商品詳細から価格・品番・色・サイズ・在庫・画像を取得する
4. 為替、送料、手数料を含めて販売価格案を計算する
5. SQLiteへ保存して前回データと比較する
6. 新商品、値下げ、値上げ、再入荷、在庫切れを判定する
7. Excel、CSV、JSONへ出品候補を出力する
8. 日本語タイトル、説明文、タグの下書きを作成する

## ディレクトリ方針

```text
BuymaFinder/
├── main.py
├── config/
├── docs/
├── buymafinder/
│   ├── core/
│   ├── shops/
│   └── services/
├── tests/
├── output/
├── logs/
└── debug/
```

## 開発資料

- `docs/ARCHITECTURE.md`
- `docs/DATA_FIELDS.md`
- `AGENTS.md`

## 現在の段階

プロジェクト基盤作成中です。商品取得処理は次の実装段階で追加します。

## Pricing configuration

Pricing is disabled until explicit production values are supplied. Create a local configuration file manually:

```bash
cp config/pricing.example.json config/pricing.json
```

`config/pricing.json` is ignored by Git and must contain verified exchange, shipping, fee, import-cost-rate, and rounding values. The engine never invents missing monetary values.

## Prepare one reviewed BUYMA draft package

This step does not log in to BUYMA or publish anything. Copy and review the
product-specific settings, then create a folder containing `listing_data.json`
and the product images:

```bash
cp config/listing.example.json config/listing.json
python prepare_listing.py \
  --product-url "https://eleonorabonucci.com/en/ami/women/clothing/tops/424047"
```

Output defaults to `~/Desktop/BUYMA/ListingImages/<supplier>/<brand>/<sku>/`.
Downloads are retried, query-string duplicates and images belonging to another
product are removed, and a different existing file is never overwritten.

## Fill and save one BUYMA draft

The browser automation is restricted to BUYMA's new-listing URL and never clicks
the public listing or confirmation buttons. The first run opens a dedicated Chrome
profile; log in to BUYMA there if requested. Start with review-only mode:

```bash
python fill_buyma_draft.py \
  "$HOME/Desktop/BUYMA/ListingImages/Eleonora_Bonucci/AMI_PARIS/FTP811_JE0117100"
```

After the filled form has been reviewed, rerun with `--save-draft` to click only
the exact draft-save button. An existing saved shipping method matching the
configured name is required.

Immediately before filling the BUYMA form, the draft filler reopens the Eleonora
Bonucci product page and verifies every size. Available sizes are marked as
`買付可`, sold-out sizes as `買付不可`, purchasable quantity is set to 1 when at
least one size is available, and on-hand stock remains 0. If live stock cannot be
verified, the run stops without saving the draft.

Overseas buying locations are selected as a two-level path. The current AMI
PARIS package uses `ヨーロッパ` followed by `イタリア`.

## Save multiple prepared BUYMA drafts

After reviewing and preparing multiple product folders, save up to five drafts
in one browser session:

```bash
python fill_buyma_drafts.py \
  "$HOME/Desktop/BUYMA/ListingImages/Eleonora_Bonucci" \
  --save-drafts \
  --limit 5
```

The command recursively finds prepared `listing_data.json` files. It verifies
live source stock separately for every product, saves only BUYMA drafts, and
records completed products in `data/buyma_batch_progress.json`. If one product
fails, the batch stops immediately. Rerunning the command skips recorded
products and resumes with the next package. It never clicks a public listing or
confirmation button.
