"""Run the isolated SRE lab's normal, error, and security stages.

Only small, redacted evidence is persisted. Tokens and full order responses
stay in memory. This script uses the synthetic shopping-mall accounts and orders.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

HERE = Path(__file__).resolve().parents[1]
PORTAL = "http://127.0.0.1:" + os.environ.get("LAB_PORTAL_PORT", "18085")
LOKI = "http://127.0.0.1:" + os.environ.get("LAB_LOKI_PORT", "13100")


def request(method: str, path: str, token: str = "", body=None, trace: str = "") -> dict:
    headers = {"X-Trace-Id": trace} if trace else {}
    if token:
        headers["Authorization"] = token
    data = None
    if body is not None:
        if path == "/sso/login":
            data = urllib.parse.urlencode(body).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif isinstance(body, dict) or isinstance(body, list):
            data = json.dumps(body, ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json"
        else:
            data = urllib.parse.urlencode(body).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(PORTAL + path, data=data, headers=headers, method=method)
    start = time.perf_counter()
    try:
        response = urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        raw = response.read()
        status = response.status
        response_trace = response.headers.get("X-Trace-Id", "")
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = {}
    return {"http": status, "body": parsed, "trace": response_trace,
            "latency_ms": round((time.perf_counter() - start) * 1000, 1)}


def login(name: str) -> str:
    r = request("POST", "/sso/login", body={"username": name, "password": "daon-pass-1!"})
    token = (r["body"].get("data") or {}).get("token")
    if r["http"] != 200 or not token:
        raise RuntimeError(f"{name} login failed: HTTP {r['http']} code={r['body'].get('code')}")
    return "Bearer " + token


def sample(name: str, r: dict, trace: str = "") -> dict:
    body = r["body"]
    return {"step": name, "http_status": r["http"], "body_code": body.get("code"),
            "message": str(body.get("message", ""))[:120], "trace_id": body.get("trace_id") or body.get("traceId") or r["trace"] or trace,
            "latency_ms": r["latency_ms"]}


def loki_entries(query: str, start_ns: int, end_ns: int) -> list[dict]:
    args = urllib.parse.urlencode({"query": query, "start": start_ns, "end": end_ns, "limit": 1000, "direction": "forward"})
    with urllib.request.urlopen(LOKI + "/loki/api/v1/query_range?" + args, timeout=15) as r:
        parsed = json.load(r)
    rows = []
    for stream in parsed.get("data", {}).get("result", []):
        for stamp, line in stream.get("values", []):
            rows.append({"timestamp_ns": stamp, "labels": stream.get("stream", {}), "line": line})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["red", "green"], required=True)
    args = ap.parse_args()
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = HERE / "measurements" / f"stages-{args.mode}-{run_id}.json"
    started_ns = time.time_ns()
    alice, bob = login("alice"), login("bob")
    steps = []
    traces = []

    def step(name, method, path, token="", body=None):
        trace = "sre-" + uuid.uuid4().hex[:16]
        result = request(method, path, token, body, trace)
        traces.append(trace)
        steps.append(sample(name, result, trace))
        return result

    step("normal_home", "GET", "/home/content")
    step("normal_own_order", "GET", "/order/detail/9001", alice)
    step("security_foreign_order_denied", "GET", "/order/detail/9001", bob)

    product = step("error_product_lookup", "GET", "/product/detail/202", alice)["body"].get("data") or {}
    p, skus = product.get("product") or {}, product.get("skuStockList") or []
    if not p or not skus:
        raise RuntimeError("product 202 / SKU missing")
    current = step("error_cart_before", "GET", "/cart/list", alice)["body"].get("data") or []
    old = [x["id"] for x in current if x.get("productId") == 202]
    if old:
        step("error_remove_old_lab_cart", "POST", "/cart/delete?ids=" + ",".join(map(str, old)), alice)
    before = {x["id"] for x in (request("GET", "/cart/list", alice)["body"].get("data") or [])}
    add = {"productId": 202, "productSkuId": skus[0]["id"], "quantity": 1,
           "productName": p.get("name"), "productSn": p.get("productSn"),
           "productCategoryId": p.get("productCategoryId"), "productSkuCode": skus[0].get("skuCode")}
    step("error_cart_add_without_price", "POST", "/cart/add", alice, add)
    mine = [x for x in (request("GET", "/cart/list", alice)["body"].get("data") or []) if x["id"] not in before]
    if not mine:
        raise RuntimeError("new lab cart item missing")
    cid = mine[-1]["id"]
    try:
        order = step("error_order_without_price", "POST", "/order/generateOrder", alice,
                     {"cartIds": [cid], "memberReceiveAddressId": 101, "payType": 0})
        oid = ((order["body"].get("data") or {}).get("order") or {}).get("id")
        if oid:
            step("cleanup_created_order", "POST", f"/order/cancelUserOrder?orderId={oid}", alice)
    finally:
        step("cleanup_lab_cart", "POST", f"/cart/delete?ids={cid}", alice)

    marker = f"SRE-LAB-{run_id}-{args.mode}"
    ret = {"orderId": 9002, "productId": 27, "orderSn": "FAKE-SN", "memberUsername": "bob",
           "returnName": "테스트", "returnPhone": "010-0000-0000", "productCount": 1,
           "reason": "단순 변심", "description": marker}
    step("security_foreign_return", "POST", "/returnApply/create", bob, ret)
    own_return = {**ret, "orderId": 9001, "memberUsername": "alice"}
    step("normal_own_return", "POST", "/returnApply/create", alice, own_return)
    # No real customer data: synthetic order 9002 and actors only.
    time.sleep(3)
    ended_ns = time.time_ns()
    evidence = {}
    for label, query in {
        "security_denied": '{app="mall-portal",stream="security",event="access.denied.not_owner"}',
        "portal_errors": '{app="mall-portal",level="ERROR"}',
    }.items():
        try:
            rows = loki_entries(query, started_ns, ended_ns)
            evidence[label] = {"count": len(rows), "timestamps_ns": [x["timestamp_ns"] for x in rows[:20]]}
        except Exception as exc:
            evidence[label] = {"query_error": type(exc).__name__}
    result = {"run_id": run_id, "mode": args.mode, "started_utc": dt.datetime.fromtimestamp(started_ns / 1e9, dt.timezone.utc).isoformat(),
              "ended_utc": dt.datetime.fromtimestamp(ended_ns / 1e9, dt.timezone.utc).isoformat(),
              "portal_url": PORTAL, "loki_url": LOKI, "synthetic_data": True,
              "steps": steps, "loki": evidence, "cleanup_return_marker": marker,
              "notes": ["Full API responses, bearer tokens, customer addresses and phone numbers are not saved.",
                        "Both synthetic return applications share one marker and must be removed by cleanup before reuse."]}
    by_name = {row["step"]: row for row in steps}
    order_ok = by_name["error_order_without_price"]["http_status"] == (500 if args.mode == "red" else 200)
    return_ok = by_name["security_foreign_return"]["body_code"] == (200 if args.mode == "red" else 500)
    own_return_ok = (by_name["normal_own_return"]["http_status"],
                     by_name["normal_own_return"]["body_code"]) == (200, 200)
    result["expectations_met"] = order_ok and return_ok and own_return_ok
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"file": str(out), "steps": steps, "loki": evidence}, ensure_ascii=False, indent=2))
    return 0 if result["expectations_met"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
