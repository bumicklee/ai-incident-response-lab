"""Transport contract tests for the two local analysis backends."""

from __future__ import annotations

import json
import os
from unittest import TestCase
from unittest.mock import patch

import local_analysis_worker as worker


REPORT = {"analysis_kind": "rca_500", "author": "agent",
          "observations": ["사건 5의 HTTP 500"], "hypotheses": ["주문 경로 오류"],
          "unknowns": ["예외 행 미확인"], "evidence_refs": ["trace_id=sre-demo"],
          "next_action": "사람이 재현한다"}


class LocalBackendContract(TestCase):
    def test_cli_disables_tools_and_parses_same_schema(self):
        class Result:
            returncode = 0
            stdout = json.dumps({"type": "result", "structured_output": REPORT,
                                 "total_cost_usd": 0.01})
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "should-not-reach-cli"}):
            with patch.object(worker.subprocess, "run", return_value=Result()) as run:
                report, meta = worker.call_cli("synthetic prompt", worker.schema_for("error_500"))
        self.assertEqual(report, REPORT)
        self.assertEqual(meta["total_cost_usd"], 0.01)
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertIn("--no-session-persistence", command)
        self.assertIn("--max-budget-usd", command)
        self.assertNotIn("ANTHROPIC_API_KEY", run.call_args.kwargs["env"])

    def test_api_uses_key_header_version_and_structured_output(self):
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def read(self, *_):
                return json.dumps({"content": [{"type": "text", "text": json.dumps(REPORT)}],
                                   "stop_reason": "end_turn", "usage": {"input_tokens": 10}}).encode()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "local-test-key"}):
            with patch.object(worker.urllib.request, "urlopen", return_value=Response()) as send:
                report, meta = worker.call_api("synthetic prompt", worker.schema_for("error_500"))
        self.assertEqual(report, REPORT)
        self.assertEqual(meta["usage"]["input_tokens"], 10)
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(request.headers["Authorization"], "Bearer local-test-key")
        self.assertEqual(request.headers["Anthropic-version"], worker.API_VERSION)
        body = json.loads(request.data)
        self.assertEqual(body["output_config"]["format"]["type"], "json_schema")
        self.assertNotIn("tools", body)

    def test_api_requires_key_without_network_call(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(worker.urllib.request, "urlopen") as send:
                with self.assertRaisesRegex(RuntimeError, "api_key_missing"):
                    worker.call_api("synthetic prompt", worker.schema_for("error_500"))
        send.assert_not_called()
