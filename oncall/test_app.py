"""Meaningful contract checks for incident grouping and access control."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import json
import urllib.error

from fastapi.testclient import TestClient


class InboxContractTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        os.environ["ONCALL_DB"] = str(Path(self.temp.name) / "incidents.sqlite3")
        os.environ["ONCALL_INGEST_TOKEN"] = "test-ingest-token"
        os.environ["ONCALL_ANALYSIS_TOKEN"] = "test-analysis-token"
        os.environ["ONCALL_CSRF_SECRET"] = "test-csrf-secret"
        os.environ["ONCALL_VIEW_USER"] = "viewer"
        os.environ["ONCALL_VIEW_PASSWORD"] = "test-view-password"
        os.environ.pop("ONCALL_NTFY_URL", None)
        import app as module
        module.DB_PATH = Path(os.environ["ONCALL_DB"])
        module.INGEST_TOKEN = os.environ["ONCALL_INGEST_TOKEN"]
        module.ANALYSIS_TOKEN = os.environ["ONCALL_ANALYSIS_TOKEN"]
        module.CSRF_SECRET = os.environ["ONCALL_CSRF_SECRET"]
        module.VIEW_USER = os.environ["ONCALL_VIEW_USER"]
        module.VIEW_PASSWORD = os.environ["ONCALL_VIEW_PASSWORD"]
        module.NTFY_URL = ""
        module.CLICK_BASE_URL = "http://127.0.0.1:18090"
        self.client = TestClient(module.app)
        self.headers = {"Authorization": "Bearer test-ingest-token"}

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_auth_dedup_and_report(self):
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 401)
        self.assertEqual(self.client.post("/api/events", json={}).status_code, 401)
        event = {"kind": "error_500", "fingerprint": "NullPointerException:order",
                 "route": "/order/generateOrder", "trace_id": "sre-test-1",
                 "occurred_at": "2026-11-14T10:00:00+09:00"}
        first = self.client.post("/api/events", json=event, headers=self.headers)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), {"incident_id": 1, "created": True})
        repeat = self.client.post("/api/events", json=event, headers=self.headers)
        self.assertEqual(repeat.json(), {"incident_id": 1, "created": False})
        event["occurred_at"] = "2026-11-14T10:01:00+09:00"
        again = self.client.post("/api/events", json=event, headers=self.headers)
        self.assertEqual(again.json(), {"incident_id": 1, "created": False})
        detail = self.client.get("/incidents/1", auth=("viewer", "test-view-password"))
        self.assertEqual(detail.status_code, 200)
        self.assertIn("접수된 알림 2건", detail.text)
        self.assertIn("장애 경보", detail.text)
        report = self.client.get("/incidents/1/report.md", auth=("viewer", "test-view-password"))
        self.assertEqual(report.status_code, 200)
        self.assertIn("사건 접수 단계의 기록", report.text)
        self.assertIn("2026-11-14T01:00:00+00:00", report.text)

    def test_grafana_security_signal_stays_investigative(self):
        payload = {"status": "firing", "alerts": [{"status": "firing",
                   "startsAt": "2026-11-14T10:00:00+09:00",
                   "labels": {"sre_kind": "security_signal", "alertname": "ForeignOrderDenied",
                              "route": "/order/detail/9001"},
                   "annotations": {"trace_id": "sre-test-sec"}}]}
        result = self.client.post("/api/grafana", json=payload, headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["events"][0]["incident_id"], 1)
        detail = self.client.get("/incidents/1", auth=("viewer", "test-view-password"))
        self.assertIn("보안 조사 신호", detail.text)
        self.assertIn("침해 여부 미확인", detail.text)
        payload["status"] = "resolved"
        payload["alerts"][0]["status"] = "resolved"
        self.assertEqual(self.client.post("/api/grafana", json=payload, headers=self.headers).json()["events"], [])

    def test_ntfy_payload_uses_utf8_json_and_incident_link(self):
        import app as module
        module.NTFY_URL = "http://ntfy/"
        module.NTFY_TOPIC = "sre-lab"
        module.NTFY_TOKEN = "test-publish-token"
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
        with patch.object(module.urllib.request, "urlopen", return_value=Response()) as send:
            result = self.client.post("/api/events", json={
                "kind": "security_signal", "fingerprint": "access.denied.not_owner",
                "route": "/order/detail/9001", "occurred_at": "2026-11-14T10:00:00+09:00",
            }, headers=self.headers)
            self.assertEqual(result.status_code, 200)
            sent = send.call_args.args[0]
            self.assertEqual(sent.full_url, "http://ntfy/")
            self.assertEqual(sent.headers["Authorization"], "Bearer test-publish-token")
            payload = json.loads(sent.data.decode("utf-8"))
            self.assertEqual(payload["title"], "보안 조사 신호")
            self.assertEqual(payload["topic"], "sre-lab")
            self.assertEqual(payload["click"], "http://127.0.0.1:18090/incidents/1")

    def test_failed_ntfy_delivery_retries_on_repeat_without_new_incident(self):
        import app as module
        module.NTFY_URL = "http://ntfy/"
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
        event = {"kind": "error_500", "fingerprint": "error.unhandled.order",
                 "route": "/order/generateOrder", "occurred_at": "2026-11-14T10:00:00+09:00"}
        with patch.object(module.urllib.request, "urlopen",
                          side_effect=[urllib.error.URLError("offline"), Response()]) as send:
            first = self.client.post("/api/events", json=event, headers=self.headers)
            self.assertTrue(first.json()["created"])
            event["occurred_at"] = "2026-11-14T10:01:00+09:00"
            repeat = self.client.post("/api/events", json=event, headers=self.headers)
            self.assertFalse(repeat.json()["created"])
            self.assertEqual(repeat.json()["incident_id"], first.json()["incident_id"])
            self.assertEqual(send.call_count, 2)
            event["occurred_at"] = "2026-11-14T10:02:00+09:00"
            self.client.post("/api/events", json=event, headers=self.headers)
            self.assertEqual(send.call_count, 2)

    def test_analysis_requires_separate_token_and_exports_evidence(self):
        event = {"kind": "error_500", "fingerprint": "NullPointerException:order",
                 "route": "/order/generateOrder", "trace_id": "sre-test-rca"}
        self.client.post("/api/events", json=event, headers=self.headers)
        analysis = {"analysis_kind": "rca_500", "author": "agent",
                    "observations": ["가격 없는 주문은 HTTP 500이었다"],
                    "hypotheses": ["가격 보완이 빠진 경로가 있다"],
                    "unknowns": ["정확한 Java 스택 행은 미확인"],
                    "evidence_refs": ["trace_id=sre-test-rca"],
                    "next_action": "사람이 재현 시험을 검토한다"}
        url = "/api/incidents/1/analysis"
        self.assertEqual(self.client.post(url, json=analysis, headers=self.headers).status_code, 401)
        self.assertEqual(self.client.get("/incidents/1/rca.md").status_code, 401)
        self.assertEqual(self.client.get("/incidents/1/rca.md", auth=("viewer", "test-view-password")).status_code, 404)
        result = self.client.post(url, json=analysis,
                                  headers={"Authorization": "Bearer test-analysis-token"})
        self.assertEqual(result.status_code, 200)
        report = self.client.get("/incidents/1/rca.md", auth=("viewer", "test-view-password"))
        self.assertEqual(report.status_code, 200)
        self.assertIn("trace_id=sre-test-rca", report.text)
        self.assertIn("정확한 Java 스택 행은 미확인", report.text)
        detail = self.client.get("/incidents/1", auth=("viewer", "test-view-password"))
        self.assertIn("분석 보고서 Markdown 내려받기", detail.text)

        from app import csrf_token
        decision_url = "/incidents/1/decision"
        self.assertEqual(self.client.post(decision_url, data={"decision": "approve_fix"},
                                          auth=("viewer", "test-view-password")).status_code, 403)
        approved = self.client.post(decision_url, data={"decision": "approve_fix",
                                                       "csrf_token": csrf_token(1),
                                                       "note": "재현과 RCA 확인"},
                                    auth=("viewer", "test-view-password"), follow_redirects=False)
        self.assertEqual(approved.status_code, 303)
        state = self.client.get("/api/approvals/1",
                                headers={"Authorization": "Bearer test-analysis-token"})
        self.assertTrue(state.json()["approved"])
        self.assertTrue(state.json()["analysis_present"])
        self.assertEqual(self.client.post(url, json=analysis,
                                          headers={"Authorization": "Bearer test-analysis-token"}).status_code, 409)
        verify_url = "/incidents/1/verification"
        form = {"csrf_token": csrf_token(1), "before_result": "500/500",
                "after_result": "200/200", "observation_minutes": "15",
                "evidence_ref": "measurements/followup-test.json"}
        self.assertEqual(self.client.post(verify_url, data={**form, "csrf_token": "bad"},
                                          auth=("viewer", "test-view-password")).status_code, 403)
        self.assertEqual(self.client.post(verify_url, data={**form, "observation_minutes": "1"},
                                          auth=("viewer", "test-view-password")).status_code, 422)
        self.assertEqual(self.client.post(verify_url, data=form,
                                          auth=("viewer", "test-view-password"),
                                          follow_redirects=False).status_code, 303)
        self.assertIn("확인 완료", self.client.get("/incidents/1",
                                              auth=("viewer", "test-view-password")).text)
        self.assertEqual(self.client.post(verify_url, data=form,
                                          auth=("viewer", "test-view-password")).status_code, 409)

    def test_security_signal_needs_evidence_before_fix_approval(self):
        self.client.post("/api/events", json={"kind": "security_signal",
                         "fingerprint": "access.denied.not_owner", "route": "/order/detail/9001"},
                         headers=self.headers)
        from app import csrf_token
        result = self.client.post("/incidents/1/decision", data={"decision": "approve_fix",
                                  "csrf_token": csrf_token(1)}, auth=("viewer", "test-view-password"))
        self.assertEqual(result.status_code, 409)
        self.assertEqual(self.client.post("/incidents/1/verification",
                                          data={"csrf_token": csrf_token(1), "before_result": "200/200",
                                                "after_result": "200/500", "observation_minutes": "15",
                                                "evidence_ref": "measurements/followup-test.json"},
                                          auth=("viewer", "test-view-password")).status_code, 409)
        analysis = {"analysis_kind": "security_triage", "author": "agent",
                    "observations": ["타인 주문 조회가 거부되었다"],
                    "hypotheses": ["반품 경로는 별도 검사가 필요하다"],
                    "unknowns": ["반품 경로의 실제 허용 여부"],
                    "evidence_refs": ["trace_id=sre-test-sec"],
                    "next_action": "가짜 계정으로 반품 확인 시험"}
        self.assertEqual(self.client.post("/api/incidents/1/analysis", json=analysis,
                                          headers={"Authorization": "Bearer test-analysis-token"}).status_code, 200)
        self.assertEqual(self.client.post("/incidents/1/decision", data={"decision": "approve_fix",
                                          "csrf_token": csrf_token(1)},
                                          auth=("viewer", "test-view-password")).status_code, 409)
        self.assertEqual(self.client.post("/incidents/1/decision", data={"decision": "confirm_defect",
                                          "csrf_token": csrf_token(1), "note": "느낌상 문제가 있다"},
                                          auth=("viewer", "test-view-password")).status_code, 422)
        confirmed = self.client.post("/incidents/1/decision", data={"decision": "confirm_defect",
                                      "csrf_token": csrf_token(1),
                                      "note": "반품 레드 확인: measurements/stages-red.json"},
                                     auth=("viewer", "test-view-password"), follow_redirects=False)
        self.assertEqual(confirmed.status_code, 303)
        state = self.client.get("/api/approvals/1", headers={"Authorization": "Bearer test-analysis-token"})
        self.assertTrue(state.json()["confirmed_defect"])
        self.assertFalse(state.json()["approved"])
        approved = self.client.post("/incidents/1/decision", data={"decision": "approve_fix",
                                     "csrf_token": csrf_token(1), "note": "실패 테스트 확인"},
                                    auth=("viewer", "test-view-password"), follow_redirects=False)
        self.assertEqual(approved.status_code, 303)
        self.assertTrue(self.client.get("/api/approvals/1",
                                     headers={"Authorization": "Bearer test-analysis-token"}).json()["approved"])

    def test_shadow_distinct_alert_records_without_incident_or_notification(self):
        alert = {"status": "firing", "startsAt": "2026-11-14T12:13:50Z",
                 "labels": {"sre_mode": "shadow", "alertname": "DistinctDeniedOrdersShadow",
                            "actor": "member:102", "sre_kind": "security_signal",
                            "fingerprint": "access.denied.distinct_targets", "route": "/order/detail"},
                 "values": {"A": 3}}
        payload = {"status": "firing", "alerts": [alert]}
        self.assertEqual(self.client.post("/api/grafana/shadow", json=payload).status_code, 401)
        # Even a misrouted shadow rule cannot turn into an on-call incident.
        primary = self.client.post("/api/grafana", json=payload, headers=self.headers)
        self.assertEqual(primary.status_code, 200)
        self.assertEqual(primary.json()["events"], [])
        with patch("app.publish_message") as notify:
            first = self.client.post("/api/grafana/shadow", json=payload, headers=self.headers)
            second = self.client.post("/api/grafana/shadow", json=payload, headers=self.headers)
        self.assertEqual(first.json()["recorded"], 1)
        self.assertEqual(second.json()["recorded"], 0)
        notify.assert_not_called()
        import app as module
        conn = module.db()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM shadow_alerts").fetchone()[0], 1)
        finally:
            conn.close()
        alerts = self.client.get("/api/shadow-alerts", auth=("viewer", "test-view-password"))
        self.assertEqual(alerts.status_code, 200)
        self.assertEqual(alerts.json()["alerts"][0]["actor"], "member:102")

    def test_local_analysis_job_claim_failure_retry_and_completion(self):
        self.client.post("/api/events", json={"kind": "error_500",
            "fingerprint": "error.unhandled.test", "route": "/order/generateOrder",
            "trace_id": "sre-job-1", "occurred_at": "2026-11-14T10:00:00Z"}, headers=self.headers)
        url = "/api/analysis-jobs/claim"
        self.assertEqual(self.client.post(url, json={"backend": "cli"}).status_code, 401)
        auth = {"Authorization": "Bearer test-analysis-token"}
        claimed = self.client.post(url, json={"backend": "cli"}, headers=auth)
        self.assertEqual(claimed.status_code, 200)
        self.assertEqual(claimed.json()["job"]["incident"]["id"], 1)
        self.assertEqual(claimed.json()["job"]["events"][0]["trace_id"], "sre-job-1")
        self.assertIn("분석 중", self.client.get("/incidents/1",
                                               auth=("viewer", "test-view-password")).text)
        self.assertIsNone(self.client.post(url, json={"backend": "api"}, headers=auth).json()["job"])
        finish = "/api/analysis-jobs/1/finish"
        self.assertEqual(self.client.post(finish, json={"state": "complete"}, headers=auth).status_code, 409)
        self.assertEqual(self.client.post(finish, json={"state": "failed", "error_code": "api_key_missing"},
                                          headers=auth).status_code, 200)
        self.assertIn("실패 후 대기 중단", self.client.get("/incidents/1",
                                                   auth=("viewer", "test-view-password")).text)
        self.assertIsNone(self.client.post(url, json={"backend": "api"}, headers=auth).json()["job"])
        self.assertEqual(self.client.post("/api/analysis-jobs/1/retry", json={}, headers=auth).status_code, 200)
        claimed_again = self.client.post(url, json={"backend": "api"}, headers=auth)
        self.assertEqual(claimed_again.json()["job"]["backend"], "api")
        report = {"analysis_kind": "rca_500", "author": "agent",
                  "observations": ["HTTP 500이 기록되었다"], "hypotheses": ["주문 경로 오류"],
                  "unknowns": ["원인 행 미확인"], "evidence_refs": ["trace_id=sre-job-1"],
                  "next_action": "사람이 재현을 확인한다"}
        self.assertEqual(self.client.post("/api/incidents/1/analysis", json=report,
                                          headers=auth).status_code, 200)
        self.assertEqual(self.client.post(finish, json={"state": "complete"}, headers=auth).status_code, 200)
        self.assertIn("로컬 자동 분석 작업", self.client.get("/incidents/1",
                                                    auth=("viewer", "test-view-password")).text)
        self.assertIsNone(self.client.post(url, json={"backend": "cli"}, headers=auth).json()["job"])


if __name__ == "__main__":
    unittest.main()
