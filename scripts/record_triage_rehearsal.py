"""Record real local developer commands and make a self-contained timed replay.

This is an agent-run terminal recording, not a browser video or human study.
The project must contain the two explicitly authored disposable check scripts.
No source is repaired, no test result is manufactured, and no server is started.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
from urllib.parse import urlparse


HTML = r'''<!doctype html>
<html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Decilist · تسجيل طرفية حقيقي</title>
<style>
:root{color-scheme:dark;--bg:#10191a;--panel:#172326;--line:#334448;--ink:#e8f0e9;--muted:#a9bdb8;--green:#c1f58d;--amber:#ffcf85}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 system-ui,sans-serif}main{max-width:1180px;margin:auto;padding:28px}h1{font-size:clamp(26px,4vw,44px);line-height:1.2;margin:12px 0}p{margin:8px 0;color:var(--muted)}.eyebrow{color:var(--green);font-size:13px;letter-spacing:.08em}.intro{max-width:850px}.controls{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:24px 0 14px}button,select{border:1px solid var(--line);border-radius:8px;background:var(--panel);color:var(--ink);padding:10px 16px;font:inherit;cursor:pointer}button.primary{background:var(--green);color:#142014;border-color:var(--green);font-weight:650}button:hover,select:hover{border-color:var(--green)}button:focus-visible,select:focus-visible,input:focus-visible{outline:3px solid var(--green);outline-offset:3px}#clock{direction:ltr;font-variant-numeric:tabular-nums;color:var(--green);margin-inline-start:auto}.timeline{width:100%;accent-color:var(--green);margin:0 0 14px;direction:ltr}.terminal{direction:ltr;text-align:left;border:1px solid var(--line);border-radius:12px;background:#0a1011;overflow:hidden}.bar{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;background:var(--panel);padding:12px 18px;color:var(--muted);font-size:13px}.terminal pre{white-space:pre-wrap;overflow-wrap:anywhere;margin:0;padding:20px;height:56vh;min-height:310px;overflow:auto;font:14px/1.6 ui-monospace,Consolas,monospace}.command{color:var(--green)}.exit{color:var(--amber)}.out{color:#e0e9e4}.meta{color:var(--muted)}.notes{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:18px}.note{border:1px solid var(--line);border-radius:10px;padding:16px;background:var(--panel)}h2{font-size:16px;margin:0 0 6px}details{margin:18px 0;border:1px solid var(--line);border-radius:10px;padding:14px}summary{cursor:pointer;color:var(--green)}details pre{direction:ltr;text-align:left;white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.6 ui-monospace,Consolas,monospace}a{color:var(--green)}.foot{font-size:13px;margin-top:16px}#status{color:var(--green)}@media(max-width:650px){main{padding:18px}.notes{grid-template-columns:1fr}.terminal pre{font-size:12px;padding:13px;height:58vh}.controls{gap:8px}button,select{padding:9px 11px}#clock{width:100%;margin:0}.bar{padding:10px 12px}}
</style>
<main><header class="intro"><div class="eyebrow">DECILIST / RECORDED DEVELOPER REHEARSAL</div><h1>تسجيل طرفية حقيقي</h1><p>إعادة عرض لأوامر شغّلها الوكيل فعلًا: اختبار فاشل، ترياج من الحزمة المثبتة، واستدعاء مباشر للموديل المحلي.</p><p>ده مش فيديو للمتصفح أو تجربة مستخدم لأول مرة. النتائج وأكواد الخروج محفوظة زي ما ظهرت، مع إخفاء المسارات الشخصية.</p></header>
<div class="controls"><button class="primary" id="play">تشغيل</button><button id="pause">إيقاف مؤقت</button><button id="restart">من البداية</button><label>السرعة <select id="speed" aria-label="سرعة التشغيل"><option value="0.25">0.25×</option><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="2">2×</option></select></label><span id="clock"></span></div>
<input class="timeline" id="seek" type="range" min="0" step="any" aria-label="موضع التسجيل"><div class="terminal"><div class="bar"><span>Actual subprocess output · stdout + stderr</span><span id="status" role="status">جاهز للتشغيل</span></div><pre id="screen" aria-label="مخرجات الطرفية المسجلة"></pre></div>
<div class="notes"><section class="note"><h2>إيه اللي اتسجّل؟</h2><p id="method"></p><p id="provenance"></p></section><section class="note"><h2>حدود الدليل</h2><p>الحالات صغيرة ومؤلفة للتحضير. اقتراح الفحص مش إثبات لسبب الخطأ أو إصلاحه. الموديل قد يمتنع، والقواعد ظاهرة بمصدرها. التسجيل استخدم loopback ولا يثبت عزل الجهاز عن الشبكة.</p></section></div>
<details><summary>كود استدعاء Python SDK المستخدم فعلًا</summary><pre id="sdk"></pre></details><details><summary>إخفاء المسارات والتوقيت</summary><p>اتستبدلت مسارات الجهاز الشخصية ومجلدات العمل بأسماء وصفية؛ نص الخطأ والقرار وكود الخروج ما اتغيروش. التوقيت هو وقت رصد كل سطر من العملية، مش قياس كتابة شخص أو زمن كل حرف.</p><pre id="redactions"></pre></details>
<p class="foot"><a href="terminal.json">سجل JSON الكامل</a> · الصفحة ذاتية الاكتفاء ولا تحتاج ملفات أو خطوط أو مكتبات خارجية.</p></main>
<script type="application/json" id="record">__RECORD__</script><script>
"use strict";
const record=JSON.parse(document.getElementById("record").textContent),screen=document.getElementById("screen"),seek=document.getElementById("seek"),clock=document.getElementById("clock"),status=document.getElementById("status"),speed=document.getElementById("speed");
let position=0,playing=false,last=0,shown=-1;
seek.max=record.duration_seconds;
document.getElementById("sdk").textContent=record.sdk_code;
document.getElementById("redactions").textContent=JSON.stringify(record.redactions,null,2);
document.getElementById("method").textContent=`${record.commands.length} أوامر فعلية. مدة التسجيل ${record.duration_seconds.toFixed(2)} ثانية. أكواد الخروج متعمدة الظهور، ومنها فشل الاختبار والامتناع.`;
document.getElementById("provenance").dir="ltr";
document.getElementById("provenance").textContent=`Package ${record.package_version} · recorded ${record.started_at_utc}`;
function stamp(t){return `${Math.floor(t/60).toString().padStart(2,"0")}:${(t%60).toFixed(1).padStart(4,"0")}`;}
function render(){const events=record.events.filter(e=>e.at<=position);if(events.length!==shown){const fragment=document.createDocumentFragment();for(const e of events){const line=document.createElement("span");line.className=e.kind==="command"?"command":e.kind==="exit"?"exit":e.kind==="output"?"out":"meta";line.textContent=e.text;fragment.append(line);}screen.replaceChildren(fragment);screen.scrollTop=screen.scrollHeight;shown=events.length;}seek.value=position;clock.textContent=`${stamp(position)} / ${stamp(record.duration_seconds)}`;}
function tick(now){if(playing){if(last)position=Math.min(record.duration_seconds,position+(now-last)/1000*Number(speed.value));if(position>=record.duration_seconds){playing=false;status.textContent="اكتمل التسجيل";}render();}last=now;requestAnimationFrame(tick);}
document.getElementById("play").onclick=()=>{if(position>=record.duration_seconds)position=0;playing=true;last=0;status.textContent="جاري العرض";render();};
document.getElementById("pause").onclick=()=>{playing=false;status.textContent="متوقف مؤقتًا";render();};
document.getElementById("restart").onclick=()=>{position=0;playing=false;last=0;status.textContent="جاهز من البداية";render();};
seek.oninput=()=>{position=Number(seek.value);status.textContent=playing?"جاري العرض":position>=record.duration_seconds?"نهاية التسجيل":"متوقف مؤقتًا";render();};render();requestAnimationFrame(tick);
</script></html>'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True, help="Installed consumer Python")
    parser.add_argument("--project", type=Path, required=True, help="Separate authored fixture project")
    parser.add_argument("--work", type=Path, required=True, help="New recording-only scratch directory")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--runtime-url", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    python, project, work, output = (p.resolve() for p in (args.python, args.project, args.work, args.output_dir))
    parsed = urlparse(args.runtime_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password:
        parser.error("Use an explicitly configured local loopback runtime")
    for path in (python, project / "check_total.py", project / "missing_dependency.py", args.wheel):
        if not path.is_file():
            parser.error(f"Required authored input does not exist: {path.name}")
    if any((output / name).exists() for name in ("terminal.json", "terminal.html")):
        parser.error("Recording outputs already exist; choose a new output directory")
    work.mkdir(parents=True, exist_ok=False)
    output.mkdir(parents=True, exist_ok=True)
    replacements = [(str(project), "<AUTHORED_PROJECT>"), (str(work), "<RECORDING_WORK>"), (str(python), "<CONSUMER_PYTHON>"), (str(Path.home()), "<USER_HOME>")]
    replacements.sort(key=lambda item: len(item[0]), reverse=True)
    redactions = {label: 0 for _, label in replacements}

    def redact(text: str) -> str:
        for original, label in replacements:
            for candidate in {original, original.replace("\\", "/")}:
                text, count = re.subn(re.escape(candidate), lambda _: label, text, flags=re.IGNORECASE)
                redactions[label] += count
        return text

    events: list[dict] = []
    commands: list[dict] = []
    start = time.perf_counter()
    started = datetime.now(timezone.utc).isoformat()
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", DECILIST_URL=args.runtime_url)
    env.pop("PYTHONPATH", None)

    def run(argv: list[str], label: str, *, log: Path | None = None) -> tuple[int, str]:
        command_at = time.perf_counter() - start
        display = redact(subprocess.list2cmdline(argv))
        events.append({"at": command_at, "kind": "command", "text": f"\n$ {display}\n"})
        process = subprocess.Popen(argv, cwd=project, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        lines: queue.Queue = queue.Queue()

        def reader() -> None:
            assert process.stdout is not None
            for raw in iter(process.stdout.readline, b""):
                lines.put((time.perf_counter() - start, raw.decode("utf-8", errors="replace")))
            lines.put(None)

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        captured: list[str] = []
        deadline = time.monotonic() + 120
        while True:
            if time.monotonic() > deadline:
                process.kill()
                process.wait()
                raise TimeoutError(f"Owned recording subprocess timed out: {label}")
            try:
                item = lines.get(timeout=.1)
            except queue.Empty:
                continue
            if item is None:
                break
            at, text = item
            captured.append(text)
            events.append({"at": at, "kind": "output", "text": redact(text)})
        exit_code = process.wait()
        end = time.perf_counter() - start
        thread.join(timeout=1)
        text = "".join(captured)
        if log is not None:
            log.write_text(text, encoding="utf-8", newline="\n")
        events.append({"at": end, "kind": "exit", "text": f"[exit {exit_code}; {end-command_at:.3f} seconds]\n"})
        commands.append({"label": label, "argv": [redact(part) for part in argv], "cwd": "<AUTHORED_PROJECT>", "started_at_seconds": command_at, "finished_at_seconds": end, "exit_code": exit_code, "output_sha256_before_path_redaction": hashlib.sha256(text.encode()).hexdigest()})
        return exit_code, text

    _, version = run([str(python), "-u", "-c", "from importlib.metadata import version; print(version('decilist'))"], "Installed distribution version")
    for name in ("check_total", "missing_dependency"):
        log = work / f"{name}.log"
        run([str(python), "-u", str(project / f"{name}.py")], f"Actual authored {name} failure", log=log)
        run([str(python), "-u", "-m", "decilist", "triage", "--log", str(log), "--project", str(project), "--mode", "auto"], f"Installed triage auto: {name}")
    sdk_code = f'''import asyncio, json
from decilist import Client, Policy
async def main():
    async with await Client.connect({json.dumps(args.runtime_url)}, policy=Policy(local_only=True, min_confidence=0.7, timeout_ms=30000)) as sdk:
        result = await sdk.choose("Please summarize the attached document in three bullet points.", "Which action is explicitly requested?", {{"summarize": "Summarize a document", "translate": "Translate text", "compare": "Compare documents"}})
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
asyncio.run(main())
'''
    sdk_file = work / "sdk_choose.py"
    sdk_file.write_text(sdk_code, encoding="utf-8", newline="\n")
    run([str(python), "-u", str(sdk_file)], "Direct installed Python Client.choose")
    record = {
        "schema_version": 1, "kind": "actual timed agent-run terminal recording", "browser_video": False, "human_first_use": False,
        "started_at_utc": started, "finished_at_utc": datetime.now(timezone.utc).isoformat(), "duration_seconds": time.perf_counter()-start,
        "package_version": version.strip(), "wheel_sha256": hashlib.sha256(args.wheel.read_bytes()).hexdigest(),
        "fixture_provenance": "Separate tiny authored clean-consumer project, never reserved agent trials; check_total.py and missing_dependency.py run without edits",
        "fixture_sha256": {name: hashlib.sha256((project/name).read_bytes()).hexdigest() for name in ("check_total.py", "missing_dependency.py")},
        "runtime_url": args.runtime_url, "network_claim": "Loopback client calls only; no OS air gap, browser offline or native-network audit established by this recording",
        "capture": "Actual merged stdout/stderr lines timestamped when observed; command prompts and exit annotations are recorder metadata, not subprocess output",
        "redactions": [{"alias": label, "replacements": count, "scope": "Display path replacement only; original personal path not retained in deliverable"} for label,count in redactions.items()],
        "commands": commands, "events": events, "sdk_code": sdk_code,
    }
    encoded = json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False)
    (output/"terminal.json").write_text(encoded+"\n", encoding="utf-8", newline="\n")
    safe = encoded.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    (output/"terminal.html").write_text(HTML.replace("__RECORD__", safe), encoding="utf-8", newline="\n")
    print(json.dumps({"commands": len(commands), "duration_seconds": record["duration_seconds"], "exit_codes": [item["exit_code"] for item in commands], "output_dir": redact(str(output))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
