"""Testes dos checks de Lambda/tags/trace_id do verify_datadog.py."""

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


class TagsClient:
    def search_spans(self, query, start, end, limit=1):
        return {"data": [{"attributes": {"custom": {"version": "abc123"}}}]}


class NoTagsClient:
    def search_spans(self, query, start, end, limit=1):
        return {"data": [{"attributes": {"custom": {}}}]}


class LogClient:
    def search_logs(self, query, start, end, limit=1):
        if query.startswith("@trace_id:") or '"' in query:
            return {"data": [{"id": "correlated"}]}
        return {"data": [{"attributes": {"trace_id": "0123456789abcdef", "service": "lata-velha-auth-cpf"}}]}


class DuplicateClient:
    def __init__(self, duplicates: bool):
        self.duplicates = duplicates

    def search_spans(self, query, start, end, limit=1):
        base = {"attributes": {"custom": {"service": "lata-velha-os-service", "http.route": "/x", "span.kind": "server"}}}
        events = [base]
        if self.duplicates:
            events.append(base)
        return {"data": events}


class TagsTests(unittest.TestCase):
    def test_tags_pass_com_version(self) -> None:
        checks = v.check_tags(TagsClient(), {"lata-velha-auth-cpf": {}}, "prod", START, END)
        self.assertEqual(checks[0]["status"], "pass")

    def test_tags_fail_sem_version(self) -> None:
        checks = v.check_tags(NoTagsClient(), {"lata-velha-auth-cpf": {}}, "prod", START, END)
        self.assertEqual(checks[0]["status"], "fail")


class CorrelationTests(unittest.TestCase):
    def test_log_correlation_pass(self) -> None:
        checks = v.check_log_correlation(LogClient(), {"lata-velha-auth-cpf": {}}, "prod", START, END)
        self.assertEqual(checks[0]["status"], "pass")
        self.assertEqual(checks[0]["details"]["trace_id"], "0123456789abcdef")

    def test_log_correlation_skip_sem_trace_id(self) -> None:
        class Empty:
            def search_logs(self, *args, **kwargs):
                return {"data": [{}]}

            def search_spans(self, *args, **kwargs):
                return {"data": []}

        checks = v.check_log_correlation(Empty(), {"lata-velha-auth-cpf": {}}, "prod", START, END)
        self.assertEqual(checks[0]["status"], "skip")


class DuplicateTests(unittest.TestCase):
    def test_duplicate_fail(self) -> None:
        check = v.check_duplicate_spans(DuplicateClient(True), "0123456789abcdef", START, END)
        self.assertEqual(check["status"], "fail")
        self.assertTrue(check["details"]["duplicates"])

    def test_sem_duplicata_pass(self) -> None:
        check = v.check_duplicate_spans(DuplicateClient(False), "0123456789abcdef", START, END)
        self.assertEqual(check["status"], "pass")

    def test_sem_trace_id_fail(self) -> None:
        check = v.check_duplicate_spans(DuplicateClient(False), None, START, END)
        self.assertEqual(check["status"], "fail")


if __name__ == "__main__":
    unittest.main()
