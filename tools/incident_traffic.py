"""Send bounded synthetic normal, 500, and denied-access requests to the red lab.

Run only after baseline collection. The script cleans up the cart item created
for each order attempt and refuses to continue if a red 500 is not observed.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import time
import uuid

from run_stages import HERE, login, loki_entries, request


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--normal", type=int, default=60)
    ap.add_argument("--errors", type=int, default=10)
    ap.add_argument("--denials", type=int, default=10)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not 0 <= args.normal <= 200 or not 1 <= args.errors <= 20 or not 0 <= args.denials <= 20:
        ap.error("normal 0..200, errors 1..20, denials 0..20")
    if args.dry_run:
        print(json.dumps({"kind": "dry_run_no_requests", "normal": args.normal,
                          "order_500_attempts": args.errors, "foreign_order_denials": args.denials,
                          "synthetic_accounts": ["alice", "bob"], "writes": ["cart add", "cart cleanup"]},
                         ensure_ascii=False))
        return 0
    alice, bob = login("alice"), login("bob")
    product = request("GET", "/product/detail/202", alice)["body"].get("data") or {}
    item, skus = product.get("product") or {}, product.get("skuStockList") or []
    if not item or not skus:
        raise SystemExit("가짜 상품 202 또는 SKU를 찾지 못했습니다")
    add_body = {"productId": 202, "productSkuId": skus[0]["id"], "quantity": 1,
                "productName": item.get("name"), "productSn": item.get("productSn"),
                "productCategoryId": item.get("productCategoryId"),
                "productSkuCode": skus[0].get("skuCode")}
    started = datetime.now(timezone.utc)
    start_ns = time.time_ns()
    rows = []

    def send(label: str, method: str, path: str, token="", body=None):
        trace = "sre-traffic-" + uuid.uuid4().hex[:12]
        response = request(method, path, token, body, trace)
        rows.append({"label": label, "http_status": response["http"],
                     "body_code": response["body"].get("code"), "trace_id": trace,
                     "latency_ms": response["latency_ms"]})
        return response

    old_lab_items = [x["id"] for x in (request("GET", "/cart/list", alice)["body"].get("data") or [])
                     if x.get("productId") == 202]
    if old_lab_items:
        send("old_lab_cart_cleanup", "POST", "/cart/delete?ids=" + ",".join(map(str, old_lab_items)), alice)

    for index in range(args.normal):
        if index % 3 == 0:
            send("normal_home", "GET", "/home/content")
        elif index % 3 == 1:
            send("normal_product", "GET", "/product/detail/202")
        else:
            send("normal_own_order", "GET", "/order/detail/9001", alice)

    for _ in range(args.errors):
        before = {x["id"] for x in (request("GET", "/cart/list", alice)["body"].get("data") or [])}
        added = send("cart_add_without_price", "POST", "/cart/add", alice, add_body)
        if (added["http"], added["body"].get("code")) != (200, 200):
            raise SystemExit("가격 없는 장바구니 추가가 예상과 다릅니다")
        mine = [x for x in (request("GET", "/cart/list", alice)["body"].get("data") or [])
                if x["id"] not in before]
        if len(mine) != 1:
            raise SystemExit("이번 요청에서 새로 만든 장바구니 항목 하나를 찾지 못했습니다")
        cid = mine[0]["id"]
        try:
            outcome = send("order_without_price_500", "POST", "/order/generateOrder", alice,
                           {"cartIds": [cid], "memberReceiveAddressId": 101, "payType": 0})
            if (outcome["http"], outcome["body"].get("code")) != (500, 500):
                oid = ((outcome["body"].get("data") or {}).get("order") or {}).get("id")
                if oid:
                    send("unexpected_order_cleanup", "POST", f"/order/cancelUserOrder?orderId={oid}", alice)
                raise SystemExit("레드 500이 확인되지 않아 추가 주문 주입을 중단했습니다")
        finally:
            send("cart_cleanup", "POST", f"/cart/delete?ids={cid}", alice)

    for _ in range(args.denials):
        denied = send("foreign_order_denied", "GET", "/order/detail/9001", bob)
        if (denied["http"], denied["body"].get("code")) != (200, 500):
            raise SystemExit("타인 주문 거부 결과가 HTTP 200 / 본문 500이 아닙니다")
    end_ns = time.time_ns()
    time.sleep(4)
    loki = {}
    for label, query in {
        "ops_error": '{app="mall-portal"} |= "error.unhandled"',
        "security_denied": '{app="mall-portal",stream="security",event="access.denied.not_owner"}',
    }.items():
        try:
            entries = loki_entries(query, start_ns, end_ns)
            loki[label] = {"count": len(entries), "possibly_truncated": len(entries) == 1000}
        except Exception as exc:
            loki[label] = {"query_error": type(exc).__name__}
    counts = {name: sum(row["label"] == name for row in rows)
              for name in ("normal_home", "normal_product", "normal_own_order", "order_without_price_500",
                           "foreign_order_denied")}
    output = {"kind": "synthetic_incident_injection", "started_utc": started.isoformat(),
              "ended_utc": datetime.now(timezone.utc).isoformat(), "counts": counts, "loki": loki,
              "records": rows, "response_bodies_and_tokens_saved": False,
              "notes": ["HTTP status and body code are separate.",
                        "This is a controlled lab injection, not production traffic or evidence of compromise."]}
    target = HERE / "measurements" / f"incident-traffic-{started:%Y%m%dT%H%M%SZ}.json"
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"file": str(target), "counts": counts, "loki": loki}, ensure_ascii=False))
    return 0 if (loki.get("ops_error", {}).get("count", 0) >= args.errors
                 and loki.get("security_denied", {}).get("count", 0) >= args.denials) else 2


if __name__ == "__main__":
    raise SystemExit(main())
