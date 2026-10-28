import asyncio,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(r"L:\CodexData\Decilist\outputs\decilist")))
from benchmarks import run_code_review_proof as study
async def main():
    study.configure("proof03")
    root=Path(r"L:\CodexData\Decilist\work\code-review-proof-03-development")
    catalog={c["id"]:c for c in study.read(study.CATALOG)["cases"]}
    lint={(r["id"],r["variant"]):r["diagnostics"] for r in study.read(Path(r"L:\CodexData\Decilist\work\code-review-proof-03\lint.json"))["rows"]}
    rows=[]
    for item in study.read(root/"plan.json")["selected_before_replay"]:
        assignment={k:item[k] for k in ("id","variant","arm")}
        rows.append(await study.trial(root,catalog[item["id"]],assignment,lint[(item["id"],item["variant"])]))
    study.dump(root/"results.json",{"kind":"exposed-case-development-replay","human_time_measured":False,"rows":rows})
asyncio.run(main())
