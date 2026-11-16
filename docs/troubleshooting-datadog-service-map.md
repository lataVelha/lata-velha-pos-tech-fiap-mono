# Troubleshooting — Service Map do Datadog e correlação assíncrona

**Data:** 2026-11-16
**Escopo:** `lata-velha-pos-tech-fiap-mono` (os, inventory, billing) + `infra/terraform`
**Sintoma relatado:** no Dependency Map/Service Map só aparecem os vizinhos diretos — e às vezes nem isso. Os traces não atravessam o barramento SNS/SQS.

---

## 1. TL;DR

Há **duas causas independentes** para o mapa pobre, mais alguns contribuintes:

| # | Causa | Severidade | Efeito no mapa |
|---|---|---|---|
| 1 | `ReceiveMessageRequest` não pede `messageAttributeNames` → `traceparent` é descartado no consumo | **P0** | Cada consumo vira trace raiz; sem parent-child não há aresta entre serviços |
| 2 | Agent 7.53.0 (chart 3.60.0) sem `compute_stats_by_span_kind` / `peer_tags_aggregation` / `peer_tags` | **P1** | Bancos, filas e APIs externas não viram nós inferidos (nem as arestas para eles) |
| 3 | Fan-out SNS: produtor referencia o tópico, consumidor referencia a fila | Estrutural | O elo tópico→fila não existe no grafo; o mapa fica em hub, não em cadeia |
| 4 | Volume baixo (~19 hits/2h) + janela curta | Contribuinte | Service Map pode ficar vazio em janelas de 15–60 min |
| 5 | Gate de CI não valida `service_dependencies` | Contribuinte | “45 pass” não prova topologia; o item 6 do checklist está desmarcado |

**Correção mínima que resolve o sintoma principal:** pedir `messageAttributeNames("All")` no receive (uma linha em cada cópia do shared-kernel) + habilitar peer tags no Agent. O resto é hardening.

---

## 2. O que está correto (não mexer)

Antes de listar o que está errado, o que já está certo — para não gastar tempo caçando fantasma:

- **Agent com OTLP HTTP receiver + hostPort 4318** (`infra/terraform/addons/main.tf:239-270`). Sem o hostPort, nada chegaria; o relatório mostra traces/logs chegando, então o caminho está bom.
- **Nomes distintos por serviço** (`OTEL_SERVICE_NAME=lata-velha-{os,inventory,billing}-service`) em `*/k8s/configmap.yaml`.
- **`env=prod` e `service.version`** via `OTEL_RESOURCE_ATTRIBUTES` nos configmaps.
- **SNS → SQS com `raw_message_delivery = true`** (`infra/terraform/messaging/subscriptions.tf:33`), condição necessária para os message attributes chegarem ao SQS.
- **Filter policy por `eventType`** (`subscriptions.tf:34-37`). O fato de o roteamento funcionar prova que o atributo `eventType` chega ao SQS — logo, o problema não é o SNS entregar atributos, é o consumidor não pedi-los.
- **Publisher injeta W3C `traceparent`** nos message attributes (`SqsEventPublisher.java:82-89`).
- **Amostragem em 100%** — os/inventory têm `micrometer-tracing-bridge-otel` no pom (a propriedade `management.tracing.sampling.probability` é efetiva); billing não tem (a propriedade é inerte lá). Em ambos os casos o default do OTel é `parentbased_always_on` (100% na raiz), então não é a causa.

---

## 3. Fluxo observado

```
Cliente
  │ HTTP
  ▼
API Gateway ──► os-service ──► SNS topic lata-velha-events
                    │                    │
                    │                    ├── filter eventType=... ──► SQS os-service-events
                    │                    ├── filter eventType=... ──► SQS inventory-service-events
                    │                    └── filter eventType=... ──► SQS billing-service-events
                    │
                    │ (JPA) PostgreSQL
                    ▼
              (eventos consumidos pelos 3 serviços, cada um no seu poller)

billing-service ──HTTP (RestClient)──► inventory-service
```

Cada serviço envia OTLP para `http://$(HOST_IP):4318` (Agent DaemonSet).

---

## 4. Hipóteses e veredictos

