"""
image_mirror.py — self-host product images on GitHub Pages.

WHY THIS EXISTS
    cdnphotos.fastmag.fr serves a robots.txt of "User-agent: * / Disallow: /",
    so Google (Googlebot-Image, Storebot-Google) may not fetch any product image.
    Merchant Center then can't run its quality & policy checks and disapproves
    the product. Our scraper is not a search crawler, so it can download the
    images and re-publish them from roi-car.github.io, which Google can reach.

HOW IT WORKS (called from zv_feed.main() after extraction, before write_feed)
    1. For each product, take the first HOSTED_IMAGES_PER_PRODUCT CDN images.
    2. Download only the ones not already in docs/img/ (incremental: a normal
       day downloads just the new products/colors).
    3. Re-compress to a sensible JPEG size (keeps full resolution up to
       MAX_DIMENSION) so the Pages site stays well under GitHub's 1 GB limit.
    4. Rewrite image_link / additional_image_link to the Pages URLs.
       Any image that fails to download keeps its CDN URL (no product is lost).
    5. Delete images no longer referenced by any product (guarded, see
       MIN_PRODUCTS_FOR_PRUNE) so the published site doesn't grow forever.

TO SWITCH OFF (e.g. once Fastmag opens the CDN robots.txt)
    Set SELF_HOST_IMAGES = False below, or run zv_feed.py with --no-mirror-images.
    The feed goes back to CDN URLs on the next run. docs/img/ can then be deleted.
"""

import io
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

try:
    from PIL import Image
except ImportError:  # Pillow missing: we still mirror, just without re-compression
    Image = None

log = logging.getLogger("zv-feed")

# =============================================================================
# CONFIGURATION
# =============================================================================

SELF_HOST_IMAGES = True

# Where images are written (inside the GitHub Pages folder) and served from.
IMG_DIR = "docs/img"
PAGES_IMG_BASE = "https://roi-car.github.io/zv-feed/img"

# Main image + 2 additional. 3 x ~740 products x ~150 KB ~= 330 MB.
HOSTED_IMAGES_PER_PRODUCT = 3

# Keep CDN URLs for images beyond the hosted ones (#4..#8)?
# True  -> Meta still gets all images (Meta can read the CDN); Google simply
#          can't fetch the extras, same as today. No disapprovals come from this.
# False -> feed only contains the hosted images.
KEEP_CDN_EXTRAS = True

# Re-compression settings. Source images are ~1294x1726; GMC apparel minimum
# is 250x250 and 800x800+ is recommended, so we keep full size.
MAX_DIMENSION = 1600
JPEG_QUALITY = 85
# Per-image size budget. If a JPEG comes out bigger, step quality down, then
# dimensions, until it fits. Real product shots on plain backgrounds normally
# land well under this at the first step.
TARGET_BYTES = 250 * 1024
QUALITY_STEPS = (85, 78, 70)
DIMENSION_STEPS = (1600, 1300, 1100)  # never below GMC's 800px recommendation

# Pruning safety: never delete images when the crawl looks incomplete.
MIN_PRODUCTS_FOR_PRUNE = 400
MAX_PRUNE_FRACTION = 0.3  # never delete more than 30% of hosted images in one run

DOWNLOAD_WORKERS = 4
DOWNLOAD_TIMEOUT = 30
SIZE_WARNING_MB = 800  # GitHub Pages hard limit is 1 GB

CDN_PATTERN = re.compile(
    r"/photos/\d+/source/(?P<sku>[^/]+)/(?P<color>[^/]+)/(?P<n>\d+)/photo\.jpg$",
    re.IGNORECASE,
)


# =============================================================================
# HELPERS
# =============================================================================

def local_name(cdn_url: str):
    """Stable filename for a CDN image URL, or None if the URL isn't recognised.

    .../source/wmsh01362/dark+blue/2/photo.jpg -> wmsh01362_dark-blue_2.jpg
    """
    if cdn_url.startswith(PAGES_IMG_BASE + "/"):
        return cdn_url.rsplit("/", 1)[1]
    m = CDN_PATTERN.search(cdn_url)
    if not m:
        return None
    sku = re.sub(r"[^a-z0-9]+", "-", m["sku"].lower()).strip("-")
    color = re.sub(r"[^a-z0-9]+", "-", m["color"].lower()).strip("-") or "nocolor"
    return f"{sku}_{color}_{m['n']}.jpg"


def recompress(raw: bytes) -> bytes:
    """Return an optimised JPEG within TARGET_BYTES where possible.
    Raises if the bytes aren't a real image."""
    if Image is None:
        if not raw.startswith(b"\xff\xd8"):
            raise ValueError("not a JPEG")
        return raw
    src = Image.open(io.BytesIO(raw))
    src.load()
    if src.mode not in ("RGB", "L"):
        src = src.convert("RGB")
    best = None
    for dim in DIMENSION_STEPS:
        img = src.copy()
        if max(img.size) > dim:
            img.thumbnail((dim, dim), Image.LANCZOS)
        for q in QUALITY_STEPS:
            out = io.BytesIO()
            img.save(out, "JPEG", quality=q, optimize=True, progressive=True)
            data = out.getvalue()
            if best is None or len(data) < len(best):
                best = data
            if len(data) <= TARGET_BYTES:
                return data
    return best  # smallest we could make; logged via the folder-size check


