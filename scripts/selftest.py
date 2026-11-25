"""Build a real synthetic research PDF and compare extraction to its drawing plan."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
import pymupdf as pdf


def make(folder):
    folder.mkdir(parents=True,exist_ok=True)
    (folder/"case.json").write_text(json.dumps({"case_id":"synthetic-layout","output_contract":{"required":["ordered_excerpt","measured_values"]}}),encoding="utf8")
    (folder/"task.md").write_text("Read the two-column passage, report both periods with units, and identify the appendix scan.",encoding="utf8")
    d=pdf.open()
    font=next((Path(v) for v in ("C:/Windows/Fonts/arial.ttf","/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf","/System/Library/Fonts/Supplemental/Arial.ttf") if Path(v).exists()),None)
    p=d.new_page(width=520,height=660)
    p.insert_text((42,68),"Timing a pendulum in two independent trials",fontsize=20)
    for i in range(4):
        for x,key in [(44,"L"),(293,"R")]:
            p.insert_text((x,120+i*19),f"{key}{i+1}  Recorded passage line.",fontsize=10)
    p.insert_text((42,235),"2. Timing results across the two independently measured series",fontsize=14)
    for i in range(2):
        p.insert_text((44,266+i*18),f"A{i+1}  Local drift measured.",fontsize=10)
        p.insert_text((293,266+i*18),f"B{i+1}  Recorder checked.",fontsize=10)
    p.insert_text((42,324),"Table 1. Period by trial",fontsize=12)
    x=[44,163,273,390]; y=[337,361,385,409,433]
    for yy in y:p.draw_line((x[0],yy),(x[-1],yy))
    for j,xx in enumerate(x):p.draw_line((xx,y[1] if j==2 else y[0]),(xx,y[-1]))
    for row,values in enumerate([["Trial","Observed timing",""],["Name","Period (s)","Repeats"],["A","1.82","12"],["B","1.96","15"]]):
        for col,value in enumerate(values):
            if value:p.insert_text((x[col]+5,y[row]+17),value,fontsize=10)
    if font:
        p.insert_font(fontname="full",fontfile=str(font))
        p.insert_text((50,484),"T = 2π √(ℓ/g)     (1)",fontname="full",fontsize=13)
    p.insert_text((42,550),"1  Synthetic instrument note.",fontsize=8)
    second=d.new_page(width=520,height=660)
    second.insert_text((42,80),"Appendix A. Calibration record",fontsize=15)
    second.insert_text((42,108),"Table 2. Unruled observations",fontsize=12)
    for i,row in enumerate([["Station","Count","Time"],["North","12","1.82"],["South","15","1.96"]]):
        for x,val in zip([42,170,285],row):second.insert_text((x,130+i*17),val,fontsize=10)
    for i in range(2):
        p=d[i];p.insert_text((42,23),"Instrument bulletin",fontsize=9);p.insert_text((249,645),str(i+1),fontsize=9)
    # Embed only rendered pixels on the last page. It contains no digital text layer.
    image_doc=pdf.open(); image_page=image_doc.new_page(width=520,height=260)
    image_page.insert_text((40,70),"Scan appendix: clock calibration",fontsize=20)
    image_page.insert_text((40,110),"Standard reading 12.0 seconds",fontsize=16)
    png=image_page.get_pixmap(matrix=pdf.Matrix(2,2)).tobytes("png")
    third=d.new_page(width=520,height=260);third.insert_image(third.rect,stream=png)
    d.save(folder/"trial.pdf");d.close(); image_doc.close()
    return bool(font)


def main():
    ap=argparse.ArgumentParser();ap.add_argument("folder",type=Path);args=ap.parse_args()
    inp=args.folder/"input";out=args.folder/"output";work=args.folder/"work"
    has_font=make(inp)
    parser=Path(__file__).with_name("parse_pdf.py")
    subprocess.run([sys.executable,"-B",str(parser),"--input",str(inp),"--output",str(out),"--work",str(work)],check=True)
    doc=json.loads((out/"documents/d1.json").read_text(encoding="utf8"));p,q,scan=doc["pages"]
    elements={e["id"]:e for e in p["elements"]}
    expected=[f"{side}{i+1}" for side,n in (("L",4),("R",4),("A",2),("B",2)) for i in range(n)]
    def sequence(lines):
        return [line.split()[0] for line in lines if line.split() and line.split()[0] in expected]
    got=sequence([elements[k]["text"] for k in p["reading_order"]])
    assert got==expected,(got,expected)
    with pdf.open(inp/"trial.pdf") as original:
        base=sequence(original[0].get_text("text",sort=False).splitlines())
    def inversions(seq):
        return sum(expected.index(a)>expected.index(b) for i,a in enumerate(seq) for b in seq[i+1:])
    assert sorted(base)==sorted(got) and inversions(base)>inversions(got)
    assert any(elements[k]["text"]=="Instrument bulletin" for k in p["margin_ids"])
    table=next(e for e in p["elements"] if e["kind"]=="table")
    assert table["rows"][2][1]=="1.82" and table["rows"][3][1]=="1.96",table
    assert any(c["colspan"]==2 and c["text"]=="Observed timing" for c in table["cells"])
    grid=next(e for e in q["elements"] if e["kind"]=="table");assert grid["rows"][-1]==["South","15","1.96"]
    if has_font:assert any("π" in e["text"] and "√" in e["text"] and "(1)" in e["text"] for e in p["elements"])
    assert any(e["kind"]=="image" for e in scan["elements"]) and scan["warnings"]
    assert (out/scan["preview"]).is_file() and (out/"documents/d1.md").is_file()
    for page in doc["pages"]:
        ids=[e["id"] for e in page["elements"]]; assert len(ids)==len(set(ids))
        assert all(k in ids for k in page["reading_order"])
        for e in page["elements"]:
            x,y,X,Y=e["bbox"];assert -.5<=x<=X<=page["width"]+.5 and -.5<=y<=Y<=page["height"]+.5
    from deliver import finalize
    report={"status":"success","fields":{"ordered_excerpt":got,"measured_values":[{"trial":"A","s":1.82},{"trial":"B","s":1.96}]},
            "findings":[{"item":"periods","state":"verified","basis":"Table 1, Period (s), rows A and B","evidence":[table["id"]]}],"artifacts":["documents/d1.json"]}
    finalize(out,report)
    assert json.loads((out/"result.json").read_text("utf8"))["case_id"]=="synthetic-layout"
    report["findings"][0]["evidence"]=["bogus"]
    try: finalize(out,report)
    except ValueError: pass
    else: raise AssertionError("invalid evidence id accepted")
    print(json.dumps({"synthetic":True,"markers":len(expected),"raw_order_inversions":inversions(base),"repaired_order_inversions":inversions(got),"table_shape":[4,3],"merged_header_span":2,"scan_warnings":scan["warnings"],"font_tested":has_font}))


if __name__=="__main__":main()
