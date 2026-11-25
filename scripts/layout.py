"""Order PDF line boxes within vertical bands and identify recurring margins."""
import re
from collections import defaultdict
from statistics import median


def mid(box):
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def boxof(boxes):
    return [round(f(b[i] for b in boxes), 2) for i, f in enumerate((min, min, max, max))]


def divide(items, depth=0):
    """Find a supported gutter, then let spanning elements subdivide its height."""
    top_down = sorted(items, key=lambda e: (e["bbox"][1], e["bbox"][0], e["id"]))
    if len(items) < 4 or depth > 5:
        return top_down
    lines = [e["bbox"] for e in items if e["kind"] not in ("image", "table")]
    if len(lines) < 4:
        return top_down
    xmin, ymin, xmax, ymax = boxof(lines)
    width = xmax - xmin
    if width < 60:
        return top_down
    best = None
    for k in range(17, 85, 2):
        x = xmin + width*k/100
        pad = max(3, width*0.012)
        left = [b for b in lines if b[2] <= x-pad]
        right = [b for b in lines if b[0] >= x+pad]
        cross = len(lines)-len(left)-len(right)
        if min(len(left),len(right)) < 2 or cross > len(lines)*0.37:
            continue
        overlap = min(max(b[3] for b in left), max(b[3] for b in right))-max(min(b[1] for b in left), min(b[1] for b in right))
        if overlap <= 0:
            continue
        gap = min(b[0] for b in right)-max(b[2] for b in left)
        centre = (min(b[0] for b in right)+max(b[2] for b in left))/2
        score = (min(len(left),len(right))-cross*0.8, overlap, gap, -abs(x-centre))
        if best is None or score > best[0]:
            best = score, x
    if best is None:
        return top_down
    x = best[1]
    separators = [e for e in top_down if e["bbox"][0] < x < e["bbox"][2]]
    remaining = [e for e in top_down if e not in separators]
    out = []
    for divider in separators + [None]:
        limit = mid(divider["bbox"])[1] if divider else float("inf")
        band = [e for e in remaining if mid(e["bbox"])[1] < limit]
        remaining = [e for e in remaining if mid(e["bbox"])[1] >= limit]
        for col in ([e for e in band if mid(e["bbox"])[0]<x], [e for e in band if mid(e["bbox"])[0]>=x]):
            out.extend(divide(col, depth+1))
        if divider:
            out.append(divider)
    return out