| Hipótese | Como foi verificada | Veredicto |
|---|---|---|
| H1. “Service Page → Dependencies só mostra 1 hop por design” | Documentação do Datadog | **Confirmado** — comportamento esperado; o mapa completo é `APM → Service Map` |
| H2. “O `traceparent` é injetado mas não extraído no SQS” | Leitura do `ReceiveMessageRequest` + `carrierOf()` + `PROPAGATOR.extract` | **Confirmado — causa raiz P0** |
| H3. “O Agent não calcula inferred services” | Chart 3.60.0 → Agent 7.53.0; documentação de peer tags | **Confirmado — causa P1** |
| H4. “Falta span de client no billing → inventory (RestClient)” | Starter 2.13 tem `RestClientInstrumentationAutoConfiguration`; o span deve existir, mas o peer pode ser hostname | **Parcial — validar se vira aresta para o serviço** |
| H5. “Volume/janela curtos esvaziam o mapa” | Relatório: ~19 hits/2h por serviço | **Confirmado — contribuinte** |
| H6. “O CI passa sem provar arestas” | `verify_datadog.py` não chama `service_dependencies`; checklist item 6 desmarcado; `correlated_events=0` | **Confirmado — contribuinte** |
| H7. “`management.tracing.sampling.probability` controla o sampler” | os/inventory têm `micrometer-tracing-bridge-otel`; billing não | **Parcial — efetiva em os/inventory, inerte em billing** |

---

## 5. Evidências detalhadas

### 5.1 P0 — `traceparent` é injetado mas nunca lido

**Arquivo:** `os-service/shared-kernel/src/main/java/br/com/lata/velha/shared/infrastructure/messaging/sqs/SqsEventListener.java`
(as três cópias — os, inventory, billing — são **byte-idênticas**, mesmo md5 `59f9fb45…`)

```java
// linha 138
var request = ReceiveMessageRequest.builder()
        .queueUrl(queueUrl)
        .maxNumberOfMessages(10)
        .waitTimeSeconds(waitTimeSeconds)
        .build();   // <-- falta .messageAttributeNames("All")

// linha 153
Context parent = tracePropagationEnabled
        ? PROPAGATOR.extract(Context.current(), carrierOf(message), GETTER)
        : Context.current();

// linha 197
private Map<String, String> carrierOf(Message message) {
    if (message.messageAttributes() == null || message.messageAttributes().isEmpty()) {
        return Map.of();
    }
    ...
}
```

**Por que quebra:** a AWS SQS **não retorna message attributes por padrão**. É preciso pedir explicitamente `.messageAttributeNames("All")` (ou os nomes desejados). Sem isso, `message.messageAttributes()` vem vazio, `carrierOf()` retorna `Map.of()`, o `extract` não encontra `traceparent` e o span CONSUMER nasce como **raiz**.

**Consequência:** cada evento consumido em inventory/billing/os é um trace novo, sem parent. O Service Map é derivado de traces completos com parent-child; sem essa ligação, não existe aresta `os-service → inventory-service` atravessando a fila. Sobram apenas os vizinhos locais de cada serviço — exatamente o sintoma.

**Sintoma irmão no mesmo arquivo:** a linha 169 lê `APPROXIMATE_RECEIVE_COUNT` de `message.attributes()`, mas o request também não pede `.messageSystemAttributeNames(...)`; a tag `messaging.message.receive_count` nunca é preenchida.

**Por que os testes não pegaram:**

- `SqsEventListenerTest` chama `listener.processMessage(message())` direto (linhas 73, 86, 98, 111) — nunca exercita o `receiveAndProcessBatch`.
- `LocalStackSqsIT` valida raw delivery + filter policy, mas o `awaitMessage` (linha 157) também não pede atributos; e o teste não verifica `traceparent`.
- O gate de CI valida hits/erros/latência/logs, não arestas.

**Prova indireta de que os atributos chegam ao SQS:** a filter policy do SNS é `filter_policy_scope = "MessageAttributes"` e usa `eventType` (`subscriptions.tf:34-37`). Se o atributo não chegasse, nenhum evento seria roteado. Logo, o único elo quebrado é a leitura no consumidor.

### 5.2 P1 — Agent sem inferred services

**Evidência de versão:** `infra/terraform/addons/main.tf` usa o chart `datadog` **3.60.0**, que pina `agents.image.tag: 7.53.0` (verificado no `values.yaml` do chart).

A documentação do Datadog (Inferred services) diz:

- **Agent 7.60.0+**: `apm_config.compute_stats_by_span_kind` e `apm_config.peer_tags_aggregation` já vêm habilitados.
- **Agent 7.55.1–7.59.1**: habilitar os dois booleanos.
- **Agent 7.50.3–7.54.1** (faixa do 7.53.0): habilitar os dois booleanos **e** a lista `apm_config.peer_tags` completa.

O Terraform **não seta nenhum desses**. Consequência: sem `compute_stats_by_span_kind`, as trace metrics são calculadas só para server spans; sem `peer_tags_aggregation`, os atributos de peer não viram tags. O resultado é que bancos, filas e APIs externas **não aparecem como nós inferidos** — e as arestas para eles somem. Isso explica o “às vezes nem os vizinhos”.

### 5.3 P2 — Fan-out SNS e nomes de destino

