"""Unit tests for gmc_supplemental + description_parser — no network needed."""
import os, sys, tempfile
from types import SimpleNamespace as P
sys.path.insert(0, ".")
from gmc_supplemental import derive_group_title, build_group_titles, write_supplemental_tsv
from description_parser import parse_description, format_details, format_highlights

FR = ["NOIR", "NOIR GOLD", "ENCRE", "GRIS CHINE", "GRIS CHINE CLAI", "ECRU", "ROAD"]

def check(actual, expected, label):
    ok = actual == expected
    print(f"  {'✓' if ok else '✗'} {label}: {actual!r}")
    if not ok:
        print(f"      expected {expected!r}"); sys.exit(1)

def split_gmc(cell, sep):
    """Split on sep outside GMC double-quoted segments ("" = literal quote)."""
    out, cur, q, i = [], "", False, 0
    while i < len(cell):
        c = cell[i]
        if c == '"':
            if q and i + 1 < len(cell) and cell[i + 1] == '"':
                cur += '"'; i += 2; continue
            q = not q
        elif c == sep and not q:
            out.append(cur); cur = ""
        else:
            cur += c
        i += 1
    out.append(cur)
    return out

print("group titles")
check(derive_group_title("ROCK BAG NOIR GOLD - BLACK GOLD", "Black Gold", FR), "ROCK BAG", "FR + EN suffix")
check(derive_group_title("REDOXAL DRESS 100% SILK CHERRY", "Cherry", FR), "REDOXAL DRESS 100% SILK", "keeps material")
gt = build_group_titles([
    P(item_group_id="G", title="WATCH WINGS GOLD GOLD", color="Gold"),
    P(item_group_id="G", title="WATCH WINGS GOLD ENCRE - DARK BLUE", color="Dark Blue")], FR)
check(gt["G"], "WATCH WINGS GOLD", "color word inside name kept")

print("description parsing — bulleted knitwear")
d = ("Striped crew-neck jumper with Rock n Roll intarsia. - Wool and cashmere jumper - Stripes and Rock n Roll "
     "jacquard pattern - Crew neck - Long sleeves - ZV logo Model is 177 cm and is wearing a size S This item has a "
     "loose fit. For a more fitted look, choose one size down from your usual size. Composition 90% WOOL* 10% CASHMERE "
     "*Recycled wool fibers Care - Hand-wash or machine-wash on a 20°C wool cycle with a maximum spin speed of 400 rpm "
     "- Do not wring - Air dry flat - Iron inside out at a maximum of 110°C")
p = parse_description(d)
check(p.highlights[:2], ["Wool and cashmere jumper", "Stripes and Rock n Roll jacquard pattern"], "highlights")
check(p.material, "Wool/Cashmere", "material")
dd = {(s, n): v for s, n, v in p.details}
check(dd[("Fit", "Model height")], "177 cm", "model height")
check(dd[("Composition", "Main material")], "90% wool, 10% cashmere", "composition")
check(dd[("Sustainability", "Recycled content")], "Recycled wool fibers", "sustainability")
check(dd[("Care", "Drying")], "Air dry flat", "care drying")

print("description parsing — flat bag copy")
d = ("Western clutch in distressed suede. Rock clutch in distressed suede Two removable intertwined leather and metal "
     "chains Flap with zip and magnetic closure Signature wings 27 x 13.5 x 2 cm. Shoulder strap/chain height: 33 - 57 cm "
     "Composition Material: 100% Calf leather Lining: 100% Cotton* *Recycled cotton fibers")
p = parse_description(d)
check(p.highlights, ["Rock clutch in distressed suede", "Two removable intertwined leather and metal chains",
                     "Flap with zip and magnetic closure", "Signature wings"], "phrase split keeps 'Rock'")
dd = {(s, n): v for s, n, v in p.details}
check(dd[("Dimensions", "Measurements")], "27 x 13.5 x 2 cm", "dimensions")
check(dd[("Dimensions", "Strap height")], "33 - 57 cm", "strap")
check(dd[("Composition", "Lining")], "100% cotton", "lining")

print("fragrance is skipped")
check(parse_description("Bold. A blend of orange, almond. Composition 80% Alcool 20% Parfum", "Accessories > Fragrance").highlights, [], "no highlights")

print("GMC quoting")
check(format_details([("Fit", "Sizing advice", "Loose fit, size down")]), 'Fit:Sizing advice:"Loose fit, size down"', "comma quoted")
check(format_highlights(["A", 'Say "hi"']), 'A,"Say ""hi"""', "quote doubled")

print("TSV round-trip")
prods = [P(id="X_BLACK", item_group_id="X", title="JUMPER NOIR - BLACK", color="Black", description=
           "Jumper. - Wool jumper - Crew neck Composition 100% WOOL Care - Do not wring - Air dry flat", product_type="Men")]
with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "s.tsv")
    stats = write_supplemental_tsv(prods, path, FR)
    header, row = open(path, encoding="utf-8").read().rstrip("\n").split("\n")
    cols = dict(zip(header.split("\t"), row.split("\t")))
    check(cols["variant_option"], "color:black", "variant_option")
    check(split_gmc(cols["product_highlight"], ","), ["Wool jumper", "Crew neck"], "highlights parse back")
    for det in split_gmc(cols["product_detail"], ","):
        check(len(split_gmc(det, ":")), 3, f"3 sub-attributes in {det[:30]}")

print("complete the look (scraper)")
from bs4 import BeautifulSoup
from zv_feed import extract_complete_the_look
from gmc_supplemental import related_products
B = "https://www.zadigetvoltaire.co.il/en/product"
html = f"""<div><a href="{B}/ready+to+wear/shirts+%252526+tops/wwsh02324,black,tyrica-shirt.html">BLACK</a>
<p>Item : WWSH02324</p>
<h3>Complete the look</h3>
<div class="item"><a href="{B}/bags/shoulder+bags/lwba04329,black,jack-mini-vintage-patent-bag.html"><img></a><p>jack mini</p></div>
<div class="item"><a href="{B}/accessories/jewelry/owjw01729,shiny+silver,rock-star-ring.html"><img></a></div>
<div class="item"><a href="{B}/ready+to+wear/pants+%252526+jeans/wwpa01937,black,pin-trousers.html"><img></a></div>
<p>Complimentary ground shipping over 500 ILS</p>
<a href="https://www.zadigetvoltaire.co.il/en/page/boutiques.html">Find a store</a>
<a href="{B}/bags/clutches/lwba00001,noir,rock-bag.html">footer promo</a></div>"""
ids = extract_complete_the_look(BeautifulSoup(html, "html.parser"), self_id="WWSH02324_BLACK")
check(ids, ["LWBA04329_BLACK", "OWJW01729_SHINY SILVER", "WWPA01937_BLACK"], "ids, stops at footer")
check(extract_complete_the_look(BeautifulSoup("<h3>You may also like</h3>", "html.parser")), [], "no block -> []")

by_id = {
    "LWBA04329_BLACK": P(id="LWBA04329_BLACK", product_type="Bags > Shoulder Bags"),
    "OWJW01729_SHINY SILVER": P(id="OWJW01729_SHINY SILVER", product_type="Accessories > Jewelry"),
    "WWPA01937_BLACK": P(id="WWPA01937_BLACK", product_type="Ready To Wear > Pants & Jeans"),
}
shirt = P(id="WWSH02324_BLACK", related_ids=ids + ["GONE_BLACK"])
check(related_products(shirt, by_id), ["accessory:id:LWBA04329_BLACK", "often_bought_with:id:WWPA01937_BLACK"],
      "typed, space-id and missing-id dropped")
print("All tests passed.")