def download(session: requests.Session, cdn_url: str, dest: str) -> bool:
    try:
        r = session.get(cdn_url, timeout=DOWNLOAD_TIMEOUT)
        if r.status_code != 200 or not r.content:
            log.warning(f"  image {r.status_code}: {cdn_url}")
            return False
        data = recompress(r.content)
    except Exception as e:
        log.warning(f"  image failed: {cdn_url} — {e}")
        return False
    tmp = dest + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dest)  # atomic: never leaves a half-written file behind
    return True


def dir_size_mb(path: str) -> float:
    total = 0
    for name in os.listdir(path):
        fp = os.path.join(path, name)
        if os.path.isfile(fp):
            total += os.path.getsize(fp)
    return total / (1024 * 1024)


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def mirror_images(products, user_agent: str, refresh: bool = False,
                  allow_prune: bool = True) -> None:
    """Download product images to IMG_DIR and rewrite the products' image URLs."""
    os.makedirs(IMG_DIR, exist_ok=True)

    # 1. Work out which files each product needs.
    plan = []          # (product, [(cdn_url, filename or None), ...])
    wanted = {}        # filename -> cdn_url
    for p in products:
        cdn_images = [p.image_link] + list(p.additional_image_links)
        entries = []
        for url in cdn_images[:HOSTED_IMAGES_PER_PRODUCT]:
            name = local_name(url)
            entries.append((url, name))
            if name:
                wanted[name] = url
        plan.append((p, entries, cdn_images[HOSTED_IMAGES_PER_PRODUCT:]))

    # 2. Download what's missing.
    to_fetch = {n: u for n, u in wanted.items()
                if refresh or not os.path.exists(os.path.join(IMG_DIR, n))}
    log.info(f"image mirror: {len(wanted)} images needed, "
             f"{len(wanted) - len(to_fetch)} already hosted, {len(to_fetch)} to download")

    failed = set()
    if to_fetch:
        with requests.Session() as s:
            s.headers.update({"User-Agent": user_agent})
            with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as ex:
                futs = {ex.submit(download, s, u, os.path.join(IMG_DIR, n)): n
                        for n, u in to_fetch.items()}
                for i, fut in enumerate(as_completed(futs), 1):
                    if not fut.result():
                        failed.add(futs[fut])
                    if i % 200 == 0:
                        log.info(f"  downloaded {i}/{len(to_fetch)}")
    if failed:
        log.warning(f"image mirror: {len(failed)} downloads failed — "
                    f"those images keep their CDN URL")

    # 3. Rewrite URLs on each product.
    hosted_main = 0
    for p, entries, extras in plan:
        new_urls = []
        for url, name in entries:
            if name and name not in failed and os.path.exists(os.path.join(IMG_DIR, name)):
                new_urls.append(f"{PAGES_IMG_BASE}/{name}")
            else:
                new_urls.append(url)  # fallback: original CDN URL
        if KEEP_CDN_EXTRAS:
            new_urls += extras
        if new_urls:
            p.image_link = new_urls[0]
            p.additional_image_links = new_urls[1:]
            if new_urls[0].startswith(PAGES_IMG_BASE):
                hosted_main += 1
    log.info(f"image mirror: {hosted_main}/{len(products)} products have a self-hosted main image")

    # 4. Prune images no product references any more.
    if allow_prune and len(products) >= MIN_PRODUCTS_FOR_PRUNE:
        hosted = [n for n in os.listdir(IMG_DIR) if n.endswith(".jpg")]
        unused = [n for n in hosted if n not in wanted]
        if hosted and len(unused) > MAX_PRUNE_FRACTION * len(hosted):
            log.warning(f"image mirror: {len(unused)}/{len(hosted)} images look unused — "
                        f"too many for one run, skipping prune (check the crawl)")
        else:
            for name in unused:
                os.remove(os.path.join(IMG_DIR, name))
            if unused:
                log.info(f"image mirror: pruned {len(unused)} unused images")
    elif allow_prune:
        log.warning(f"image mirror: only {len(products)} products — "
                    f"skipping prune (safety threshold {MIN_PRODUCTS_FOR_PRUNE})")

    size = dir_size_mb(IMG_DIR)
    msg = f"image mirror: docs/img is {size:.0f} MB"
    if size > SIZE_WARNING_MB:
        log.warning(msg + f" — approaching GitHub Pages 1 GB limit, lower "
                          f"HOSTED_IMAGES_PER_PRODUCT or JPEG_QUALITY")
    else:
        log.info(msg)
