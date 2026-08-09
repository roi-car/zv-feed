"""
Zadig & Voltaire Israel - Product Feed Generator
=================================================
Scrapes the Z&V Israel website and produces a Meta-compatible
(also Google Merchant Center-compatible) product feed in XML format.

Usage:
    python zv_feed.py                  # full crawl, outputs feed.xml
    python zv_feed.py --limit 20       # only process 20 products (for testing)
    python zv_feed.py --urls urls.txt  # use a pre-built list of product URLs

Output:
    feed.xml - upload to Meta Commerce Manager / Google Merchant Center
              as a scheduled feed URL.
"""

import argparse
import logging
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin, urlparse, unquote
from xml.sax.saxutils import escape as xml_escape

import requests
from bs4 import BeautifulSoup

from sitemap_writer import write_sitemap

# =============================================================================
# CONFIGURATION
# =============================================================================

BASE_URL = "https://www.zadigetvoltaire.co.il"
CDN_BASE = "https://cdnphotos.fastmag.fr/photos/27539"
BRAND = "ZADIG&VOLTAIRE"
CURRENCY = "ILS"
LANG = "en"

# Top-level umbrella categories — these collectively cover the catalog.
# The site doesn't have a /women/ landing page; women's clothing lives under
# /ready+to+wear/ instead. /zadig+days/ catches the sale section; /new+arrivals/
# catches recent additions. Dedup happens by SKU later so overlap doesn't matter.
SEED_CATEGORIES = [
    "/en/product/ready+to+wear/",   # women's clothing
    "/en/product/men/",              # men's clothing
    "/en/product/bags/",             # all bags
    "/en/product/accessories/",      # shoes, jewelry, etc.
    "/en/product/new+arrivals/",     # new items across all categories
    "/en/product/zadig+days/",       # current sale
    "/en/product/sales/",            # standing sale section
]

# How many image variants to probe per product (1..MAX_IMAGES_PER_PRODUCT).
# Fastmag stores images at numbered paths. Most products have 2-5; some have more.
MAX_IMAGES_PER_PRODUCT = 8

# Politeness — delay between requests, max concurrent workers.
REQUEST_DELAY_SECONDS = 0.3
MAX_WORKERS = 4

USER_AGENT = (
    "ZV-Feed-Generator/1.0 "
    "(contact: digital@zadigetvoltaire.co.il)"
)

REQUEST_TIMEOUT = 20

# =============================================================================
# LOGGING
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("zv-feed")


# =============================================================================
# HTTP SESSION
# =============================================================================

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9,he;q=0.8",
    })
    return s


# =============================================================================
# GOOGLE PRODUCT CATEGORY INFERENCE
# =============================================================================
# Rules derived from manual category assignments. We use a two-stage approach:
# 1. Leaf category match (most reliable) — e.g. "Clutches" -> 5841
# 2. Title keyword match (fallback for ambiguous leaves like "Ceremony Collection")
# When neither matches, we leave the field empty rather than guess wrong —
# Meta does not require this field and a missing value is safer than a wrong one.

# Stage 0: HIGH-CONFIDENCE title overrides — checked BEFORE leaf matching.
# These identify the product so definitively that even when the site categorizes
# them under a broader/wrong leaf (e.g. wallets sold in "Last Chance > Bags"
# or "Bags > Our Lines > Shopper"), the title wins.
# Keep this list SHORT and CONSERVATIVE — only words that unambiguously mean
# one product type.
GPC_TITLE_OVERRIDES = [
    ("card holder", 2668),
    ("cardholder", 2668),
    ("card case", 2668),
    ("card wallet", 2668),
    ("pass card", 2668),   # "ZV PASS CARD HOLDER"
    ("wallet", 2668),      # catches MINI ZV WALLET, SUNNY WALLET, etc.
]


