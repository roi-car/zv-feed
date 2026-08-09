"""
Sitemap writer — add-on to zv_feed.py

Reuses the BFS crawl already performed for the product feed. No new
scraping logic; this only serializes URLs already discovered.

Integration points (marked below):
  1. In the BFS crawl loop, keep category/subcategory URLs instead of
     discarding them once product links are extracted.
  2. After the crawl finishes and the feed is written, call
     write_sitemap() with the product URL list + category URL list.
"""

from datetime import date, timezone, datetime
from xml.sax.saxutils import escape

# Hardcoded static pages not reachable via the category BFS.
# Update if the site adds/removes top-level pages.
STATIC_PAGES = [
    "https://www.zadigetvoltaire.co.il/en/",
    "https://www.zadigetvoltaire.co.il/en/page/about-us.html",
    "https://www.zadigetvoltaire.co.il/en/page/stores.html",
    "https://www.zadigetvoltaire.co.il/en/page/contact.html",
]

SITEMAP_URL_LIMIT = 50_000  # Google's per-file cap; ~700-800 URLs is nowhere close


def write_sitemap(
    product_urls,
    category_urls=None,
    static_pages=STATIC_PAGES,
    output_path="docs/sitemap.xml",
    lastmod_date=None,
):
    """
    Write a standard XML sitemap (urlset) from already-discovered URLs.

    Args:
        product_urls: iterable of canonical product page URLs
                       (dedup by SKU+color+slug, same as the feed —
                       pass the same deduped set used for feed.xml)
        category_urls: iterable of category/subcategory URLs visited
                        during the BFS crawl. Optional but recommended —
                        gives crawlers a path into the site structure,
                        not just leaf product pages.
        static_pages: list of fixed non-product URLs (home, about, etc.)
        output_path: where to write the sitemap XML
        lastmod_date: date object to stamp on every <url>. Defaults to
                      today (the scraper run date). Deliberately not
                      per-product — Fastmag exposes no real modified
                      timestamp, and an honest "still live as of this
                      crawl" signal is safer than a fabricated one.

    Returns:
        int — number of URLs written (for logging / sanity checks)
    """
    if lastmod_date is None:
        lastmod_date = date.today()
    lastmod_str = lastmod_date.isoformat()

    # Priority/changefreq are optional per the sitemap spec and Google
    # has said it largely ignores them, but Bing and some other crawlers
    # still weight them lightly. Cheap to include, harmless if ignored.
    entries = []

    for url in static_pages:
        entries.append(_url_entry(url, lastmod_str, changefreq="weekly", priority="0.8"))

    if category_urls:
        # dedupe while preserving nothing in particular — order doesn't matter to crawlers
        for url in sorted(set(category_urls)):
            entries.append(_url_entry(url, lastmod_str, changefreq="daily", priority="0.6"))

    seen_products = set()
    for url in product_urls:
        if url in seen_products:
            continue
        seen_products.add(url)
        entries.append(_url_entry(url, lastmod_str, changefreq="daily", priority="0.5"))

    total = len(entries)
    if total > SITEMAP_URL_LIMIT:
        raise ValueError(
            f"{total} URLs exceeds the {SITEMAP_URL_LIMIT}-URL sitemap limit — "
            "split into a sitemap index + multiple files if the catalog grows this large."
        )

    xml_parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        *entries,
        "</urlset>",
    ]

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(xml_parts))

    return total


def _url_entry(url, lastmod_str, changefreq="daily", priority="0.5"):
    return (
        "  <url>\n"
        f"    <loc>{escape(url)}</loc>\n"
        f"    <lastmod>{lastmod_str}</lastmod>\n"
        f"    <changefreq>{changefreq}</changefreq>\n"
        f"    <priority>{priority}</priority>\n"
        "  </url>"
    )


# --- Integration point 1: inside the existing BFS crawl -------------------
#
# Wherever the crawler currently does something like:
#
#     for link in discover_category_links(page):
#         if link not in visited:
#             queue.append(link)
#             visited.add(link)
#
# ...also append `link` to a `category_urls` list/set that survives past
# the crawl (right now these are probably only used to reach products,
# then dropped). No new HTTP requests needed — you already fetch these
# pages to extract product links from them.


# --- Integration point 2: after the crawl, alongside feed writing --------
#
#     product_urls = [p["url"] for p in products]   # same dedup as feed.xml
#     write_feed(products, "docs/feed.xml")          # existing call
#     write_sitemap(product_urls, category_urls, output_path="docs/sitemap.xml")
#
# Both files come from the same single crawl — no extra load on the site.
