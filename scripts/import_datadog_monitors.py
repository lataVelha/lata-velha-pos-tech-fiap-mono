#!/usr/bin/env python3
"""Importa monitores Datadog existentes para o state do Terraform.

Contexto: o projeto roda em contas AWS Academy, que são trocadas
periodicamente. A troca recria o bucket de state (`lata-velha-tfstate-<conta>`),
mas os monitores do Datadog são globais à organização. Sem import, o
`terraform apply` falha com:

    Duplicate of an existing monitor_id:23755619

Este script lista os monitores com a tag `managed-by:terraform`, casa o nome
com o endereço do recurso Terraform e roda `terraform import` apenas para os
que ainda não estão no state. É idempotente: se o state já tem o recurso, pula.

Uso:
  DD_API_KEY=... DD_APP_KEY=... \
    python3 scripts/import_datadog_monitors.py \
      --terraform-dir infra/terraform/observability --env prod

  # simulação (não executa import)
  ... --dry-run

O provider Datadog do Terraform lê `TF_VAR_dd_api_key`/`TF_VAR_dd_app_key`;
o script lê `DD_API_KEY`/`DD_APP_KEY` para consultar a API de monitores.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

SERVICE_KEYS = {
    "lata-velha-os-service": "os",
    "lata-velha-inventory-service": "inventory",
    "lata-velha-billing-service": "billing",
}

MONITOR_KINDS = {
    "erros 5xx > 1%": "http_5xx",
    "p95 acima do baseline": "http_p95",
    "heap JVM > 85%": "jvm_heap",
    "mais de 3 restarts em 15 min": "pod_restarts",
}


def datadog_get(path: str, api_key: str, app_key: str, site: str) -> Any:
    url = f"https://api.{site}{path}"
    request = urllib.request.Request(
        url,
        headers={
            "DD-API-KEY": api_key,
            "DD-APPLICATION-KEY": app_key,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"[]")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Datadog API HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"falha de rede ao consultar o Datadog: {error}") from error


def list_monitors(api_key: str, app_key: str, site: str) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"page_size": 1000})
    data = datadog_get(f"/api/v1/monitor?{query}", api_key, app_key, site)
    if not isinstance(data, list):
        raise RuntimeError(f"resposta inesperada da API de monitores: {type(data)!r}")
    return data


def monitor_address(name: str, environment: str) -> str | None:
    """Converte o nome do monitor no endereço do recurso Terraform."""
    prefix = f"[{environment}] "
    if not name.startswith(prefix):
        return None
    rest = name[len(prefix) :]

    for service, key in SERVICE_KEYS.items():
        marker = f"{service} — "
        if rest.startswith(marker):
            kind = MONITOR_KINDS.get(rest[len(marker) :])
            if kind:
                return f'datadog_monitor.{kind}["{key}"]'
            return None

    match = re.fullmatch(r"DLQ de (.+) com mensagens", rest)
    if match:
        return f'datadog_monitor.dlq_messages["{match.group(1)}"]'

    match = re.fullmatch(r"Fila (.+) envelhecendo", rest)
    if match:
        return f'datadog_monitor.queue_age["{match.group(1)}"]'

    return None


def terraform_state_list(terraform_dir: str) -> set[str]:
    result = subprocess.run(
        ["terraform", f"-chdir={terraform_dir}", "state", "list"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        # State vazio (bucket novo) é esperado; apenas reporta.
        message = (result.stderr or result.stdout).strip().splitlines()
        if message:
            print(f"  state list: {message[-1]}", file=sys.stderr)
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def terraform_import(terraform_dir: str, address: str, monitor_id: int) -> bool:
    result = subprocess.run(
        ["terraform", f"-chdir={terraform_dir}", "import", address, str(monitor_id)],
        capture_output=True,
        text=True,
        check=False,
    )
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode == 0:
        print(f"  importado {address} <- {monitor_id}")
        return True
    if "already managed" in output.lower() or "already exists in state" in output.lower():
        print(f"  já gerenciado, pulando {address}")
        return True
    print(f"  FALHA ao importar {address} <- {monitor_id}", file=sys.stderr)
    print(output.strip()[:1500], file=sys.stderr)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terraform-dir", required=True)
    parser.add_argument("--env", default="prod")
    parser.add_argument("--site", default="us5.datadoghq.com")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    api_key = os.environ.get("DD_API_KEY", "").strip()
    app_key = os.environ.get("DD_APP_KEY", "").strip()
    if not api_key or not app_key:
        print("ERRO: defina DD_API_KEY e DD_APP_KEY (nunca versionar).", file=sys.stderr)
        return 2

    print(f"Consultando monitores managed-by:terraform em {args.site}...")
    monitors = list_monitors(api_key, app_key, args.site)
    managed = [
        monitor
        for monitor in monitors
        if "managed-by:terraform" in (monitor.get("tags") or [])
    ]
    print(f"  {len(managed)} monitor(es) gerenciado(s) encontrado(s)")

    state = terraform_state_list(args.terraform_dir)
    print(f"  {len(state)} recurso(s) no state do Terraform")

    failures = 0
    imports = 0
    for monitor in sorted(managed, key=lambda item: item.get("name", "")):
        name = monitor.get("name", "")
        address = monitor_address(name, args.env)
        if not address:
            print(f"  sem mapeamento Terraform: {name}")
            continue
        if address in state:
            continue
        monitor_id = monitor.get("id")
        if monitor_id is None:
            continue
        imports += 1
        if args.dry_run:
            print(f"  [dry-run] importaria {address} <- {monitor_id}")
            continue
        if not terraform_import(args.terraform_dir, address, int(monitor_id)):
            failures += 1

    if args.dry_run:
        print(f"Dry-run: {imports} import(s) pendente(s)")
    else:
        print(f"Import: {imports - failures} ok, {failures} falha(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
