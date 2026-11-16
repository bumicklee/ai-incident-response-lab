#!/usr/bin/env python3
"""Run one lab command for real, keep its output as text, and record it for the student screenshots.

    python3 tools/capture_terminal.py 03-1-doctor 3-1 "make doctor" --caption "..."
    uv run --no-project --with playwright python tools/capture_terminal.py --render

Output: captures/student/<id>.txt (real output, masked) and manifest.json.
--render draws every .txt as a terminal-window PNG. Long output keeps the head and tail
and says how many lines were left out; nothing is rewritten.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import time
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]
OUT = LAB / "captures/student"
MANIFEST = OUT / "manifest.json"
HOME = str(Path.home())


def secrets() -> list[str]:
    values = []
    env = LAB / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                value = line.split("=", 1)[1].strip().strip("'\"")
                if len(value) >= 6:
                    values.append(value)
    for name in ("ANTHROPIC_API_KEY", "AZURE_SPEECH_KEY", "GITHUB_TOKEN"):
        if len(os.environ.get(name, "")) >= 6:
            values.append(os.environ[name])
    return sorted(set(values), key=len, reverse=True)


def mask(text: str) -> str:
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text).replace("\r", "")
    text = text.replace(str(LAB), "~/이 랩/lab").replace(HOME, "~")
    for value in secrets():
        text = text.replace(value, "••••••")
    text = re.sub(r"(?i)(sk-ant-[a-z0-9_-]{6})[a-z0-9_-]+", r"\1••••", text)
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._-]{12,}", r"\1••••••", text)
    return text


def load() -> list[dict]:
    return json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else []


def run(args) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    started = time.time()
    env = {**os.environ, "PATH": os.environ.get("PATH", "") + ":/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin"}
    proc = subprocess.run(args.command, shell=True, cwd=LAB, capture_output=True, text=True,
                          timeout=args.timeout, executable="/bin/zsh", env=env)
    output = mask(proc.stdout + proc.stderr).rstrip("\n")
    (OUT / f"{args.id}.txt").write_text(output + "\n", encoding="utf-8")
    entry = {"id": args.id, "lesson": args.lesson, "command": args.shown or args.command,
             "caption": args.caption, "exit": proc.returncode,
             "seconds": round(time.time() - started, 1), "head": args.head, "tail": args.tail, "start": args.start,
             "captured_at": time.strftime("%Y-%m-%d %H:%M")}
    rows = [row for row in load() if row["id"] != args.id] + [entry]
    MANIFEST.write_text(json.dumps(sorted(rows, key=lambda r: r["id"]), ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
    print(output[-3000:])
    print(f"[capture] {args.id} exit={proc.returncode} {entry['seconds']}s")
    return proc.returncode


PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;background:transparent;font-family:Menlo,"Apple SD Gothic Neo",AppleGothic,monospace}
.win{display:inline-block;min-width:860px;max-width:1180px;margin:18px;border-radius:12px;overflow:hidden;
background:#1d2733;box-shadow:0 18px 40px rgba(21,48,71,.28),0 2px 6px rgba(21,48,71,.18)}
.bar{height:34px;background:linear-gradient(#e9eef1,#d7dfe3);display:flex;align-items:center;padding:0 14px;
font:13px "Apple SD Gothic Neo",AppleGothic,sans-serif;color:#4a5c68}
.dot{width:12px;height:12px;border-radius:50%;margin-right:8px}
.title{flex:1;text-align:center;margin-right:60px}
pre{margin:0;padding:16px 20px 18px;color:#e6edf2;font-size:15px;line-height:1.55;white-space:pre-wrap;word-break:break-all}
.p{color:#7fd1c4}.c{color:#ffffff;font-weight:bold}.cut{color:#f0a868}
.ok{color:#8fd694}.bad{color:#ff8f7a}
</style></head><body><div class="win" id="w"><div class="bar">
<span class="dot" style="background:#ff5f57"></span><span class="dot" style="background:#febc2e"></span>
<span class="dot" style="background:#28c840"></span><span class="title">lab — zsh</span></div><pre>BODY</pre></div></body></html>"""


def colour(line: str) -> str:
    safe = html.escape(line)
    if re.search(r"(?i)\b(ok|pass(ed)?|success|ready|healthy|통과|성공)\b", line):
        return f'<span class="ok">{safe}</span>'
    if re.search(r"(?i)\b(error|fail(ed)?|denied|refus\w*|실패|거부|차단)\b", line):
        return f'<span class="bad">{safe}</span>'
    return safe


def body(entry: dict, text: str) -> str:
    lines = text.rstrip("\n").splitlines()
    start = entry.get("start") or 1
    if start > 1:
        lines = [f"\0… 앞 {start - 1}줄 생략 …"] + lines[start - 1:]
    head, tail = entry.get("head") or 40, entry.get("tail") or 0
    if len(lines) > head + tail:
        cut = len(lines) - head - tail
        lines = lines[:head] + [f"\0… {'중간' if tail else '뒤'} {cut}줄 생략 …"] + (lines[-tail:] if tail else [])
    shown = []
    for cmd in entry["command"].split("\n"):
        shown.append(f'<span class="p">lab %</span> <span class="c">{html.escape(cmd)}</span>')
    for line in lines:
        shown.append(f'<span class="cut">{html.escape(line[1:])}</span>' if line.startswith("\0") else colour(line))
    return "\n".join(shown)


def render(only: list[str]) -> None:
    from playwright.sync_api import sync_playwright
    rows = [r for r in load() if "command" in r and (not only or r["id"] in only)]
    with sync_playwright() as p:
        browser = launch(p)
        page = browser.new_page(device_scale_factor=2, viewport={"width": 1300, "height": 900})
        for entry in rows:
            text = (OUT / f"{entry['id']}.txt").read_text(encoding="utf-8")
            page.set_content(PAGE.replace("BODY", body(entry, text)))
            page.locator("#w").screenshot(path=str(OUT / f"{entry['id']}.png"), omit_background=True)
            print("png", entry["id"])
        browser.close()


def launch(p):
    """Use the installed headless shell when the current Playwright wants a newer browser build."""
    try:
        return p.chromium.launch()
    except Exception:
        shells = sorted(Path.home().glob("Library/Caches/ms-playwright/chromium_headless_shell-*/*/chrome-headless-shell"))
        return p.chromium.launch(executable_path=str(shells[-1]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("id", nargs="?")
    parser.add_argument("lesson", nargs="?")
    parser.add_argument("command", nargs="?")
    parser.add_argument("--caption", default="")
    parser.add_argument("--shown", help="command text to show when it differs from what runs")
    parser.add_argument("--head", type=int, default=40)
    parser.add_argument("--tail", type=int, default=0)
    parser.add_argument("--start", type=int, default=1, help="first output line to show")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--render", nargs="*", metavar="ID")
    args = parser.parse_args()
    if args.render is not None:
        render(args.render)
        return 0
    if not (args.id and args.lesson and args.command):
        parser.error("id, lesson and command are required")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
