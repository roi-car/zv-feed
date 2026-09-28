"""Unit tests for zv_feed parsing logic — runs without network access."""

import sys
from bs4 import BeautifulSoup

# Import functions we want to test
sys.path.insert(0, ".")
from zv_feed import (
    parse_product_url,
    extract_prices,
    Product,
    PRICE_RE,
    PRODUCT_URL_RE,
)


def assert_eq(actual, expected, label):
    status = "✓" if actual == expected else "✗"
    print(f"  {status} {label}: got {actual!r}")
    if actual != expected:
        print(f"      expected {expected!r}")
        sys.exit(1)


print("=" * 60)
print("TEST 1: URL parsing")
print("=" * 60)

test_url = "https://www.zadigetvoltaire.co.il/en/product/bags/shoulder+bags/lwba04001,black,moonrise-bag.html"
parsed = parse_product_url(test_url)
assert_eq(parsed["sku"], "LWBA04001", "SKU")
assert_eq(parsed["color"], "black", "color")
assert_eq(parsed["slug"], "moonrise bag", "slug")
assert_eq(parsed["category_path"], ["bags", "shoulder bags"], "category_path")

# Multi-word color with URL encoding
test_url2 = "https://www.zadigetvoltaire.co.il/en/product/new+arrivals/the+white+edit/wwow01735,light+blue,lienna-denim-jacket.html"
parsed2 = parse_product_url(test_url2)
assert_eq(parsed2["sku"], "WWOW01735", "SKU (multi-word color)")
assert_eq(parsed2["color"], "light blue", "color (multi-word)")


print()
print("=" * 60)
print("TEST 2: Price extraction (sale price)")
print("=" * 60)

# Simulating the price block from the moonrise bag page
mock_html_sale = """
<html><body>
<h1>moonrise bag</h1>
<span class="price">1340.50 ILS-30%1915.00 ILS</span>
</body></html>
"""
soup = BeautifulSoup(mock_html_sale, "html.parser")
price, sale = extract_prices(soup)
assert_eq(price, 1915.00, "original price")
assert_eq(sale, 1340.50, "sale price")


print()
print("=" * 60)
print("TEST 3: Price extraction (no sale)")
print("=" * 60)

mock_html_full = """
<html><body>
<h1>some product</h1>
<span class="price">1915.00 ILS</span>
</body></html>
"""
soup = BeautifulSoup(mock_html_full, "html.parser")
price, sale = extract_prices(soup)
assert_eq(price, 1915.00, "full price")
assert_eq(sale, None, "sale price (should be None)")


print()
print("=" * 60)
print("TEST 4: URL pattern regex (discovery filter)")
print("=" * 60)

valid_paths = [
    "/en/product/bags/shoulder+bags/lwba04001,black,moonrise-bag.html",
    "/en/product/ready+to+wear/t-shirts/jwts01508,pastel,omma-zadig-t-shirt.html",
    "/en/product/zadig+days/bags+%2526+accessories/swct02097,caramelo,angie-gourmette-mules.html",
]
invalid_paths = [
    "/en/product/bags/",  # category page, not product
    "/en/page/contact.html",  # static page
    "/en/cms/who_we_are.html",
    "/en/product/bags/shoulder+bags/",  # subcategory, no product slug
]
for p in valid_paths:
    match = bool(PRODUCT_URL_RE.match(p))
    assert_eq(match, True, f"should match: {p[:50]}...")
for p in invalid_paths:
    match = bool(PRODUCT_URL_RE.match(p))
    assert_eq(match, False, f"should not match: {p[:50]}")


print()
print("=" * 60)
print("TEST 5: Product XML rendering")
print("=" * 60)

prod = Product(
    id="LWBA04001_BLACK",
    item_group_id="LWBA04001",
    title="MOONRISE BAG - BLACK",
    description="Baguette bag in grained leather with signature wings.",
    link="https://www.zadigetvoltaire.co.il/en/product/bags/shoulder+bags/lwba04001,black,moonrise-bag.html",
    price=1915.00,
    sale_price=1340.50,
    image_link="https://cdnphotos.fastmag.fr/photos/27539/source/lwba04001/black/1/photo.jpg",
    additional_image_links=[
        f"https://cdnphotos.fastmag.fr/photos/27539/source/lwba04001/black/{n}/photo.jpg"
        for n in range(2, 6)
    ],
    color="Black",
    size="U",
    product_type="Bags > Shoulder Bags",
)
xml = prod.to_xml()
print(xml)
print()

