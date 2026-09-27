"""Write the Google Merchant Center supplemental feed (TSV, joined on `id`).

Columns:
    item_group_title   - product name shared by all color variants   (conversational)
    variant_option     - variant-identifying property, "color:black" (conversational)
    product_highlight  - 2-10 feature bullets from the description
    product_detail     - section:name:value facts (fit, dimensions, composition, care)
    material           - main fibres, e.g. "Wool/Cashmere"
    related_product    - "Complete the look" items, e.g. "accessory:id:LWBA04329_BLACK"

Kept separate from feed.xml on purpose: Meta doesn't use most of these, and a
supplemental source can be detached in GMC without touching the primary feed.

Specs: https://support.google.com/merchants/answer/17085370 (conversational)
       https://support.google.com/merchants/answer/9218260 (product_detail)
"""

import logging
import re
from collections import Counter, defaultdict

from description_parser import parse_description, format_details, format_highlights

log = logging.getLogger(__name__)

COLUMNS = ["id", "item_group_title", "variant_option",
           "product_highlight", "product_detail", "material", "related_product"]

# Relationship type per related item, decided by the RELATED item's category.
# Bags/jewellery/shoes etc. styled with a garment -> accessory.
# Garment-to-garment ("Complete the look" trousers for a shirt) has no exact
# enum; often_bought_with is the closest. Set to None to emit accessories only.
ACCESSORY_TYPE_RE = re.compile(
    r"^(bags|accessories)\b|bag|clutch|wallet|jewel|shoe|belt|scarf|scarves|hat|cap|watch|strap",
    re.IGNORECASE)
APPAREL_RELATIONSHIP = "often_bought_with"
MAX_RELATED = 30
# Spec: identifier may only contain letters, digits, underscores and dashes.
VALID_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _strip_trailing(title: str, phrase: str) -> str:
    """Remove `phrase` (and an optional ' - ' before it) from the end of title."""
    if not phrase:
        return title
    pattern = rf"\s*(?:-\s*)?\b{re.escape(phrase.strip())}\s*$"
    return re.sub(pattern, "", title, flags=re.IGNORECASE).strip()


def derive_group_title(title: str, color: str, french_colors=()) -> str:
    """Strip color words from the end of a variant title.

    Site H1s often carry the French color and the scraper appends the English
    one, e.g. "ROCK BAG NOIR GOLD - BLACK GOLD" -> "ROCK BAG".
    """
    result = _strip_trailing(title, color or "")
    # Longest French names first so "NOIR GOLD" wins over "NOIR".
    for fr in sorted(french_colors, key=len, reverse=True):
        stripped = _strip_trailing(result, fr)
        if stripped and stripped != result:
            result = stripped
            break
    return result or title


def _common_word_prefix(titles) -> str:
    split = [t.split() for t in titles]
    prefix = []
    for words in zip(*split):
        if len({w.upper() for w in words}) != 1:
            break
        prefix.append(words[0])
    return " ".join(prefix)


def build_group_titles(products, french_colors=()) -> dict:
    """Return {item_group_id: item_group_title}, consistent across variants."""
    by_group = defaultdict(list)
    for p in products:
        by_group[p.item_group_id].append(derive_group_title(p.title, p.color, french_colors))

    titles = {}
    for gid, candidates in by_group.items():
        unique = set(candidates)
        if len(unique) == 1:
            titles[gid] = candidates[0]
            continue
        prefix = _common_word_prefix(unique)
        # Guard against collapsing to a single generic word like "ZV".
        if len(prefix.split()) >= 2:
            titles[gid] = prefix
        else:
            titles[gid] = Counter(candidates).most_common(1)[0][0]
        log.debug(f"group title for {gid}: {sorted(unique)} -> {titles[gid]!r}")
    return titles


def related_products(p, by_id: dict) -> list:
    """Build related_product values for p, keeping only ids that exist in the
    feed and are valid GMC identifiers."""
    out = []
    for rid in getattr(p, "related_ids", None) or []:
        target = by_id.get(rid)
        if target is None or rid == p.id or not VALID_IDENTIFIER_RE.match(rid):
            continue
        if ACCESSORY_TYPE_RE.search(target.product_type or ""):
            rel = "accessory"
        elif APPAREL_RELATIONSHIP:
            rel = APPAREL_RELATIONSHIP
        else:
            continue
        out.append(f"{rel}:id:{rid}")
    return out[:MAX_RELATED]


def _cell(s: str) -> str:
    return (s or "").replace("\t", " ").replace("\r", " ").replace("\n", " ")


def write_supplemental_tsv(products, path: str, french_colors=()) -> dict:
    """Write the supplemental TSV. Returns fill-rate stats per column.

    Written by hand rather than with csv.writer: GMC's own quoting rules for
    repeated/group attributes (see description_parser._q) must reach the file
    untouched, and TSV cells never contain tabs or newlines after _cell().
    """
    group_titles = build_group_titles(products, french_colors)
    by_id = {p.id: p for p in products}
    stats = Counter()
    dropped = Counter()
    seen = set()
    lines = ["\t".join(COLUMNS)]
    for p in products:
        if p.id in seen:
            continue
        seen.add(p.id)
        parsed = parse_description(p.description, getattr(p, "product_type", "") or "")
        row = [
            p.id,
            group_titles.get(p.item_group_id, ""),
            f"color:{p.color.lower()}" if p.color else "",
            format_highlights(parsed.highlights),
            format_details(parsed.details),
            parsed.material,
            ",".join(related_products(p, by_id)),
        ]
        for rid in getattr(p, "related_ids", None) or []:
            if rid not in by_id:
                dropped["not_in_feed"] += 1
            elif not VALID_IDENTIFIER_RE.match(rid):
                dropped["space_in_id"] += 1
        for col, val in zip(COLUMNS, row):
            stats[col] += bool(val)
        lines.append("\t".join(_cell(v) for v in row))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    stats["rows"] = len(lines) - 1
    stats["related_dropped_not_in_feed"] = dropped["not_in_feed"]
    stats["related_dropped_space_in_id"] = dropped["space_in_id"]
    log.info(f"wrote {stats['rows']} supplemental rows to {path}")
    return dict(stats)