# Stage 1: leaf-category -> GPC code. Keys are lowercased product_type leaves.
# All codes verified against Google's official taxonomy
# (https://www.google.com/basepages/producttype/taxonomy-with-ids.en-US.txt).
# When uncertain, we leave the field empty and let Google auto-categorize
# rather than guess wrong.
GPC_BY_LEAF = {
    # Handbags - 3032 = Apparel & Accessories > Handbags, Wallets & Cases > Handbags
    "bags": 3032, "handbags": 3032, "shoulder bags": 3032, "clutches": 3032,
    "mini bags": 3032, "shopper": 3032, "tote bags": 3032, "crossbody bags": 3032,
    # Wallets - 2668 = Apparel & Accessories > Handbags, Wallets & Cases > Wallets & Money Clips
    "wallets & purses": 2668, "wallets": 2668, "card holders": 2668,
    "card wallets": 2668, "small leather goods": 2668,
    # Card cases - 2668 (grouped with wallets, since Z&V card cases are fashion
    # small leather goods, not workplace pass holders). Was previously 6170.
    "card cases": 2668, "pass holders": 2668,
    # Bag accessories - 6552 = Apparel & Accessories > Handbag & Wallet Accessories (parent)
    "shoulder straps": 6552, "bag accessories": 6552,
    # Shoes - 187 = Apparel & Accessories > Shoes
    "shoes": 187, "sneakers": 187, "boots": 187, "trainers": 187,
    "sandals": 187, "mules": 187,
    # Jewelry - 188 = Apparel & Accessories > Jewelry (parent for charms/general)
    "jewelry": 188,
    # Watches - 201 = Apparel & Accessories > Jewelry > Watches
    "watches": 201,
    # Belts - 169 = Apparel & Accessories > Clothing Accessories > Belts
    "belts": 169,
    # Hats - 173 = Apparel & Accessories > Clothing Accessories > Hats
    "hats & caps": 173, "hats": 173, "caps": 173,
    # Scarves - 177 = Apparel & Accessories > Clothing Accessories > Scarves & Shawls
    "scarves": 177, "scarves & shawls": 177,
    # Hair accessories - 171 = Apparel & Accessories > Clothing Accessories > Hair Accessories
    "hair accessories": 171,
    # Shirts & Tops - 212 = Apparel & Accessories > Clothing > Shirts & Tops
    "shirts & tops": 212, "shirts": 212, "tops": 212,
    "t-shirts": 212, "blouses": 212, "tank tops": 212,
    "tunics": 212, "sweatshirts": 212, "hoodies": 212,
    # Pants - 204 = Apparel & Accessories > Clothing > Pants
    "pants & jeans": 204, "pants": 204, "jeans": 204, "trousers": 204,
    # Skirts - 1581 = Apparel & Accessories > Clothing > Skirts
    "skirts": 1581,
    # Shorts - 207 = Apparel & Accessories > Clothing > Shorts
    "shorts": 207,
    # Sweaters/cardigans/jumpers - 212 = Shirts & Tops (Google groups knitwear here;
    # no dedicated sweater code exists in current taxonomy). More specific and better
    # for search than the generic 1604 Clothing parent.
    "sweaters & cardigans": 212, "sweaters": 212,
    "cardigans": 212, "knitwear": 212,
    # Outerwear - 5598 = Apparel & Accessories > Clothing > Outerwear > Coats & Jackets
    "coats & jackets": 5598, "jackets": 5598, "coats": 5598, "outerwear": 5598,
}

# Stage 2: title keyword -> GPC code. Order matters — more specific first
# (e.g. "tank top" before "top", "card holder" before "card").
# Same verified-codes principle. Edge categories with unclear taxonomy mapping
# are intentionally omitted so Google's auto-classifier takes over.
GPC_BY_KEYWORD = [
    # --- Multi-word product types (must come before single-word fragments) ---
    ("card holder", 2668), ("cardholder", 2668),
    ("card case", 2668), ("card wallet", 2668),
    ("tank top", 212), ("t-shirt", 212), ("t shirt", 212),
    # --- Specific accessories ---
    ("watch", 201),
    ("scarf", 177), ("bandana", 177),
    ("keyring", 175), ("keychain", 175),
    ("belt", 169),
    ("new era", 173), ("bob ", 173), (" cap", 173), ("hat", 173),
    # --- Jewelry (specific child categories) ---
    ("bracelet", 191), ("necklace", 196), ("earring", 194),
    ("choker", 196), ("ring", 200), ("charm", 192),
    # --- Footwear ---
    ("trainer", 187), ("sneaker", 187), ("boot", 187),
    ("mule", 187), ("sandal", 187),
    # --- Clothing (specific items before generic) ---
    ("dress", 2271), ("tomboy", 2271),
    ("skirt", 1581), ("short", 207),
    ("jeans", 204), ("pant", 204), ("trouser", 204),
    # "waistcoat" before "coat" (substring conflict)
    ("waistcoat", 1831),  # 1831 = Outerwear > Vests
    ("blazer", 5598), ("jacket", 5598), ("jackt", 5598), ("coat", 5598),
    ("cardigan", 212), ("sweater", 212), ("knit", 212),
    ("jumper", 212), ("vest", 1831),
    ("hoodie", 212), ("sweatshirt", 212), ("blouse", 212),
    ("tunic", 212), ("camisole", 212), ("polo", 212),
    ("shirt", 212), ("tee", 212),
    # --- Bag-specific types (must come before "bag") ---
    ("wallet", 2668), ("purse", 2668),
    ("strap", 6552),
    ("backpack", 3032), ("clutch", 3032), ("tote", 3032),
    ("crossbody", 3032), ("pouch", 3032),
    # --- Generic bag/top last ---
    ("bag", 3032), ("top", 212),
]


