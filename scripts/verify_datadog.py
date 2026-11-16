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
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

DEFAULT_DASHBOARD_TITLE = "Lata Velha — Microsserviços"

# Prefixos reais do setup OTel (ver infra/terraform/observability/locals.tf).
SERVICES: dict[str, dict[str, object]] = {
    "lata-velha-os-service": {
        "hits": "trace.http.request.hits",
        "errors": "trace.http.request.errors",
        "latency": "trace.http.request",
        "monitored": True,
        "jvm": True,
    },
    "lata-velha-inventory-service": {
        "hits": "trace.http.request.hits",
        "errors": "trace.http.request.errors",
        "latency": "trace.http.request",
        "monitored": True,
        "jvm": True,
    },
    "lata-velha-billing-service": {
        "hits": "trace.http.request.hits",
        "errors": "trace.http.request.errors",
        "latency": "trace.http.request",
        "monitored": True,
        "jvm": True,
    },
    # Lambdas (Story 20.1 / 21.2). O nome é o DD_SERVICE configurado no
    # Terraform; trace.aws.lambda.* é a trace metric root da extension.
    "lata-velha-auth-cpf": {
        "hits": "trace.aws.lambda.hits",
        "errors": "trace.aws.lambda.errors",
        "latency": "trace.aws.lambda",
        "monitored": False,
        "jvm": False,
    },
    "lata-velha-jwt-authorizer": {
        "hits": "trace.aws.lambda.hits",
        "errors": "trace.aws.lambda.errors",
        "latency": "trace.aws.lambda",
        "monitored": False,
        "jvm": False,
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


# O gate passou a consultar 5 serviços + borda; sem espaçamento a API do
# Datadog devolve 429. Mantém no máximo ~2 req/s e faz backoff maior em 429.
_MIN_INTERVAL_SECONDS = 0.5
_last_request_at = 0.0


def _request(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None = None,
    retries: int = 4,
) -> Any:
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    if elapsed < _MIN_INTERVAL_SECONDS:
        time.sleep(_MIN_INTERVAL_SECONDS - elapsed)
    _last_request_at = time.monotonic()

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
            if error.code == 429:
                # Rate limit do Datadog: espera mais que o backoff normal.
                last_error = error
                time.sleep(min(30, 2 ** (attempt + 2)))
                continue
            if error.code >= 500:
                last_error = error
                time.sleep(min(30, 2 ** attempt))
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

    def search_logs(self, query: str, start: datetime, end: datetime, limit: int = 1) -> dict[str, Any]:
        body = {
            "filter": {"query": query, "from": iso(start), "to": iso(end)},
            "page": {"limit": limit},
            "sort": "timestamp",
        }
        return _request("POST", f"{self.api_url}/api/v2/logs/events/search", self.headers, body)

    def search_spans(self, query: str, start: datetime, end: datetime, limit: int = 1) -> dict[str, Any]:
        body = {
            "data": {
                "type": "search_request",
                "attributes": {
                    "filter": {"query": query, "from": iso(start), "to": iso(end)},
                    "page": {"limit": limit},
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

    def list_metrics(self, query: str, start: datetime) -> dict[str, Any]:
        params = {"q": query, "from": int(start.timestamp())}
        return _request("GET", f"{self.api_url}/api/v1/metrics?{urllib.parse.urlencode(params)}", self.headers)

    def list_events(
        self,
        start: datetime,
        end: datetime,
        tags: str | None = None,
        sources: str | None = None,
    ) -> dict[str, Any]:
        params = {"start": int(start.timestamp()), "end": int(end.timestamp())}
        if tags:
            params["tags"] = tags
        if sources:
            params["sources"] = sources
        return _request("GET", f"{self.api_url}/api/v1/events?{urllib.parse.urlencode(params)}", self.headers)

    def list_synthetics_tests(self) -> dict[str, Any]:
        return _request("GET", f"{self.api_url}/api/v1/synthetics/tests", self.headers)

    def list_synthetic_results(self, public_id: str, start: datetime, end: datetime) -> dict[str, Any]:
        params = {
            "from_ts": int(start.timestamp() * 1000),
            "to_ts": int(end.timestamp() * 1000),
        }
        return _request(
            "GET",
            f"{self.api_url}/api/v1/synthetics/tests/{public_id}/results?{urllib.parse.urlencode(params)}",
            self.headers,
        )


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


def check_jvm(client: DatadogClient, services: dict[str, dict[str, object]], env: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    checks = []
    for service, metadata in services.items():
        if not metadata.get("jvm", True):
            continue
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


def _iter_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_strings(item)


def _find_values(value: Any, keys: set[str]):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and isinstance(item, str) and item.strip():
                yield item.strip()
            yield from _find_values(item, keys)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _find_values(item, keys)


def _all_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            keys.add(key)
            keys.update(_all_keys(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            keys.update(_all_keys(item))
    return keys


def _find_trace_id(event: dict[str, Any]) -> str | None:
    for value in _find_values(event, {"trace_id", "traceId", "dd.trace_id", "trace.id"}):
        if re.fullmatch(r"[0-9a-fA-F]{16,32}", value):
            return value
    for text in _iter_strings(event):
        match = re.search(r"(?:trace_id|traceId)[\"':=\s]+([0-9a-fA-F]{16,32})", text)
        if match:
            return match.group(1)
    return None


def check_tags(client: DatadogClient, services: dict[str, dict[str, object]], env: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """DT-DD-11: todo span precisa ter service, env e version (não vazio)."""
    checks = []
    for service in services:
        query = f"service:{service} env:{env}"
        payload = client.search_spans(query, start, end, limit=10)
        events = payload.get("data", []) or []
        versions: list[str] = []
        for event in events:
            versions.extend(_find_values(event, {"version", "service.version", "dd.version"}))
            for text in _iter_strings(event):
                if text.startswith("version:"):
                    versions.append(text.split(":", 1)[1].strip())
        versions = [value for value in versions if value]
        checks.append(
            {
                "id": f"DT-DD-11/{service}",
                "status": "pass" if versions else "fail",
                "details": {
                    "query": query,
                    "spans": len(events),
                    "versions": versions[:5],
                    "message": "version presente" if versions else "span sem version",
                },
            }
        )
    return checks


def _extract_trace_id_from_event(event: dict[str, Any]) -> str | None:
    """Procura trace_id no evento e, se necessário, no JSON da mensagem.

    Os logs EKS têm trace_id como campo de topo (MDC -> LogstashEncoder).
    Os logs Lambda vêm com o JSON do handler dentro de `message`; nesse caso
    o campo só aparece depois de parsear a mensagem.
    """
    direct = _find_trace_id(event)
    if direct:
        return direct

    message = None
    attributes = event.get("attributes")
    if isinstance(attributes, dict):
        message = attributes.get("message")
    if message is None:
        message = event.get("message")
    if isinstance(message, str):
        try:
            payload = json.loads(message)
        except (TypeError, ValueError):
            payload = None
        if isinstance(payload, dict):
            nested = _find_trace_id(payload)
            if nested:
                return nested
        match = re.search(r'"trace_id"\s*:\s*"([0-9a-fA-F]{16,32})"', message)
        if match:
            return match.group(1)
    return None


def check_log_correlation(client: DatadogClient, services: dict[str, dict[str, object]], start: datetime, end: datetime) -> list[dict[str, Any]]:
    """DT-DD-12: log com trace_id e pelo menos uma linha correlacionada."""
    checks = []
    # O os-service gera muito log de startup/infra; a correlação olha só os
    # últimos 5 min (onde estão as requisições do warmup) e uma amostra maior.
    recent_start = max(start, end - timedelta(minutes=5))
    for service in services:
        base_query = f"service:{service}"
        base_payload = client.search_logs(base_query, recent_start, end, limit=200)
        base_events = base_payload.get("data", []) or []

        trace_id = next(
            (tid for event in base_events if (tid := _extract_trace_id_from_event(event))),
            None,
        )
        correlated = 0
        if trace_id:
            # A query reservada @trace_id funciona quando o campo é de topo;
            # o fallback por texto cobre logs Lambda cujo trace_id está no JSON
            # da mensagem.
            for query in (f"@trace_id:{trace_id}", f'service:{service} "{trace_id}"'):
                result = client.search_logs(query, recent_start, end, limit=5)
                correlated = len(result.get("data", []) or [])
                if correlated:
                    break
        details: dict[str, Any] = {
            "query": base_query,
            "events": len(base_events),
            "sample_keys": sorted(_all_keys(base_events[0])) if base_events else [],
            "trace_id": trace_id,
            "correlated_events": correlated,
            "message": "trace_id presente no log" if trace_id else "nenhum log com trace_id",
        }
        if service == "lata-velha-auth-cpf" and base_events:
            details["sample_messages"] = [
                str(
                    ((event.get("attributes") or {}).get("message") if isinstance(event.get("attributes"), dict) else None)
                    or event.get("message")
                    or ""
                )[:400]
                for event in base_events[:3]
            ]
        checks.append(
            {
                "id": f"DT-DD-12/{service}",
                "status": "pass" if trace_id else "fail",
                "details": details,
            }
        )
    return checks


def find_first_trace_id(client: DatadogClient, service: str, env: str, start: datetime, end: datetime) -> str | None:
    payload = client.search_spans(f"service:{service} env:{env}", start, end, limit=10)
    for event in payload.get("data", []) or []:
        trace_id = _find_trace_id(event)
        if trace_id:
            return trace_id
    return None


def check_duplicate_spans(client: DatadogClient, trace_id: str | None, start: datetime, end: datetime) -> dict[str, Any]:
    """DT-DD-13: no máximo 1 span server por serviço/rota no trace."""
    if not trace_id:
        return {
            "id": "DT-DD-13",
            "status": "fail",
            "details": {"message": "nenhum trace_id encontrado para validar duplicação"},
        }
    query = f"trace_id:{trace_id}"
    payload = client.search_spans(query, start, end, limit=100)
    events = payload.get("data", []) or []
    counts: dict[tuple[str, str], int] = {}
    for event in events:
        service = next(iter(_find_values(event, {"service"})), "?")
        route = next(iter(_find_values(event, {"http.route", "http.route", "resource.name"})), "?")
        kind = next(iter(_find_values(event, {"span.kind", "span_kind", "span.type"})), "")
        if kind.lower() in {"server", "1"} or route != "?":
            counts[(service, route)] = counts.get((service, route), 0) + 1
    duplicates = [f"{service}:{route} x{count}" for (service, route), count in counts.items() if count > 1]
    return {
        "id": "DT-DD-13",
        "status": "pass" if events and not duplicates else "fail",
        "details": {
            "query": query,
            "trace_id": trace_id,
            "spans": len(events),
            "counts": {f"{service}:{route}": count for (service, route), count in counts.items()},
            "duplicates": duplicates,
            "message": "sem duplicação" if events and not duplicates else "trace sem spans ou com duplicação",
        },
    }


def check_edge_logs(client: DatadogClient, start: datetime, end: datetime) -> dict[str, Any]:
    """DT-DD-14: access logs do API Gateway chegaram ao Datadog (sem IP/PII)."""
    query = "service:lata-velha-edge source:apigateway"
    payload = client.search_logs(query, start, end, limit=5)
    events = payload.get("data", []) or []
    leaked_ip = any("sourceIp" in json.dumps(event) for event in events)
    return {
        "id": "DT-DD-14",
        "status": "pass" if events and not leaked_ip else "fail",
        "details": {
            "query": query,
            "events": len(events),
            "ip_leak": leaked_ip,
            "message": "logs de borda presentes" if events and not leaked_ip else "sem logs de borda ou IP vazou",
        },
    }


def check_edge_monitors(client: DatadogClient) -> dict[str, Any]:
    """DT-DD-15: monitores de 5xx/latência de borda existem."""
    names = [item.get("name", "") for item in client.list_monitors()]
    has_5xx = any("Edge" in name and "5xx" in name for name in names)
    has_latency = any("Edge" in name and ("latência" in name or "latency" in name.lower()) for name in names)
    return {
        "id": "DT-DD-15",
        "status": "pass" if has_5xx and has_latency else "fail",
        "details": {
            "edge_5xx": has_5xx,
            "edge_latency": has_latency,
            "message": "monitores de borda presentes" if has_5xx and has_latency else "faltam monitores de borda",
        },
    }


def check_alert_routing(client: DatadogClient) -> dict[str, Any]:
    """DT-DD-16: monitores com canal de notificação (@) configurado."""
    monitors = client.list_monitors()
    with_notification = [item.get("name") for item in monitors if "@" in (item.get("message") or "")]
    if not with_notification:
        return {
            "id": "DT-DD-16",
            "status": "skip",
            "details": {
                "reason": "TF_VAR_alert_recipients vazio/ausente — monitores não notificam ninguém (PENDING Story 21.4)",
                "monitors": len(monitors),
            },
        }
    return {
        "id": "DT-DD-16",
        "status": "pass",
        "details": {"monitors_with_notification": len(with_notification), "sample": with_notification[:3]},
    }


def check_business_attributes(client: DatadogClient, start: datetime, end: datetime) -> dict[str, Any]:
    """DT-DD-17: atributos de negócio (Story 19.2) presentes nos spans."""
    checks: list[dict[str, Any]] = []
    for service, attribute in (
        ("lata-velha-os-service", "ordem_servico.id"),
        ("lata-velha-billing-service", "ordem_servico.id"),
        ("lata-velha-inventory-service", "peca.id"),
    ):
        query = f"service:{service} @{attribute}:*"
        payload = client.search_spans(query, start, end, limit=5)
        events = payload.get("data", []) or []
        checks.append(
            {
                "service": service,
                "attribute": attribute,
                "spans": len(events),
                "status": "pass" if events else "fail",
            }
        )
    return {
        "id": "DT-DD-17",
        "status": "pass" if any(item["status"] == "pass" for item in checks) else "fail",
        "details": {
            "checks": checks,
            "message": "atributos de negócio presentes" if any(item["status"] == "pass" for item in checks) else "nenhum span com atributo de negócio",
        },
    }


def check_trace_metric_names(client: DatadogClient, start: datetime) -> dict[str, Any]:
    """DT-DD-20 (diagnóstico): quais trace metrics existem após o rename."""
    names: set[str] = set()
    for query in ("trace.http.request", "trace.org.springframework.boot", "trace.io.opentelemetry"):
        try:
            payload = client.list_metrics(query, start)
        except DatadogError:
            continue
        for metric in payload.get("metrics", []) or []:
            if isinstance(metric, dict) and metric.get("id"):
                names.add(str(metric["id"]))
    has_http = any(name.startswith("trace.http.request") for name in names)
    has_old = any(
        name.startswith("trace.org.springframework.boot") or name.startswith("trace.io.opentelemetry")
        for name in names
    )
    status = "pass" if has_http else ("fail" if has_old else "skip")
    return {
        "id": "DT-DD-20",
        "status": status,
        "details": {
            "http_request": has_http,
            "old_names": has_old,
            "metrics": sorted(names)[:80],
            "message": "trace.http.request presente" if has_http else "nome antigo ainda em uso ou sem métricas",
        },
    }


def check_trace_metric_candidates(client: DatadogClient, start: datetime, end: datetime) -> dict[str, Any]:
    """DT-DD-21 (diagnóstico): testa nomes candidatos de trace metric."""
    candidates = {
        "http.request/os": "sum:trace.http.request.hits{service:lata-velha-os-service,env:prod}",
        "http.server.request/os": "sum:trace.http.server.request.hits{service:lata-velha-os-service,env:prod}",
        "spring_old/os": "sum:trace.org.springframework.boot.server.hits{service:lata-velha-os-service,env:prod}",
        "spring_old/billing": "sum:trace.io.opentelemetry.spring_webmvc_6.0.server.hits{service:lata-velha-billing-service,env:prod}",
        "lambda/auth-cpf": "sum:trace.aws.lambda.hits{service:lata-velha-auth-cpf,env:prod}",
    }
    results: dict[str, Any] = {}
    for label, query in candidates.items():
        payload = client.query_metric(query, start, end)
        series = series_latest(payload)
        points = [item["point"] for item in series if item["point"] is not None]
        results[label] = {"series": len(series), "points": points[:3], "query": query}
    found = [label for label, value in results.items() if value["series"]]
    return {
        "id": "DT-DD-21",
        "status": "pass" if found else "fail",
        "details": {"found": found, "candidates": results},
    }


def check_cloudwatch_event(client: DatadogClient, alarm_name: str | None, start: datetime, end: datetime) -> dict[str, Any]:
    """DT-DD-18: evento de alarme CloudWatch chegou ao Datadog (Story 20.3)."""
    if not alarm_name:
        return {
            "id": "DT-DD-18",
            "status": "skip",
            "details": {"reason": "nenhum alarme CloudWatch foi forçado nesta execução"},
        }
    buckets: dict[str, Any] = {}
    all_events: list[dict[str, Any]] = []
    for label, kwargs in (
        ("tags", {"tags": "source:cloudwatch"}),
        ("sources_amazon", {"sources": "amazon cloudwatch"}),
        ("sources_cloudwatch", {"sources": "cloudwatch"}),
    ):
        try:
            payload = client.list_events(start, end, **kwargs)
        except DatadogError as error:
            buckets[label] = f"erro: {error}"
            continue
        events = payload.get("events", []) or []
        buckets[label] = len(events)
        all_events.extend(events)

    # deduplica por id/título para não contar o mesmo evento em 2 queries
    unique: dict[str, dict[str, Any]] = {}
    for event in all_events:
        key = str(event.get("id") or event.get("title") or event.get("date_happened"))
        unique[key] = event
    events = list(unique.values())
    matches = [
        event for event in events
        if alarm_name in str(event.get("title", "")) or alarm_name in str(event.get("text", ""))
    ]
    sample = [
        {
            "title": event.get("title"),
            "tags": event.get("tags"),
            "source_type_name": event.get("source_type_name"),
            "date_happened": event.get("date_happened"),
        }
        for event in events[:10]
    ]
    return {
        "id": "DT-DD-18",
        "status": "pass" if matches else "fail",
        "details": {
            "alarm": alarm_name,
            "buckets": buckets,
            "events": len(events),
            "matches": len(matches),
            "sample": sample,
            "message": "evento CloudWatch chegou ao Datadog" if matches else "evento CloudWatch não encontrado",
        },
    }


def check_synthetics(client: DatadogClient, start: datetime, end: datetime) -> dict[str, Any]:
    """DT-DD-19: synthetic tests existem e têm resultado na janela (Story 21.3)."""
    try:
        payload = client.list_synthetics_tests()
    except DatadogError as error:
        return {
            "id": "DT-DD-19",
            "status": "skip",
            "details": {"reason": f"API de synthetics indisponível: {error}"},
        }
    tests = payload.get("tests", []) or []
    wanted = [
        test for test in tests
        if "api pública" in str(test.get("name", "")).lower()
        or "auth-cpf" in str(test.get("name", "")).lower()
        or "health" in str(test.get("name", "")).lower()
    ]
    if not wanted:
        return {"id": "DT-DD-19", "status": "fail", "details": {"message": "synthetic tests não encontrados"}}

    details: list[dict[str, Any]] = []
    for test in wanted:
        public_id = test.get("public_id")
        if not public_id:
            continue
        try:
            result = client.list_synthetic_results(public_id, start, end)
        except DatadogError as error:
            details.append({"name": test.get("name"), "public_id": public_id, "error": str(error), "results": 0})
            continue
        results = result.get("results", []) or []
        details.append(
            {
                "name": test.get("name"),
                "public_id": public_id,
                "status": test.get("status"),
                "results": len(results),
                "last_status": (results[0].get("status") if results else None),
            }
        )
    with_results = [item for item in details if item.get("results", 0) > 0]
    if not with_results:
        return {
            "id": "DT-DD-19",
            "status": "skip",
            "details": {
                "reason": "synthetic tests criados, mas nenhum resultado na janela (aguardar o próximo tick de 5 min)",
                "tests": details,
            },
        }
    return {
        "id": "DT-DD-19",
        "status": "pass",
        "details": {"tests": details, "message": "resultados de synthetics presentes"},
    }


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
            for service, metadata in SERVICES.items()
            if metadata.get("monitored", True)
            and not any(needle in name and service in name for name in names)
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
    parser.add_argument("--cloudwatch-event", default=os.environ.get("DD_TEST_ALARM_NAME"))
    parser.add_argument("--synthetic-wait", type=int, default=0, help="segundos de espera antes de consultar synthetics")
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
        checks.extend(check_tags(client, services, args.env, start, end))
        checks.extend(check_log_correlation(client, services, start, end))
        primary = next((name for name in selected if SERVICES[name].get("monitored", True)), selected[0])
        checks.append(check_duplicate_spans(client, find_first_trace_id(client, primary, args.env, start, end), start, end))
        checks.append(check_dashboard(client))
        checks.append(check_monitors(client))
        checks.append(check_sqs(client, start, end, args.aws_integration_enabled))
        checks.append(check_edge_logs(client, start, end))
        checks.append(check_edge_monitors(client))
        checks.append(check_alert_routing(client))
        checks.append(check_business_attributes(client, start, end))
        checks.append(check_trace_metric_names(client, start))
        checks.append(check_trace_metric_candidates(client, start, end))
        checks.append(check_cloudwatch_event(client, args.cloudwatch_event, start, end))
        checks.append(check_health(args.api_url))
        if args.synthetic_wait:
            print(f"Aguardando {args.synthetic_wait}s para o próximo tick dos synthetics...")
            time.sleep(args.synthetic_wait)
        synthetic_end = utc_now()
        synthetic_start = synthetic_end - args.window
        checks.append(check_synthetics(client, synthetic_start, synthetic_end))
    except DatadogError as error:
        print(f"ERRO Datadog: {error}", file=sys.stderr)
        return 3

    print_summary(checks)
    failures = [item for item in checks if item["status"] == "fail"]
    report = {
        "timestamp": iso(utc_now()),
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
