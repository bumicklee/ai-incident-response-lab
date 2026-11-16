#!/usr/bin/env python3
"""Screenshot real lab web screens (Grafana, on-call web) for the student notes.

    uv run --no-project --with playwright python tools/capture_web.py ID LESSON URL_PATH --site grafana|oncall [--caption ...]
      [--loki 'LogQL'] [--hours 1] [--wait 4] [--full]

Credentials are read from compose defaults and lab/.env and never printed. The home path and
passwords do not appear on these pages; the screenshot is the real page at 1440x900.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse
import urllib.request
import base64
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]
OUT = LAB / "captures/student"
MANIFEST = OUT / "manifest.json"


def env_value(name: str, default: str = "") -> str:
    env = LAB / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip("'\"")
    return os.environ.get(name, default)


SITES = {
    "grafana": ("http://127.0.0.1:13030", lambda: ("admin", env_value("LAB_GRAFANA_PASSWORD", "sre-lab-grafana-2026"))),
    "oncall": ("http://127.0.0.1:18090", lambda: (env_value("LAB_ONCALL_VIEW_USER"), env_value("LAB_ONCALL_VIEW_PASSWORD"))),
}


def loki_uid(base: str, auth: tuple[str, str]) -> str:
    req = urllib.request.Request(base + "/api/datasources")
    req.add_header("Authorization", "Basic " + base64.b64encode(":".join(auth).encode()).decode())
    rows = json.load(urllib.request.urlopen(req, timeout=10))
    return next(r["uid"] for r in rows if r["type"] == "loki")


def explore_path(uid: str, query: str, hours: float) -> str:
    panes = {"a": {"datasource": uid, "queries": [{"refId": "A", "expr": query, "queryType": "range",
                                                    "datasource": {"type": "loki", "uid": uid}, "editorMode": "code"}],
                   "range": {"from": f"now-{int(hours * 60)}m", "to": "now"}}}
    return "/explore?schemaVersion=1&orgId=1&panes=" + urllib.parse.quote(json.dumps(panes, separators=(",", ":")))


def launch(p):
    """Use the installed headless shell when the current Playwright wants a newer browser build."""
    try:
        return p.chromium.launch()
    except Exception:
        shells = sorted(Path.home().glob("Library/Caches/ms-playwright/chromium_headless_shell-*/*/chrome-headless-shell"))
        return p.chromium.launch(executable_path=str(shells[-1]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("id")
    parser.add_argument("lesson")
    parser.add_argument("path", nargs="?", default="/")
    parser.add_argument("--site", choices=SITES, required=True)
    parser.add_argument("--caption", default="")
    parser.add_argument("--loki")
    parser.add_argument("--hours", type=float, default=1)
    parser.add_argument("--wait", type=float, default=4)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--fill", action="append", default=[], help="SELECTOR::VALUE typed before the clicks")
    parser.add_argument("--scroll", help="text or CSS selector to scroll into view before the screenshot")
    parser.add_argument("--click", action="append", default=[], help="text or CSS selector to click before the screenshot")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright

    base, creds = SITES[args.site]
    auth = creds()
    # Grafana's query editor crashes when a |= filter arrives through the URL, so the page opens
    # with the stream selector only and the full query is typed into the editor like a person would.
    path = explore_path(loki_uid(base, auth), args.loki.split("|")[0].strip(), args.hours) if args.loki else args.path
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = launch(p)
        context = browser.new_context(viewport={"width": args.width, "height": args.height}, device_scale_factor=2,
                                      http_credentials=None if args.site == "grafana" else {"username": auth[0], "password": auth[1]},
                                      locale="ko-KR", timezone_id="Asia/Seoul", color_scheme="light")
        page = context.new_page()
        if args.site == "grafana":
            page.goto(base + "/login")
            page.fill("input[name=user]", auth[0])
            page.fill("input[name=password]", auth[1])
            page.click("button[type=submit]")
            page.wait_for_load_state("networkidle")
        page.goto(base + path + (("&" if "?" in path else "?") + "theme=light" if args.site == "grafana" else ""))
        page.wait_for_load_state("networkidle")
        if args.loki and "|" in args.loki:
            page.wait_for_selector("[role=code]", state="attached", timeout=20000)
            page.wait_for_timeout(1500)
            page.locator("[role=code] textarea").first.focus()
            page.keyboard.press("Meta+A")
            page.keyboard.press("Backspace")
            page.keyboard.insert_text(args.loki)
            page.keyboard.press("Shift+Enter")
            page.wait_for_load_state("networkidle")
            page.mouse.click(5, args.height - 5)
        for item in args.fill:
            selector, value = item.split("::", 1)
            page.fill(selector, value)
        for target in args.click:
            locator = page.locator(target) if target[:1] in ".#[" else page.get_by_text(target, exact=True)
            locator.first.click()
            page.wait_for_timeout(1200)
        page.wait_for_load_state("networkidle")
        if args.scroll:
            target = page.locator(args.scroll) if args.scroll[:1] in ".#[" else page.get_by_text(args.scroll, exact=True)
            target.first.scroll_into_view_if_needed()
        time.sleep(args.wait)
        page.screenshot(path=str(OUT / f"{args.id}.png"), full_page=args.full)
        browser.close()
    entry = {"id": args.id, "lesson": args.lesson, "kind": "web", "site": args.site,
             "url": base + (args.path if not args.loki else "/explore"), "loki": args.loki,
             "caption": args.caption, "captured_at": time.strftime("%Y-%m-%d %H:%M")}
    rows = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else []
    rows = [r for r in rows if r["id"] != args.id] + [entry]
    MANIFEST.write_text(json.dumps(sorted(rows, key=lambda r: r["id"]), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("[web]", args.id, path[:80])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