O produtor usa `messaging.destination.name` = **tópico** (`lata-velha.events`). O consumidor usa `messaging.destination.name` = **fila** (`lata-velha-inventory-service-events`, etc.). O Datadog não tem telemetria da assinatura SNS→SQS, então tópico e filas são entidades distintas. Mesmo corrigindo o P0, o mapa não será uma cadeia linear; será um hub com o tópico e as filas como nós inferidos.

**Decisão recomendada:** aceitar o hub como representação correta do barramento e declarar a topologia de negócio no **Software Catalog** (`dependsOn`), que não depende de tráfego. Não vale a pena publicar direto nas filas só para “embelezar” o mapa — perderia o fan-out.

### 5.4 P2 — `billing → inventory` (RestClient)

`billing-service/.../infrastructure/inventory/InventoryServiceClient.java` usa `RestClient`. O starter 2.13 **tem** `RestClientInstrumentationAutoConfiguration` (interceptor via `RestClientCustomizer`), então o span CLIENT deve existir. O que validar é o peer: se ele sai como `server.address`/hostname em vez de `peer.service=lata-velha-inventory-service`, o mapa mostra um nó inferido (host) em vez de aresta para o serviço.

**Validar:** abrir um trace de `POST /billing/...` e procurar um span client `http get`/`POST` com `server.address` ou `peer.service` apontando para o inventory. Se não houver, instrumentar explicitamente (interceptor que cria span CLIENT com `peer.service`) ou trocar por `RestTemplate`.

### 5.5 P2 — Janela e volume

O relatório de evidência mostra ~19 hits/2h por serviço. O Service Map só mostra nós com dados **na janela selecionada** e é renderizado a partir de traces completos com root spans. Em janelas de 15–60 min, é esperado ficar vazio ou quase. Para validar, use uma janela de 24h ou gere tráfego antes.

### 5.6 P2 — O gate não prova topologia

- `scripts/verify_datadog.py` não chama `/api/v1/service_dependencies`.
- `docs/evidence/datadog-validation.md:23` — “Service map com arestas esperadas” está `[ ]` (desmarcado).
- `check_log_correlation` (`verify_datadog.py:500-554`) marca `pass` se encontrar **qualquer** `trace_id` (inclusive fallback de span); o relatório tem `correlated_events: 0` em todos os serviços. Ou seja: “45 pass” não prova nem arestas nem correlação de log.

### 5.7 P2 — Instrumentação inconsistente entre os serviços

Os três serviços não estão instrumentados da mesma forma:

| Serviço | `micrometer-tracing-bridge-otel` | `OpenTelemetryAppender` explícito no logback `prod` | Trace metric de servidor |
|---|---|---|---|
| os-service | sim (`pom.xml:170-173`) | sim (`logback-spring.xml:69-73`) | `trace.org.springframework.boot.server.*` |
| inventory-service | sim (`pom.xml:173-176`) | sim | `trace.org.springframework.boot.server.*` |
| billing-service | **não** | **não** (só `CONSOLE_JSON`) | `trace.io.opentelemetry.spring_webmvc_6.0.server.*` |

Consequências:

- **Nomes de span/métrica diferentes** entre billing e os demais — o `verify_datadog.py` já compensa isso com métricas distintas por serviço, mas dashboards e monitores ficam assimétricos.
- **Appender de log**: o `OpenTelemetryAppender` explícito em os/inventory é **redundante** — o starter auto-instala o appender quando `otel.instrumentation.logback-appender.enabled=true` (`LogbackAppenderInstaller`), e essa propriedade está `true` nos três `application.yaml`. Ou seja, billing também exporta logs via OTLP; a diferença real é o **bridge Micrometer**, que muda o naming dos spans e pode mudar o MDC (`%X{trace_id}`). O `correlated_events: 0` do relatório precisa de diagnóstico próprio (query/facet), não é explicado só por isso.
- **Risco de duplicidade**: os/inventory podem ter span de servidor do OTel starter **e** span do bridge Micrometer; o `check_duplicate_spans` não achou duplicata no trace amostrado, mas o teste olha só `span.kind=server` + rota. Vale validar com um trace real.

**Decisão recomendada (Fase 3):** escolher um caminho único. O mais simples é remover o `micrometer-tracing-bridge-otel` de os/inventory e usar só o OTel starter (que já traz `opentelemetry-logback-mdc-1.0` para o `%X{trace_id}`), alinhando billing ao mesmo padrão. A alternativa é adicionar o bridge ao billing, mas isso muda os nomes de span/métrica dele e exige atualizar dashboards/monitores.

---

## 6. Por que o sintoma é “só vizinhos, às vezes nem isso”

