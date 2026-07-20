# BuymaFinder Handover (AI-agnostic)

Last updated: 2026-07-19 night (Eleonora color extraction fixed, pending real-run verification)
Repository: https://github.com/Kaiousama59/BuymaFinder (main branch is canonical)

## How to use this file

Give this file to any AI assistant (Claude, ChatGPT, etc.) at the start of a
session. The latest code is always on GitHub main — if the assistant cannot
fetch GitHub, ask the owner to paste only the specific files needed for the
task. This file is the single source of truth for project state; update it at
the end of each work phase.

## Project goal

Collect products from the Eleonora Bonucci online shop and prepare
ready-to-review BUYMA drafts (selection, pricing, images, Japanese
titles/descriptions, category, color, size, shipping) so the owner only
reviews and publishes.

Hard rules:
- Final publishing on BUYMA stays manual (the tool only clicks 下書き保存).
- Never bypass CAPTCHA or access controls. Never commit secrets.
- Do not invent unknown costs/specs; ask the owner or verify officially.
- All code/comments/logs/tests/commits in English; only BUYMA buyer-facing
  Japanese text (titles, descriptions) in Japanese.
- WARNING: an obsolete "B version" of main.py (imports buymafinder.browser/
  config/storage, logs "rebuild v0.1.0") exists outside the repo. Never use it.

## Architecture

- `main.py` = collection orchestration only.
- `buymafinder/shops/eleonora.py` = Eleonora-specific scraping.
- `buymafinder/services/` = shared services; `buymafinder/core/` = models.
- Pipeline scripts at repo root: `main.py` (collect) →
  `select_listing_candidates.py` (score/filter to listing_candidates.csv) →
  `prepare_listing_candidates.py` (build listing packages + queue) →
  `fill_buyma_drafts.py` (fill BUYMA forms, save drafts).

## Current state (all verified on real sites)

### Collection (commit fcf57b5, stable)
- `python3 main.py --limit 100` collects 100 products in ~20 min, 0 dupes.
- Pagination (`?start=N`), SQLite resume (`--resume`), graceful Ctrl+C
  (press ONCE, wait 10-20s), realistic Chrome UA (fixes site blocking),
  60s navigation timeout with retry.

### Draft filling (working; 19/20 then 3/3 after fixes)
- `fill_buyma_drafts.py PACKAGE_ROOT --queue output/prepared_candidate_queue.json
  --limit N --delay-seconds 3 --save-drafts` fills and saves BUYMA drafts in a
  persistent Chrome profile (`data/buyma_browser`, owner logs in manually).
- Progress file `data/buyma_batch_progress.json` records completed packages.
  DELETE IT when re-running the same packages after deleting drafts on BUYMA,
  otherwise "No pending listing packages were found."
- Shipping: multiple methods are checked per product type
  (thin items: ゆうパケット+ゆうパック60; jackets/dresses: 60+80;
  coats/leather: 80+100). Methods missing from the form are logged and
  skipped; product is skipped only if none exist.
- Color: auto-classified from source color/name/description keywords
  (IT/EN → BUYMA family+name, 16 families). BUYMA family options are matched
  by PREFIX ("ブラック" → "ブラック（黒）系"). Unknown color → 色指定なし +
  warning (owner sets manually; reversible items stay unspecified by design).
- Sizes: IT numeric sizes use unit 指定なし (NOT cm) with reference JP sizes
  IT38=S, IT40=M, IT42=L, IT44+=XL (reference only; brands vary).
- Candidates failing preparation rules are skipped with warnings, not fatal.

## Owner's environment

- Mac, `/Users/inouryuji/Desktop/BUYMA/BuymaFinder`; `source venv/bin/activate`;
  use `python3` (no `python`). Owner is not a programmer: give copy-pasteable
  one-line commands, explain errors simply, ask before destructive actions.
- Package output root: `$HOME/Desktop/BUYMA/ListingImages`.
- Typical rerun sequence after code/config changes:
  1. overwrite changed files, `git add ... && git commit && git push`
  2. `rm data/buyma_batch_progress.json` (only if BUYMA drafts were deleted)
  3. `python3 prepare_listing_candidates.py --candidates-csv output/listing_candidates.csv --output-root "$HOME/Desktop/BUYMA/ListingImages" --queue output/prepared_candidate_queue.json`
  4. `python3 fill_buyma_drafts.py "$HOME/Desktop/BUYMA/ListingImages" --queue output/prepared_candidate_queue.json --limit 3 --delay-seconds 3 --save-drafts` (test 3, inspect, then --limit 20)
- Pitfalls seen: Finder-duplicated "* 2.py" files break pytest collection;
  running scripts without venv gives "No module named playwright"; multi-line
  commands with trailing "\\" can leave the shell waiting (use one-liners).

## Backlog (priority order)

1. ~~Collect color from Eleonora product pages~~ — DONE (2026-07-19). Root
   cause: product pages render color as
   `<label>color<small>DENIM</small></label>` (no `itemprop`/dedicated
   `class` to key off), so the old `itemprop="color"` / `class="color"`
   lookups always returned empty. Fixed by adding
   `_EleonoraHTMLParser.label_value(label, root)` in
   `buymafinder/shops/eleonora.py`, which matches on a `<label>`'s own text
   and returns its child `<small>` value; wired in as a third fallback in
   `parse_product_detail_html`'s `color=` field. Verified against a real
   product page (Maison Margiela denim jacket, SKU S29AM0412_M30066962).
   NOTE: not yet verified against a full `--limit 100` real run — if any
   products still come back with empty color, the label may sit outside
   `product_root` (the `single-product` div); widen the search root in that
   case.
2. Richer Japanese descriptions: better material/origin formatting, brand
   blurbs, per-type copy. Remove 品番 line from the buyer-facing description
   (keep it in the private memo).
3. Garment measurements (着丈/肩幅/胸囲/袖丈) if the source page has them.
4. Production pricing config review (config/pricing.json exists locally and
   works; NOT committed by design). Rate/fee questions belong to the owner.
5. Shutdown hardening for the collector (extra Ctrl+C during cleanup).
6. Update README.md / docs/ARCHITECTURE.md (stale).
7. DENIM color priority (explicit color words should beat material-based
   guesses) — only if real misclassifications appear.

## Verified fee/shipping facts (owner-confirmed)

- BUYMA shipping options seen on real forms: 日本郵便 - ゆうパック,
  かんたんBUYMA便【匿名配送】 - ゆうパック 60/80/100サイズ; ゆうパケット is
  NOT offered for some products/categories.
- Color family labels on BUYMA forms use the pattern ホワイト（白）系,
  ブラック（黒）系, グレー（灰色）系, ブラウン（茶色）系, ベージュ系,
  グリーン（緑）系, ... (prefix matching handles these).
