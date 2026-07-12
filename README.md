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

Test: Codex repository connection
