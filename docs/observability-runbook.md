# Runbook — observabilidade (Datadog + OpenTelemetry)

Story 18.4. Serviços cobertos: `lata-velha-api`, `lata-velha-os-service`,
`lata-velha-inventory-service`, `lata-velha-billing-service`.

## 1. Encontrar o serviço no APM

1. Datadog → APM → Services.
2. Filtro `env:prod` e `service:<nome>`.
3. Se não aparecer, siga a seção 3 (serviço sem telemetria).

## 2. Filtrar logs por `trace_id`

```
service:lata-velha-os-service @trace_id:<id>
```

O `trace_id` aparece no span e nos logs (logback appender com
`capture-mdc-attributes`). Copie o ID direto da aba "Logs" do span.

## 3. Serviço sem telemetria — árvore de decisão

```bash
kubectl get pods -n <namespace>
kubectl exec -n <namespace> deploy/<service> -- env | grep -E 'OTEL|DD_'
kubectl logs -n datadog ds/datadog-agent --tail=100 | grep -i otlp
```

| Sintoma | Causa provável | Correção |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` ausente | Deployment antigo | aplicar Terraform do serviço |
| endpoint aponta para `otlp.us5.datadoghq.com` em prod | falta o override via `HOST_IP` | conferir `env` do Deployment |
| `401 Unauthorized` no Agent | `DD_API_KEY` inválida/ausente | atualizar Secret e reiniciar pods |
| nenhuma env OTel no pod | ConfigMap/Secret não montados | `kubectl describe pod` (envFrom) |
| spans duplicados | Admission Controller ativo | `datadog.apm.instrumentation.enabled=false` e label no pod |
| `service.name` errado | ConfigMap com nome antigo (`lata-velha-api`) | corrigir `OTEL_SERVICE_NAME` e reiniciar |

## 4. Validar OTLP no nó

O Agent expõe OTLP HTTP em `hostPort 4318` (e gRPC em 4317). Teste de dentro
de um pod:

```bash
kubectl run otlp-test --rm -i --restart=Never --image=curlimages/curl -- \
  curl -s -o /dev/null -w '%{http_code}\n' http://$HOST_IP:4318/v1/traces
```

> `$HOST_IP` é o `status.hostIP` do pod de teste; o Agent roda como DaemonSet.

## 5. Latência e erros

- p95 alto: verifique dependências (RDS, inventory REST) e GC.
- 5xx: filtre `status:error` no APM; correlacione com logs.
- Baseline de p95 está em `infra/terraform/observability/variables.tf`
  (`p95_baseline_ms`).

## 6. JVM e restarts

```bash
kubectl top pod -n <namespace>
kubectl describe pod -n <namespace> -l app=<service>
kubectl logs -n <namespace> deploy/<service> --previous
```

- Heap > 85%: avaliar `-Xmx`/limites e vazamento.
- Restarts > 3 em 15 min: ver `OOMKilled`/probe no describe.
- Métricas: `jvm.heap_memory`, `jvm.heap_memory_max`, `jvm.gc.*`,
  `kubernetes.containers.restarts`.

## 7. DLQ / filas

1. Alarme `[prod] DLQ de <fila> com mensagens` → siga
   [`infra/terraform/messaging/RUNBOOK-sqs.md`](../infra/terraform/messaging/RUNBOOK-sqs.md).
2. Alarme de idade de fila → verifique consumidor (`kubectl logs`).
3. As métricas `aws.sqs.*` exigem a integração AWS no Datadog. Sem ela
   (Academy), use os alarmes CloudWatch do infra-messaging.

## 8. Checklist final de validação

| Serviço | Traces | Logs c/ trace_id | Métricas | Dashboards |
|---|---|---|---|---|
| lata-velha-api | ✅ | ✅ | ✅ | ✅ |
| lata-velha-os-service | ✅ | ✅ | ✅ | ✅ |
| lata-velha-inventory-service | ✅ | ✅ | ✅ | ✅ |
| lata-velha-billing-service | ✅ | ✅ | ✅ | ✅ |

Evidências: [`docs/evidence/datadog-billing.md`](./evidence/datadog-billing.md)
e [`docs/evidence/datadog-validation.md`](./evidence/datadog-validation.md).

## 9. Onboarding

Um engenheiro novo deve conseguir, seguindo este runbook:

1. achar um serviço no APM;
2. filtrar um trace e seus logs;
3. identificar se um serviço sem telemetria está sem env, com Agent fora,
   com credencial inválida ou sem instrumentação;
4. agir em caso de DLQ.

Se algum passo falhar, abra issue e atualize este documento (Story 18.4 AC:
mudança de configuração exige atualização do runbook).
