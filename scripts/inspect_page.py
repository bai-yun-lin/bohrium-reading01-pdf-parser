"""Produce a side-by-side audit sheet from one real preview and its evidence boxes."""
import argparse
import json
from pathlib import Path
from PIL import Image,ImageDraw


def compare(output,doc_id,number,destination):
    doc=json.loads((output/"documents"/(doc_id+".json")).read_text("utf8"))
    page=next(p for p in doc["pages"] if p["page"]==number)
    if "preview" not in page:
        raise ValueError("Render this page with --previews N before generating the sheet")
    original=Image.open(output/page["preview"]).convert("RGB");w,h=original.size
    sheet=Image.new("RGB",(2*w+14,h+32),"white");sheet.paste(original,(0,32));sheet.paste(original,(w+14,32))
    draw=ImageDraw.Draw(sheet);draw.text((10,9),"Original PDF page "+str(number),fill="#182f44")
    draw.text((w+24,9),"Reading order / region IDs / table cells",fill="#182f44")
    def box(b):
        return (w+14+b[0]*w/page["width"],32+b[1]*h/page["height"],w+14+b[2]*w/page["width"],32+b[3]*h/page["height"])
    byid={e["id"]:e for e in page["elements"]}
    for i,key in enumerate(page.get("reading_order",[]),1):
        e=byid[key];b=box(e["bbox"]);color="#00769d"
        if e["kind"]=="table":
            color="#ac4b08"
            for c in e["cells"]:draw.rectangle(box(c["bbox"]),outline=color,width=1)
        elif e["kind"]=="image":color="#713e87"
        draw.rectangle(b,outline=color,width=1)
        if i<=2 or e["kind"] in ("table","caption","heading_candidate") or i%10==0:
            draw.text((max(w+14,b[0]-18),b[1]),str(i),fill=color)
    destination.parent.mkdir(parents=True,exist_ok=True)
    sheet.save(destination)


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("output",type=Path);p.add_argument("document");p.add_argument("page",type=int);p.add_argument("destination",type=Path)
    a=p.parse_args();compare(a.output,a.document,a.page,a.destination)
