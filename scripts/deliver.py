"""Present extracted evidence and finalize caller-specific results after review."""
import argparse
import json
import re
from pathlib import Path


def editable(doc):
    lines=[]
    for p in doc["pages"]:
        lines.extend([f'## {doc["source"]} · PDF page {p["page"]}',""])
        elements={e["id"]:e for e in p["elements"]}
        for key in p.get("reading_order",[]):
            e=elements[key]; kind=e["kind"]
            if kind=="table":
                cells=e["rows"]; width=max(map(len,cells),default=0)
                for i,row in enumerate(cells):
                    values=[str(v if v is not None else "").replace("|","\\|").replace("\n","<br>") for v in row]
                    lines.append("| "+" | ".join(values+[""]*(width-len(values)))+" |")
                    if i==0: lines.append("| "+" | ".join(["---"]*width)+" |")
                lines.extend([f'<!-- evidence {key}; merged cells and coordinates are in the JSON -->',""])
            elif kind=="image":
                lines.append(f'<!-- image {key} bbox={e["bbox"]} -->')
            else:
                marker="#"*min(6,e.get("level",1)+2)+" " if kind=="heading_candidate" else ""
                lines.extend([f'{marker}{e["text"]} <!-- {key} -->',""])
        if p.get("margin_ids"):
            lines.append("<!-- marginal text: "+", ".join(p["margin_ids"])+" -->")
    return "\n".join(lines)+"\n"


def finalize(folder,review):
    from parse_pdf import save
    path=folder/"result.json"
    result=json.loads(path.read_text(encoding="utf-8"))
    valid=set()
    for item in result["documents"]:
        doc=json.loads((folder/item["artifact"]).read_text(encoding="utf-8"))
        for p in doc["pages"]:
            valid.update(e["id"] for e in p["elements"])
            valid.add(f'{p["doc_id"]}-p{p["page"]}')
    original=result["output_contract"]
    fields=review["fields"]
    if not isinstance(fields,dict) or set(fields)&{"case_id","output_contract","stage","status","changes"}:
        raise ValueError("review fields cannot replace case identity, status or output contract")
    findings=review["findings"]
    if not isinstance(findings,list) or not findings:
        raise ValueError("supply reviewed findings with evidence or an explicit gap")
    states={"verified","not_reported","uncertain","conflicting","unreadable"}
    for f in findings:
        if f.get("state") not in states or not f.get("item") or not f.get("basis"):
            raise ValueError("each finding requires item, state and basis")
        refs=f.get("evidence",[])
        if not set(refs)<=valid or f["state"]=="verified" and not refs:
            raise ValueError("invalid evidence references for "+f["item"])
    result.update(fields);result["reviewed_findings"]=findings
    required=original if isinstance(original,list) else original.get("required",original.get("required_result_fields",[])) if isinstance(original,dict) else []
    if isinstance(original,dict) and not required and not any(k in original for k in ("type","properties","description","files","artifacts")):
        required=list(original)
    missing=[k for k in required if isinstance(k,str) and result.get(k) is None]
    status=review.get("status","partial")
    if status not in ("success","partial","failed","unable_to_determine"):
        raise ValueError("invalid final status")
    if status=="success" and (missing or any(f["state"] in {"uncertain","conflicting","unreadable"} for f in findings) or not valid):
        raise ValueError("success requires resolved requested findings and all required fields")
    for rel in review.get("artifacts",[]):
        artifact=(folder/rel).resolve()
        if not artifact.is_relative_to(folder.resolve()) or not artifact.is_file():
            raise ValueError("missing declared artifact "+rel)
    result.update(status=status,stage="reviewed",missing_contract_fields=missing)
    result["changes"].extend(review.get("changes",[]))
    save(path,result)


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("review",type=Path)
    parser.add_argument("--output",type=Path,default=Path("/app/output"))
    args=parser.parse_args()
    finalize(args.output,json.loads(args.review.read_text(encoding="utf-8")))
