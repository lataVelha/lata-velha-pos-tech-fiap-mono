# Evidência — Datadog observability (stories 21.1/21.2/21.5)

Validação real executada em **2026-11-14** contra `env:prod`, site `us5`.
Relatório bruto: [`datadog-observability-report.json`](./datadog-observability-report.json).

## PRs e runs

| Item | Referência |
|---|---|
| Infra PR (stack + workflow) | https://github.com/lataVelha/lata-velha-pos-tech-fiap-infra/pull/13 |
| Mono PR (pipeline + docs + evidência) | https://github.com/lataVelha/lata-velha-pos-tech-fiap-mono/pull/7 |
| Mono CI (PR, validação) | https://github.com/lataVelha/lata-velha-pos-tech-fiap-mono/actions/runs/43635091779 |
| Infra CI (PR, observability + plan) | https://github.com/lataVelha/lata-velha-pos-tech-fiap-infra/actions/runs/43635079467 |

## Recursos aplicados

| Recurso | ID | URL/observação |
|---|---|---|
| Dashboard “Lata Velha — Microsserviços” | `ybt-6vw-p2t` | https://app.us5.datadoghq.com/dashboard/ybt-6vw-p2t/lata-velha--microsservios |
| Monitores 5xx (os/inventory/billing) | `23755620`, `23755619`, `23755618` | tag `managed-by:terraform` |
| Monitores p95 (os/inventory/billing) | `23755615`, `23755617`, `23755612` | baseline 0.8s / 0.5s / 1.0s |
| Monitores heap (os/inventory/billing) | `23755621`, `23755623`, `23755622` | >85% |
| Monitores restarts (os/inventory/billing) | `23755616`, `23755613`, `23755614` | >3 em 15 min |
| SQS | — | N/A no AWS Academy; fallback CloudWatch (ver runbook §7) |

`terraform plan` após o apply: **No changes** (idempotente).
As 12 queries de monitor foram revalidadas na API `/api/v1/monitor/validate`:
**12/12 HTTP 200**.

## Queries e resultados (janela 2h)

| Critério | Serviço | Query | Resultado |
|---|---|---|---|
| Hits | os | `sum:trace.org.springframework.boot.server.hits{service:lata-velha-os-service,env:prod}` | 18.5 |
| Hits | inventory | `sum:trace.org.springframework.boot.server.hits{service:lata-velha-inventory-service,env:prod}` | 18.0 |
| Hits | billing | `sum:trace.io.opentelemetry.spring_webmvc_6.0.server.hits{service:lata-velha-billing-service,env:prod}` | 19.5 |
| p95 | os | `p95:trace.org.springframework.boot.server{service:lata-velha-os-service,env:prod}` | 3.23 ms (0.00323 s) |
| p95 | inventory | `p95:trace.org.springframework.boot.server{service:lata-velha-inventory-service,env:prod}` | 3.03 ms (0.00303 s) |
| p95 | billing | `p95:trace.io.opentelemetry.spring_webmvc_6.0.server{service:lata-velha-billing-service,env:prod}` | 6.09 ms (0.00609 s) |
| Heap | os | `avg:jvm.heap_memory{service:lata-velha-os-service,env:prod}` | 28.96 MB |
| Heap | inventory | `avg:jvm.heap_memory{service:lata-velha-inventory-service,env:prod}` | 17.28 MB |
| Heap | billing | `avg:jvm.heap_memory{service:lata-velha-billing-service,env:prod}` | 21.02 MB |
| Erros | os/inventory/billing | `sum:trace.<prefixo>.errors{...}` | sem erros na janela (monitor OK) |
| Logs | os/inventory/billing | `service:<svc>` | ≥1 evento por serviço |
| Spans | os/inventory/billing | `service:<svc>` | ≥1 span por serviço |
| Health | app | `GET <api>/actuator/health` | 200 `{"status":"UP"}` |

## Validação de 4xx × 5xx

- `GET /ordens-servico/00000000-0000-0000-0000-000000000000` autenticado →
  HTTP 404 no os-service; span com `custom.status=404`, `status=ok`.
- `.errors` do os-service não incrementou na janela → 4xx não conta como 5xx.
- O monitor de 5xx usa `.errors/.hits` e `on_missing_data=resolve`.

## Observações

- O Agent do cluster ainda usa a API key anterior (validada e funcionando).
  A nova API key foi validada na API do Datadog, mas a rotação não foi
  necessária e não foi feita.
- `kubernetes.containers.restarts` não tem tags `service`/`env`; os monitores
  usam `kube_cluster_name`/`kube_namespace`/`kube_deployment`.
- Logs de startup não têm `trace_id`; a correlação por `trace_id` depende de
  logs emitidos no contexto de uma requisição de negócio.
- A API key/APP key nunca foram impressas nem versionadas.

## Reprodução

```bash
cd infra/terraform/observability
DD_API_KEY=... DD_APP_KEY=... ./apply.sh --auto

cd ../../..
DD_API_KEY=... DD_APP_KEY=... \
  python3 scripts/verify_datadog.py --env prod --window 2h \
    --api-url https://we5lklh1vk.execute-api.us-east-1.amazonaws.com \
    --report docs/evidence/datadog-observability-report.json
```
