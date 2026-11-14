# Runbook — observabilidade (Datadog + OpenTelemetry)

Stories 18.4, 21.1, 21.2, 21.4 e 21.5. Serviços cobertos:
`lata-velha-os-service`, `lata-velha-inventory-service`,
`lata-velha-billing-service` (o monolito `lata-velha-api` foi removido).

Stack gerenciado por Terraform: `infra/terraform/observability`
(dashboard + monitores) e `infra/terraform/addons` (Datadog Agent/OTLP).

## 1. Visão geral

| Recurso | Onde |
|---|---|
| Dashboard gerenciado | Datadog → Dashboards → **Lata Velha — Microsserviços** |
| Monitores gerenciados | tag `managed-by:terraform` (5xx, p95, heap, restarts; DLQ/idade quando integração AWS on) |
| Aplicação pública | `terraform -chdir=infra/terraform/addons output -raw app_api_endpoint` |
| Health público | `<app_api_endpoint>/actuator/health` → 200 `{"status":"UP"}` |
| Relatório de verificação | artifact `datadog-report` do job `verify-observability` |

## 2. Métricas reais do OTel (este setup)

As queries antigas `trace.http.request.hits` e
`p95:trace.http.request.duration` **não existem** aqui. O nome da operação
do span raiz depende da instrumentação:

| Serviço | Hits/errors | Latência p95 |
|---|---|---|
| os | `trace.org.springframework.boot.server.{hits,errors}` | `p95:trace.org.springframework.boot.server` |
| inventory | `trace.org.springframework.boot.server.{hits,errors}` | `p95:trace.org.springframework.boot.server` |
| billing | `trace.io.opentelemetry.spring_webmvc_6.0.server.{hits,errors}` | `p95:trace.io.opentelemetry.spring_webmvc_6.0.server` |

- A latência é publicada em **segundos** e a métrica-base é a distribuição
  de duração (`trace.<operation>`); **não** existe `trace.<operation>.duration`.
- O baseline em `variables.tf` (`p95_baseline_ms`) é convertido para
  segundos no monitor (`ms / 1000`).
- O mapa fica em `trace_metric_prefixes` (`infra/terraform/observability/variables.tf`).
  Se a instrumentação mudar, altere só essa variável.

### Por que não padronizar o span name para `http.request`?

O `OpenTelemetryWebConfig` define `operation.name=http.server.request` em um
span interno, mas a trace metric do span raiz continua no nome da
instrumentação (`org.springframework.boot.server` ou `spring_webmvc`).
Padronizar exigiria um `SpanProcessor` customizado nos três `shared-kernel`,
rebuild/redeploy dos serviços e risco de quebrar o APM/cardinalidade. A opção
escolhida (menor risco) é mapear os prefixos em Terraform.

## 3. Encontrar o serviço no APM

1. Datadog → APM → Services.
2. Filtro `env:prod` e `service:<nome>`.
3. Se não aparecer, siga a seção 10 (troubleshooting).

## 4. Filtrar logs por `trace_id`

```
service:lata-velha-os-service @trace_id:<id>
```

O `trace_id` aparece no span e, quando a aplicação loga no contexto de uma
requisição, o logback appender inclui `trace_id`/`span_id`. Logs de startup
não têm `trace_id` — use um trace de request real (ex.: `POST /ordens-servico`).

## 5. Latência e erros

- p95 alto: verifique dependências (RDS, inventory REST) e GC. Query:
  `p95:trace.org.springframework.boot.server{service:lata-velha-os-service,env:prod}`
  (segundos).
- 5xx: `sum:trace.org.springframework.boot.server.errors{service:<svc>,env:prod}.as_rate()`
  dividido por `.hits`. O `.errors` conta spans HTTP server marcados como erro
  (5xx e falhas internas). **4xx não conta**: validado com `GET` autenticado
  retornando 404 (`status=ok`, `.errors` não incrementou).
- `trace.<op>.errors` pode não ter série quando o serviço está saudável; o
  monitor trata “sem dados” como OK (`on_missing_data=resolve`).
- Monitores: `[prod] <serviço> — erros 5xx > 1%` e
  `[prod] <serviço> — p95 acima do baseline`.

## 6. JVM e restarts

```bash
kubectl top pod -n <namespace>
kubectl describe pod -n <namespace> -l app=<service>
kubectl logs -n <namespace> deploy/<service> --previous
```

- Heap > 85%: avaliar `-Xmx`/limites e vazamento.
- Restarts > 3 em 15 min: ver `OOMKilled`/probe no describe.
- Métricas JVM: `jvm.heap_memory`, `jvm.heap_memory_max`, `jvm.gc.duration`,
  `jvm.thread.count`, `jvm.cpu.recent_utilization`.
- **Restarts:** `kubernetes.containers.restarts` **não** tem tags `service`/`env`.
  Use `kube_cluster_name`, `kube_namespace` e `kube_deployment` (mapa em
  `service_namespaces`/`service_deployments`). Query do monitor:
  `max:kubernetes.containers.restarts{kube_cluster_name:lata-velha-prod,kube_namespace:<ns>,kube_deployment:<dep>}`.

## 7. DLQ / filas

