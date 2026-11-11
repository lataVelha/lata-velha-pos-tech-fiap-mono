# Evidências — Datadog do billing-service

Story 16.4. Este documento registra a validação **pendente de execução contra
AWS real** e o checklist objetivo usado no ambiente de staging/produção.

## Pré-requisitos aplicados

- [x] `billing-service/src/main/resources/application.yaml` com bloco `otel:` e
      `management.tracing.sampling.probability=1.0`.
- [x] `billing-service/k8s/configmap.yaml` com `OTEL_SERVICE_NAME=lata-velha-billing-service`,
      `DEPLOYMENT_ENVIRONMENT_NAME=prod`, `SERVICE_VERSION=${service_version}` e
      `OTEL_RESOURCE_ATTRIBUTES` coerente.
- [x] `billing-service/k8s/deployment.yaml` com `HOST_IP` e os quatro
      `OTEL_EXPORTER_OTLP_*_ENDPOINT`.
- [x] `billing-service/k8s/secret.yaml` com `DD_API_KEY`.
- [x] `billing-service/terraform/variables.tf` com `dd_api_key` (sensitive).
- [x] `billing-service/terraform/k8s.tf` passando `dd_api_key_b64` ao Secret.
- [x] `billing-service/terraform/apply.sh` exportando `TF_VAR_dd_api_key`.
- [x] Workflow do billing passando `TF_DD_API_KEY`.

## Checklist de validação (executar após deploy)

| # | Ação | Evidência esperada |
|---|---|---|
| 1 | `POST /pagamentos` | span `POST /pagamentos` em `service:lata-velha-billing-service` |
| 2 | `GET /pagamentos/{id}` | span `GET /pagamentos/{id}` |
| 3 | `POST /pagamentos/webhook` | span `POST /pagamentos/webhook` |
| 4 | Logs filtrados por `service:lata-velha-billing-service` | linhas com `trace_id` |
| 5 | Métricas | `jvm.heap_memory`, `trace.http.request.*` do billing |
| 6 | Trace tags | `env:prod`, `version:<sha>` |
| 7 | `kubectl logs -n datadog ds/datadog-agent` | zero erros de OTLP para o billing |

## Comandos

```bash
kubectl -n lata-velha-billing port-forward svc/lata-velha-billing 18081:80 &
curl -s -X POST localhost:18081/pagamentos -H 'Content-Type: application/json' \
  -d '{"ordemServicoId":1,"valor":250.00,"descricao":"evidencia datadog"}'
curl -s localhost:18081/pagamentos/1

kubectl -n lata-velha-billing exec deploy/lata-velha-billing -- env | grep -E 'OTEL|DD_API_KEY'
```

## Resultado

- [ ] Executado em staging
- [ ] Executado em produção
- [ ] Links/IDs de trace anexados ao PR

> Enquanto o ambiente AWS não estiver disponível, o código/configuração estão
> prontos e validados por `terraform validate` + `mvn test`.