def load_gpc_overrides(path: str = "gpc_overrides.csv") -> dict:
    """Load manual GPC overrides from a CSV (id, google_product_category).

    Lets you correct specific products without editing code. Format:
        id,google_product_category
        LWBA00001-ROAD,5841
        SWCT02097-BLACK,187

    Returns an empty dict if the file doesn't exist (overrides are optional).
    """
    import csv
    import os
    if not os.path.exists(path):
        return {}
    overrides = {}
    try:
        with open(path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                pid = (row.get("id") or "").strip()
                gpc = (row.get("google_product_category") or "").strip()
                if pid and gpc:
                    try:
                        overrides[pid] = int(gpc)
                    except ValueError:
                        log.warning(f"  override skipped (non-integer GPC): {pid}={gpc}")
        log.info(f"loaded {len(overrides)} GPC overrides from {path}")
    except Exception as e:
        log.warning(f"  could not load GPC overrides: {e}")
    return overrides


def infer_google_category(product_type: str, title: str) -> Optional[int]:
    """Return the Google Product Category code for a product.

    Strategy:
    0. HIGH-CONFIDENCE title override — for words like "wallet" that
       unambiguously identify the product type. This runs FIRST because
       the site occasionally shelves products under the wrong leaf
       (e.g. wallets in a "Bags" clearance category).
    1. Leaf-category match ('Bags > Clutches' -> 'clutches')
    2. Full title keyword scan (handles collection-named leaves like
       'Ceremony Collection' or 'Resort Selection')
    3. Return None if no rule matches — better to omit than to mislabel.
    """
    # Stage 0: high-confidence title overrides (win over leaf)
    if title:
        title_lower = " " + title.lower() + " "
        for keyword, code in GPC_TITLE_OVERRIDES:
            if keyword in title_lower:
                return code

    # Stage 1: leaf category
    if product_type:
        leaf = product_type.split(" > ")[-1].lower().strip()
        if leaf in GPC_BY_LEAF:
            return GPC_BY_LEAF[leaf]

    # Stage 2: full title keyword scan
    if title:
        for keyword, code in GPC_BY_KEYWORD:
            if keyword in title_lower:
                return code

    return None


# =============================================================================
# GENDER INFERENCE
# =============================================================================
# Z&V Israel SKUs follow a [collection-letter][gender-letter][category]nnnnn pattern.
# Position 2 indicates gender: W = women's, M = men's.

def infer_gender(sku: str) -> Optional[str]:
    """Return Meta-compatible gender: 'female' / 'male' / 'unisex'.
    Returns None only if the SKU is too short to read."""
    if not sku or len(sku) < 2:
        return None
    second = sku[1].upper()
    if second == "W":
        return "female"
    if second == "M":
        return "male"
    # Accessories and items without a W/M marker default to unisex
    return "unisex"


# =============================================================================
# COLOR TRANSLATION (French -> English)
# =============================================================================
# Applied to the DISPLAYED color (g:color, title text) only.
# The g:id keeps the original French slug to preserve catalog continuity —
# changing IDs would orphan all existing Meta ad history, pixel matches,
# and ad-set targeting.

COLOR_FR_TO_EN = {
    "NOIR": "Black",
    "NOIR GOLD": "Black Gold",
    "ENCRE": "Dark Blue",
    "INK": "Dark Blue",
    "MARINE": "Blue Navy",
    "GRIS": "Grey",
    "GRIS CHINE CLAI": "Heather Grey",
    "GRIS CHINE": "Grey Melange",
    "BLANC": "White",
    "ECRU": "Off White",
    "ROAD": "Road Grey",
    # Common additions that may appear over time:
    "ROUGE": "Red",
    "BLEU": "Blue",
    "VERT": "Green",
    "JAUNE": "Yellow",
    "ORANGE": "Orange",
    "ROSE": "Pink",
    "VIOLET": "Purple",
    "BEIGE": "Beige",
    "MARRON": "Brown",
}


def translate_color(color: str) -> str:
    """Translate a French color name to English.
    Returns title-cased original if no mapping exists (e.g., 'caramelo' -> 'Caramelo').
    """
    if not color:
        return color
    upper = color.upper().strip()
    if upper in COLOR_FR_TO_EN:
        return COLOR_FR_TO_EN[upper]
    return color.title()


# =============================================================================
# DATA MODEL
# =============================================================================

@dataclass
class Product:
    """A single product variant (e.g. moonrise bag in black).

    Variants of the same product share an item_group_id (the SKU root).
    """

    # Identity
    id: str  # unique per variant, e.g. "LWBA04001-BLACK"
    item_group_id: str  # parent SKU, e.g. "LWBA04001"

    # Display
    title: str
    description: str
    link: str

    # Pricing (in ILS, as decimals)
    price: float  # original / list price
    sale_price: Optional[float] = None  # set if discounted

    # Media
    image_link: str = ""
    additional_image_links: list = field(default_factory=list)

    # Attributes
    brand: str = BRAND
    color: Optional[str] = None
    size: Optional[str] = None
    gender: Optional[str] = None
    google_product_category: Optional[str] = None
    product_type: Optional[str] = None  # breadcrumb path

    # Inventory
    availability: str = "in stock"
    condition: str = "new"
    age_group: str = "adult"

    # Custom labels — always emitted in XML even when empty so the schema is
    # explicit. Populate via supplemental feed in Commerce Manager rather than
    # hard-coding here, since labels (bestseller, margin tier, season) are
    # marketing decisions that change independently of product data.
    custom_label_0: Optional[str] = None
    custom_label_1: Optional[str] = None
    custom_label_2: Optional[str] = None
    custom_label_3: Optional[str] = None
    custom_label_4: Optional[str] = None

    def to_xml(self) -> str:
        """Render this product as a Meta/Google-compatible RSS <item>."""
        parts = [
            f"  <item>",
            tag("g:id", self.id),
            tag("g:item_group_id", self.item_group_id),
            tag("title", self.title),
            tag("description", self.description),
            tag("link", self.link),
            tag("g:image_link", self.image_link),
        ]

        for extra in self.additional_image_links[:10]:  # Meta caps at 10
            parts.append(tag("g:additional_image_link", extra))

        parts.append(tag("g:availability", self.availability))
        parts.append(tag("g:condition", self.condition))
        parts.append(tag("g:age_group", self.age_group))
        parts.append(tag("g:price", f"{self.price:.2f} {CURRENCY}"))

        if self.sale_price is not None and self.sale_price < self.price:
            parts.append(tag("g:sale_price", f"{self.sale_price:.2f} {CURRENCY}"))

        parts.append(tag("g:brand", self.brand))

        if self.color:
            parts.append(tag("g:color", self.color))
        if self.size:
            parts.append(tag("g:size", self.size))
        if self.gender:
            parts.append(tag("g:gender", self.gender))
        if self.product_type:
            parts.append(tag("g:product_type", self.product_type))
        if self.google_product_category:
            parts.append(tag("g:google_product_category", self.google_product_category))

        # We have brand + MPN (the SKU), which satisfies Meta's identifier
        # requirements even without GTIN. Flag accordingly for better match quality.
        parts.append(tag("g:mpn", self.item_group_id))
        parts.append(tag("g:identifier_exists", "yes"))

        # Custom labels — always emitted, even when empty, so the schema is
        # explicit and supplemental feeds in Commerce Manager can populate them.
        for i in range(5):
            val = getattr(self, f"custom_label_{i}", None) or ""
            text = xml_escape(str(val))
            parts.append(f"    <g:custom_label_{i}>{text}</g:custom_label_{i}>")

        parts.append("  </item>")
        return "\n".join(parts)


def tag(name: str, value) -> str:
    """Produce a safely-escaped XML tag."""
    if value is None or value == "":
        return ""
    text = xml_escape(str(value))
    return f"    <{name}>{text}</{name}>"


# =============================================================================
# DISCOVERY — find product URLs by crawling category pages
# =============================================================================

PRODUCT_URL_RE = re.compile(
    r"^/en/product/[^/]+/.*?/[a-z0-9]+,[^,]+,[^,]+\.html$",
    re.IGNORECASE,
)


CATEGORY_URL_RE = re.compile(
    r"^/en/product/[^/]+(?:/[^/]+)*/$",
    re.IGNORECASE,
)


def _product_key(url: str) -> str:
    """Return the SKU,color,slug identifier from a product URL.
    Two URLs with the same key are the same product, even if they live
    under different category paths (e.g. /bags/our+lines/sunny/X.html and
    /zadig+days/bags+%2526+accessories/X.html)."""
    return url.rsplit("/", 1)[-1].lower()


def discover_product_urls(session: requests.Session, seeds: list) -> list:
    """BFS crawl: from each seed, follow every subcategory link the site exposes
    and collect all product URLs we encounter.

    This avoids needing to know Fastmag's pagination URL pattern — instead we
    rely on the fact that every product appears under at least one leaf category
    that fits on a single page (sub-50 products per leaf is typical).

    Deduplication is by product filename (sku,color,slug.html), not full URL,
    because the same product is often linked from multiple category paths.
    """
    seen_products = set()       # set of product keys (sku,color,slug)
    out = []
    visited_categories = set()
    queue = list(seeds)

    while queue:
        path = queue.pop(0)
        if path in visited_categories:
            continue
        visited_categories.add(path)

        url = urljoin(BASE_URL, path)
        html = fetch(session, url)
        if not html:
            continue

        soup = BeautifulSoup(html, "html.parser")
        new_products = 0
        new_categories = 0

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("http"):
                parsed = urlparse(href)
                # Only follow same-site links
                if parsed.netloc and not parsed.netloc.endswith("zadigetvoltaire.co.il"):
                    continue
                link_path = parsed.path
            else:
                link_path = href

            # Strip query strings and fragments
            link_path = link_path.split("?")[0].split("#")[0]

            # Product page?
            if PRODUCT_URL_RE.match(link_path):
                full = urljoin(BASE_URL, link_path)
                key = _product_key(full)
                if key not in seen_products:
                    seen_products.add(key)
                    out.append(full)
                    new_products += 1
                continue

            # Subcategory page? (anything under /en/product/ that ends with /)
            if CATEGORY_URL_RE.match(link_path) and link_path not in visited_categories:
                if link_path not in queue:
                    queue.append(link_path)
                    new_categories += 1

        log.info(f"  {path}: +{new_products} products, +{new_categories} new subcategories"
                 f" (total products: {len(out)}, queue: {len(queue)})")
        time.sleep(REQUEST_DELAY_SECONDS)

    log.info(f"discovery complete: {len(out)} unique product URLs from "
             f"{len(visited_categories)} category pages")
    return out, visited_categories


# =============================================================================
# PRODUCT EXTRACTION
# =============================================================================

# Matches "1340.50 ILS-30%1915.00 ILS"  or  "1915.00 ILS" (no sale)
PRICE_RE = re.compile(
    r"(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)\s*ILS"
    r"(?:\s*-\s*\d+\s*%\s*(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)\s*ILS)?"
)


def full_unquote(s: str) -> str:
    """Unquote until stable. Some site URLs are double or triple URL-encoded
    (e.g. '%252526' which decodes to '%2526' which decodes to '%26' = '&').
    """
    prev = None
    for _ in range(5):  # safety limit
        if s == prev:
            break
        prev = s
        s = unquote(s)
    return s


def parse_product_url(url: str) -> dict:
    """Parse SKU, color, slug from the URL.

    /en/product/bags/shoulder+bags/lwba04001,black,moonrise-bag.html
        -> {sku: LWBA04001, color: black, slug: moonrise-bag,
            category_path: ["bags", "shoulder bags"]}
    """
    path = urlparse(url).path
    parts = [full_unquote(p).replace("+", " ") for p in path.strip("/").split("/")]
    # parts -> ['en', 'product', 'bags', 'shoulder bags', 'lwba04001,black,moonrise-bag.html']

    filename = parts[-1].replace(".html", "")
    file_parts = filename.split(",", 2)
    if len(file_parts) != 3:
        return {}

    sku, color, slug = file_parts
    category_path = parts[2:-1]  # everything between 'product' and the filename

    return {
        "sku": sku.upper(),
        "color": color.replace("+", " ").strip(),
        "slug": slug.replace("-", " ").strip(),
        "category_path": category_path,
    }


def clean_text(text: str) -> str:
    """Repair common mojibake patterns in product descriptions.

    The site's source data has some characters stored as broken bytes
    (likely cp1252 leakage in a UTF-8 CMS), which decode to the Unicode
    replacement character �. We patch the predictable cases.
    """
    if not text:
        return text
    # Degree sign: "20�C" -> "20°C"
    text = re.sub(r"(\d+)\uFFFDC\b", r"\1°C", text)
    # Curly quotes around a word or phrase: "�Voltaire�" -> "\"Voltaire\""
    text = re.sub(r"\uFFFD([^\uFFFD]{1,40}?)\uFFFD", r'"\1"', text)
    # Anything left over — just drop the lone replacement chars
    text = text.replace("\uFFFD", "")
    return text


def extract_product(session: requests.Session, url: str) -> Optional[Product]:
    """Fetch a product page and extract all available data."""
    html = fetch(session, url)
    if not html:
        return None

    meta = parse_product_url(url)
    if not meta:
        log.warning(f"  could not parse URL: {url}")
        return None

    soup = BeautifulSoup(html, "html.parser")

    # --- Title ---
    # Prefer the H1, fall back to og:title or URL slug.
    title = None
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)
    if not title:
        og_title = soup.find("meta", property="og:title")
        if og_title:
            title = og_title.get("content", "").strip()
    if not title:
        title = meta["slug"]
    # Capitalize nicely
    title = title.upper() if title.islower() else title

    # Translate color to English for display (g:color, title), but keep
    # the original French slug for g:id so catalog continuity is preserved.
    color_display = translate_color(meta["color"]) if meta["color"] else None

    # Include color in title so Meta can show variants distinctly
    if color_display and color_display.lower() not in title.lower():
        title = f"{title} - {color_display.upper()}"

    # --- Description ---
    description = ""
    og_desc = soup.find("meta", property="og:description")
    if og_desc:
        description = og_desc.get("content", "").strip()
    # Trim newlines & truncate (Meta limit: 9999 chars; Google: 5000 — keep short)
    description = " ".join(description.split())[:5000]
    if not description:
        description = f"{title} from {BRAND}."
    description = clean_text(description)
    title = clean_text(title)

    # --- Prices ---
    # The page contains text like "1340.50 ILS-30%1915.00 ILS"
    # First group = sale price, second group (if present) = original
    price, sale_price = extract_prices(soup)
    if price is None:
        log.warning(f"  no price found, skipping: {url}")
        return None

    # --- Images ---
    images = discover_images(meta["sku"], meta["color"])
    if not images:
        log.warning(f"  no images discovered for {meta['sku']} / {meta['color']}")
        return None

    # --- Availability ---
    availability = detect_availability(soup)

    # --- Size ---
    size = detect_size(soup)

    # --- Category path (for product_type) ---
    product_type = " > ".join(p.title() for p in meta["category_path"])

    # --- Compose variant ID ---
    # Format matches production Meta catalog convention:
    # SKU_COLOR (underscore separator between SKU and color),
    # translated English color, uppercase, with internal spaces preserved.
    # e.g. LWBA00001_BLACK (single-word), WWPA01902_DARK BLUE (multi-word).
    if color_display:
        color_id = re.sub(r"[^A-Z0-9 ]+", "", color_display.upper()).strip()
        # Collapse repeated spaces to a single space
        color_id = re.sub(r"\s+", " ", color_id)
        variant_id = f"{meta['sku']}_{color_id}" if color_id else meta["sku"]
    else:
        variant_id = meta["sku"]

    return Product(
        id=variant_id,
        item_group_id=meta["sku"],
        title=title,
        description=description,
        link=url,
        price=price,
        sale_price=sale_price,
        image_link=images[0],
        additional_image_links=images[1:],
        color=color_display,
        size=size,
        gender=infer_gender(meta["sku"]),
        product_type=product_type,
        availability=availability,
        google_product_category=infer_google_category(product_type, title),
    )


