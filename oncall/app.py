"""Small, local on-call inbox for the backend incident and intrusion detection course.

It ingests Grafana webhook alerts, groups repeat events, and lets students open
redacted incident and analysis records. Decision recording never grants the
service itself code or deployment access.
"""

from __future__ import annotations

from datetime import datetime, timezone
from contextlib import closing
import hmac
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi import Depends


HERE = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("ONCALL_DB", "/data/incidents.sqlite3"))
INGEST_TOKEN = os.environ.get("ONCALL_INGEST_TOKEN", "")
VIEW_USER = os.environ.get("ONCALL_VIEW_USER", "")
VIEW_PASSWORD = os.environ.get("ONCALL_VIEW_PASSWORD", "")
NTFY_URL = os.environ.get("ONCALL_NTFY_URL", "")
NTFY_TOKEN = os.environ.get("ONCALL_NTFY_TOKEN", "")
NTFY_TOPIC = os.environ.get("ONCALL_NTFY_TOPIC", "sre-lab")
ANALYSIS_TOKEN = os.environ.get("ONCALL_ANALYSIS_TOKEN", "")
CLICK_BASE_URL = os.environ.get("ONCALL_CLICK_BASE_URL", "http://127.0.0.1:18090").rstrip("/")
CSRF_SECRET = os.environ.get("ONCALL_CSRF_SECRET", "")