def recurring(pages):
    positions = defaultdict(set)
    def identity(p, e):
        b, h = e["bbox"], p["height"]
        zone = "header" if b[3] < h*.10 else "footer" if b[1] > h*.90 else None
        literal = re.sub(r"\s+", " ", e["text"].strip().casefold())
        if not zone or len(literal)>120 or not literal:
            return None
        # Normalize standalone page numbers only. Repeated observations near a margin
        # are still retained outside the body stream, with their original coordinates.
        literal = re.sub(r"^(?:page\s+)?\d+(?:\s*(?:/|of)\s*\d+)?$", "<page>", literal)
        return zone, round(mid(b)[1]/h*50), literal
    for p in pages:
        for e in p["elements"]:
            key = identity(p,e)
            if key:
                positions[key].add(p["page"])
    for p in pages:
        for e in p["elements"]:
            key = identity(p,e)
            if key and len(positions[key])>=max(2, (len(pages)+1)//2):
                e["kind"] = key[0]


def classify(pages):
    recurring(pages)
    sizes = [e["size"] for p in pages for e in p["elements"] if e.get("size") and len(e["text"])>12]
    body = median(sizes) if sizes else 10
    outline = []
    for p in pages:
        for e in p["elements"]:
            if e["kind"] != "line":
                continue
            s=e["text"].strip()
            cap=re.match(r"^(fig(?:ure)?\.?|table|图|表)\s*([A-Z]?\d+[A-Za-z]?)\b[.：:\s]?", s,re.I)
            numbered = re.match(r"^(\d+(?:\.\d+){0,4})\.?\s+\D",s)
            if cap and not re.match(r"^(?:fig(?:ure)?\.?|table)\s*[A-Z]?\d+\s+(?:shows?|illustrates?|lists?|reports?|compares?|summari[sz]es?)\b",s,re.I):
                e.update(kind="caption",label=cap.group(0).strip())
            elif re.search(r"[=∑∫≈≤≥]",s) and (re.search(r"[()（）]\s*\d+\s*[)）]\s*$",s) or len(s)<85):
                e["kind"]="equation_candidate"
            elif len(s)<100 and (numbered or re.fullmatch(r"abstract|references|bibliography|acknowledg(?:e)?ments|摘要|参考文献|引言|结论",s,re.I) or e.get("size",0)>body*1.26):
                level=numbered[1].count(".")+1 if numbered else 1
                e.update(kind="heading_candidate",level=level)
            elif e.get("size") and e["size"]<body*.85 and e["bbox"][1]>p["height"]*.76:
                e["kind"]="footnote_candidate"
        margins=[e for e in p["elements"] if e["kind"] in ("header","footer")]
        body_elements=divide([e for e in p["elements"] if e["kind"] not in ("header","footer") and not e.get("table_id")])
        p["reading_order"]=[e["id"] for e in body_elements]
        p["margin_ids"]=[e["id"] for e in margins]
        outline.extend({"id":e["id"],"text":e["text"],"level":e["level"]} for e in body_elements if "level" in e)
    return outline


def logical_view(pages):
    """Group consecutive evidence lines without modifying their literal strings."""
    groups=[]; objects=[]; section=None; reference_mode=False
    for p in pages:
        by_id={e["id"]:e for e in p["elements"]}
        current=None; last=None
        for key in p.get("reading_order",[]):
            e=by_id[key];kind=e["kind"];b=e["bbox"]
            if kind=="heading_candidate":
                section=key
                reference_mode=bool(re.fullmatch(r"(?:\d+\.?\s*)?(?:references|bibliography|参考文献)",e["text"].strip(),re.I))
            if kind=="caption":
                tab=bool(re.match(r"table|表",e["text"],re.I)); target="table" if tab else "image"
                scored=[]
                for obj in by_id.values():
                    if obj["kind"]!=target: continue
                    r=obj["bbox"]; overlap=min(b[2],r[2])-max(b[0],r[0])
                    gap=max(0,b[1]-r[3],r[1]-b[3])
                    if overlap>0 and gap<p["height"]*.12:scored.append((gap,obj["id"]))
                scored.sort()
                objects.append({"caption_id":key,"label":e.get("label"),"page":p["page"],"nearby_object_ids":[v for _,v in scored[:3]],"status":"candidate" if scored else "inspect_render"})
            family="reference_text" if reference_mode and kind=="line" else kind
            continues=False
            if last and current and family in ("line","reference_text") and current["kind"]==family:
                a=last["bbox"];h=max(4,b[3]-b[1])
                continues=(-h*.35<=b[1]-a[3]<=h*.76 and abs(b[0]-a[0])<=max(12,p["width"]*.035)
                           and not re.match(r"^\[\d+\]",e["text"]))
            if not continues:
                current={"kind":family,"page":p["page"],"section_id":section,"element_ids":[],"bbox":b[:],"text":""}
                groups.append(current)
            current["element_ids"].append(key);current["bbox"]=boxof([current["bbox"],b])
            current["text"]+=("\n" if current["text"] else "")+e["text"]
            last=e
    continuations=[]
    for earlier,later in zip(pages,pages[1:]):
        for a in earlier["elements"]:
            if a["kind"]!="table" or a["bbox"][3]<.62*earlier["height"]:continue
            for b in later["elements"]:
                if b["kind"]!="table" or b["bbox"][1]>.36*later["height"]:continue
                header=lambda t:[re.sub(r"\s+","",str(x or "")).casefold() for x in t["rows"][0]]
                if header(a)==header(b):
                    continuations.append({"previous":a["id"],"next":b["id"],"basis":"adjacent pages, edge positions and matching header","status":"review_caption_and_units"})
    return {"paragraphs":groups,"caption_links":objects,"table_continuation_candidates":continuations}
