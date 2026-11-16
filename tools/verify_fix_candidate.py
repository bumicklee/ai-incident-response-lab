"""Exercise the isolated price-fix container with fake accounts and clean up cart/order."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
import os
import time
import uuid

from run_stages import HERE, PORTAL, login, request


def main() -> int:
    # 기본은 격리 후보(19186)만. AI 수정 하네스(ai_fix.py)는 배포 뒤 본 쇼핑몰 재시험에도 쓰므로 VERIFY_ANY_PORT=1로 연다
    if PORTAL != "http://127.0.0.1:19186" and os.environ.get("VERIFY_ANY_PORT") != "1":
        raise SystemExit("candidate verifier only accepts LAB_PORTAL_PORT=19186")
    started = datetime.now(timezone.utc)
    for _ in range(45):
        try:
            if request("GET", "/home/content")["http"] == 200:
                break
        except OSError:
            pass
        time.sleep(2)
    else:
        raise SystemExit("candidate portal did not become HTTP ready within 90 seconds")
    alice = login("alice")
    product = request("GET", "/product/detail/202", alice)["body"].get("data") or {}
    item, skus = product.get("product") or {}, product.get("skuStockList") or []
    if not item or not skus or skus[0].get("price") is None:
        raise SystemExit("synthetic product 202 or SKU price is missing")
    sku = skus[0]
    expected = Decimal(str(sku["price"]))
    existing = request("GET", "/cart/list", alice)["body"].get("data") or []
    if any(row.get("productId") == 202 for row in existing):
        raise SystemExit("product 202 is already in Alice's cart; refuse to modify pre-existing rows")
    body = {"productId": 202, "productSkuId": sku["id"], "quantity": 1,
            "productName": item.get("name"), "productSn": item.get("productSn"),
            "productCategoryId": item.get("productCategoryId"), "productSkuCode": sku.get("skuCode")}
    records = []
    for label, extra_price in (("missing_price", None), ("tampered_one_unit", 1),
                               ("normal_sku_price", str(expected))):
        payload = dict(body)
        if extra_price is not None:
            payload["price"] = extra_price
        trace = "sre-fix-" + uuid.uuid4().hex[:12]
        cart_id = None
        order_id = None
        try:
            added = request("POST", "/cart/add", alice, payload, trace)
            cart = request("GET", "/cart/list", alice)["body"].get("data") or []
            mine = [row for row in cart if row.get("productId") == 202 and row.get("productSkuId") == sku["id"]]
            if len(mine) != 1:
                raise RuntimeError(f"{label}: expected exactly one new cart row")
            cart_id = mine[0]["id"]
            if mine[0].get("price") is None:
                raise RuntimeError(f"{label}: cart row has no price field; available fields: {sorted(mine[0])}")
            actual = Decimal(str(mine[0]["price"]))
            if (added["http"], added["body"].get("code")) != (200, 200) or actual != expected:
                raise RuntimeError(f"{label}: add or server price was wrong")
            ordered = request("POST", "/order/generateOrder", alice,
                              {"cartIds": [cart_id], "memberReceiveAddressId": 101, "payType": 0}, trace)
            order_id = (((ordered["body"].get("data") or {}).get("order") or {}).get("id"))
            records.append({"scenario": label, "trace_id": trace,
                            "cart_http": added["http"], "cart_code": added["body"].get("code"),
                            "cart_price_matches_sku": actual == expected,
                            "order_http": ordered["http"], "order_code": ordered["body"].get("code"),
                            "order_created": bool(order_id)})
            if (ordered["http"], ordered["body"].get("code")) != (200, 200) or not order_id:
                raise RuntimeError(f"{label}: order was not successful")
        finally:
            if order_id:
                canceled = request("POST", f"/order/cancelUserOrder?orderId={order_id}", alice)
                if (canceled["http"], canceled["body"].get("code")) != (200, 200):
                    raise RuntimeError(f"{label}: test order cleanup failed")
            if cart_id:
                deleted = request("POST", f"/cart/delete?ids={cart_id}", alice)
                if (deleted["http"], deleted["body"].get("code")) != (200, 200):
                    raise RuntimeError(f"{label}: test cart cleanup failed")
    target = HERE / "measurements" / f"fix-candidate-http-{started:%Y%m%dT%H%M%SZ}.json"
    target.write_text(json.dumps({"kind": "isolated_candidate_http_verification",
        "started_utc": started.isoformat(), "ended_utc": datetime.now(timezone.utc).isoformat(),
        "candidate_url": PORTAL, "server_price_flag_in_candidate": "false",
        "shared_synthetic_lab_database": True, "synthetic_order_and_cart_cleaned": True,
        "expected_sku_price": str(expected), "records": records,
        "limitations": ["Not a GitHub PR or Actions deployment", "No discount scenario here"]},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"file": str(target), "records": records}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
