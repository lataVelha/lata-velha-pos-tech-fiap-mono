#!/usr/bin/env python3
"""Verificação de observabilidade do Lata Velha contra o Datadog.

Critérios (rastreáveis às stories 21.2/21.5):

  DT-DD-01  tráfego (hits) dos 3 serviços
  DT-DD-02  erros de span (5xx/falhas) — reporta, não falha se não houver erro
  DT-DD-03  latência p95 dos 3 serviços
  DT-DD-04  JVM heap/max dos 3 serviços
  DT-DD-05  logs dos 3 serviços na janela
  DT-DD-06  spans dos 3 serviços na janela
  DT-DD-07  dashboard gerenciado existe
  DT-DD-08  monitores gerenciados existem/saudáveis
  DT-DD-09  SQS (skip justificado quando a integração AWS está off)
  DT-DD-10  health público 200/UP

Uso:
  DD_API_KEY=... DD_APP_KEY=... \
    python3 scripts/verify_datadog.py --env prod \
      --api-url https://<api>.execute-api.us-east-1.amazonaws.com \
      --report datadog-report.json

Sem dependências externas (somente stdlib). Nunca imprime as chaves.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

DEFAULT_DASHBOARD_TITLE = "Lata Velha — Microsserviços"

# Prefixos reais do setup OTel (ver infra/terraform/observability/locals.tf).
SERVICES: dict[str, dict[str, str]] = {
    "lata-velha-os-service": {
        "hits": "trace.org.springframework.boot.server.hits",
        "errors": "trace.org.springframework.boot.server.errors",
        "latency": "trace.org.springframework.boot.server",
    },
    "lata-velha-inventory-service": {
        "hits": "trace.org.springframework.boot.server.hits",
        "errors": "trace.org.springframework.boot.server.errors",
        "latency": "trace.org.springframework.boot.server",
    },
    "lata-velha-billing-service": {
        "hits": "trace.io.opentelemetry.spring_webmvc_6.0.server.hits",
        "errors": "trace.io.opentelemetry.spring_webmvc_6.0.server.errors",
        "latency": "trace.io.opentelemetry.spring_webmvc_6.0.server",
    },
}


class DatadogError(RuntimeError):
    """Erro de API do Datadog com status HTTP."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_window(value: str) -> timedelta:
    value = value.strip().lower()
    if value.endswith("h"):
        return timedelta(hours=int(value[:-1]))
    if value.endswith("m"):
        return timedelta(minutes=int(value[:-1]))
    if value.endswith("s"):
        return timedelta(seconds=int(value[:-1]))
    raise argparse.ArgumentTypeError("janela inválida; use 30m, 1h, 2h...")


def _request(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None = None,
    retries: int = 3,
) -> Any:
    payload = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:500]
            if error.code == 403:
                raise DatadogError(
                    403,
                    "403 Forbidden: verifique DD_API_KEY/DD_APP_KEY e os escopos "
                    f"de leitura de métricas/logs/spans/monitores. Resposta: {detail}",
                ) from error
            if error.code == 429 or error.code >= 500:
                last_error = error
                time.sleep(2 ** attempt)
                continue
            raise DatadogError(error.code, f"HTTP {error.code}: {detail}") from error
        except urllib.error.URLError as error:
            last_error = error
            time.sleep(2 ** attempt)
    raise DatadogError(0, f"falha de rede após {retries} tentativas: {last_error}")


class DatadogClient:
    def __init__(self, api_key: str, app_key: str, site: str = "us5.datadoghq.com") -> None:
        self.api_url = f"https://api.{site}".rstrip("/")
        self.headers = {
            "DD-API-KEY": api_key,
            "DD-APPLICATION-KEY": app_key,
            "Content-Type": "application/json",
        }

    def query_metric(self, query: str, start: datetime, end: datetime) -> dict[str, Any]:
        params = urllib.parse.urlencode(
            {"from": int(start.timestamp()), "to": int(end.timestamp()), "query": query}
        )
        return _request("GET", f"{self.api_url}/api/v1/query?{params}", self.headers)

    def search_logs(self, query: str, start: datetime, end: datetime) -> dict[str, Any]:
        body = {
            "filter": {"query": query, "from": iso(start), "to": iso(end)},
            "page": {"limit": 1},
            "sort": "timestamp",
        }
        return _request("POST", f"{self.api_url}/api/v2/logs/events/search", self.headers, body)

    def search_spans(self, query: str, start: datetime, end: datetime) -> dict[str, Any]:
        body = {
            "data": {
                "type": "search_request",
                "attributes": {
                    "filter": {"query": query, "from": iso(start), "to": iso(end)},
                    "page": {"limit": 1},
                    "sort": "timestamp",
                },
            }
        }
        return _request("POST", f"{self.api_url}/api/v2/spans/events/search", self.headers, body)

    def list_dashboards(self) -> dict[str, Any]:
        return _request("GET", f"{self.api_url}/api/v1/dashboard", self.headers)

    def get_dashboard(self, dashboard_id: str) -> dict[str, Any]:
        return _request("GET", f"{self.api_url}/api/v1/dashboard/{dashboard_id}", self.headers)

    def list_monitors(self, monitor_tags: str = "managed-by:terraform") -> list[dict[str, Any]]:
        params = urllib.parse.urlencode({"monitor_tags": monitor_tags})
        return _request("GET", f"{self.api_url}/api/v1/monitor?{params}", self.headers)