def extract_prices(soup: BeautifulSoup) -> tuple:
    """Return (price, sale_price). sale_price is None if not discounted.

    Strategy:
    1. Prefer the schema.org Offer microdata (<meta itemprop="price" content="X">).
       This is the site's own authoritative current price and is immune to
       layout changes or promotional banner text.
    2. Look for a separate original/list price elsewhere on the page (strikethrough
       in HTML, or via the "X ILS - N% Y ILS" pattern). If found and higher than
       the schema price, treat the schema price as sale_price.
    3. Fall back to the regex approach if schema markup is missing.
    """
    # --- Step 1: Schema.org price (authoritative current price) ---
    schema_price = None
    price_meta = soup.find("meta", attrs={"itemprop": "price"})
    if price_meta and price_meta.get("content"):
        try:
            schema_price = float(price_meta["content"].replace(",", "").strip())
        except (ValueError, AttributeError):
            schema_price = None

    text = soup.get_text(" ", strip=True)

    # --- Step 2: Look for a higher original price (means schema price is a sale) ---
    if schema_price is not None:
        # Pattern A: explicit discount text like "1704 ILS -20% 2130 ILS"
        m = re.search(
            r"(\d[\d,]*\.\d{2})\s*ILS\s*-\s*(\d+)\s*%\s*(\d[\d,]*\.\d{2})\s*ILS",
            text,
        )
        if m:
            displayed_sale = float(m.group(1).replace(",", ""))
            displayed_original = float(m.group(3).replace(",", ""))
            # The schema price is the true current price; use the higher of the
            # two for the "original" comparison if it's higher than schema price.
            original = max(displayed_original, displayed_sale)
            if original > schema_price:
                return original, schema_price
            return schema_price, None

        # Pattern B: strikethrough HTML (<del>, <s>, or class containing "old"/"strike")
        for el in soup.find_all(["del", "s"]):
            m = re.search(r"(\d[\d,]*\.\d{2})", el.get_text())
            if m:
                original = float(m.group(1).replace(",", ""))
                if original > schema_price:
                    return original, schema_price

        # No original price visible — schema price is just the price
        return schema_price, None

    # --- Step 3: Fallback to pure regex if schema markup absent ---
    m = re.search(
        r"(\d[\d,]*\.\d{2})\s*ILS\s*-\s*(\d+)\s*%\s*(\d[\d,]*\.\d{2})\s*ILS",
        text,
    )
    if m:
        sale = float(m.group(1).replace(",", ""))
        original = float(m.group(3).replace(",", ""))
        return original, sale

    m = re.search(r"(\d[\d,]*\.\d{2})\s*ILS", text)
    if m:
        return float(m.group(1).replace(",", "")), None

    return None, None