1. **Service Page → Dependencies** é 1 hop por design. Para o grafo completo, use `APM → Service Map`.
2. **Service Map é grafo de traces**, não diagrama de arquitetura. Sem parent-child entre serviços, não há aresta.
3. Com o P0, cada consumo é trace raiz → inventory/billing/os ficam **desconectados** entre si. Sobram os vizinhos locais (Postgres/Mongo) — e, com o P1, nem esses aparecem como nós inferidos.
4. Com volume baixo e janela curta, até os nós locais somem.

---

## 7. Plano de correção

### Fase 0 — Baseline e reprodução (0,5 dia)

Objetivo: provar o defeito antes de mexer, no ciclo AI-ATDD (RED).

- [ ] **0.1** Capturar um trace de um POST que publique evento (ex.: `POST /api/v1/os` ou aprovar orçamento). Salvar o `trace_id` do produtor.
- [ ] **0.2** No Datadog, confirmar que o consumidor tem **outro** `trace_id` (ou nenhum parent). Esse é o RED.
- [ ] **0.3** Consultar `GET /api/v1/service_dependencies?env=prod&start=<epoch>&end=<epoch>` e registrar as arestas atuais (provavelmente vazio ou só self).
- [ ] **0.4** Rodar `kubectl -n datadog get ds datadog-agent -o jsonpath='{.spec.template.spec.containers[0].image}'` e confirmar `7.53.0`.
- [ ] **0.5** Rodar `kubectl -n datadog exec ds/datadog-agent -- agent config | grep -E "compute_stats_by_span_kind|peer_tags_aggregation|peer_tags"` e confirmar que está vazio/false.

### Fase 1 — P0: corrigir o receive do SQS (shared-kernel)

**Repo:** `lata-velha-pos-tech-fiap-shared` (submódulo em cada serviço, todos no commit `fe871a4`)
**Arquivo:** `src/main/java/br/com/lata/velha/shared/infrastructure/messaging/sqs/SqsEventListener.java`

- [ ] **1.1** Alterar o request:

```java
var request = ReceiveMessageRequest.builder()
        .queueUrl(queueUrl)
        .maxNumberOfMessages(10)
        .waitTimeSeconds(waitTimeSeconds)
        .messageAttributeNames("All")
        .messageSystemAttributeNames(MessageSystemAttributeName.APPROXIMATE_RECEIVE_COUNT)
        .build();
```

- [ ] **1.2** Trocar o nome do flag de propagação para algo que não pareça configuração da AWS SDK:

```java
@Value("${messaging.trace-propagation.enabled:${OTEL_INSTRUMENTATION_AWS_SDK_ENABLED:true}}")
private boolean tracePropagationEnabled;
```

  (mantém compatibilidade com a env atual por um release; depois remove o fallback)

- [ ] **1.3** Adicionar semconv que ajuda o Datadog a inferir o barramento (opcional, mas recomendado):

```java
span.setAttribute("messaging.destination.kind", "queue");
// no publisher:
span.setAttribute("messaging.destination.kind", "topic");
```

  > Não troque o `W3CTraceContextPropagator` pelo propagador global (`GlobalOpenTelemetry.getPropagators()`) sem avaliar o limite de **10 message attributes** do SNS: baggage/tracestate podem estourar o limite. O W3C puro já resolve o parent-child.

- [ ] **1.4** Deixar `receiveAndProcessBatch()` e `carrierOf()` package-private para teste.

- [ ] **1.5** Testes (novos):

  1. **Request shape** — mockar `SqsClient`, capturar o `ReceiveMessageRequest` e assertar `messageAttributeNames` contém `"All"` e `attributeNamesAsStrings` contém `APPROXIMATE_RECEIVE_COUNT`.
  2. **Carrier extraction** — montar um `Message` com `traceparent` e assertar que `carrierOf()` devolve o valor.
  3. **Span parent** — com `SdkTracerProvider` + `InMemorySpanExporter`, criar um span PRODUCER, injetar o contexto, chamar `processMessage` e assertar que o span CONSUMER tem o mesmo `traceId` e `parentSpanId` do produtor.
  4. **Fallback** — mensagem sem `traceparent` continua sendo processada (span raiz), sem exceção.
  5. **LocalStack IT** — publicar no SNS com `eventType` + `traceparent`, receber com `messageAttributeNames("All")` e assertar que o atributo sobreviveu ao raw delivery; em seguida, rodar o listener e assertar a continuidade.

- [ ] **1.6** Rodar os testes nas 3 cópias (`os`, `inventory`, `billing`), porque o módulo é copiado como submódulo.

### Fase 2 — P1: habilitar inferred services no Agent (infra)

**Repo:** `lata-velha-pos-tech-fiap-infra`
**Arquivo:** `infra/terraform/addons/main.tf`

- [ ] **2.1** Adicionar env vars no chart 3.60.0 (Agent 7.53.0):

