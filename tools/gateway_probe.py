"""Prove one synthetic request appears in gateway and app-security log streams."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from run_stages import HERE, loki_entries


GATEWAY = "http://127.0.0.1:" + os.environ.get("LAB_GATEWAY_PORT", "18086")


def call(path: str, *, method="GET", token="", trace="", form: dict | None = None) -> dict:
    headers = {"X-Trace-Id": trace} if trace else {}
    if token:
        headers["Authorization"] = token
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(GATEWAY + path, data=data, headers=headers, method=method)
    try:
        response = urllib.request.urlopen(req, timeout=15)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return {"http_status": response.status,
                "body": json.loads(response.read()),
                "response_trace_id": response.headers.get("X-Trace-Id", "")}


def main() -> int:
    started_ns = time.time_ns()
    login = call("/sso/login", method="POST", form={"username": "bob", "password": "daon-pass-1!"})
    token = (login["body"].get("data") or {}).get("token")
    if login["http_status"] != 200 or not token:
        raise SystemExit("가짜 회원 Bob 로그인 실패")
    trace = "sre-gateway-" + uuid.uuid4().hex[:12]
    result = call("/order/detail/9001", token="Bearer " + token, trace=trace)
    if (result["http_status"], result["body"].get("code")) != (200, 500):
        raise SystemExit("타인 주문 거부의 HTTP 200 / 본문 500을 확인하지 못했습니다")
    time.sleep(4)
    ended_ns = time.time_ns()
    gateway_lines = loki_entries('{app="gateway"} |= "' + trace + '"', started_ns, ended_ns)
    security_lines = loki_entries('{app="mall-portal",stream="security",event="access.denied.not_owner"} |= "' + trace + '"',
                                  started_ns, ended_ns)
    gateway_events = []
    for row in gateway_lines:
        try:
            value = json.loads(row["line"])
        except ValueError:
            continue
        gateway_events.append({key: value.get(key) for key in ("source", "method", "path", "http_status", "trace_id")})
    app_events = []
    for row in security_lines:
        try:
            value = json.loads(row["line"])
        except ValueError:
            continue
        app_events.append({key: value.get(key) for key in ("event", "target", "trace_id")})
    matched = (bool(gateway_events) and bool(app_events)
               and all(item["trace_id"] == trace for item in gateway_events + app_events)
               and result["response_trace_id"] == trace)
    output = {"kind": "two_source_trace_correlation", "recorded_utc": datetime.now(timezone.utc).isoformat(),
              "synthetic_actor": "bob", "request_path": "/order/detail/9001",
              "http_status": result["http_status"], "body_code": result["body"].get("code"),
              "trace_id": trace, "gateway_events": gateway_events, "app_security_events": app_events,
              "same_trace_proven": matched,
              "limits": ["One denied request is a security investigation signal, not evidence of compromise.",
                         "Gateway client IP is not used for identity or correlation.",
                         "The response is HTTP 200 with body code 500; count both separately."]}
    target = HERE / "measurements" / f"gateway-correlation-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"file": str(target), "same_trace_proven": matched,
                      "gateway_lines": len(gateway_events), "app_security_lines": len(app_events)}, ensure_ascii=False))
    return 0 if matched else 2


if __name__ == "__main__":
    raise SystemExit(main())