def detect_availability(soup: BeautifulSoup) -> str:
    """Return 'in stock' / 'out of stock' / 'preorder'.

    Prefers schema.org Offer availability microdata (machine-readable, reliable):
        <link itemprop="availability" href="https://schema.org/InStock" />
    Falls back to text-based detection if microdata is missing.
    """
    # Stage 1: schema.org availability (authoritative)
    avail = soup.find("link", attrs={"itemprop": "availability"})
    if avail and avail.get("href"):
        href = avail["href"].lower()
        if "outofstock" in href:
            return "out of stock"
        if "preorder" in href or "preorderpending" in href:
            return "preorder"
        if "instock" in href:
            return "in stock"

    # Stage 2: text-based fallback
    text_lower = soup.get_text(" ", strip=True).lower()
    oos_signals = ["out of stock", "sold out", "אזל מהמלאי", "אזל"]
    if any(sig in text_lower for sig in oos_signals):
        return "out of stock"
    return "in stock"


SIZE_LABEL_RE = re.compile(r"select\s+your\s+size", re.IGNORECASE)


def detect_size(soup: BeautifulSoup) -> Optional[str]:
    """Try to grab the size, if a single size is implied (e.g. 'U' for bags)."""
    # Find the size selector area
    for el in soup.find_all(string=SIZE_LABEL_RE):
        # Look at the next siblings for size options
        parent = el.parent
        if parent:
            siblings_text = parent.find_next().get_text(" ", strip=True) if parent.find_next() else ""
            tokens = [t.strip() for t in siblings_text.split() if t.strip()]
            # If exactly one size token, return it (common for bags = "U")
            if len(tokens) == 1 and len(tokens[0]) <= 4:
                return tokens[0]
    return None