```hcl
# O chart 3.60.0 pina Agent 7.53.0; para essa faixa os peer tags não vêm
# habilitados. Use `values` (e não `set`) para o array JSON não ser
# interpretado como lista pelo parser --set do Helm.
values = [
  yamlencode({
    datadog = {
      env = [
        { name = "DD_APM_COMPUTE_STATS_BY_SPAN_KIND", value = "true" },
        { name = "DD_APM_PEER_TAGS_AGGREGATION", value = "true" },
        {
          name = "DD_APM_PEER_TAGS"
          value = jsonencode([
            "_dd.base_service", "active_record.db.vendor", "amqp.destination",
            "amqp.exchange", "amqp.queue", "aws.queue.name", "aws.s3.bucket",
            "bucketname", "cassandra.keyspace", "db.cassandra.contact.points",
            "db.couchbase.seed.nodes", "db.hostname", "db.instance", "db.name",
            "db.namespace", "db.system", "db.type", "dns.hostname", "grpc.host",
            "hostname", "http.host", "http.server_name", "messaging.destination",
            "messaging.destination.name", "messaging.kafka.bootstrap.servers",
            "messaging.rabbitmq.exchange", "messaging.system", "mongodb.db",
            "msmq.queue.path", "net.peer.name", "network.destination.ip",
            "network.destination.name", "out.host", "peer.hostname",
            "peer.service", "queuename", "rpc.service", "rpc.system",
            "sequel.db.vendor", "server.address", "streamname", "tablename",
            "topicname"
          ])
        },
      ]
    }
  })
]
```

> A lista é a exigida pela faixa 7.50.3–7.54.1. Se o Agent for atualizado para ≥7.60, os três blocos podem ser removidos.

- [ ] **2.2** Alternativa (preferível a médio prazo): atualizar o chart para uma versão que pina Agent ≥7.60 e remover a configuração manual. Fazer em staging primeiro; upgrade de chart pode mudar defaults.

- [ ] **2.3** Verificar no pod:

```bash
kubectl -n datadog exec ds/datadog-agent -- agent config | \
  grep -E "compute_stats_by_span_kind|peer_tags_aggregation|peer_tags"
```

- [ ] **2.4** Monitorar custo: `compute_stats_by_span_kind` e `peer_tags_aggregation` aumentam a cardinalidade de custom metrics. Conferir `datadog.estimated_usage.metrics.custom` antes/depois.

### Fase 3 — Secundários

- [ ] **3.1** Validar o span de `billing → inventory`. O starter 2.13 já instrumenta `RestClient`; se o peer sair como hostname, adicionar `peer.service=lata-velha-inventory-service` (interceptor/customizer) ou registrar a aresta no Catalog.
- [ ] **3.2** Declarar a topologia de negócio no **Software Catalog** (`service.datadog.yaml`), com `dependsOn` entre os três serviços e as filas/tópico. Isso dá um mapa “de arquitetura” independente de tráfego.
- [ ] **3.3** Alinhar `spring.application.name` (`*-api`) com `OTEL_SERVICE_NAME` (`*-service`) para não fragmentar o nó se a env faltar.
- [ ] **3.4** Alinhar a configuração de sampling: em billing a propriedade é inerte; em os/inventory é efetiva. Ou padronizar tudo em `otel.traces.sampler: parentbased_always_on` (e remover `management.tracing.*`), ou padronizar o bridge nos três. Não deixar os dois modelos convivendo.
- [ ] **3.5** (Opcional) Avaliar **USM** no Agent (`datadog.serviceMonitoring.enabled=true`) para ver conexões TCP de serviços não instrumentados. Não substitui o parent-child, mas ajuda no hop HTTP.
- [ ] **3.6** Normalizar a instrumentação (ver 5.7): remover o `micrometer-tracing-bridge-otel` redundante de os/inventory **ou** adicionar o bridge + `OpenTelemetryAppender` ao billing. Preferência: remover o bridge e ficar só com o OTel starter (`opentelemetry-logback-mdc-1.0` já está no starter e popula `%X{trace_id}`).
- [ ] **3.7** Validar a correlação de log em billing (o appender é auto-instalado pelo starter; o `%X{trace_id}` depende do MDC do OTel) e remover o `OpenTelemetryAppender` explícito redundante de os/inventory, se o auto-instalador já cobrir os três. Não manter dois caminhos de exportação de log sem necessidade.

### Fase 4 — Gate de CI

> **Ordem AI-ATDD:** implemente 4.1–4.3 **antes** das Fases 1–2 e rode o gate para registrar o RED (as arestas vão faltar). O GREEN vem depois do deploy da Fase 1 e da config da Fase 2.

**Repo:** mono (`scripts/verify_datadog.py`, `.github/workflows/verify-observability.yml`)

- [ ] **4.1** Adicionar `DatadogClient.get_service_dependencies(env, start, end)`:

