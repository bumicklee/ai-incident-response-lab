"""Check the copied app's real login without printing the bearer token."""

import json
import os
import urllib.error
import urllib.parse
import urllib.request


def main() -> int:
    port = os.environ.get("LAB_PORTAL_PORT", "18085")
    body = urllib.parse.urlencode({"username": "alice", "password": "daon-pass-1!"}).encode()
    request = urllib.request.Request(f"http://127.0.0.1:{port}/sso/login", data=body, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = {}
    token = (parsed.get("data") or {}).get("token")
    print({"http_status": status, "body_code": parsed.get("code"), "message": parsed.get("message"), "token_issued": bool(token)})
    return 0 if status == 200 and parsed.get("code") == 200 and token else 1


if __name__ == "__main__":
    raise SystemExit(main())
