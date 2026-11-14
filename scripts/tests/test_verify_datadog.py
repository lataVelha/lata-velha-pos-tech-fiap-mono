"""Testes unitários do verify_datadog.py (sem chamadas HTTP reais)."""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import verify_datadog as v  # noqa: E402


SERVICES = {
    "lata-velha-os-service": v.SERVICES["lata-velha-os-service"],
    "lata-velha-inventory-service": v.SERVICES["lata-velha-inventory-service"],
    "lata-velha-billing-service": v.SERVICES["lata-velha-billing-service"],
}


def metric_payload(value: float | None) -> dict:
    return {"status": "ok", "series": [{"scope": "service:x", "pointlist": [[1, value]]}]}


class FakeClient:
    def __init__(self) -> None:
        self.metric_value = 1.0
        self.logs = 1
        self.spans = 1
        self.dashboard = True
        self.monitor_state = "OK"

    def query_metric(self, query: str, start, end):
        return metric_payload(self.metric_value)

    def search_logs(self, query: str, start, end):
        return {"data": [{}] * self.logs}

    def search_spans(self, query: str, start, end):
        return {"data": [{}] * self.spans}

    def list_dashboards(self):
        dashboards = []
        if self.dashboard:
            dashboards.append({"id": "abc-123", "title": v.DEFAULT_DASHBOARD_TITLE, "url": "/dashboard/abc-123"})
        return {"dashboards": dashboards}

    def get_dashboard(self, dashboard_id: str):
        return {"id": dashboard_id, "widgets": [{}, {}]}

    def list_monitors(self, monitor_tags: str = "managed-by:terraform"):
        names = []
        for service in SERVICES:
            for needle in ("5xx", "p95", "heap", "restarts"):
                names.append({"name": f"[prod] {service} — {needle}", "overall_state": self.monitor_state})
        return names


class SeriesTests(unittest.TestCase):
    def test_series_latest_ignores_null(self) -> None:
        payload = {"series": [{"scope": "a", "pointlist": [[1, None], [2, 5.0], [3, None]]}]}
        self.assertEqual(v.series_latest(payload), [{"scope": "a", "point": 5.0}])


class CheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = FakeClient()
        self.end = v.utc_now()
        self.start = self.end - timedelta(hours=1)

    def test_services_pass(self) -> None:
        checks = v.check_services(self.client, SERVICES, "prod", self.start, self.end)
        self.assertEqual(len(checks), 3)
        self.assertTrue(all(item["status"] == "pass" for item in checks))

    def test_services_fail_without_series(self) -> None:
        self.client.query_metric = lambda query, start, end: {"status": "ok", "series": []}
        checks = v.check_services(self.client, SERVICES, "prod", self.start, self.end)
        self.assertTrue(all(item["status"] == "fail" for item in checks))

    def test_errors_without_series_is_pass(self) -> None:
        self.client.query_metric = lambda query, start, end: {"status": "ok", "series": []}
        checks = v.check_errors(self.client, SERVICES, "prod", self.start, self.end)
        self.assertTrue(all(item["status"] == "pass" for item in checks))
        self.assertIn("sem erros", checks[0]["details"]["message"])

    def test_latency_prefers_positive_series(self) -> None:
        checks = v.check_latency(self.client, SERVICES, "prod", self.start, self.end)
        self.assertTrue(all(item["status"] == "pass" for item in checks))

    def test_jvm_includes_max(self) -> None:
        checks = v.check_jvm(self.client, SERVICES, "prod", self.start, self.end)
        self.assertEqual(len(checks), 3)
        self.assertIn("heap_max", checks[0]["details"])

    def test_logs_and_spans(self) -> None:
        logs = v.check_logs(self.client, SERVICES, self.start, self.end)
        spans = v.check_spans(self.client, SERVICES, self.start, self.end)
        self.assertTrue(all(item["status"] == "pass" for item in logs))
        self.assertTrue(all(item["status"] == "pass" for item in spans))

    def test_logs_fail_when_empty(self) -> None:
        self.client.logs = 0
        logs = v.check_logs(self.client, SERVICES, self.start, self.end)
        self.assertTrue(all(item["status"] == "fail" for item in logs))

    def test_dashboard_pass_and_fail(self) -> None:
        self.assertEqual(v.check_dashboard(self.client)["status"], "pass")
        self.client.dashboard = False
        self.assertEqual(v.check_dashboard(self.client)["status"], "fail")

    def test_monitors_pass_and_alert(self) -> None:
        self.assertEqual(v.check_monitors(self.client)["status"], "pass")
        self.client.monitor_state = "Alert"
        result = v.check_monitors(self.client)
        self.assertEqual(result["status"], "fail")
        self.assertTrue(result["details"]["alerting"])


class MainTests(unittest.TestCase):
    def test_dry_run_returns_zero_without_http(self) -> None:
        with patch.object(v, "DatadogClient", side_effect=AssertionError("não deve criar client")):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(v.main(["--dry-run"]), 0)

    def test_missing_keys_returns_two(self) -> None:
        with patch.dict(v.os.environ, {"DD_API_KEY": "", "DD_APP_KEY": ""}, clear=False):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(v.main(["--report", "/tmp/nao-usado.json"]), 2)


class RequestTests(unittest.TestCase):
    def test_403_has_clear_message(self) -> None:
        error = HTTPError("https://api.example", 403, "Forbidden", {}, io.BytesIO(b'{"errors":["forbidden"]}'))
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(v.DatadogError) as ctx:
                v._request("GET", "https://api.example", {}, retries=1)
        self.assertEqual(ctx.exception.status, 403)
        self.assertIn("DD_API_KEY", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