def series_latest(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extrai a última amostra não nula de cada série."""
    result: list[dict[str, Any]] = []
    for series in payload.get("series", []) or []:
        point = None
        for candidate in reversed(series.get("pointlist", []) or []):
            if len(candidate) > 1 and candidate[1] is not None:
                point = candidate[1]
                break
        result.append({"scope": series.get("scope", ""), "point": point})
    return result


def check_metric(
    client: DatadogClient,
    check_id: str,
    query: str,
    start: datetime,
    end: datetime,
    *,
    require_positive: bool = True,
    detail_name: str = "series",
) -> dict[str, Any]:
    payload = client.query_metric(query, start, end)
    series = series_latest(payload)
    points = [item["point"] for item in series if item["point"] is not None]
    ok = bool(series)
    if require_positive:
        ok = any(point > 0 for point in points)
    return {
        "id": check_id,
        "status": "pass" if ok else "fail",
        "details": {
            "query": query,
            detail_name: series,
            "message": "série encontrada" if ok else "sem série/dados positivos na janela",
        },
    }


def check_services(client: DatadogClient, services: dict[str, dict[str, str]], env: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    checks = []
    for service, metrics in services.items():
        checks.append(
            check_metric(
                client,
                f"DT-DD-01/{service}",
                f"sum:{metrics['hits']}{{service:{service},env:{env}}} by {{service}}",
                start,
                end,
            )
        )
    return checks


def check_errors(client: DatadogClient, services: dict[str, dict[str, str]], env: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    checks = []
    for service, metrics in services.items():
        query = f"sum:{metrics['errors']}{{service:{service},env:{env}}} by {{service}}"
        payload = client.query_metric(query, start, end)
        series = series_latest(payload)
        checks.append(
            {
                "id": f"DT-DD-02/{service}",
                "status": "pass",
                "details": {
                    "query": query,
                    "series": series,
                    "message": "sem erros na janela" if not series else "erros observados (monitor alerta se >1%)",
                },
            }
        )
    return checks


def check_latency(client: DatadogClient, services: dict[str, dict[str, str]], env: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    checks = []
    for service, metrics in services.items():
        checks.append(
            check_metric(
                client,
                f"DT-DD-03/{service}",
                f"p95:{metrics['latency']}{{service:{service},env:{env}}} by {{service}}",
                start,
                end,
            )
        )
    return checks


def check_jvm(client: DatadogClient, services: dict[str, dict[str, str]], env: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    checks = []
    for service in services:
        item = check_metric(
            client,
            f"DT-DD-04/{service}",
            f"avg:jvm.heap_memory{{service:{service},env:{env}}} by {{service}}",
            start,
            end,
        )
        max_payload = client.query_metric(
            f"avg:jvm.heap_memory_max{{service:{service},env:{env}}} by {{service}}",
            start,
            end,
        )
        item["details"]["heap_max"] = series_latest(max_payload)
        checks.append(item)
    return checks


def check_logs(client: DatadogClient, services: dict[str, dict[str, str]], start: datetime, end: datetime) -> list[dict[str, Any]]:
    checks = []
    for service in services:
        query = f"service:{service}"
        payload = client.search_logs(query, start, end)
        count = len(payload.get("data", []) or [])
        checks.append(
            {
                "id": f"DT-DD-05/{service}",
                "status": "pass" if count > 0 else "fail",
                "details": {"query": query, "events": count},
            }
        )
    return checks


def check_spans(client: DatadogClient, services: dict[str, dict[str, str]], start: datetime, end: datetime) -> list[dict[str, Any]]:
    checks = []
    for service in services:
        query = f"service:{service}"
        payload = client.search_spans(query, start, end)
        count = len(payload.get("data", []) or [])
        checks.append(
            {
                "id": f"DT-DD-06/{service}",
                "status": "pass" if count > 0 else "fail",
                "details": {"query": query, "spans": count},
            }
        )
    return checks


def check_dashboard(client: DatadogClient) -> dict[str, Any]:
    payload = client.list_dashboards()
    dashboards = payload.get("dashboards", []) or []
    match = next((item for item in dashboards if item.get("title") == DEFAULT_DASHBOARD_TITLE), None)
    details: dict[str, Any] = {
        "title": DEFAULT_DASHBOARD_TITLE,
        "dashboards_seen": len(dashboards),
    }
    if match:
        details["id"] = match.get("id")
        details["url"] = match.get("url")
        full = client.get_dashboard(match["id"])
        details["widgets"] = len(full.get("widgets", []) or [])
    return {
        "id": "DT-DD-07",
        "status": "pass" if match else "fail",
        "details": details,
    }


def check_monitors(client: DatadogClient) -> dict[str, Any]:
    monitors = client.list_monitors()
    required = ("5xx", "p95", "heap", "restarts")
    names = [item.get("name", "") for item in monitors]
    missing = sorted(
        {
            f"{needle}/{service}"
            for needle in required
            for service in SERVICES
            if not any(needle in name and service in name for name in names)
        }
    )
    states = {item.get("name"): item.get("overall_state") for item in monitors}
    alerting = [name for name, state in states.items() if state == "Alert"]
    status = "pass" if monitors and not missing and not alerting else "fail"
    return {
        "id": "DT-DD-08",
        "status": status,
        "details": {
            "count": len(monitors),
            "missing": missing,
            "alerting": alerting,
            "states": states,
        },
    }


def check_sqs(client: DatadogClient, start: datetime, end: datetime, enabled: bool) -> dict[str, Any]:
    if not enabled:
        return {
            "id": "DT-DD-09",
            "status": "skip",
            "details": {
                "reason": "integração AWS desabilitada no AWS Academy; fallback = alarmes CloudWatch do messaging",
            },
        }
    query = "max:aws.sqs.approximate_number_of_messages_visible{*} by {queuename}"
    return check_metric(client, "DT-DD-09", query, start, end)


def check_health(api_url: str | None) -> dict[str, Any]:
    if not api_url:
        return {
            "id": "DT-DD-10",
            "status": "skip",
            "details": {"reason": "use --api-url para validar o health público"},
        }
    url = f"{api_url.rstrip('/')}/actuator/health"
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            body = json.loads(response.read() or b"{}")
        status = "pass" if response.status == 200 and body.get("status") == "UP" else "fail"
        return {
            "id": "DT-DD-10",
            "status": status,
            "details": {"url": url, "http_status": response.status, "body": body},
        }
    except Exception as error:  # noqa: BLE001 - health é fronteira externa
        return {
            "id": "DT-DD-10",
            "status": "fail",
            "details": {"url": url, "error": str(error)},
        }


def print_summary(checks: list[dict[str, Any]]) -> None:
    print(f"{'check':<46} {'status':<6} detalhe")
    print("-" * 100)
    for item in checks:
        detail = item.get("details", {})
        if item["id"].startswith("DT-DD-01") or item["id"].startswith("DT-DD-03") or item["id"].startswith("DT-DD-04"):
            message = detail.get("message", "")
            values = [entry.get("point") for entry in detail.get("series", []) if entry.get("point") is not None]
            extra = f" values={values[:3]}"
        else:
            message = ""
            extra = ""
        print(f"{item['id']:<46} {item['status']:<6} {message}{extra}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verifica observabilidade Lata Velha no Datadog")
    parser.add_argument("--env", default="prod")
    parser.add_argument("--site", default="us5.datadoghq.com")
    parser.add_argument("--window", type=parse_window, default=timedelta(hours=1))
    parser.add_argument("--wait", type=int, default=0, help="segundos de espera antes do primeiro check")
    parser.add_argument("--service", action="append", choices=sorted(SERVICES), help="restringe a um serviço")
    parser.add_argument("--api-url", default=os.environ.get("APP_API_URL"))
    parser.add_argument("--aws-integration-enabled", action="store_true")
    parser.add_argument("--report", default="datadog-report.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    selected = args.service or sorted(SERVICES)
    services = {name: SERVICES[name] for name in selected}
    now = utc_now()
    start = now - args.window
    end = now

    if args.dry_run:
        print("Dry-run (nenhuma chamada HTTP):")
        for service, metrics in services.items():
            print(f"- {service}: {metrics['hits']} / {metrics['errors']} / p95:{metrics['latency']}")
        return 0

    api_key = os.environ.get("DD_API_KEY", "")
    app_key = os.environ.get("DD_APP_KEY", "")
    if not api_key or not app_key:
        print("ERRO: defina DD_API_KEY e DD_APP_KEY (nunca versionar).", file=sys.stderr)
        return 2

    if args.wait:
        time.sleep(args.wait)

    client = DatadogClient(api_key, app_key, args.site)
    checks: list[dict[str, Any]] = []
    try:
        checks.extend(check_services(client, services, args.env, start, end))
        checks.extend(check_errors(client, services, args.env, start, end))
        checks.extend(check_latency(client, services, args.env, start, end))
        checks.extend(check_jvm(client, services, args.env, start, end))
        checks.extend(check_logs(client, services, start, end))
        checks.extend(check_spans(client, services, start, end))
        checks.append(check_dashboard(client))
        checks.append(check_monitors(client))
        checks.append(check_sqs(client, start, end, args.aws_integration_enabled))
        checks.append(check_health(args.api_url))
    except DatadogError as error:
        print(f"ERRO Datadog: {error}", file=sys.stderr)
        return 3

    print_summary(checks)
    failures = [item for item in checks if item["status"] == "fail"]
    report = {
        "timestamp": iso(now),
        "env": args.env,
        "window": str(args.window),
        "services": selected,
        "checks": checks,
        "summary": {
            "passed": sum(1 for item in checks if item["status"] == "pass"),
            "failed": len(failures),
            "skipped": sum(1 for item in checks if item["status"] == "skip"),
        },
    }
    with open(args.report, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(f"\nRelatório: {args.report}")
    print(f"Resumo: {report['summary']}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