1. Alarme `[prod] DLQ de <fila> com mensagens` → siga
   [`infra/terraform/messaging/RUNBOOK-sqs.md`](../infra/terraform/messaging/RUNBOOK-sqs.md).
2. Alarme de idade de fila → verifique consumidor (`kubectl logs`).
3. **Limitação AWS Academy:** as métricas `aws.sqs.*` exigem a integração AWS
   no Datadog. Sem ela, os monitores de fila **não são criados**
   (`aws_integration_enabled=false`) e a cobertura fica nos alarmes CloudWatch
   do `infra/terraform/messaging/alarms.tf` (tópico SNS de alertas). O
   dashboard mostra um note explicando o fallback.

## 8. Deploy e operação do stack

### Local (validado)

```bash
cd infra/terraform/observability
DD_API_KEY=... DD_APP_KEY=... ./apply.sh --plan   # fmt + validate + plan
DD_API_KEY=... DD_APP_KEY=... ./apply.sh --auto   # aplica dashboard + monitores
# idempotência: rode de novo e confira "No changes"
```

O script exige `DD_API_KEY`/`DD_APP_KEY`, roda `terraform fmt -check`/`validate`
e imprime `dashboard_url` + `monitor_ids`. O state é
`lata-velha/observability/terraform.tfstate`.

### Pipeline

- Workflow reutilizável: `infra/.github/workflows/observability.yml`
  (`workflow_call`; inputs `aws_region`, `destroy`, `aws_integration_enabled`;
  secrets `TF_VAR_dd_api_key`/`TF_VAR_dd_app_key`).
- Mono: `deploy-observability` (após addons), `destroy-observability` (antes
  do destroy dos addons) e `verify-observability` (após os três workloads).
- `verify-observability` roda `scripts/verify_datadog.py` e publica o artifact
  `datadog-report`.
- Em PR os jobs de CD são skipped; só a validação (`fmt`/`validate` + testes
  Python) roda.

## 9. Verificação automática

```bash
DD_API_KEY=... DD_APP_KEY=... \
  python3 scripts/verify_datadog.py \
    --env prod --window 2h \
    --api-url "$(terraform -chdir=infra/terraform/addons output -raw app_api_endpoint)" \
    --report /tmp/datadog-report.json
```

Critérios: `DT-DD-01` hits, `DT-DD-02` erros, `DT-DD-03` p95, `DT-DD-04` JVM,
`DT-DD-05` logs, `DT-DD-06` spans, `DT-DD-07` dashboard, `DT-DD-08` monitores,
`DT-DD-09` SQS (skip justificado no Academy), `DT-DD-10` health 200/UP.
Exit code 1 em falha; 0 com skips justificados.

## 10. Troubleshooting (serviço sem telemetria)

```bash
kubectl get pods -n <namespace>
kubectl exec -n <namespace> deploy/<service> -- env | grep -E 'OTEL|DD_'
kubectl logs -n datadog ds/datadog --tail=100 | grep -i otlp
```

| Sintoma | Causa provável | Correção |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` ausente | Deployment antigo | aplicar Terraform do serviço |
| endpoint aponta para `otlp.us5.datadoghq.com` em prod | falta o override via `HOST_IP` | conferir `env` do Deployment |
| `401 Unauthorized` no Agent | `DD_API_KEY` inválida/ausente | atualizar Secret e reiniciar pods |
| nenhuma env OTel no pod | ConfigMap/Secret não montados | `kubectl describe pod` (envFrom) |
| spans duplicados | Admission Controller ativo | `datadog.apm.instrumentation.enabled=false` e label no pod |
| `service.name` errado | ConfigMap com nome antigo | corrigir `OTEL_SERVICE_NAME` e reiniciar |
| trace metric some após deploy | nome da operação mudou | atualizar `trace_metric_prefixes` e reaplicar |

## 11. Checklist final de validação

| Verificação | Resultado |
|---|---|
| Dashboard gerenciado com séries dos 3 serviços | ✅ (ver `docs/evidence/datadog-observability.md`) |
| Monitores 5xx/p95/heap/restarts dos 3 serviços | ✅ 12 monitores `managed-by:terraform` |
| Logs dos 3 serviços na janela | ✅ relatório `datadog-report` |
| Spans dos 3 serviços na janela | ✅ relatório `datadog-report` |
| Trace metrics hits/p95/errors | ✅ relatório `datadog-report` |
| JVM heap/max dos 3 serviços | ✅ relatório `datadog-report` |
| Health público 200/UP | ✅ `DT-DD-10` |
| SQS | ⚠️ N/A no AWS Academy; alarmes CloudWatch documentados |

Evidência principal: [`docs/evidence/datadog-observability.md`](./evidence/datadog-observability.md)
e [`docs/evidence/datadog-observability-report.json`](./evidence/datadog-observability-report.json).

## 12. Onboarding

Um engenheiro novo deve conseguir, seguindo este runbook:

1. achar um serviço no APM e o dashboard;
2. filtrar um trace e correlacionar logs;
3. identificar se um serviço sem telemetria está sem env, com Agent fora,
   com credencial inválida ou sem instrumentação;
4. entender por que `aws.sqs.*` não aparece no Academy e qual é o fallback;
5. rodar `apply.sh --plan` e `verify_datadog.py` com segurança.

Se algum passo falhar, abra issue e atualize este documento.
