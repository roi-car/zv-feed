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
print("ALL TESTS PASSED ✓")
print("=" * 60)