# Validate critical pieces are in the output
checks = [
    ("g:id", "<g:id>LWBA04001_BLACK</g:id>"),
    ("g:item_group_id", "<g:item_group_id>LWBA04001</g:item_group_id>"),
    ("g:price", "<g:price>1915.00 ILS</g:price>"),
    ("g:sale_price", "<g:sale_price>1340.50 ILS</g:sale_price>"),
    ("g:brand", "<g:brand>ZADIG&amp;VOLTAIRE</g:brand>"),
    ("g:color", "<g:color>Black</g:color>"),
    ("g:size", "<g:size>U</g:size>"),
    ("g:age_group", "<g:age_group>adult</g:age_group>"),
    ("additional images", "<g:additional_image_link>"),
]
for label, needle in checks:
    found = needle in xml
    assert_eq(found, True, f"contains {label}")
# Make sure the ampersand was properly escaped (XML safety)
assert "ZADIG&VOLTAIRE" not in xml, "raw ampersand leaked into XML (should be &amp;)"
print("  ✓ XML escaping is correct")


print()
print("=" * 60)
print("TEST 7: Carousel discounts must not become our sale price")
print("=" * 60)
from zv_feed import extract_description, clean_text, infer_google_category, make_variant_id

CAROUSEL = """
<div class="reco">
  <div class="tile">ZV PASS CARD HOLDER 300.00 ILS -87% 2325.00 ILS</div>
  <div class="tile"><del>645.00 ILS</del> 115.00 ILS</div>
</div>"""

html_a = f"""<html><body><h1>keyring</h1>
<div itemprop="offers" itemscope><meta itemprop="price" content="580.00">
<span class="price">580.00 ILS</span></div>{CAROUSEL}</body></html>"""
p, s = extract_prices(BeautifulSoup(html_a, "html.parser"))
assert_eq((p, s), (580.0, None), "full price ignores carousel -N% and <del>")

html_b = """<html><body><div itemprop="offers"><meta itemprop="price" content="300.00">
</div><div class="reco">300.00 ILS -10% 2325.00 ILS</div></body></html>"""
p, s = extract_prices(BeautifulSoup(html_b, "html.parser"))
assert_eq((p, s), (300.0, None), "rejects discount block with inconsistent %")

html_c = f"""<html><body>{CAROUSEL}<div itemprop="offers">
<meta itemprop="price" content="1792.00">1792.00 ILS -30% 2560.00 ILS</div></body></html>"""
p, s = extract_prices(BeautifulSoup(html_c, "html.parser"))
assert_eq((p, s), (2560.0, 1792.0), "real sale still detected after a carousel")

html_d = f"""<html><body><div itemprop="offers"><meta itemprop="price" content="1407.00">
<del>2345.00 ILS</del> <span>1407.00 ILS</span></div>{CAROUSEL}</body></html>"""
p, s = extract_prices(BeautifulSoup(html_d, "html.parser"))
assert_eq((p, s), (2345.0, 1407.0), "strikethrough inside offer block")

print()
print("=" * 60)
print("TEST 8: Descriptions with unescaped quotes / boilerplate")
print("=" * 60)
raw = ('<html><head><meta property="og:description" content="Hoodie with front embroidery. '
       '- Contrasting "Rock\'N\'Roll Is Not Dead" embroidery on the front - Hood" />'
       '</head><body><h1>x</h1></body></html>')
d = extract_description(BeautifulSoup(raw, "html.parser"), raw)
assert_eq(d.endswith("on the front - Hood"), True, "og:description not cut at inner quote")

raw2 = ('<html><head><meta property="og:description" content="zadig & voltaire האתר הרשמי של המותג" />'
        '</head><body><div itemprop="description">Men\'s henley t-shirt in black. - Short sleeves</div>'
        '</body></html>')
d2 = extract_description(BeautifulSoup(raw2, "html.parser"), raw2)
assert_eq(d2, "Men's henley t-shirt in black. - Short sleeves", "Hebrew boilerplate rejected")