```python
def get_service_dependencies(self, env: str, start: datetime, end: datetime) -> dict[str, Any]:
    params = urllib.parse.urlencode({
        "env": env,
        "start": int(start.timestamp()),
        "end": int(end.timestamp()),
    })
    return _request("GET", f"{self.api_url}/api/v1/service_dependencies?{params}", self.headers)
```

- [ ] **4.2** Adicionar `DT-DD-22` — arestas esperadas:

```python
EXPECTED_EDGES = {
    "lata-velha-os-service": {"lata-velha-inventory-service", "lata-velha-billing-service"},
    "lata-velha-billing-service": {"lata-velha-os-service", "lata-velha-inventory-service"},
    "lata-velha-inventory-service": {"lata-velha-os-service"},
}
```

  A resposta da API é `{"servico": {"calls": ["outro_servico", ...]}}`. Ignorar self-edges e nós inferidos (filas/tópico).

- [ ] **4.3** O check deve ser **fail** se faltar qualquer aresta esperada. Se o ambiente não tiver tráfego suficiente na janela, usar uma janela maior (24h) ou gerar o fluxo antes (o workflow já faz warmup).

- [ ] **4.4** Endurecer `check_log_correlation`: se `trace_id` foi encontrado mas `correlated_events == 0`, retornar `fail` (hoje retorna `pass`). Se não houver `trace_id` nenhum, `skip` com motivo explícito.

- [ ] **4.5** Atualizar `docs/evidence/datadog-validation.md`: marcar o item 6 e adicionar o `DT-DD-22` à tabela de evidências.

### Fase 5 — Higiene e documentação

- [ ] **5.1** Documentar no `docs/datadog-k8s-production.md` que o mapa é trace-derived e que o barramento é hub (SNS).
- [ ] **5.2** Registrar o “runbook” de investigação: se o mapa sumir, checar (a) tráfego na janela, (b) `traceparent` presente no SQS, (c) config do Agent, (d) `service_dependencies`.
- [ ] **5.3** Adicionar um alerta (ou monitor) para queda de arestas? Melhor: o gate de CI já cobre o deploy; um monitor de “trace contínuo” seria sintético demais. Opcional.

---

## 8. Critérios de aceite (AI-ATDD)

| ID | Given / When / Then | Camada |
|---|---|---|
| AC-1 | Dado um evento publicado com `traceparent`, quando o consumidor recebe, então o span CONSUMER tem o mesmo `traceId` do PRODUCER e `parentSpanId` = `spanId` do PRODUCER | Unit (in-memory exporter) |
| AC-2 | Dado o poller, quando ele monta o `ReceiveMessageRequest`, então o request inclui `messageAttributeNames("All")` e `messageSystemAttributeNames(APPROXIMATE_RECEIVE_COUNT)` | Unit (mock SqsClient) |
| AC-3 | Dada uma mensagem sem `traceparent` (mensagem antiga), quando o consumidor processa, então não há exceção e o span é raiz | Unit |
| AC-4 | Dado o LocalStack, quando o SNS publica com `eventType` + `traceparent` e raw delivery, então o SQS entrega o atributo intacto | IT (LocalStack) |
| AC-5 | Dado `env=prod`, quando o gate consulta `service_dependencies`, então as arestas esperadas existem | E2E (Datadog API) |
| AC-6 | Dado um log emitido dentro de um span, quando o gate consulta logs, então pelo menos 1 evento tem `trace_id` correlacionável | E2E (Datadog API) |

**Matriz de testes:**

| Teste | Onde | O que cobre |
|---|---|---|
| `SqsEventListenerTest.receiveRequestPedeMessageAttributes` | shared-kernel | AC-2 |
| `SqsEventListenerTest.consumerSpanIsChildOfProducer` | shared-kernel | AC-1 |
| `SqsEventListenerTest.mensagemSemTraceparentNaoQuebra` | shared-kernel | AC-3 |
| `LocalStackSqsIT.traceparentSobreviveAoFanout` | shared-kernel | AC-4 |
| `verify_datadog.py` `DT-DD-22` | mono/CI | AC-5 |
| `verify_datadog.py` `DT-DD-12` endurecido | mono/CI | AC-6 |

---

## 9. Rollout multi-repo

O shared-kernel é submódulo do mesmo repo (`lata-velha-pos-tech-fiap-shared`) em três serviços, todos no commit `fe871a4`. A ordem é:

1. **shared** — branch `fix/sqs-trace-context`, implementar Fase 1 + testes, PR, merge.
2. **os-service**, **inventory-service**, **billing-service** — `git submodule update --remote shared-kernel`, rodar `./mvnw test`, commitar o bump do ponteiro, PR, merge.
3. **mono** — bump dos três submódulos de serviço, atualizar docs/evidence, PR.
4. **infra** — Fase 2 (env vars do Agent), PR, apply.
5. **Deploy** dos três serviços (a correção é backward compatible: mensagens sem `traceparent` continuam funcionando).
6. **Gate** — rodar `verify_datadog.py` e atualizar a evidência.

