# OS ↔ Inventory saga — end-to-end test suite

Real HTTP against the real `os-service` and `inventory-service`, over a real
RabbitMQ broker, against real Postgres databases. No mocks, no Java.
`billing-service` is treated as an external actor: since it has no RabbitMQ
wiring yet, this suite hand-publishes the two events it would eventually
produce (`OrcamentoAprovado`, `OrcamentoReprovado`) straight onto the shared
`lata-velha.events` exchange, and lets the real os-service/inventory-service
consumers process them for real.

## Setup

```bash
pip install -r e2e/requirements.txt
docker compose up --build -d   # from the repo root, once (or whenever code changes)
```

## Running

```bash
pytest e2e -v
```

The test session brings the stack up itself (`docker compose up -d`, no
`--build`) and waits for every service's `/actuator/health` to report `UP`
before running anything. Set `E2E_MANAGE_COMPOSE=0` to skip that and just wait
on health checks against a stack you're already running/managing yourself.

The suite never runs `docker compose down` — containers are left running so a
failed run can be inspected (service logs, DB rows, anything still queued in
RabbitMQ). Tear the stack down yourself with `docker compose down` when done.

## What's covered

- `test_happy_path.py` — full OS lifecycle with sufficient stock: creation →
  diagnosis → client approval → billing's `OrcamentoAprovado` → inventory
  reservation → execution → real stock deduction on `ServiceEnded` → delivery.
- `test_compensation_orcamento_reprovado.py` — billing rejects the budget;
  the OS finalizes as `REPROVADA`.
- `test_compensation_peca_completamente_alocada.py` — a part is backordered,
  blocking its execução (`AGUARDANDO_PECA`); a later stock entry unblocks it
  via a real `PecaCompletamenteAlocada` event. Covers both the fully
  backordered (`ENCOMENDA`) and partially reserved (`PARCIAL`) branches.
- `test_compensation_deducao_estoque_falhou.py` — reserved stock is lost
  before installation, so the real deduction fails; inventory-service emits
  `DeducaoEstoqueFalhou` instead of failing silently (log-only compensation,
  verified by observing the event on the bus, since there's no OS-visible
  state change).
- `test_idempotency.py` — the same `OrcamentoAprovado` and `ServiceEnded`
  delivered twice must reserve and deduct stock exactly once. This matters
  because the listener now retries and dead-letters rather than swallowing
  failures, so redelivery is a real possibility.
- `test_multi_servico_partial_availability.py` — one OS with two execuções,
  only one of which has stock. Only the unstocked one blocks; the other runs to
  completion and the OS stays open until the blocked one is unblocked.
- `test_billing_contracts.py` — asserts the exact payload shapes of
  `DiagnosticoFinalizado`, `ServiceEnded` and `OSEnded`, the events
  billing-service consumes. Nothing else verifies these contracts.
- `test_payment_flow.py` — flow steps 18 and 21: `CobrancaEmitida` and
  `PaymentDone` drive the OS to `PAGA`, which can then be delivered.

Each test creates its own `Peca`/stock and its own `OrdemServico` (reusing
seeded proprietário/veículo/serviço catalog entries), so tests are independent
and safe to re-run repeatedly against the same stack.

## Notes

- Auth: `POST :8080/auth/login` with the seeded users (`admin@latavelha.com`,
  `atendente@latavelha.com`, `mecanico@latavelha.com` — see os-service's
  `V2__insert_data.sql`); the same JWT works against inventory-service too.
- There is no HTTP read endpoint for `PecaAlocada` in inventory-service, so
  allocation outcomes are verified via `GET /pecas/{id}/estoque` quantities
  and via the events inventory-service publishes onto the bus
  (`rabbitmq_events.EventWatcher`), not via a direct read.
- Failed events are retried three times and then dead-lettered to
  `<service-queue>.dlq`. If a test times out waiting for a saga effect, check
  those queues in the management UI at :15672 — the message will be sitting
  there with the failure reason rather than having vanished.
