"""Testes dos checks extras: atributos de negócio, evento CloudWatch e synthetics."""

from __future__ import annotations

import sys
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import verify_datadog as v  # noqa: E402

NOW = v.utc_now()
START = NOW - timedelta(minutes=30)
END = NOW


class BusinessClient:
    def __init__(self, has_spans: bool):
        self.has_spans = has_spans

    def search_spans(self, query, start, end, limit=1):
        return {"data": [{"attributes": {"custom": {"ordem_servico.id": "1"}}}] if self.has_spans else []}


class EventClient:
    def __init__(self, match: bool):
        self.match = match

    def list_events(self, start, end, tags=None, sources=None):
        title = "CloudWatch alarm lata-velha-os-service-events-dlq-messages" if self.match else "outro alarme"
        return {"events": [{"title": title, "text": ""}]}


class SyntheticClient:
    def __init__(self, results: int = 1, raise_error: bool = False):
        self.results = results
        self.raise_error = raise_error

    def list_synthetics_tests(self):
        return {"tests": [
            {"public_id": "abc-def-ghi", "name": "[prod] API pública — health", "status": "live"},
            {"public_id": "jkl-mno-pqr", "name": "[prod] API pública — OpenAPI da auth-cpf", "status": "live"},
        ]}

    def list_synthetic_results(self, public_id, start, end):
        if self.raise_error:
            raise v.DatadogError(403, "sem permissão")
        return {"results": [{"status": 0}] * self.results}


class BusinessAttributeTests(unittest.TestCase):
    def test_pass_com_span(self) -> None:
        self.assertEqual(v.check_business_attributes(BusinessClient(True), START, END)["status"], "pass")

    def test_fail_sem_span(self) -> None:
        self.assertEqual(v.check_business_attributes(BusinessClient(False), START, END)["status"], "fail")


class CloudwatchEventTests(unittest.TestCase):
    def test_skip_sem_alarme(self) -> None:
        self.assertEqual(v.check_cloudwatch_event(EventClient(True), None, START, END)["status"], "skip")

    def test_pass_com_evento(self) -> None:
        check = v.check_cloudwatch_event(EventClient(True), "lata-velha-os-service-events-dlq-messages", START, END)
        self.assertEqual(check["status"], "pass")

    def test_fail_sem_evento(self) -> None:
        check = v.check_cloudwatch_event(EventClient(False), "lata-velha-os-service-events-dlq-messages", START, END)
        self.assertEqual(check["status"], "fail")


class CandidateClient:
    def __init__(self, hit: bool):
        self.hit = hit

    def query_metric(self, query, start, end):
        if self.hit and "trace.aws.lambda" in query:
            return {"series": [{"scope": "x", "pointlist": [[1, 1.0]]}]}
        return {"series": []}


class CandidateMetricTests(unittest.TestCase):
    def test_pass_com_candidato(self) -> None:
        self.assertEqual(v.check_trace_metric_candidates(CandidateClient(True), START, END)["status"], "pass")

    def test_fail_sem_candidato(self) -> None:
        self.assertEqual(v.check_trace_metric_candidates(CandidateClient(False), START, END)["status"], "fail")


class SyntheticTests(unittest.TestCase):
    def test_pass_com_resultados(self) -> None:
        self.assertEqual(v.check_synthetics(SyntheticClient(1), START, END)["status"], "pass")

    def test_skip_sem_resultados(self) -> None:
        self.assertEqual(v.check_synthetics(SyntheticClient(0), START, END)["status"], "skip")

    def test_skip_quando_api_falha(self) -> None:
        self.assertEqual(v.check_synthetics(SyntheticClient(1, raise_error=True), START, END)["status"], "skip")


if __name__ == "__main__":
    unittest.main()
