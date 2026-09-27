"""Parse Z&V Israel product descriptions into structured GMC attributes.

Fastmag descriptions follow a loose template, flattened to one line by the scraper:

    <intro sentence>. <features> Model is 177 cm and is wearing a size S
    This item has a loose fit. <advice>. [W x H x D : 27 x 13 x 2 cm]
    Composition 90% Wool* 10% Cashmere** *Recycled wool fibers
    Care - Hand-wash ... - Do not wring - Air dry flat

Features appear either as " - " bullets or as run-together sentence-case
phrases ("V-neckline Lace insert Short sleeves"). Everything here is
extraction only: if a section isn't found, nothing is emitted for it.
"""

import re
from dataclasses import dataclass, field

MAX_LEN = 150  # per highlight / per product detail
BRAND_PREFIX_RE = re.compile(r"^(?:zadig\s*&\s*voltaire|z&v)\s+", re.I)

# Words that start with a capital but don't start a new feature phrase.
NO_SPLIT_WORDS = {
    "Rock", "Roll", "Zadig", "Voltaire", "Zadig&Voltaire", "ZV", "Z&V", "Era",
    "New", "Nano", "Kate", "Madison", "Sunny", "Wings", "Swing", "Angel",
    "Le", "Zouzou", "Tome", "La", "RWS", "iPhone", "Pro",
}

MODEL_RE = re.compile(
    r"Model is (\d{3})\s?cm(?:\s*/\s*\d+'\s*\d*\"*'*)?(?:\s+(?:tall\s+)?and is wearing (?:a )?size (\w+))?",
    re.I)
FIT_RE = re.compile(
    r"(?:This item (?:has an? [^.]*?fit|fits [^.]*?)\.(?:\s+[^.]*\bsize\b[^.]*\.)?"
    r"|We recommend [^.]*\bsize\b[^.]*\."
    r"|Women should choose [^.]*\bsize\b\.(?:\s*Men should choose [^.]*\bsize\b\.)?)", re.I)
MADE_IN_RE = re.compile(r"\bMade in ([A-Z][a-z]+)\b")
PCT_OF_RE = re.compile(r"^\d+% of the\b|certified|Leather Working Group", re.I)
DIMS_RE = re.compile(
    r"(?:(Width x Height(?: x Depth)?)\s*:?\s*)?"
    r"(\d+(?:[.,]\d+)?\s*x\s*\d+(?:[.,]\d+)?(?:\s*x\s*\d+(?:[.,]\d+)?)?)\s*cm\.?", re.I)
STRAP_RE = re.compile(
    r"((?:Shoulder |Chain )?strap(?:/chain)? (?:height|length)|Strap|Chains?(?: length)?)"
    r"\s*:\s*(\d[\d.,\s\-–andto]*?cm(?:\s+and\s+\d+(?:[.,]\d+)?\s*cm)?)\.?", re.I)
DIM_KV_RE = re.compile(
    r"\b(Length|Width|Height|Depth|Diameter)\s*:?\s*(\d+(?:[.,]\d+)?\s*(?:cm|mm))\.?", re.I)
DIMS_LABELLED_RE = re.compile(
    r"\b((?:Width|Height|Depth|Length)(?:\s*x\s*(?:Width|Height|Depth|Length))+)\s*:\s*"
    r"(\d+(?:[.,]\d+)?\s*(?:cm)?(?:\s*x\s*\d+(?:[.,]\d+)?\s*(?:cm)?)+)\.?", re.I)
JEWEL_RECYCLED_RE = re.compile(
    r"The jewell?ery line from Zadig\s*&\s*Voltaire is made from ([^.]+)\.", re.I)
COMP_START_RE = re.compile(r"\bComposition(\s*&\s*care)?\b", re.I)
CARE_START_RE = re.compile(r"\bCare\b\s*(?:-\s*)?")
FIBER_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*%\s*(?!of\b|the\b)"
    r"([A-Z]{2,}(?: [A-Z]{2,})?(?![a-z])|[A-Za-z][a-z]+(?: [a-z]+)?)"
    r"(\*{0,3})")
COMP_LABEL_RE = re.compile(
    r"\b(Main material|Secondary material|Material|Lining|Trims?|Filling|Padding|Sole|Upper)\s*:",
    re.I)
