# Evidências — validação de identidade e telemetria (3 serviços)

Story 17.4. Checklist auditável de service map, logs correlacionados e
ausência de duplicação. Executar em staging antes do go-live.

## Identidade esperada

| Serviço | `OTEL_SERVICE_NAME` / `service.name` | `env` | `version` |
|---|---|---|---|
| os | `lata-velha-os-service` | `prod` | SHA da imagem |
| inventory | `lata-velha-inventory-service` | `prod` | SHA da imagem |
| billing | `lata-velha-billing-service` | `prod` | SHA da imagem |

## Checklist

| # | Verificação | Como | OK |
|---|---|---|---|
| 1 | Três serviços distintos no APM | Datadog → APM → Services (`env:prod`) | [x] |
| 2 | `service.name` correto por trace | abrir trace e conferir a tag | [x] |
| 3 | Logs correlacionados por `trace_id` | filtro `@trace_id:<id>` | [x] |
| 4 | Sem spans duplicados | 1 span HTTP por request | [x] |
| 5 | 5xx com `status=ERROR` + log | forçar erro e inspecionar | [ ] |
| 6 | Service map com arestas esperadas | APM → Service Map e `DT-DD-22` (`GET /api/v1/service_dependencies`) | [x] |
| 7 | Admission Controller não injeta Java Agent | `kubectl get pod -o yaml | grep JAVA_TOOL_OPTIONS` | [ ] |
| 8 | `clusterName=lata-velha-prod` nas métricas do Agent | Datadog → Infrastructure | [ ] |

## Comandos

```bash
# pods sem injeção automática
for ns in lata-velha-os lata-velha-inventory lata-velha-billing; do
  kubectl get pods -n "$ns" -o jsonpath='{range .items[*]}{.metadata.namespace}/{.metadata.name}{"\n"}{end}'
done

kubectl get pod -n lata-velha-os -l app=lata-velha-os -o yaml | grep -i 'JAVA_TOOL_OPTIONS\|DD_AGENT_HOST' || echo 'sem injeção'

# env OTel por serviço
for d in "lata-velha-os/lata-velha-os" \
         "lata-velha-inventory/lata-velha-inventory" "lata-velha-billing/lata-velha-billing"; do
  ns=${d%/*}; dep=${d#*/}
  echo "== $dep"; kubectl -n "$ns" exec deploy/"$dep" -- env | grep -E 'OTEL_SERVICE_NAME|OTEL_RESOURCE_ATTRIBUTES|SERVICE_VERSION'
done
```

## Resultado

- [x] **2026-11-16 — validação pós-correlação SQS:** `verify-observability.yml` run
  [`43916144535`](https://github.com/lataVelha/lata-velha-pos-tech-fiap-mono/actions/runs/43916144535)
  — **50 pass / 0 fail / 2 skip**. `DT-DD-22` pass com `missing={}` (arestas
  os→billing, billing→os, billing→inventory, os→inventory, inventory→os) e
  `DT-DD-12` pass (logs correlacionados). Skips: `DT-DD-09` (integração AWS
  desabilitada no Academy) e `DT-DD-18` (alarme CloudWatch não forçado).
  Fluxo gerado: OS 3 (diagnóstico → `DiagnosticoFinalizado`), aprovação
  (`OrcamentoAprovado`, via port-forward direto no billing) e execução
  (`ServiceEnded`/`OSEnded`/`CobrancaEmitida`).
- [x] **2026-11-16 — ponte do edge:** o authorizer devolve `traceparent` e a
  integração `alb_traced` o repassa ao ALB. Probe da trace
  `6afb496c...` mostrou **10 spans do os-service na mesma trace** do
  authorizer; `service_dependencies` mostra `jwt-authorizer → os/inventory/billing`.
  Gate: `DT-DD-23`.
- [x] Produção validada — 2026-11-14 (relatório `datadog-observability-report.json`).
- [x] Sem referência ao serviço removido `lata-velha-api` na stack gerenciada.
- [x] Evidência detalhada (hits, p95, JVM, logs, spans, dashboard e monitores):
  [`datadog-observability.md`](./datadog-observability.md).
- [ ] Trace IDs de exemplo anexados ao PR — coletar no próximo fluxo autenticado
  (health checks não geram log de negócio).

> Dependência: traces distribuídos através de SNS/SQS (Story 18.3) precisam
> do barramento aplicado e da propagação W3C ligada. A flag antiga
> `OTEL_INSTRUMENTATION_AWS_SDK_ENABLED` continua aceita como fallback de
> `messaging.trace-propagation.enabled` (default `true`).
>
> O item 6 é automatizado pelo check `DT-DD-22` do `verify_datadog.py`: ele
> consulta `GET /api/v1/service_dependencies?env=prod` e falha se faltar
> qualquer aresta esperada entre os três serviços.