app = FastAPI(title="On-call inbox", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
basic = HTTPBasic()


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS incidents (
        id INTEGER PRIMARY KEY,
        origin TEXT NOT NULL,
        kind TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        route TEXT NOT NULL,
        title TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT '조사 대기',
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL,
        count INTEGER NOT NULL DEFAULT 1,
        trace_id TEXT NOT NULL DEFAULT '',
        evidence_url TEXT NOT NULL DEFAULT '',
        assessment TEXT NOT NULL,
        unknown TEXT NOT NULL,
        next_action TEXT NOT NULL,
        UNIQUE(origin, kind, fingerprint, route)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS incident_events (
        id INTEGER PRIMARY KEY,
        incident_id INTEGER NOT NULL REFERENCES incidents(id),
        occurred_at TEXT NOT NULL,
        trace_id TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL,
        UNIQUE(incident_id, occurred_at, trace_id, source)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS incident_analyses (
        incident_id INTEGER PRIMARY KEY REFERENCES incidents(id),
        analysis_kind TEXT NOT NULL,
        observations TEXT NOT NULL,
        hypotheses TEXT NOT NULL,
        unknowns TEXT NOT NULL,
        evidence_refs TEXT NOT NULL,
        next_action TEXT NOT NULL,
        author TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS incident_decisions (
        id INTEGER PRIMARY KEY,
        incident_id INTEGER NOT NULL REFERENCES incidents(id),
        decision TEXT NOT NULL,
        decided_at TEXT NOT NULL,
        actor TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        UNIQUE(incident_id, decision)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS incident_verifications (
        incident_id INTEGER PRIMARY KEY REFERENCES incidents(id),
        recorded_at TEXT NOT NULL,
        actor TEXT NOT NULL,
        before_result TEXT NOT NULL,
        after_result TEXT NOT NULL,
        observation_minutes INTEGER NOT NULL,
        evidence_ref TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS notification_deliveries (
        incident_id INTEGER PRIMARY KEY REFERENCES incidents(id),
        delivered_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS shadow_alerts (
        id INTEGER PRIMARY KEY,
        rule_name TEXT NOT NULL,
        actor TEXT NOT NULL,
        starts_at TEXT NOT NULL,
        received_at TEXT NOT NULL,
        observed_value TEXT NOT NULL,
        UNIQUE(rule_name, actor, starts_at)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS analysis_jobs (
        incident_id INTEGER PRIMARY KEY REFERENCES incidents(id),
        state TEXT NOT NULL DEFAULT 'pending',
        backend TEXT NOT NULL DEFAULT '',
        claimed_at TEXT NOT NULL DEFAULT '',
        finished_at TEXT NOT NULL DEFAULT '',
        error_code TEXT NOT NULL DEFAULT ''
    )""")
    return conn


def require_view(credentials: HTTPBasicCredentials = Depends(basic)) -> None:
    if not VIEW_USER or not VIEW_PASSWORD:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "view credentials are not configured")
    if not (hmac.compare_digest(credentials.username, VIEW_USER)
            and hmac.compare_digest(credentials.password, VIEW_PASSWORD)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials",
                            headers={"WWW-Authenticate": "Basic"})


def require_ingest(request: Request) -> None:
    if not INGEST_TOKEN:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "ingest token is not configured")
    header = request.headers.get("Authorization", "")
    if not hmac.compare_digest(header, "Bearer " + INGEST_TOKEN):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid ingest token")


def require_analysis(request: Request) -> None:
    if not ANALYSIS_TOKEN:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "analysis token is not configured")
    if not hmac.compare_digest(request.headers.get("Authorization", ""), "Bearer " + ANALYSIS_TOKEN):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid analysis token")


def tidy(value, maximum=120) -> str:
    return re.sub(r"[\r\n\t]+", " ", str(value or "")).strip()[:maximum]


def timestamp(value) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("missing timezone")
        return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (ValueError, TypeError):
        return datetime.now(timezone.utc).isoformat(timespec="seconds")


def evidence_url(value) -> str:
    url = tidy(value, 500)
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme in ("http", "https") and parsed.netloc in ("127.0.0.1:13030", "localhost:13030"):
        return url
    return ""


def normalize_simple(item: dict, source="lab") -> dict:
    kind = tidy(item.get("kind"), 30)
    if kind not in ("error_500", "security_signal"):
        raise HTTPException(422, "kind must be error_500 or security_signal")
    fingerprint = tidy(item.get("fingerprint"), 100)
    if not fingerprint:
        raise HTTPException(422, "fingerprint is required")
    route = tidy(item.get("route"), 100) or "미확인"
    trace = tidy(item.get("trace_id"), 100)
    title = ("500 오류 · " if kind == "error_500" else "접근 거부 조사 · ") + route
    return {"kind": kind, "fingerprint": fingerprint, "route": route, "trace_id": trace,
            "title": title, "occurred_at": timestamp(item.get("occurred_at")),
            "evidence_url": evidence_url(item.get("evidence_url")), "source": source,
            "origin": "grafana" if source == "grafana" else "lab_replay" if source == "lab_replay" else "lab_manual"}


def normalize_grafana(payload: dict) -> list[dict]:
    events = []
    for alert in payload.get("alerts", []):
        if alert.get("status", payload.get("status")) != "firing":
            continue
        labels = alert.get("labels") or {}
        if labels.get("sre_mode") == "shadow":
            continue
        annotations = alert.get("annotations") or {}
        kind = labels.get("sre_kind")
        if kind not in ("error_500", "security_signal"):
            continue
        events.append(normalize_simple({
            "kind": kind,
            "fingerprint": labels.get("fingerprint") or labels.get("alertname"),
            "route": labels.get("route") or "미확인",
            "trace_id": annotations.get("trace_id"),
            "occurred_at": alert.get("startsAt"),
            "evidence_url": alert.get("generatorURL"),
        }, source="grafana"))
    return events


def publish_message(incident_id: int, title: str, route: str) -> bool:
    if not NTFY_URL:
        return False
    body = f"{title} · {route} · 사건 #{incident_id} 열기"
    headers = {"Content-Type": "application/json"}
    if NTFY_TOKEN:
        headers["Authorization"] = "Bearer " + NTFY_TOKEN
    payload = {"topic": NTFY_TOPIC, "title": title, "message": body,
               "click": f"{CLICK_BASE_URL}/incidents/{incident_id}"}
    req = urllib.request.Request(NTFY_URL, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=3):
            pass
        return True
    except (urllib.error.URLError, TimeoutError) as exc:
        # Receipt remains usable even when a notification transport is down.
        print(f"ntfy delivery failed for incident {incident_id}: {type(exc).__name__}", flush=True)
        return False


def publish(incident_id: int, event: dict) -> None:
    with closing(db()) as conn:
        if conn.execute("SELECT 1 FROM notification_deliveries WHERE incident_id=?", (incident_id,)).fetchone():
            return
    title = "장애 경보" if event["kind"] == "error_500" else "보안 조사 신호"
    if publish_message(incident_id, title, event["route"]):
        with closing(db()) as conn, conn:
            conn.execute("INSERT OR IGNORE INTO notification_deliveries (incident_id, delivered_at) VALUES (?, ?)",
                         (incident_id, datetime.now(timezone.utc).isoformat()))


def store(event: dict) -> tuple[int, bool]:
    with closing(db()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT id FROM incidents WHERE origin=? AND kind=? AND fingerprint=? AND route=?",
                                (event["origin"], event["kind"], event["fingerprint"], event["route"])).fetchone()
        if existing:
            incident_id = existing["id"]
            inserted = conn.execute("""INSERT OR IGNORE INTO incident_events
                (incident_id, occurred_at, trace_id, source) VALUES (?, ?, ?, ?)""",
                (incident_id, event["occurred_at"], event["trace_id"], event["source"])).rowcount
            if inserted:
                conn.execute("""UPDATE incidents SET count=count+1,
                    first_seen=min(first_seen, ?), last_seen=max(last_seen, ?),
                    trace_id=CASE WHEN ?!='' THEN ? ELSE trace_id END,
                    evidence_url=CASE WHEN ?!='' THEN ? ELSE evidence_url END
                    WHERE id=?""", (event["occurred_at"], event["occurred_at"],
                                    event["trace_id"], event["trace_id"],
                                    event["evidence_url"], event["evidence_url"], incident_id))
            created = False
        else:
            assessment = "원인 분석 대기" if event["kind"] == "error_500" else "조사할 신호 · 침해 여부 미확인"
            unknown = "같은 입력의 재현 결과와 코드 경로" if event["kind"] == "error_500" else "실제 허용 여부와 다른 경로의 결함"
            action = "원문·추적 ID 확인 후 RCA 작성" if event["kind"] == "error_500" else "원문·반증을 확인하고 결함 시험 제안"
            cur = conn.execute("""INSERT INTO incidents
                (origin, kind, fingerprint, route, title, first_seen, last_seen, trace_id,
                 evidence_url, assessment, unknown, next_action)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (event["origin"], event["kind"], event["fingerprint"], event["route"], event["title"],
                 event["occurred_at"], event["occurred_at"], event["trace_id"],
                 event["evidence_url"], assessment, unknown, action))
            incident_id = cur.lastrowid
            conn.execute("""INSERT INTO incident_events (incident_id, occurred_at, trace_id, source)
                            VALUES (?, ?, ?, ?)""",
                         (incident_id, event["occurred_at"], event["trace_id"], event["source"]))
            created = True
    publish(incident_id, event)
    return incident_id, created


@app.get("/healthz")
def healthz():
    with closing(db()) as conn:
        conn.execute("SELECT 1")
    return {"ok": True}


@app.post("/api/events")
async def ingest_event(request: Request):
    require_ingest(request)
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(422, "expected object")
    source = "lab_replay" if payload.get("source") == "lab_replay" else "lab_manual"
    incident_id, created = store(normalize_simple(payload, source=source))
    return {"incident_id": incident_id, "created": created}


@app.post("/api/grafana")
async def ingest_grafana(request: Request):
    require_ingest(request)
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(422, "expected object")
    results = []
    for event in normalize_grafana(payload):
        incident_id, created = store(event)
        results.append({"incident_id": incident_id, "created": created})
    return {"events": results}


@app.post("/api/grafana/shadow")
async def ingest_grafana_shadow(request: Request):
    require_ingest(request)
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(422, "expected object")
    recorded = 0
    with closing(db()) as conn, conn:
        for alert in payload.get("alerts", []):
            if not isinstance(alert, dict) or alert.get("status", payload.get("status")) != "firing":
                continue
            labels = alert.get("labels") or {}
            if labels.get("sre_mode") != "shadow":
                continue
            rule = tidy(labels.get("alertname"), 100)
            actor = tidy(labels.get("actor"), 100)
            if not rule or not actor:
                continue
            values = alert.get("values") or {}
            observed = tidy(values.get("A") if isinstance(values, dict) else "", 40)
            recorded += conn.execute("""INSERT OR IGNORE INTO shadow_alerts
                (rule_name, actor, starts_at, received_at, observed_value)
                VALUES (?, ?, ?, ?, ?)""",
                (rule, actor, timestamp(alert.get("startsAt")),
                 datetime.now(timezone.utc).isoformat(timespec="seconds"), observed)).rowcount
    return {"recorded": recorded, "incident_created": False, "notification_sent": False}


@app.get("/api/shadow-alerts")
def shadow_alerts(_: None = Depends(require_view)):
    with closing(db()) as conn:
        rows = [dict(row) for row in conn.execute("""SELECT rule_name, actor, starts_at,
            received_at, observed_value FROM shadow_alerts ORDER BY id DESC LIMIT 100""")]
    return {"alerts": rows}


@app.get("/", response_class=HTMLResponse)
def incident_list(request: Request, _: None = Depends(require_view)):
    with closing(db()) as conn:
        incidents = [dict(row) for row in conn.execute("SELECT * FROM incidents ORDER BY last_seen DESC, id DESC")]
    return templates.TemplateResponse(request, "list.html", {"incidents": incidents})


def get_incident(incident_id: int) -> dict:
    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
    if not row:
        raise HTTPException(404, "incident not found")
    return dict(row)


def get_analysis(incident_id: int) -> dict | None:
    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM incident_analyses WHERE incident_id=?", (incident_id,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    for name in ("observations", "hypotheses", "unknowns", "evidence_refs"):
        result[name] = json.loads(result[name])
    return result


def get_analysis_job(incident_id: int) -> dict | None:
    with closing(db()) as conn:
        row = conn.execute("""SELECT state, backend, claimed_at, finished_at
            FROM analysis_jobs WHERE incident_id=?""", (incident_id,)).fetchone()
    return dict(row) if row else None


@app.post("/api/analysis-jobs/claim")
async def claim_analysis_job(request: Request):
    require_analysis(request)
    payload = await request.json()
    backend = payload.get("backend") if isinstance(payload, dict) else None
    requested = payload.get("incident_id") if isinstance(payload, dict) else None
    if backend not in ("cli", "api") or (requested is not None and (type(requested) is not int or requested < 1)):
        raise HTTPException(422, "backend=cli|api and a positive incident_id are required")
    with closing(db()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""INSERT OR IGNORE INTO analysis_jobs (incident_id)
            SELECT i.id FROM incidents i LEFT JOIN incident_analyses a ON a.incident_id=i.id
            WHERE a.incident_id IS NULL""")
        row = conn.execute("""SELECT i.* FROM incidents i JOIN analysis_jobs j ON j.incident_id=i.id
            LEFT JOIN incident_analyses a ON a.incident_id=i.id
            WHERE j.state='pending' AND a.incident_id IS NULL
              AND NOT EXISTS (SELECT 1 FROM incident_decisions d
                              WHERE d.incident_id=i.id AND d.decision='approve_fix')
              AND (? IS NULL OR i.id=?) ORDER BY i.id LIMIT 1""", (requested, requested)).fetchone()
        if row is None:
            return {"job": None}
        incident_id = row["id"]
        conn.execute("""UPDATE analysis_jobs SET state='running', backend=?, claimed_at=?,
            finished_at='', error_code='' WHERE incident_id=?""",
            (backend, datetime.now(timezone.utc).isoformat(timespec="seconds"), incident_id))
        events = [dict(item) for item in conn.execute("""SELECT occurred_at, trace_id, source
            FROM incident_events WHERE incident_id=? ORDER BY id DESC LIMIT 5""", (incident_id,))]
    incident = {key: row[key] for key in ("id", "origin", "kind", "fingerprint", "route",
                                          "first_seen", "last_seen", "count", "trace_id")}
    return {"job": {"incident": incident, "events": events, "backend": backend}}


@app.post("/api/analysis-jobs/{incident_id}/finish")
async def finish_analysis_job(request: Request, incident_id: int):
    require_analysis(request)
    payload = await request.json()
    state = payload.get("state") if isinstance(payload, dict) else None
    code = payload.get("error_code", "") if isinstance(payload, dict) else ""
    if state not in ("complete", "failed") or not isinstance(code, str) or len(code) > 80:
        raise HTTPException(422, "state=complete|failed and a short error_code are required")
    with closing(db()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT state FROM analysis_jobs WHERE incident_id=?", (incident_id,)).fetchone()
        if row is None or row["state"] != "running":
            raise HTTPException(409, "job is not running")
        if state == "complete" and not conn.execute(
                "SELECT 1 FROM incident_analyses WHERE incident_id=?", (incident_id,)).fetchone():
            raise HTTPException(409, "analysis report is missing")
        conn.execute("""UPDATE analysis_jobs SET state=?, finished_at=?, error_code=?
            WHERE incident_id=?""", (state, datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                      tidy(code, 80) if state == "failed" else "", incident_id))
    return {"incident_id": incident_id, "state": state}


@app.post("/api/analysis-jobs/{incident_id}/retry")
async def retry_analysis_job(request: Request, incident_id: int):
    require_analysis(request)
    with closing(db()) as conn, conn:
        changed = conn.execute("""UPDATE analysis_jobs SET state='pending', backend='',
            claimed_at='', finished_at='', error_code=''
            WHERE incident_id=? AND state='failed' AND NOT EXISTS
            (SELECT 1 FROM incident_analyses WHERE incident_id=?)""",
            (incident_id, incident_id)).rowcount
        if not changed:
            raise HTTPException(409, "only failed jobs without a report can be retried")
    return {"incident_id": incident_id, "state": "pending"}


def decisions_for(incident_id: int) -> list[dict]:
    with closing(db()) as conn:
        return [dict(row) for row in conn.execute(
            "SELECT decision, decided_at, actor, note FROM incident_decisions WHERE incident_id=? ORDER BY id",
            (incident_id,))]


def verification_for(incident_id: int) -> dict | None:
    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM incident_verifications WHERE incident_id=?", (incident_id,)).fetchone()
    return dict(row) if row else None


def csrf_token(incident_id: int) -> str:
    if not CSRF_SECRET:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "CSRF secret is not configured")
    return hmac.new(CSRF_SECRET.encode(), f"{VIEW_USER}:{incident_id}".encode(), hashlib.sha256).hexdigest()


def bounded_lines(value, field: str) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 20:
        raise HTTPException(422, f"{field} must contain 1..20 lines")
    lines = []
    for line in value:
        if not isinstance(line, str) or not 1 <= len(line.strip()) <= 500:
            raise HTTPException(422, f"{field} contains an empty or overlong line")
        lines.append(tidy(line, 500))
    return lines


@app.post("/api/incidents/{incident_id}/analysis")
async def attach_analysis(request: Request, incident_id: int):
    require_analysis(request)
    incident = get_incident(incident_id)
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(422, "expected object")
    analysis_kind = payload.get("analysis_kind")
    expected = "rca_500" if incident["kind"] == "error_500" else "security_triage"
    if analysis_kind != expected:
        raise HTTPException(422, f"analysis_kind must be {expected}")
    fields = {name: bounded_lines(payload.get(name), name)
              for name in ("observations", "hypotheses", "unknowns", "evidence_refs")}
    action = tidy(payload.get("next_action"), 500)
    author = tidy(payload.get("author"), 40)
    if not action or author not in ("agent", "human"):
        raise HTTPException(422, "next_action and author=agent|human are required")
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(db()) as conn, conn:
        approved = conn.execute("""SELECT 1 FROM incident_decisions
            WHERE incident_id=? AND decision='approve_fix'""", (incident_id,)).fetchone()
        if approved:
            raise HTTPException(409, "approved analysis cannot be replaced")
        conn.execute("""INSERT INTO incident_analyses
            (incident_id, analysis_kind, observations, hypotheses, unknowns, evidence_refs,
             next_action, author, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(incident_id) DO UPDATE SET
            analysis_kind=excluded.analysis_kind, observations=excluded.observations,
            hypotheses=excluded.hypotheses, unknowns=excluded.unknowns,
            evidence_refs=excluded.evidence_refs, next_action=excluded.next_action,
            author=excluded.author, created_at=excluded.created_at""",
            (incident_id, analysis_kind, *(json.dumps(fields[name], ensure_ascii=False)
              for name in ("observations", "hypotheses", "unknowns", "evidence_refs")),
             action, author, created_at))
    return {"incident_id": incident_id, "analysis_kind": analysis_kind,
            "created_at": created_at, "export_url": f"/incidents/{incident_id}/rca.md"}


@app.get("/incidents/{incident_id}", response_class=HTMLResponse)
def incident_detail(request: Request, incident_id: int, _: None = Depends(require_view)):
    return templates.TemplateResponse(request, "detail.html", {
        "incident": get_incident(incident_id), "analysis": get_analysis(incident_id),
        "analysis_job": get_analysis_job(incident_id),
        "decisions": decisions_for(incident_id), "verification": verification_for(incident_id),
        "csrf_token": csrf_token(incident_id)})


@app.post("/incidents/{incident_id}/decision")
async def record_decision(request: Request, incident_id: int, _: None = Depends(require_view)):
    incident = get_incident(incident_id)
    body = await request.body()
    if len(body) > 4096:
        raise HTTPException(413, "form is too large")
    form = urllib.parse.parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
    token = (form.get("csrf_token") or [""])[0]
    if not hmac.compare_digest(token, csrf_token(incident_id)):
        raise HTTPException(403, "invalid CSRF token")
    decision = (form.get("decision") or [""])[0]
    if decision not in ("hold", "confirm_defect", "approve_fix"):
        raise HTTPException(422, "decision must be hold, confirm_defect or approve_fix")
    note = tidy((form.get("note") or [""])[0], 300)
    if decision == "confirm_defect":
        if incident["kind"] != "security_signal" or get_analysis(incident_id) is None:
            raise HTTPException(409, "security triage is required before defect confirmation")
        if len(note) < 15 or not re.search(r"[A-Za-z0-9_-]+\.(json|md|py|java)(?::[0-9]+)?", note):
            raise HTTPException(422, "confirmation note must cite a saved test or source file")
    if decision == "approve_fix":
        if get_analysis(incident_id) is None:
            raise HTTPException(409, "RCA report is required before approval")
        if incident["kind"] == "security_signal":
            with closing(db()) as conn:
                confirmed = conn.execute("""SELECT 1 FROM incident_decisions
                    WHERE incident_id=? AND decision='confirm_defect'""", (incident_id,)).fetchone()
            if not confirmed:
                raise HTTPException(409, "security signal requires a separate confirmed-defect review")
    decided_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(db()) as conn, conn:
        already_approved = conn.execute("""SELECT 1 FROM incident_decisions
            WHERE incident_id=? AND decision='approve_fix'""", (incident_id,)).fetchone()
        if already_approved and decision != "approve_fix":
            raise HTTPException(409, "approved incident cannot receive another decision")
        inserted = conn.execute("""INSERT OR IGNORE INTO incident_decisions
            (incident_id, decision, decided_at, actor, note) VALUES (?, ?, ?, ?, ?)""",
            (incident_id, decision, decided_at, VIEW_USER, note)).rowcount
        if inserted:
            state = {"approve_fix": "수정 승인", "confirm_defect": "결함 확인", "hold": "보류"}[decision]
            conn.execute("UPDATE incidents SET state=? WHERE id=?", (state, incident_id))
    return RedirectResponse(f"/incidents/{incident_id}", status_code=303)


@app.post("/incidents/{incident_id}/verification")
async def record_verification(request: Request, incident_id: int, _: None = Depends(require_view)):
    incident = get_incident(incident_id)
    body = await request.body()
    if len(body) > 4096:
        raise HTTPException(413, "form is too large")
    form = urllib.parse.parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
    token = (form.get("csrf_token") or [""])[0]
    if not hmac.compare_digest(token, csrf_token(incident_id)):
        raise HTTPException(403, "invalid CSRF token")
    approved = next((row for row in decisions_for(incident_id) if row["decision"] == "approve_fix"), None)
    if not approved:
        raise HTTPException(409, "fix approval is required before verification")
    before = tidy((form.get("before_result") or [""])[0], 40)
    after = tidy((form.get("after_result") or [""])[0], 40)
    evidence = tidy((form.get("evidence_ref") or [""])[0], 300)
    try:
        minutes = int((form.get("observation_minutes") or [""])[0])
    except ValueError:
        minutes = -1
    if not re.fullmatch(r"[1-5][0-9]{2}/[1-5][0-9]{2}", before) or not re.fullmatch(
            r"[1-5][0-9]{2}/[1-5][0-9]{2}", after):
        raise HTTPException(422, "before/after must be HTTP/body-code pairs")
    if not 15 <= minutes <= 120 or not re.search(r"[A-Za-z0-9_-]+\.(json|md)(?::[0-9]+)?", evidence):
        raise HTTPException(422, "15..120 observation minutes and a saved evidence file are required")
    if incident["kind"] == "error_500" and (before != "500/500" or after != "200/200"):
        raise HTTPException(422, "500 incident must show 500/500 to 200/200")
    if incident["kind"] == "security_signal" and (before != "200/200" or after != "200/500"):
        raise HTTPException(422, "return defect must show 200/200 to 200/500")
    recorded = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(db()) as conn, conn:
        inserted = conn.execute("""INSERT OR IGNORE INTO incident_verifications
            (incident_id, recorded_at, actor, before_result, after_result, observation_minutes, evidence_ref)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (incident_id, recorded, VIEW_USER, before, after, minutes, evidence)).rowcount
        if inserted:
            conn.execute("UPDATE incidents SET state='확인 완료' WHERE id=?", (incident_id,))
    if not inserted:
        raise HTTPException(409, "verification already recorded")
    publish_message(incident_id, "확인 완료", incident["route"])
    return RedirectResponse(f"/incidents/{incident_id}", status_code=303)


@app.get("/api/approvals/{incident_id}")
def approval_state(request: Request, incident_id: int):
    require_analysis(request)
    get_incident(incident_id)
    rows = decisions_for(incident_id)
    approved = next((row for row in rows if row["decision"] == "approve_fix"), None)
    confirmed = next((row for row in rows if row["decision"] == "confirm_defect"), None)
    return {"incident_id": incident_id, "approved": approved is not None,
            "approved_at": approved["decided_at"] if approved else None,
            "confirmed_defect": confirmed is not None,
            "analysis_present": get_analysis(incident_id) is not None}


@app.get("/incidents/{incident_id}/rca.md", response_class=PlainTextResponse)
def analysis_report(incident_id: int, _: None = Depends(require_view)):
    incident = get_incident(incident_id)
    analysis = get_analysis(incident_id)
    if analysis is None:
        raise HTTPException(404, "analysis not available")
    sections = [("관측 사실", analysis["observations"]),
                ("원인 가설·판단", analysis["hypotheses"]),
                ("아직 모르는 것", analysis["unknowns"]),
                ("근거 위치", analysis["evidence_refs"])]
    lines = [f"# 사건 #{incident_id} · {incident['title']}", "",
             f"- 작성 시각(UTC): {analysis['created_at']}",
             f"- 작성자 유형: {analysis['author']}",
             f"- 분석 유형: {analysis['analysis_kind']}",
             f"- 추적 ID: {incident['trace_id'] or '미확인'}", ""]
    for heading, values in sections:
        lines.extend([f"## {heading}", "", *(f"- {value}" for value in values), ""])
    lines.extend(["## 다음 행동", "", analysis["next_action"], "",
                  "이 분석은 접수·조사 자료입니다. 코드 수정·침해 확정·배포 완료의 증거가 아닙니다.", ""])
    return PlainTextResponse("\n".join(lines), media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="rca-{incident_id}.md"'})


@app.get("/incidents/{incident_id}/report.md", response_class=PlainTextResponse)
def incident_report(incident_id: int, _: None = Depends(require_view)):
    incident = get_incident(incident_id)
    lines = [f"# 사건 #{incident_id} · {incident['title']}", "", "## 관측 사실", "",
             f"- 상태: {incident['state']}", f"- 출처: {incident['origin']}",
             f"- 첫 접수 신호: {incident['first_seen']}",
             f"- 최근 접수 신호: {incident['last_seen']}", f"- 접수된 알림 수: {incident['count']}",
             f"- 경로: {incident['route']}", f"- 지문: {incident['fingerprint']}",
             f"- 추적 ID: {incident['trace_id'] or '미확인'}",
             f"- 원문 링크: {incident['evidence_url'] or '미확인'}", "",
             "## 현재 판단", "", incident["assessment"], "", "## 아직 모르는 것", "",
             incident["unknown"], "", "## 다음 행동", "", incident["next_action"], "",
             "알림 수는 원본 로그 건수가 아닙니다.",
             "이 파일은 사건 접수 단계의 기록입니다. RCA나 침해 확정 보고서가 아닙니다.", ""]
    return PlainTextResponse("\n".join(lines), media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="incident-{incident_id}.md"'})