CARE_VERBS = (
    "Hand-wash", "Machine-wash", "Wash", "Do", "Air", "Iron", "Tumble", "Dry",
    "Avoid", "Protect", "Store", "Clean", "It", "This", "Keep", "Remove", "Use",
    "Apply", "Steam", "Bleach")


FIBER_FIXES = {
    "elasthanne": "elastane", "polyurthane": "polyurethane", "reaphia": "raffia",
    "rapha": "raffia", "inox": "stainless steel", "wool merinos": "merino wool",
    "merinos wool": "merino wool", "polyamid": "polyamide",
}
SKIP_TYPES = ("fragrance", "perfume", "beauty")
STOP_PREV = {"a", "an", "the", "of", "with", "in", "and", "by", "on", "for", "to", "from", "or"}
PROSE_RE = re.compile(r"\b(has|have|had|was|were|is|are)\b", re.I)


def _fiber(name: str) -> str:
    n = name.lower().strip()
    return FIBER_FIXES.get(n, n)


@dataclass
class Parsed:
    highlights: list = field(default_factory=list)
    details: list = field(default_factory=list)  # (section, name, value)
    material: str = ""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _tidy(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip(" -–;,")
    return s


def _sentence_case(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def _split_phrases(text: str) -> list:
    """Split "V-neckline Lace insert Short sleeves" into phrases at sentence-case
    boundaries, keeping proper nouns and acronyms attached."""
    words = text.split()
    phrases, cur = [], []
    for w in words:
        bare = w.strip(",.;()")
        digit_start = (
            bare[:1].isdigit() and cur
            and re.match(r"^[A-Za-z]{3,}$", cur[-1].strip(",.;()"))
            and cur[-1].lower() not in {"of", "to", "up", "with", "and", "for", "from", "size"}
        )
        prev = cur[-1].strip(",.;()") if cur else ""
        acronym_start = (
            cur and len(bare) > 1 and (bare.isupper() or bare == "Z&V")
            and prev[:1].islower() and prev.lower() not in STOP_PREV
            and not prev.endswith("ed")
        )
        starts_new = digit_start or acronym_start or (
            cur
            and bare[:1].isupper()
            and bare not in NO_SPLIT_WORDS
            and not (len(bare) > 1 and bare.isupper())        # acronyms: ZV, XL
            and not cur[-1].endswith(("&", "x", "/"))
            and prev.lower() not in STOP_PREV
        )
        if starts_new:
            phrases.append(" ".join(cur))
            cur = []
        cur.append(w)
    if cur:
        phrases.append(" ".join(cur))
    return [_tidy(p) for p in phrases if _tidy(p)]


def _split_items(text: str) -> list:
    """Split a feature/care block into items: ' - ' bullets if present,
    otherwise sentence-case phrases."""
    text = _tidy(text)
    if not text:
        return []
    if re.search(r"(?:^|\s)-\s*[A-Z0-9]", " " + text):
        parts = re.split(r"(?:^|\s)-\s*(?=\S)", text)
        parts = [re.split(r"\s(?=(?:This|These|The|Its|It)\s)", p)[0] for p in parts]
        return [_tidy(p) for p in parts if _tidy(p)]
    return _split_phrases(text)


def _care_name(item: str) -> str:
    low = item.lower()
    if re.search(r"dry-clean|dry clean|professional|stain|\bclean\b|cream|nourish", low):
        return "Cleaning"
    if re.search(r"wash|wring|bleach|soak", low):
        return "Washing"
    if re.search(r"tumble|air dry|dry flat|to dry", low):
        return "Drying"
    if re.search(r"\biron|steam", low):
        return "Ironing"
    return "Handling"


def _fits(section: str, name: str, value: str) -> bool:
    return 0 < len(f"{section}:{name}:{value}") <= MAX_LEN


# ---------------------------------------------------------------------------
# sections
# ---------------------------------------------------------------------------

def _parse_composition(comp: str, out: Parsed) -> None:
    # Footnotes: "*Recycled wool fibers", "**Viscose fibers from ..."
    label_alt = r"(?:Main material|Secondary material|Material|Lining|Trims?|Filling|Padding|Sole|Upper)\s*:"
    note_re = re.compile(r"(?<![\w%])\*+\s*([A-Z][^*]*?)(?=\s*\*|\s*" + label_alt + r"|$)")
    footnotes = [_tidy(m.group(1)) for m in note_re.finditer(comp)]
    body = note_re.sub(" ", comp)

    # Unasterisked footnote form: "100% Cotton Cotton fibers from organic farming"
    m = re.search(r"\b([A-Z][a-z]+ fibers? from [^*]+?)$", body)
    if m:
        footnotes.append(_tidy(m.group(1)))
        body = body[: m.start()]
    m = re.search(r"\b(Contains [^*]+?)$", body)
    if m:
        footnotes.append(_tidy(m.group(1)))
        body = body[: m.start()]

    # Labelled parts (Main material: / Lining: ...) or a single unlabelled block
    parts = []
    labels = list(COMP_LABEL_RE.finditer(body))
    if labels:
        for i, lab in enumerate(labels):
            end = labels[i + 1].start() if i + 1 < len(labels) else len(body)
            parts.append((lab.group(1).capitalize(), body[lab.end():end]))
    else:
        parts.append(("Main material", body))

    main_fibers = []
    for label, text in parts:
        fibers = FIBER_RE.findall(text)
        if not fibers:
            continue
        label = "Main material" if label == "Material" else label
        value = ", ".join(f"{float(pct):g}% {_fiber(name)}" for pct, name, _ in fibers)
        if _fits("Composition", label, value):
            out.details.append(("Composition", label, value))
        if label == "Main material":
            main_fibers = [_fiber(name) for _, name, _ in fibers]

    # g:material — distinct main fibers in listed (percentage) order, max 3
    seen = []
    for f in main_fibers:
        if f not in seen:
            seen.append(f)
    out.material = "/".join(_sentence_case(f) for f in seen[:3])

    for note in footnotes:
        low = note.lower()
        if "recycled" in low:
            name = "Recycled content"
        elif "organic" in low:
            name = "Organic content"
        elif "certified" in low or "rws" in low or "sustainabl" in low:
            name = "Responsible sourcing"
        else:
            continue
        note = note.rstrip(".")
        if _fits("Sustainability", name, note):
            out.details.append(("Sustainability", name, _sentence_case(note)))


def _parse_care(care: str, out: Parsed) -> None:
    care = _tidy(care)
    if re.search(r"(?:^|\s)-\s", " " + care):
        items = _split_items(care)
    else:
        # Prose care: sentences first, then sentence-case phrases, re-joining
        # fragments that don't open with an instruction verb.
        items = []
        for sent in re.split(r"(?<=\.)\s+", care):
            for ph in _split_phrases(sent):
                if items and not ph.startswith(CARE_VERBS) and not items[-1].endswith("."):
                    items[-1] = f"{items[-1]} {ph}"
                else:
                    items.append(ph)
    merged = [i for i in items if len(i) >= 8]

    grouped = {}
    for it in merged:
        it = _sentence_case(it.rstrip("."))
        grouped.setdefault(_care_name(it), []).append(it)

    for name, vals in grouped.items():
        joined = "; ".join(vals)
        if _fits("Care", name, joined):
            out.details.append(("Care", name, joined))
        else:
            for v in vals:
                if _fits("Care", name, v):
                    out.details.append(("Care", name, v))


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def parse_description(desc: str, product_type: str = "") -> Parsed:
    out = Parsed()
    if not desc:
        return out
    text = re.sub(r"\s+", " ", desc).strip()

    # Split off composition/care tail
    comp_m = COMP_START_RE.search(text)
    head, comp, care = text, "", ""
    if comp_m:
        head, tail = text[: comp_m.start()], text[comp_m.end():]
        care_m = CARE_START_RE.search(tail)
        if care_m:
            comp, care = tail[: care_m.start()], tail[care_m.end():]
        elif comp_m.group(1):  # "Composition & care 100% COW LEATHER Do not expose..."
            fibers = list(FIBER_RE.finditer(tail))
            cut = fibers[-1].end() if fibers else 0
            comp, care = tail[:cut], tail[cut:]
        else:
            comp = tail
    else:
        care_m = CARE_START_RE.search(text)
        if care_m:
            head, care = text[: care_m.start()], text[care_m.end():]

    # --- Fit ---
    m = MODEL_RE.search(head)
    if m:
        out.details.append(("Fit", "Model height", f"{m.group(1)} cm"))
        if m.group(2):
            out.details.append(("Fit", "Model wears size", m.group(2).upper()))
        head = head[: m.start()] + " " + head[m.end():]
    m = FIT_RE.search(head)
    if m:
        advice = _tidy(m.group(0)).rstrip(".")
        if _fits("Fit", "Sizing advice", advice):
            out.details.append(("Fit", "Sizing advice", advice))
        head = head[: m.start()] + " " + head[m.end():]

    # --- Dimensions ---
    for m in list(STRAP_RE.finditer(head)):
        name = "Chain length" if re.match(r"chains?( length)?$", m.group(1), re.I) else "Strap height"
        out.details.append(("Dimensions", name, _tidy(m.group(2)).rstrip(".")))
    head = STRAP_RE.sub(" ", head)
    m = DIMS_LABELLED_RE.search(head)
    if m:
        value = re.sub(r"\s*cm", "", m.group(2))
        value = re.sub(r"\s*x\s*", " x ", value).replace(",", ".") + " cm"
        out.details.append(("Dimensions", m.group(1).capitalize(), value))
        head = head[: m.start()] + " " + head[m.end():]
    for m in list(DIM_KV_RE.finditer(head)):
        out.details.append(("Dimensions", m.group(1).capitalize(), m.group(2).replace(",", ".")))
    head = DIM_KV_RE.sub(" ", head)
    m = None if any(d[0] == "Dimensions" and " x " in d[2] for d in out.details) else DIMS_RE.search(head)
    if m:
        label = (m.group(1) or "Measurements").capitalize()
        value = re.sub(r"\s*x\s*", " x ", m.group(2)).replace(",", ".") + " cm"
        out.details.append(("Dimensions", label, value))
        head = head[: m.start()] + " " + head[m.end():]

    # --- Country of manufacture ---
    m = MADE_IN_RE.search(head)
    if m:
        out.details.append(("General", "Made in", m.group(1)))
        head = head[: m.start()] + " " + head[m.end():]

    # --- Sustainability sentence on jewellery ---
    m = JEWEL_RECYCLED_RE.search(head)
    if m:
        val = _sentence_case(_tidy(m.group(1)))
        if _fits("Sustainability", "Recycled content", val):
            out.details.append(("Sustainability", "Recycled content", val))
        head = head[: m.start()] + " " + head[m.end():]

    # --- Highlights: features after the intro sentence ---
    bullet = re.search(r"\s-\s+(?=\S)", head)
    if bullet:
        features = head[bullet.start():]
    else:
        intro_end = head.find(". ")
        features = head[intro_end + 2:] if intro_end != -1 else ""
    for item in _split_items(features):
        item = BRAND_PREFIX_RE.sub("", item)
        item = _sentence_case(item.rstrip(". "))
        item = item.split(". ")[0]
        item = re.split(r"\s(?=Do not |Dimensions\s*:|Handle height|Customisations by|Ajustable|Adjustable strap height)", item)[0].strip()
        if re.search(r"\b(he|his|she|her)\b", item):
            continue
        if PCT_OF_RE.search(item):
            if _fits("Sustainability", "Responsible sourcing", item):
                out.details.append(("Sustainability", "Responsible sourcing", item))
            continue
        if item.lower().startswith(("size guide", "this ", "the ", "to prolong", "in the event")) \
                or item.startswith(("Do not", "Avoid", "Protect", "Store")):
            continue
        if '"' in item or item.endswith(":") or (PROSE_RE.search(item) and len(item.split()) > 8):
            continue
        if 3 <= len(item) <= MAX_LEN and item not in out.highlights:
            out.highlights.append(item)
    if any(t in (product_type or "").lower() for t in SKIP_TYPES):
        out.highlights = []  # fragrance copy is prose, not features
    if len(out.highlights) < 2:  # spec: at least 2 if submitted
        out.highlights = []
    out.highlights = out.highlights[:10]

    if comp and not any(t in (product_type or "").lower() for t in SKIP_TYPES):
        _parse_composition(comp, out)
    if care:
        _parse_care(care, out)
    return out


# ---------------------------------------------------------------------------
# TSV cell formatting (GMC text-feed quoting rules)
# ---------------------------------------------------------------------------

def _q(s: str) -> str:
    """Quote a (sub-)value if it contains a comma, colon or quote."""
    s = s.replace("\t", " ").replace("\n", " ")
    if any(c in s for c in ',:"'):
        return '"' + s.replace('"', '""') + '"'
    return s


def format_highlights(items: list) -> str:
    return ",".join(_q(i) for i in items)


def format_details(details: list) -> str:
    return ",".join(f"{_q(s)}:{_q(n)}:{_q(v)}" for s, n, v in details)
