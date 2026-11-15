# Runbook — observabilidade (Datadog + OpenTelemetry)

> **Estado em 2026-11-15:** 19.1–19.5 implementados no branch
> `feature/observability`; o deploy final está bloqueado por credenciais AWS
> Academy canceladas. As queries abaixo descrevem o alvo **após o deploy**
> (`trace.http.request`, `messaging.*`, `http.request`); a última validação
> live é o run 43670113903.

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

A Story 19.3 padronizou o nome da operação do span HTTP server para
`http.request` via `HttpSpanNameConfig` (um `SpanProcessor` no
`shared-kernel`). As trace metrics dos 3 serviços EKS são:

| Serviço | Hits/errors | Latência p95 |
|---|---|---|
| os | `trace.http.request.{hits,errors}` | `p95:trace.http.request` |
| inventory | `trace.http.request.{hits,errors}` | `p95:trace.http.request` |
| billing | `trace.http.request.{hits,errors}` | `p95:trace.http.request` |

- A latência é publicada em **segundos** e a métrica-base é a distribuição
  de duração (`trace.http.request`); **não** existe `.duration`.
- O baseline em `variables.tf` (`p95_baseline_ms`) é convertido para
  segundos no monitor (`ms / 1000`).
- O mapa fica em `trace_metric_prefixes` (`infra/terraform/observability/variables.tf`).
  Se a instrumentação mudar, altere só essa variável.
- As Lambdas continuam com `trace.aws.lambda.{hits,errors}`.

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
  Esses jobs usam os passos Terraform inline (mesmo `init`/`fmt`/`validate`/
  apply do workflow reutilizável) para não depender de o arquivo já existir em
  `infra@master` antes do merge; o workflow reutilizável fica disponível para
  dispatch manual e outros callers.
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
| Lambda auth-cpf/jwt-authorizer no APM | ✅ `DT-DD-01/03/11/12` (hits, p95, version, trace_id) |
| Logs de borda do API Gateway | ✅ `DT-DD-14` (5 eventos, sem IP) |
| Monitores de 5xx/latência de borda | ✅ `DT-DD-15` |
| SLOs e synthetics aplicados | ✅ `slos.tf`/`synthetics.tf` |
| Fallback CloudWatch → Datadog Events | ✅ recursos aplicados; entrega depende de alarme real |
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

## 13. Lambda (serverless)

As duas Lambdas de autenticação são instrumentadas pela **Datadog Lambda
Extension** + `datadog-lambda` (Story 20.1). A extensão envia direto ao site
`us5`, sem depender da integração AWS.

| Função | `DD_SERVICE` | Queries principais |
|---|---|---|
| `auth-cpf` | `lata-velha-auth-cpf` | `sum:trace.aws.lambda.hits{service:lata-velha-auth-cpf,env:prod}`, `p95:trace.aws.lambda{...}` |
| `jwt-authorizer` | `lata-velha-jwt-authorizer` | idem, com o nome da authorizer |

- Env: `DD_API_KEY` (secret), `DD_SITE=us5.datadoghq.com`, `DD_ENV=prod`,
  `DD_VERSION=<sha>`, `DD_TRACE_ENABLED=true`, `DD_LOGS_INJECTION=true`,
  `DD_SERVERLESS_LOGS_ENABLED=true`.
- Layer: `Datadog-Extension:103` em `us-east-1`.
- Os handlers logam JSON com `trace_id`/`span_id`; a negação do authorizer é
  `auth.result=denied` (decisão de negócio, não 5xx).
- Cold start: `p95:trace.aws.lambda` da auth-cpf pode chegar a ~1s; o baseline
  dos serviços EKS não se aplica a Lambda.

## 14. Borda (API Gateway)

O access log do API Gateway (`/aws/apigateway/lata-velha-app-api`) é JSON,
**sem IP/PII**, e uma subscription filter invoca a Lambda `logs-forwarder`.
Ela faz allowlist dos campos e envia ao Datadog Logs API com
`service:lata-velha-edge`, `source:apigateway`, `env:prod`.

```
logs("service:lata-velha-edge source:apigateway env:prod @http.status_code:>=500").index("*").rollup("count").last("5m") > 5
logs("service:lata-velha-edge source:apigateway env:prod @response_latency:>1000").index("*").rollup("count").last("5m") > 10
```

Monitores: `[prod] Edge (API Gateway) — 5xx acima de 5 em 5 min` e
`— latência > 1s`. O forwarder nunca envia `sourceIp`, body, query string ou
headers.

## 15. SLOs, synthetic tests e fallback AWS

- **SLOs** (`slos.tf`): disponibilidade 99.5% (warning 99.9%) e latência p95
  dentro do baseline, por serviço EKS, 30 dias.
- **Synthetics** (`synthetics.tf`): `GET /actuator/health` e
  `GET /auth/cpf-openapi.json` a cada 5 min, 3 localizações, sem dados sensíveis.
- **Fallback AWS** (`cloudwatch-events.tf`): quando
  `aws_integration_enabled=false`, um EventBridge rule encaminha
  `CloudWatch Alarm State Change` para o Datadog Events via API destination.
  No AWS Academy a role usada é a `LabRole`; a entrega depende de um alarme
  real disparar.
- `TF_VAR_alert_recipients` configura os `@` dos monitores; o gate
  `DT-DD-16` reporta `skip` explícito quando o secret está vazio.

## 16. Critérios DT-DD do gate atual

| Código | Critério |
|---|---|
| `DT-DD-01..03` | hits, erros e p95 dos 3 EKS + 2 Lambdas |
| `DT-DD-04` | JVM heap/max dos 3 EKS |
| `DT-DD-05..06` | logs e spans dos 5 serviços |
| `DT-DD-07..08` | dashboard e monitores gerenciados |
| `DT-DD-09` | SQS (skip justificado no Academy) |
| `DT-DD-10` | health público 200/UP |
| `DT-DD-11` | tags `service/env/version` nos 5 serviços |
| `DT-DD-12` | correlação log↔trace por `trace_id` nos 5 serviços |
| `DT-DD-13` | ausência de spans duplicados no trace |
| `DT-DD-14` | logs de borda no Datadog, sem IP |
| `DT-DD-15` | monitores de 5xx/latência de borda |
| `DT-DD-16` | canal de notificação dos monitores |