def discover_images(sku: str, color: str) -> list:
    """Probe the Fastmag CDN for available image numbers.

    Fastmag stores images at:
      https://cdnphotos.fastmag.fr/photos/27539/source/{sku}/{color}/{N}/photo.jpg

    We HEAD-check 1..MAX_IMAGES_PER_PRODUCT and keep the ones that 200.
    """
    sku_lc = sku.lower()
    color_lc = (color or "").lower().replace(" ", "+") or "_"
    found = []

    # Use a small session with no special headers for the CDN
    with requests.Session() as s:
        s.headers.update({"User-Agent": USER_AGENT})
        for n in range(1, MAX_IMAGES_PER_PRODUCT + 1):
            url = f"{CDN_BASE}/source/{sku_lc}/{color_lc}/{n}/photo.jpg"
            try:
                r = s.head(url, timeout=10, allow_redirects=True)
                if r.status_code == 200:
                    found.append(url)
                else:
                    # Stop probing as soon as we get a miss — image numbers are sequential
                    break
            except requests.RequestException:
                break

    # Fallback: try with color "_" (some products store without color)
    if not found and color_lc != "_":
        for n in range(1, MAX_IMAGES_PER_PRODUCT + 1):
            url = f"{CDN_BASE}/source/{sku_lc}/_/{n}/photo.jpg"
            try:
                r = requests.head(url, timeout=10, allow_redirects=True,
                                  headers={"User-Agent": USER_AGENT})
                if r.status_code == 200:
                    found.append(url)
                else:
                    break
            except requests.RequestException:
                break

    return found


