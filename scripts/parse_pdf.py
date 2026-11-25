"""Extract page-addressable evidence from actual PDFs and save case checkpoints."""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from statistics import median
from layout import boxof, classify, logical_view, mid
from deliver import editable

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        fitz=None


def save(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile("w",encoding="utf-8",dir=path.parent,delete=False) as f:
        json.dump(data,f,ensure_ascii=False,indent=1)
    os.replace(f.name,path)


def command(args, timeout=45):
    p=subprocess.run(list(map(str,args)),capture_output=True,timeout=timeout)
    if p.returncode:
        raise ValueError(p.stderr.decode("utf-8","replace").strip()[-350:] or str(args[0])+" failed")
    return p.stdout


def coords(b):
    return [round(float(n),2) for n in b]


def add(p,kind,b,text="",**meta):
    eid=f'{p["doc_id"]}-p{p["page"]}-e{len(p["elements"])+1}'
    e=dict(id=eid,kind=kind,bbox=coords(b),text=text,**meta)
    p["elements"].append(e)
    return e


def refine_grid(page,table):
    """Use a simple header and recurrent aligned word rows to split sparse ruling."""
    words=page.get_text("words",clip=fitz.Rect(table["bbox"]))
    lines=[]
    for w in sorted(words,key=lambda v:((v[1]+v[3])/2,v[0])):
        y=(w[1]+w[3])/2
        if not lines or abs(y-lines[-1][0])>max(2.2,.34*(w[3]-w[1])):
            lines.append((y,[]))
        lines[-1][1].append(w)
    if len(lines)<4:
        return
    header=[]
    for w in sorted(lines[0][1],key=lambda w:w[0]):
        if not header or w[0]-header[-1][-1][2]>max(6,.75*(w[3]-w[1])):
            header.append([])
        header[-1].append(w)
    if len(header)<=len(table["rows"][0]) or len(header)>18:
        return
    ruling={c["bbox"][2] for c in table["cells"]}
    cuts=[]
    for a,b in zip(header,header[1:]):
        left,right=a[-1][2],b[0][0]
        candidates=[x for x in ruling if left<x<right]
        cuts.append(min(candidates,key=lambda x:abs(x-(left+right)/2)) if candidates else (left+right)/2)
    rows=[]; cells=[]
    for _,row in lines:
        slots=[[] for _ in header]
        for w in sorted(row,key=lambda w:w[0]):
            slots[sum((w[0]+w[2])/2>x for x in cuts)].append(w)
        values=[" ".join(w[4] for w in ws) for ws in slots]
        rows.append(values)
        for c,ws in enumerate(slots):
            if ws:
                cells.append(dict(row=len(rows),column=c+1,text=values[c],bbox=boxof([w[:4] for w in ws]),rowspan=1,colspan=1))
    if sum(sum(bool(v) for v in row)>=len(header)-1 for row in rows[1:]) < .75*(len(rows)-1):
        return
    table.update(vector_grid={"rows":table["rows"],"cells":table["cells"]},rows=rows,cells=cells,method="vector-region+aligned-word-rows")


def ruled_tables(page,p):
    if not hasattr(page,"find_tables"):
        p["warnings"].append("Table detector unavailable; inspect aligned words and page preview.")
        return
    try:
        tables=page.find_tables().tables
        for tbl in tables:
            rows=tbl.extract()
            boxes=[coords(cell) for cell in tbl.cells if cell]
            xs=sorted({x for b in boxes for x in (b[0],b[2])})
            ys=sorted({y for b in boxes for y in (b[1],b[3])})
            cells=[]
            for r,row in enumerate(tbl.rows):
                for c,b in enumerate(row.cells):
                    if b is None:
                        continue
                    b=coords(b)
                    detector_text=rows[r][c]
                    clipped=page.get_text("text",clip=fitz.Rect(b),sort=True).strip()
                    if clipped:
                        rows[r][c]=clipped
                    cells.append(dict(row=r+1,column=c+1,bbox=b,text=rows[r][c],grid_transcription=detector_text,
                                      rowspan=max(1,sum(b[1]+1<y<=b[3]+.1 for y in ys)),
                                      colspan=max(1,sum(b[0]+1<x<=b[2]+.1 for x in xs))))
            if not cells:
                continue
            e=add(p,"table",tbl.bbox,cells=cells,rows=rows,method="vector-grid")
            refine_grid(page,e)
            for l in p["elements"]:
                if l["kind"]=="line" and any(c["bbox"][0]-1<=mid(l["bbox"])[0]<=c["bbox"][2]+1 and c["bbox"][1]-1<=mid(l["bbox"])[1]<=c["bbox"][3]+1 for c in cells):
                    l["table_id"]=e["id"]
    except (ValueError,RuntimeError,IndexError,AttributeError,TypeError) as ex:
        p["warnings"].append("Table grid needs visual review: "+str(ex)[:140])


def native(page,p):
    p.update(width=page.rect.width,height=page.rect.height)
    for block in page.get_text("dict",flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)["blocks"]:
        for line in block.get("lines",[]):
            runs=[]
            for s in line["spans"]:
                runs.append(dict(text=s["text"],bbox=coords(s["bbox"]),font=s["font"],size=round(s["size"],2),
                                 baseline=round(s["origin"][1],2),superscript_flag=bool(s["flags"]&1)))
            text="".join(r["text"] for r in runs)
            if text.strip():
                add(p,"line",line["bbox"],text,runs=runs,size=max(r["size"] for r in runs),direction=line["dir"],method="native")
    for obj in page.get_image_info():
        if obj["bbox"][2]>obj["bbox"][0] and obj["bbox"][3]>obj["bbox"][1]:
            add(p,"image",obj["bbox"],pixels=[obj["width"],obj["height"]],method="image-placement")
    ruled_tables(page,p)


def poppler(pdf,p):
    # bbox-layout supplies word boxes directly in displayed page coordinates.
    n=p["page"]
    xml=ET.fromstring(command(["pdftotext","-f",n,"-l",n,"-bbox-layout","-enc","UTF-8",pdf,"-"]))
    elem=next(xml.iterfind(".//{*}page"))
    p.update(width=float(elem.get("width")),height=float(elem.get("height")))
    for line in elem.iterfind(".//{*}line"):
        words=[dict(text=w.text or "",bbox=coords([w.get(k) for k in ("xMin","yMin","xMax","yMax")])) for w in line.iterfind("{*}word")]
        if words:
            add(p,"line",boxof([w["bbox"] for w in words])," ".join(w["text"] for w in words),words=words,method="poppler-bbox")
    p["warnings"].append("Poppler fallback: inspect grids, images and script positions against the rendered page.")


def text_tables(p,words):
    """Conservatively propose borderless grids immediately below a table caption."""
    for cap in list(p["elements"]):
        if not re.match(r"^\s*(?:table|表)\s*[A-Z]?\d+",cap["text"],re.I):
            continue
        bottom=cap["bbox"][3]
        if any(t["kind"]=="table" and -3<=t["bbox"][1]-bottom<55 for t in p["elements"]):
            continue
        candidates=[w for w in words if bottom-.5<=w["bbox"][1]<bottom+min(200,p["height"]*.28)]
        visual=[]
        for w in sorted(candidates,key=lambda a:(mid(a["bbox"])[1],a["bbox"][0])):
            y=mid(w["bbox"])[1]
            if not visual or abs(y-visual[-1][0])>3:
                visual.append((y,[]))
            visual[-1][1].append(w)
        rows=[]; starts=[]; prev_y=bottom
        for y,group in visual:
            if y-prev_y>max(28,p["height"]*.04): break
            cells=[]
            for w in sorted(group,key=lambda a:a["bbox"][0]):
                if not cells or w["bbox"][0]-cells[-1][-1]["bbox"][2]>max(10,p["width"]*.023):
                    cells.append([])
                cells[-1].append(w)
            anchors=[c[0]["bbox"][0] for c in cells]
            if len(cells)<2 or len(cells)>18: break
            if starts and (len(starts)!=len(anchors) or max(abs(a-b) for a,b in zip(starts,anchors))>p["width"]*.035): break
            starts=starts or anchors
            rows.append([{"row":len(rows)+1,"column":j+1,"text":" ".join(w["text"] for w in cell),"bbox":boxof([w["bbox"] for w in cell]),"rowspan":1,"colspan":1} for j,cell in enumerate(cells)])
            prev_y=y
        if len(rows)>=3:
            flat=[c for row in rows for c in row]
            e=add(p,"table",boxof([c["bbox"] for c in flat]),cells=flat,rows=[[c["text"] for c in row] for row in rows],method="aligned-words-candidate",caption_id=cap["id"])
            for line in p["elements"]:
                x,y=mid(line["bbox"]);b=e["bbox"]
                if line["kind"]=="line" and b[0]-.5<=x<=b[2]+.5 and b[1]<=y<=b[3]:
                    line["table_id"]=e["id"]
            p["warnings"].append("Visually verify borderless grid "+e["id"]+"; merged cells are unresolved.")


def raster(pdf,page,n,path,dpi):
    if page is not None:
        page.get_pixmap(matrix=fitz.Matrix(dpi/72,dpi/72),alpha=False).save(str(path))
    else:
        command(["pdftoppm","-f",n,"-l",n,"-singlefile","-scale-to",round(11*dpi),"-png",pdf,path.with_suffix("")])


def ocr(pdf,page,p,work,lang):
    img=work/(p["doc_id"]+"-"+str(p["page"])+".png")
    raster(pdf,page,p["page"],img,240)
    import struct
    with img.open("rb") as f:
        f.seek(16)
        iw,ih=struct.unpack(">II",f.read(8))
    out=command(["tesseract",img,"stdout","-l",lang,"--psm","3","tsv"],75).decode("utf-8","replace")
    groups={}
    for w in csv.DictReader(io.StringIO(out),delimiter="\t",quoting=csv.QUOTE_NONE):
        if w["level"]!="5" or not (w["text"] or "").strip():
            continue
        x,y,width,height=[int(w[k]) for k in ("left","top","width","height")]
        b=[x*p["width"]/iw,y*p["height"]/ih,(x+width)*p["width"]/iw,(y+height)*p["height"]/ih]
        groups.setdefault((w["block_num"],w["par_num"],w["line_num"]),[]).append(dict(text=w["text"],bbox=coords(b),confidence=float(w["conf"])))
    existing=[e for e in p["elements"] if e["kind"]=="line"]
    for words in groups.values():
        text=" ".join(w["text"] for w in words)
        b=boxof([w["bbox"] for w in words])
        if any(re.sub(r"\s","",text)==re.sub(r"\s","",e["text"]) and abs(mid(b)[1]-mid(e["bbox"])[1])<9 for e in existing):
            continue
        add(p,"line",b,text,words=words,method="ocr",confidence=round(median(w["confidence"] for w in words),1))
    if not groups:
        p["warnings"].append("OCR found no words.")
    p["warnings"].append("OCR transcription; verify symbols, numbers and low-confidence words visually.")


def inspect_pdf(pdf,root,did,out,work,args):
    doc=None
    record={"doc_id":did,"source":pdf.relative_to(root).as_posix(),"sha256":hashlib.sha256(pdf.read_bytes()).hexdigest(),"pages":[],"warnings":[]}
    try:
        if fitz:
            doc=fitz.open(str(pdf))
            if doc.needs_pass:
                raise ValueError("Encrypted PDF requires an authorized unlocked copy.")
            total=len(doc)
            record["metadata"]=doc.metadata
            record["bookmarks"]=doc.get_toc(simple=True)
        else:
            info=command(["pdfinfo",pdf]).decode("utf-8","replace")
            total=int(re.search(r"(?m)^Pages:\s*(\d+)",info)[1])
        record["page_count"]=total
        for n in range(1,total+1):
            page=None
            p=dict(doc_id=did,page=n,width=0,height=0,elements=[],warnings=[])
            record["pages"].append(p)
            try:
                if doc is not None:
                    page=doc[n-1];p["original_rotation"]=page.rotation
                    p["printed_label"]=page.get_label()
                    page.set_rotation(0)
                    native(page,p)
                else:
                    poppler(pdf,p)
                words=([dict(text=w[4],bbox=coords(w[:4])) for w in page.get_text("words")] if page is not None else
                       [w for line in p["elements"] for w in line.get("words",[])])
                text_tables(p,words)
                lines=[e for e in p["elements"] if e["kind"]=="line"]
                text="".join(e["text"] for e in lines)
                large_scan=any(e["kind"]=="image" and (e["bbox"][2]-e["bbox"][0])*(e["bbox"][3]-e["bbox"][1])>.6*p["width"]*p["height"] for e in p["elements"])
                suspected=not text.strip() or (large_scan and len(text)<100) or text.count("\ufffd")>max(1,len(text)*.04)
                if suspected:
                    if shutil.which("tesseract"):
                        try:
                            ocr(pdf,page,p,work,args.lang)
                        except (ValueError,OSError,subprocess.TimeoutExpired) as ex:
                            p["warnings"].append("OCR failed: "+str(ex)[:220])
                    elif page is not None and hasattr(page,"get_textpage_ocr"):
                        try:
                            tp=page.get_textpage_ocr(language=args.lang,dpi=240,full=True)
                            seen={(e["text"].strip(),round(mid(e["bbox"])[1]/5)) for e in lines}
                            for block in page.get_text("dict",textpage=tp)["blocks"]:
                                for line in block.get("lines",[]):
                                    text="".join(s["text"] for s in line["spans"]).strip()
                                    if text and (text,round(mid(line["bbox"])[1]/5)) not in seen:
                                        add(p,"line",line["bbox"],text,method="mupdf-ocr")
                            p["warnings"].append("OCR transcription; compare numbers and glyphs to the preview.")
                        except Exception as ex:
                            p["warnings"].append("OCR unavailable or failed: "+str(ex)[:180])
                    else:
                        p["warnings"].append("Raster/sparse text: OCR unavailable; transcribe from source image.")
                if n<=args.previews or suspected and n<=20:
                    path=out/"previews"/f"{did}-p{n}.png"
                    path.parent.mkdir(parents=True,exist_ok=True)
                    raster(pdf,page,n,path,112)
                    p["preview"]=path.relative_to(out).as_posix()
            except Exception as ex:
                p["warnings"].append(f"Page extraction incomplete: {type(ex).__name__}: {str(ex)[:230]}")
            if n%8==0:
                save(work/(did+"-checkpoint.json"),record)
        record["outline"]=classify(record["pages"])
        record.update(logical_view(record["pages"]))
    except Exception as ex:
        record["warnings"].append(f"{type(ex).__name__}: {str(ex)[:250]}")
    finally:
        if doc is not None:
            doc.close()
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",type=Path,default=Path("/input"))
    parser.add_argument("--output",type=Path,default=Path("/app/output"))
    parser.add_argument("--work",type=Path,default=Path("/app/work/pdf-parse"))
    parser.add_argument("--lang",default="eng")
    parser.add_argument("--previews",type=int,default=3)
    parser.add_argument("--pdf",action="append",help="relative PDF named by the case; repeat for supplements")
    args=parser.parse_args()
    root=args.input.resolve();out=args.output;work=args.work
    case=json.loads((root/"case.json").read_text(encoding="utf-8-sig"))
    work.mkdir(parents=True,exist_ok=True)
    contract=case.get("output_contract",{})
    result=dict(case_id=case["case_id"],output_contract=contract,status="partial",documents=[],sources=[],uncertainties=[],changes=[])
    if isinstance(contract,dict):
        fields=contract.get("required",contract.get("required_result_fields",list(contract)))
        if isinstance(fields,list):
            for field in fields:
                if isinstance(field,str):
                    result.setdefault(field,None)
    if not (root/"task.md").is_file():
        result["uncertainties"].append("task.md absent; verify the question from the case.")
    save(out/"result.json",result)
    paths=[root/v for v in args.pdf] if args.pdf else sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower()==".pdf")
    for i,pdf in enumerate(paths,1):
        if not pdf.resolve().is_relative_to(root) or not pdf.is_file():
            result["uncertainties"].append(f"Unavailable input PDF #{i}");continue
        did=f"d{i}"
        rec=inspect_pdf(pdf,root,did,out,work,args)
        artifact=f"documents/{did}.json";save(out/artifact,rec)
        manuscript=f"documents/{did}.md"
        (out/manuscript).write_text(editable(rec),encoding="utf-8")
        result["documents"].append({"source":rec["source"],"artifact":artifact,"editable":manuscript,"pages":len(rec["pages"]),"sha256":rec["sha256"]})
        result["sources"].append({"doc_id":did,"path":rec["source"],"sha256":rec["sha256"]})
        result["uncertainties"].extend({"doc_id":did,"reason":w} for w in rec["warnings"])
        for p in rec["pages"]:
            result["uncertainties"].extend({"doc_id":did,"page":p["page"],"reason":w} for w in p["warnings"])
        save(out/"result.json",result)
    if not paths:
        result["uncertainties"].append("No PDF found in selected case materials.")
    result["stage"]="extraction complete; apply task-specific contract and inspect evidence"
    save(out/"result.json",result)
    print(json.dumps({"case_id":result["case_id"],"document_count":len(result["documents"]),"status":result["status"]},ensure_ascii=True))


if __name__=="__main__":
    main()
