"""Testes unitários do import_datadog_monitors.py (sem Datadog/Terraform reais)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import import_datadog_monitors as imp  # noqa: E402


class MonitorAddressTest(unittest.TestCase):
    def test_servicos(self) -> None:
        cases = {
            "[prod] lata-velha-os-service — erros 5xx > 1%": 'datadog_monitor.http_5xx["os"]',
            "[prod] lata-velha-inventory-service — p95 acima do baseline": 'datadog_monitor.http_p95["inventory"]',
            "[prod] lata-velha-billing-service — heap JVM > 85%": 'datadog_monitor.jvm_heap["billing"]',
            "[prod] lata-velha-os-service — mais de 3 restarts em 15 min": 'datadog_monitor.pod_restarts["os"]',
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(imp.monitor_address(name, "prod"), expected)

    def test_filas(self) -> None:
        self.assertEqual(
            imp.monitor_address("[prod] DLQ de os-events-dlq com mensagens", "prod"),
            'datadog_monitor.dlq_messages["os-events-dlq"]',
        )
        self.assertEqual(
            imp.monitor_address("[prod] Fila os-events envelhecendo", "prod"),
            'datadog_monitor.queue_age["os-events"]',
        )

    def test_borda(self) -> None:
        self.assertEqual(
            imp.monitor_address("[prod] Edge (API Gateway) — 5xx acima de 5 em 5 min", "prod"),
            "datadog_monitor.edge_5xx",
        )
        self.assertEqual(
            imp.monitor_address("[prod] Edge (API Gateway) — latência > 1s", "prod"),
            "datadog_monitor.edge_latency",
        )

    def test_ignora_outro_ambiente_ou_nome(self) -> None:
        self.assertIsNone(imp.monitor_address("[dev] lata-velha-os-service — erros 5xx > 1%", "prod"))
        self.assertIsNone(imp.monitor_address("[prod] lata-velha-api — erros 5xx > 1%", "prod"))
        self.assertIsNone(imp.monitor_address("[prod] lata-velha-os-service — métrica nova", "prod"))


if __name__ == "__main__":
    unittest.main()