# =============================================================================
# FETCHING (with retry + politeness)
# =============================================================================

def fetch(session: requests.Session, url: str, retries: int = 2) -> Optional[str]:
    for attempt in range(retries + 1):
        try:
            r = session.get(url, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                # Force UTF-8 — the site sends UTF-8 but doesn't always
                # declare it in headers, so requests guesses wrong (often
                # ISO-8859-1) and special chars like °, “”, etc. become �
                r.encoding = "utf-8"
                return r.text
            if r.status_code == 404:
                return None
            log.warning(f"  HTTP {r.status_code} for {url}")
        except requests.RequestException as e:
            log.warning(f"  request error for {url}: {e}")
        if attempt < retries:
            time.sleep(1.0 * (attempt + 1))
    return None


# =============================================================================
# FEED OUTPUT (RSS 2.0 with Google namespace — Meta-compatible)
# =============================================================================

def write_feed(products: list, path: str) -> None:
    # Safety dedup: keep only the first occurrence of each product.id.
    # The discovery stage already dedupes by SKU+color, but this guards
    # against any edge case where two URLs map to the same id.
    seen_ids = set()
    deduped = []
    for p in products:
        if p.id in seen_ids:
            continue
        seen_ids.add(p.id)
        deduped.append(p)
    dropped = len(products) - len(deduped)
    if dropped:
        log.warning(f"dropped {dropped} duplicate-id product(s) at write time")

    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:g="http://base.google.com/ns/1.0">',
        '<channel>',
        f'  <title>Zadig &amp; Voltaire Israel Product Feed</title>',
        f'  <link>{BASE_URL}</link>',
        f'  <description>Auto-generated catalog feed for Meta and Google Merchant Center</description>',
        f'  <lastBuildDate>{now}</lastBuildDate>',
    ]
    for p in deduped:
        lines.append(p.to_xml())
    lines.append('</channel>')
    lines.append('</rss>')

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    log.info(f"wrote {len(deduped)} products to {path}")