> A ordem entre 2 e 4 não importa: a correção de propagação e o inferred services são independentes. Se quiser minimizar risco, faça 4 primeiro (é só config) e observe o mapa por 1 dia.

---

## 10. Validação

### Local (sem Datadog)

```bash
# shared-kernel
cd os-service && ./mvnw test -Dtest='SqsEventListenerTest'

# LocalStack
docker compose -f docker-compose.test.yml up -d
LOCALSTACK_ENDPOINT=http://localhost:4566 ./scripts/test-sqs.sh
```

### Datadog (pós-deploy)

```bash
# 1. Arestas
curl -s "https://api.us5.datadoghq.com/api/v1/service_dependencies?env=prod" \
  -H "DD-API-KEY: $DD_API_KEY" \
  -H "DD-APPLICATION-KEY: $DD_APP_KEY" | jq

# 2. Versão e config do Agent
kubectl -n datadog get ds datadog-agent -o jsonpath='{.spec.template.spec.containers[0].image}'
kubectl -n datadog exec ds/datadog-agent -- agent config | \
  grep -E "compute_stats_by_span_kind|peer_tags_aggregation|peer_tags"

# 3. Trace contínuo
# Na UI: APM → Traces → filtrar por um trace_id de POST que publique evento.
# Verificar se o trace tem spans dos 3 serviços.
```

**Critério de sucesso:** o `GET /api/v1/service_dependencies` retorna as arestas esperadas e um trace de evento tem spans de produtor e consumidor no mesmo `trace_id`.

---

## 11. Riscos e rollback

| Risco | Mitigação |
|---|---|
| Aumento de custom metrics com `compute_stats_by_span_kind` / peer tags | Comparar `datadog.estimated_usage.metrics.custom` antes/depois; se estourar, reduzir a lista de `peer_tags` ou desligar `peer_tags_aggregation` |
| Upgrade de chart do Agent quebrar valores existentes | Fazer em staging; manter os `set` atuais; rollback é `helm rollback` |
| Mensagens antigas na fila sem `traceparent` | Já são tratadas: span raiz, sem exceção (AC-3) |
| Aumento de payload do SQS | Atributos são pequenos (traceparent ~55 bytes); sem impacto relevante |
| Cardinalidade de `peer_tags` (hostnames) | Datadog bloqueia IPs; monitorar a lista de inferred services; usar renaming rules se necessário |
| O gate falhar por falta de tráfego na janela | Janela de 24h ou warmup explícito antes do check |

**Rollback do P0:** reverter o commit do shared + bumps. Os consumidores continuam funcionando (só perdem a continuidade).
**Rollback do P1:** remover o bloco `datadog.env` (ou o `values` correspondente) e reiniciar o Agent.

---

## 12. Anexos

### 12.1 Arestas esperadas (derivadas das filter policies)

Com base em `infra/terraform/messaging/subscriptions.tf:5-24`:

| Produtor | Eventos | Consumidores |
|---|---|---|
| os-service | `ServiceEnded` | inventory |
| os-service | `DiagnosticoFinalizado`, `OSEnded` | billing |
| billing-service | `OrcamentoAprovado` | os, inventory |
| billing-service | `OrcamentoReprovado` | os |
| billing-service | `CobrancaEmitida`, `PaymentDone` | os |
| inventory-service | `InventoryChecked`, `PecaCompletamenteAlocada`, `DeducaoEstoqueFalhou` | os |
| billing-service | HTTP `RestClient` | inventory (síncrono — validar span) |

> O grafo tem ciclos (os ↔ billing, os ↔ inventory, billing → inventory). Isso é esperado numa saga por coreografia; o Service Map vai mostrar um mesh, não uma árvore.

### 12.2 Snippet do teste de continuidade (esqueleto)

