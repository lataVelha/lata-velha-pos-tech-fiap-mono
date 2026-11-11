# Evidências — validação de identidade e telemetria (4 serviços)

Story 17.4. Checklist auditável de service map, logs correlacionados e
ausência de duplicação. Executar em staging antes do go-live.

## Identidade esperada

| Serviço | `OTEL_SERVICE_NAME` / `service.name` | `env` | `version` |
|---|---|---|---|
| app | `lata-velha-api` | `prod` | SHA da imagem (`TF_VAR_service_version`) |
| os | `lata-velha-os-service` | `prod` | SHA da imagem |
| inventory | `lata-velha-inventory-service` | `prod` | SHA da imagem |
| billing | `lata-velha-billing-service` | `prod` | SHA da imagem |

## Checklist

| # | Verificação | Como | OK |
|---|---|---|---|
| 1 | Quatro serviços distintos no APM | Datadog → APM → Services (`env:prod`) | [ ] |
| 2 | `service.name` correto por trace | abrir trace e conferir a tag | [ ] |
| 3 | Logs correlacionados por `trace_id` | filtro `@trace_id:<id>` | [ ] |
| 4 | Sem spans duplicados | 1 span HTTP por request | [ ] |
| 5 | 5xx com `status=ERROR` + log | forçar erro e inspecionar | [ ] |
| 6 | Service map com arestas esperadas | APM → Service Map | [ ] |
| 7 | Admission Controller não injeta Java Agent | `kubectl get pod -o yaml | grep JAVA_TOOL_OPTIONS` | [ ] |
| 8 | `clusterName=lata-velha-prod` nas métricas do Agent | Datadog → Infrastructure | [ ] |

## Comandos

```bash
# pods sem injeção automática
for ns in lata-velha lata-velha-os lata-velha-inventory lata-velha-billing; do
  kubectl get pods -n "$ns" -o jsonpath='{range .items[*]}{.metadata.namespace}/{.metadata.name}{"\n"}{end}'
done

kubectl get pod -n lata-velha-os -l app=lata-velha-os -o yaml | grep -i 'JAVA_TOOL_OPTIONS\|DD_AGENT_HOST' || echo 'sem injeção'

# env OTel por serviço
for d in "lata-velha/lata-velha-api" "lata-velha-os/lata-velha-os" \
         "lata-velha-inventory/lata-velha-inventory" "lata-velha-billing/lata-velha-billing"; do
  ns=${d%/*}; dep=${d#*/}
  echo "== $dep"; kubectl -n "$ns" exec deploy/"$dep" -- env | grep -E 'OTEL_SERVICE_NAME|OTEL_RESOURCE_ATTRIBUTES|SERVICE_VERSION'
done
```

## Resultado

- [ ] Staging validado — data: ____
- [ ] Produção validada — data: ____
- [ ] Trace IDs de exemplo anexados ao PR:
  - app: ____
  - os: ____
  - inventory: ____
  - billing: ____

> Dependência: traces distribuídos através de SNS/SQS (Story 18.3) precisam
> do barramento aplicado e da feature flag `OTEL_INSTRUMENTATION_AWS_SDK_ENABLED`
> ligada (default).