# =============================================================================
# MAIN
# =============================================================================

def main():
    ap = argparse.ArgumentParser(description="Generate Z&V Israel product feed")
    ap.add_argument("--limit", type=int, default=0,
                    help="Process only N products (for testing)")
    ap.add_argument("--urls", type=str, default=None,
                    help="Path to a file with product URLs (one per line); skips discovery")
    ap.add_argument("--output", type=str, default="feed.xml",
                    help="Output XML path (default: feed.xml)")
    ap.add_argument("--workers", type=int, default=MAX_WORKERS,
                    help=f"Concurrent workers (default: {MAX_WORKERS})")
    args = ap.parse_args()

    session = make_session()

    # Load optional GPC overrides
    gpc_overrides = load_gpc_overrides()

    # --- Discovery ---
    if args.urls:
        with open(args.urls) as f:
            urls = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
        log.info(f"loaded {len(urls)} URLs from {args.urls}")
        category_paths = set()  # no crawl happened, so no category pages to record
    else:
        urls, category_paths = discover_product_urls(session, SEED_CATEGORIES)

    if args.limit:
        urls = urls[: args.limit]
        log.info(f"limited to {len(urls)} URLs")

    # --- Extraction (parallel) ---
    products = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(extract_product, session, u): u for u in urls}
        for i, fut in enumerate(as_completed(futures), 1):
            url = futures[fut]
            try:
                p = fut.result()
                if p:
                    products.append(p)
                    if i % 25 == 0 or i == len(urls):
                        log.info(f"  processed {i}/{len(urls)}")
            except Exception as e:
                log.error(f"  failed: {url} — {e}")

    log.info(f"extracted {len(products)} products successfully")

    # --- Apply GPC overrides (if any) ---
    if gpc_overrides:
        overridden = 0
        for p in products:
            if p.id in gpc_overrides:
                p.google_product_category = gpc_overrides[p.id]
                overridden += 1
        if overridden:
            log.info(f"applied {overridden} GPC override(s)")

    # --- Write feed ---
    write_feed(products, args.output)
    print(f"\n✓ Feed ready: {args.output}")
    print(f"  Products: {len(products)}")
    print(f"  Upload this file (or host its URL) to Meta Commerce Manager")
    print(f"  and/or Google Merchant Center as a scheduled feed.")

    # --- Write sitemap (reuses the same crawl — no extra site load) ---
    # full_unquote handles the same triple-encoded-ampersand issue here as it
    # does for product URLs (e.g. "hats+%2526+caps" -> "hats+&+caps")
    category_urls = [urljoin(BASE_URL, full_unquote(p)) for p in category_paths]
    product_urls = [p.link for p in products]
    sitemap_path = args.output.replace("feed.xml", "sitemap.xml")
    if sitemap_path == args.output:
        # --output wasn't the default feed.xml name; fall back to a sibling path
        sitemap_path = args.output.rsplit(".", 1)[0] + "-sitemap.xml"
    n = write_sitemap(product_urls, category_urls, output_path=sitemap_path)
    print(f"\n✓ Sitemap ready: {sitemap_path}")
    print(f"  URLs: {n} total ({len(product_urls)} products, {len(category_urls)} categories, "
          f"plus static pages)")
    print(f"  Submit this file's hosted URL in Google Search Console.")


if __name__ == "__main__":
    sys.exit(main())
