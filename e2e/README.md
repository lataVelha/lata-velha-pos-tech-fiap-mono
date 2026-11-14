# Saga end-to-end test suite

Real HTTP against the real `os-service`, `inventory-service` and
`billing-service`, over a real RabbitMQ broker, against the real Postgres and
MongoDB databases. No mocks and no Java.

Every event in the 22-step flow of `general-flow.md` is produced by real
service code. Nothing is hand-published to make the happy path work — the only
events the suite injects are deliberate faults that no healthy service would
emit (duplicates, drifted quantities, malformed envelopes), and each injection
is the subject of the test doing it.

Assertions go through three channels: the HTTP APIs, the event bus, and direct
queries against all three databases. A value that is correct in a response but
wrong in (or missing from) its table will fail.

## Mercado Pago

`billing-service` calls Mercado Pago on two paths — `POST /checkout/preferences`
when it bills an `OSEnded`, and `GET /v1/payments/{id}` when a webhook arrives.
Without credentials both fail, so `CobrancaEmitida` and `PaymentDone` would
never fire and billing would dead-letter on every run.

`fakes/mercadopago/app.py` is a programmable stand-in for those two endpoints,
wired in as the `fake-mercadopago` compose service, with billing pointed at it
through `MERCADOPAGO_BASE_URL`. Tests drive it via `clients/fake_mp_client.py`:

- `program_payment(id, external_reference, status, status_detail)` — decides what
  the next `GET /v1/payments/{id}` returns, so a test can force `approved` or
  `rejected`
- `fail_preferences(500)` — simulates a Mercado Pago outage at billing time
- `reset()` / `clear_faults()`

`MERCADOPAGO_WEBHOOK_SECRET` is left empty, which disables signature validation,
so the suite can post the webhook with no headers. Real HMAC verification is not
covered here; it needs its own test with the secret set.

## Setup

```bash
pip install -r e2e/requirements.txt
docker compose up --build -d   # from the repo root, once, or whenever code changes
```

## Running

```bash
pytest e2e -v
```

The session brings the stack up itself (`docker compose up -d`, no `--build`),
waits for every service plus the fake to report healthy, and purges the three
dead-letter queues. Set `E2E_MANAGE_COMPOSE=0` to skip the compose call and only
health-wait against a stack you are managing yourself.

The suite never runs `docker compose down` — containers are left up so a failed
run can be inspected (service logs, DB rows, anything still queued). The
RabbitMQ management UI is at <http://localhost:15672> (guest/guest).

## Layout

| Path | Purpose |
|---|---|
| `support/config.py` | every URL, port, credential and queue name, in one place |
| `support/bus.py` | `SagaRecorder`, `publish_event`, `publish_raw`, `queue_depth`, `purge_queue` |
| `support/waiting.py` | `wait_until`, `settle` |
| `support/db_os.py`, `db_billing.py`, `db_inventory.py` | read-only accessors per database |
| `support/asserts.py` | `assert_ladder`, `assert_conservacao_estoque`, `assert_valor` |
| `clients/` | one HTTP client per service, plus the fake's control client |
| `drivers/saga.py` | `SagaDriver` and `OsFixture` — the shared steps every test reuses |
| `fakes/mercadopago/` | the Mercado Pago stand-in |

### `SagaRecorder`

Binds its own exclusive queue to `lata-velha.events` with `#` and drains it on a
background thread into a buffer, so assertions are **non-destructive and
order-independent**: `wait_for`, `wait_for_count`, `count`, `assert_count`,
`assert_not_seen`, `assert_order`. It must be entered before the call that
triggers the events, since the queue only exists from that point.

It replaced an earlier watcher that acked and discarded every non-matching
message, which made any second assertion in a test silently unreliable.

### Two rules worth knowing

**An observed event does not mean the database is readable.** There is no
transactional outbox — `EntradaPecaEstoqueUseCase` publishes before saving, and
several handlers publish inside the save transaction. Use the bus to prove an
event was produced and `wait_until` against the database for state. Never assert
on a table immediately after a `wait_for`.

**Every test creates its own peça and its own OS.** Stock is global mutable
state, so sharing a seeded part across tests makes the arithmetic depend on
execution order.

## What's covered

| File | Scenarios |
|---|---|
| `test_happy_path.py` | the full 22 steps with one serviço and with two; bus and queue hygiene afterwards |
| `test_payment_gate.py` | FINALIZADA with work done is **not** deliverable; PAGA is; REPROVADA is; an empty diagnosis is; a rejected payment blocks delivery until a later approval |
| `test_event_contracts.py` | exact payload shapes of the events billing consumes; the orçamento total equals labour plus inventory's part prices; the role matrix |
| `test_stock_backorder.py` | zero stock → ENCOMENDA → unblock on arrival; partial stock → PARCIAL; two serviços with only one stocked |
| `test_stock_failures.py` | stock removed after reservation; deduction refused when never reserved; ghost reservation created by approving after the work finished; reservations are never released |
| `test_idempotency.py` | duplicate `OrcamentoAprovado`, `ServiceEnded`, `DiagnosticoFinalizado` and webhook each take effect once |
| `test_poison_dlq.py` | a malformed envelope lands in all three DLQs with no retries; an unprocessable payload lands only in inventory's after retries; a Mercado Pago outage blocks billing and the payment gate holds through it |

Marked `dlq` tests assert on queue depth and should not run in parallel with
anything else.

## Notes

- Auth: `POST :8080/auth/login`. The users come from os-service's
  `V2__insert_data.sql`; one token is accepted by all three services, since they
  share the same `app.pub`.
- `inventory-service` has no read endpoint for `PecaAlocada`, so reservations are
  verified directly in MongoDB.
- Mongo stores `_id` as a BSON `ObjectId` while Java sees the hex string, so
  `support/db_inventory.py` matches on both forms.
- A failed event is retried 3 times and then dead-lettered to
  `<service-queue>.events.dlq`. If a test times out, check those queues first.

## Not covered

- **Notification e-mails.** `Orcamento.create` always passes a null client
  e-mail, so the orçamento e-mail is never attempted, and the other services
  send through a real SMTP host with blank credentials. Covering this needs a
  Mailpit container and `SPRING_MAIL_HOST` pointed at it.
- **`CobrancaEmitida` and `DeducaoEstoqueFalhou` in os-service** are log-only
  with no database effect, so they are only assertable on the bus.
- **Real webhook signature verification** — the secret is unset here.
- **The missing-outbox loss window** — reproducing it needs a container killed
  between a save and its publish.
- **The SQS transport.** `messaging.provider=sqs` has its own listener and
  publisher; this suite covers only the RabbitMQ path. See `e2e/sqs/`.