raw3 = '<html><head><meta property="og:description" content="zadig & voltaire האתר הרשמי" /></head></html>'
assert_eq(extract_description(BeautifulSoup(raw3, "html.parser"), raw3), "", "boilerplate only -> empty")

raw4 = ('<html><head><meta property="og:description" content="Mid-length dress. Model is 176 cm / 5\' 9" '
        'and is wearing a size S Composition 100% Silk" /></head></html>')
assert_eq(extract_description(BeautifulSoup(raw4, "html.parser"), raw4).endswith("100% Silk"), True,
          "inch mark in model height does not truncate")

print()
print("=" * 60)
print("TEST 9: clean_text mojibake")
print("=" * 60)
R = "\uFFFD"
assert_eq(clean_text(f"Long dress. {R}Long dress in linen {R}V-neck {R}Sleeveless"),
          "Long dress. - Long dress in linen - V-neck - Sleeveless", "bullets -> ' - '")
assert_eq(clean_text(f"{R}Voltaire{R} print on the front"), '"Voltaire" print on the front', "curly quotes")
assert_eq(clean_text(f"{R}Rock{R}N{R}Roll Is Not Dead{R} embroidery"),
          '"Rock\'N\'Roll Is Not Dead" embroidery', "apostrophes inside quotes")
assert_eq(clean_text(f"wash at 20{R}C"), "wash at 20°C", "degree sign")

print()
print("=" * 60)
print("TEST 10: GPC + colour mapping fixes")
print("=" * 60)
assert_eq(infer_google_category("Men > Pants & Jeans", "PABLO SHORTS ENCRE - DARK BLUE"), 207, "shorts under Pants leaf -> 207")
assert_eq(infer_google_category("Men > Pants & Jeans", "POMA PANTS"), 204, "pants unaffected")
assert_eq(infer_google_category("Accessories > Fragrance", "TOME 1 LA PURETE 50ML"), 479, "fragrance leaf -> 479")
assert_eq(infer_google_category("New Arrivals > Gifts", "PARFUM ZADIG 30ML"), 479, "parfum keyword -> 479")
assert_eq(make_variant_id("JMTS01794", "ardoise"), "JMTS01794_DARK GRAY", "ARDOISE id matches production")


print()
print("=" * 60)
print("TEST 11: Name-only descriptions + override CSVs")
print("=" * 60)
import os, tempfile
from zv_feed import load_description_overrides, load_gpc_overrides

raw4 = ('<html><head><meta property="og:description" content="zadig & voltaire האתר הרשמי" /></head>'
        '<body><div itemprop="description">sweela sweatshirt black</div></body></html>')
assert_eq(extract_description(BeautifulSoup(raw4, "html.parser"), raw4), "", "name-only description rejected")

tmp = tempfile.mkdtemp()
dpath = os.path.join(tmp, "d.csv")
with open(dpath, "w", encoding="utf-8") as f:
    f.write('# comment line\n#\nid,description\n'
            'OWLI01099_BLACK,"Gloves in black. - Soft, warm hand feel"\n'
            'KWSW03101_HEATHER GREY,Sweater with space in id\n')
d = load_description_overrides(dpath)
assert_eq(d.get("OWLI01099_BLACK"), "Gloves in black. - Soft, warm hand feel", "quoted value with comma")
assert_eq("KWSW03101_HEATHER GREY" in d, True, "id with space in colour")

gpath = os.path.join(tmp, "g.csv")
with open(gpath, "w", encoding="utf-8") as f:
    f.write('# Manual GPC overrides\n# more comments\nid,google_product_category\nLWBA00001_ROAD,5841\n# LWBA9_X,1\n')
assert_eq(load_gpc_overrides(gpath), {"LWBA00001_ROAD": 5841}, "GPC CSV with comment header now loads")
assert_eq(load_description_overrides(os.path.join(tmp, "missing.csv")), {}, "missing file -> no overrides")
assert_eq(len(load_description_overrides("description_overrides.csv")), 7, "repo CSV has the 7 seeded ids")


print()
print("=" * 60)
print("ALL TESTS PASSED ✓")
print("=" * 60)