```java
@Test
void consumerSpanIsChildOfProducer() {
    var exporter = InMemorySpanExporter.create();
    var provider = SdkTracerProvider.builder()
            .addSpanProcessor(SimpleSpanProcessor.create(exporter))
            .build();
    GlobalOpenTelemetry.set(OpenTelemetrySdk.builder()
            .setTracerProvider(provider)
            .setPropagators(ContextPropagators.create(W3CTraceContextPropagator.getInstance()))
            .build());

    var producer = provider.get("test").spanBuilder("SNS Publish")
            .setSpanKind(SpanKind.PRODUCER).startSpan();
    var carrier = new HashMap<String, String>();
    W3CTraceContextPropagator.getInstance()
            .inject(Context.current().with(producer), carrier, Map::put);
    producer.end();

    var message = Message.builder()
            .messageId("m1").receiptHandle("r1").body("{\"eventId\":\"x\"}")
            .messageAttributes(Map.of("traceparent",
                    MessageAttributeValue.builder().dataType("String")
                            .stringValue(carrier.get("traceparent")).build()))
            .build();

    listener.processMessage(message);

    var consumer = exporter.getFinishedSpanItems().stream()
            .filter(s -> s.getKind() == SpanKind.CONSUMER)
            .findFirst().orElseThrow();
    assertThat(consumer.getTraceId()).isEqualTo(producer.getSpanContext().getTraceId());
    assertThat(consumer.getParentSpanId()).isEqualTo(producer.getSpanContext().getSpanId());
}
```

> O `SqsEventListener` recebe `OpenTelemetry` por construtor; o teste usa um `SdkTracerProvider` com `InMemorySpanExporter`. Em runtime, o starter injeta o bean `OpenTelemetry`.

### 12.3 Referências

- [Datadog Service Map](https://docs.datadoghq.com/tracing/services/services_map/)
- [Datadog Inferred services / peer tags](https://docs.datadoghq.com/tracing/services/inferred_services/)
- [Datadog OTLP Ingestion by the Agent](https://docs.datadoghq.com/opentelemetry/interoperability/otlp_ingest_in_the_agent)
- [Datadog Service Dependencies API](https://docs.datadoghq.com/api/latest/service-dependencies/)
- [Tracing asynchronous systems: parent-child vs span links](https://www.datadoghq.com/blog/parent-child-vs-span-links-tracing/)

---

## 13. Execução em 2026-11-16 (branch `feature/observability`)

Implementação feita **sem merge para master**:

| Item | Resultado |
|---|---|
| Shared-kernel (P0) | commit `873215c`; `messageAttributeNames("All")` + `messageSystemAttributeNames` + `OpenTelemetry` injetado |
| os/inventory/billing | commits `1c55ce8` / `6597484` / `4fa7125` (bump do shared-kernel + `opentelemetry-sdk-testing`) |
| Infra (P1) | commits `908e75d` (env do Agent), `a7626c6` (timeout 900s) e `229f3f1` (revert do env — o upgrade travou o DaemonSet) |
| Mono (gate/docs) | commits `efa9f82`, `a82894b`, `15ae419` (`DT-DD-22`, `DT-DD-12` com skip e candidatos de query) |
| Testes locais | os: 807 testes / 0 falhas; inventory: 254 / 0; billing: 125 / 0; LocalStack IT: 4 / 0; E2E: 28 / 0 |
| CI (PR) | validação verde (Terraform, Maven, pytest, scripts) |
| CD | os/inventory/billing deployados; addons falhou no Helm release do Agent por timeout de 5m; o timeout foi para 15m, mas o DaemonSet não ficou ready e o env do P1 foi revertido (`229f3f1`) |
| Datadog depois do deploy | sem spans/JVM/logs dos serviços EKS: o Agent não estava recebendo OTLP; os endpoints da API respondiam 500/502. `DT-DD-22` continuou fail |

> O `DT-DD-22` foi consultado ao vivo via `GET /api/v1/service_dependencies?env=prod` e retornou
> os três serviços + o API Gateway, **sem nenhuma chamada entre eles** — o RED que motivou a correção.

### 13.1 Pendências após esta execução

- [x] Reverter o `values` do Agent (commit `229f3f1`) para restaurar o DaemonSet; o P1 (inferred services) fica pendente.
- [ ] Com credenciais AWS válidas, aplicar o addons revertido (ou `helm rollback datadog 1 -n datadog`) e confirmar `kubectl -n datadog rollout status ds/datadog-agent`.
- [ ] Reaplicar o P1 em duas etapas: primeiro só `DD_APM_COMPUTE_STATS_BY_SPAN_KIND` + `DD_APM_PEER_TAGS_AGGREGATION`, validar o rollout; depois `DD_APM_PEER_TAGS`, validar de novo. Ou atualizar o chart para Agent ≥7.60, onde os defaults já bastam.
- [ ] Rodar `verify-observability.yml` com `api_url` informado (bypass do S3 quando as credenciais do Academy expiram) e confirmar `DT-DD-22` GREEN.
- [ ] Investigar os 500/502 dos serviços EKS após o deploy (health 200 no cluster, mas API Gateway 500/502) antes de gerar tráfego de negócio.
- [ ] Investigar por que `@trace_id:<id>` não fecha com o `trace_id` decimal dos logs OTLP; o check já tenta decimal/hex, `trace_id` e `@dd.trace_id`.
- [ ] `DT-DD-18` (evento CloudWatch) continua falhando por limitação do AWS Academy (sem integração AWS nativa).
