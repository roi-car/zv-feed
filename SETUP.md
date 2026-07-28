# Zadig & Voltaire Israel — Product Feed Generator

Self-contained scraper that turns the Z&V Israel website into a Meta- and Google-compatible product feed XML.

**What it solves:**
- Multiple product images per item (Fastmag CDN stores 5+ images per product; only 1 reaches OpenGraph today)
- Correct `price` vs `sale_price` separation (no more "sale price as regular price" mismatches)
- Variant-level identifiers (each color is a separate `g:id`, grouped by `g:item_group_id`)
- Up-to-date inventory and pricing on whatever schedule you choose
- Zero dev team involvement — you own the entire pipeline

The output is a single `feed.xml` file you upload (or host) for **Meta Commerce Manager** and/or **Google Merchant Center** to fetch on schedule.

---

## Quick start (local test)

```bash
# 1. Install Python 3.11+ and dependencies
pip install -r requirements.txt

# 2. Test on 20 products first to verify everything works
python zv_feed.py --limit 20

# 3. Open feed.xml to inspect the output

# 4. Full crawl (a few minutes depending on catalog size)
python zv_feed.py
```

That produces `feed.xml` in the current directory.

---

## How it works

1. **Discovery** — Crawls the four top-level category pages (`women`, `men`, `bags`, `accessories`) and follows pagination to find every product URL. Deduplicates by SKU.
2. **Extraction** — For each product page:
   - Parses SKU, color, and slug from the URL
   - Extracts title from `<h1>`, description from `og:description`
   - Parses prices from the visible HTML (correctly separating sale price from original price — fixes the bug where Meta sees sale-as-regular)
   - Probes the Fastmag CDN at `cdnphotos.fastmag.fr/photos/27539/source/{sku}/{color}/{1..8}/photo.jpg` to find every available image (this is the actual fix for the "multiple images" problem)
   - Detects size and availability from the page
3. **Feed generation** — Outputs RSS 2.0 with the `g:` Google namespace, which is the format both Meta and Google accept.

Each variant gets its own `<item>` with a unique `g:id` (e.g. `LWBA04001-BLACK`) and a shared `g:item_group_id` (`LWBA04001`) that links color variants together. This is what Meta needs to display all colors of a bag as one product family in shopping ads.

---

## Hosting the feed (so Meta/Google can fetch it)

You need a **public URL** that Meta/Google can fetch. The easiest free option:

### Option A — GitHub Actions + GitHub Pages (recommended, $0/month)

1. Create a new GitHub repo (private is fine for the code; the feed itself ends up published).
2. Upload all the files in this folder.
3. In the repo: **Settings → Pages → Source: deploy from branch → `main` / `/docs`** — this publishes anything in `docs/` to `https://<your-username>.github.io/<repo-name>/feed.xml`.
4. The included `.github/workflows/feed.yml` runs daily at 04:00 UTC, regenerates `docs/feed.xml`, and commits it. Meta/Google will see updates on their next fetch.

### Option B — S3 / Cloudflare R2

Run `zv_feed.py` on any server or your laptop, upload `feed.xml` to an S3 bucket with public read, give Meta the bucket URL.

### Option C — Run locally and upload manually

For testing or if you want full control: run the script, take the file, upload to Commerce Manager as a one-off.

---

## Connecting the feed in Meta Commerce Manager

1. Open Commerce Manager → your catalog → **Data Sources** → **Add Items** → **Use Bulk Upload** → **Scheduled Feed**
2. Paste the feed URL (e.g. `https://yourname.github.io/zv-feed/feed.xml`)
3. Set the schedule (daily is fine; hourly if you want faster price/stock updates)
4. Meta will fetch and validate. Within ~30 minutes you'll see items in the catalog with proper images, prices, and variants.

For Google Merchant Center, the same XML works: **Products → Feeds → Add Primary Feed → Scheduled Fetch → paste URL**.

---

## Tuning

All knobs are at the top of `zv_feed.py`:

| Setting | What it controls |
|---|---|
| `SEED_CATEGORIES` | Which category pages discovery crawls. Add `/en/product/sales/` if you want sale items always included. |
| `MAX_IMAGES_PER_PRODUCT` | How many image numbers to probe (default 8). Increase if you find products with more. |
| `REQUEST_DELAY_SECONDS` | Politeness delay between discovery requests |
| `MAX_WORKERS` | Concurrent product-extraction workers. Don't go above ~8 to avoid hammering the server. |

---

## Common adjustments you may need

**Sale period dates.** If you want to advertise sale prices with explicit start/end dates, add a `g:sale_price_effective_date` field in `Product.to_xml()` formatted as `2026-01-01T00:00-08:00/2026-02-01T00:00-08:00`.

**Out-of-stock detection is conservative.** The current detection looks for "out of stock" / "sold out" text. If you find OOS products being reported as in-stock, inspect a real OOS page and adjust `detect_availability()`.

**Size variants.** Currently the script treats each color URL as one item with a single size. If you need separate `<item>` entries per size (rare — usually one row per color is enough for Meta), modify the loop in `main()` to expand sizes.

**Hebrew descriptions.** Currently scrapes the `/en/` version. To switch to Hebrew, change `LANG` to `he` at the top and update `SEED_CATEGORIES` paths to `/product/...` (no `/en/`).

---

## Troubleshooting

**"no images discovered"** — The image CDN path may have changed, or the color slug in the URL doesn't match the CDN folder name. Check one failing product manually: open `https://cdnphotos.fastmag.fr/photos/27539/source/{sku-lowercase}/{color}/1/photo.jpg` in a browser.

**"no price found"** — The price markup on the page changed. Open a product page, grep for "ILS" in the HTML, and adjust the regex in `extract_prices()`.

**Empty discovery** — Either the seed category URLs returned no products (check by opening them in a browser) or pagination isn't using `?p=N`. Open a category page in Chrome, click page 2, and confirm the URL pattern.

---

## Why this approach (vs. an XML feed built by dev)

You don't have a dev team that can produce XML on demand, and the underlying Fastmag instance doesn't expose one. This bypasses both constraints by treating the rendered website as the source of truth — which is actually how Google Shopping is reading you today anyway. The difference is now you control the schema, the field mapping, the schedule, and the channels it serves.
