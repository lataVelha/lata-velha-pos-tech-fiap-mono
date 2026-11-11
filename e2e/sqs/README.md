# E2E da saga SQS (Story 15.3)

Executa o fluxo completo contra **AWS real** e verifica filas/DLQ.

```bash
API_URL=https://xxxx.execute-api.us-east-1.amazonaws.com bash saga-e2e.sh
```

## Pré-requisitos

- Cluster com os quatro serviços (`app`, `os`, `inventory`, `billing`) e o
  barramento `infra-messaging` aplicados.
- AWS CLI autenticado com permissão de `sqs:GetQueueUrl` /
  `GetQueueAttributes` nas filas `lata-velha-*-events`.
- Endpoints da API conforme o Swagger de cada serviço.

## O que é validado

1. `POST /ordens-servico` cria a OS.
2. Diagnóstico iniciado/adicionado/finalizado.
3. `DiagnosticoFinalizado` drenado da fila do billing (orçamento criado).
4. Aprovação → `OrcamentoAprovado` drenado da fila do inventory.
5. Execução dos serviços / pagamento / retirada.
6. Status final `ENTREGUE`.
7. As três filas e as três DLQs terminam vazias.

## Poison message (DLQ)

Para provar a DLQ, publique um payload inválido na fila do inventory e
aguarde o `maxReceiveCount=5`:

```bash
QUEUE_URL=$(aws sqs get-queue-url --queue-name lata-velha-inventory-service-events --query QueueUrl --output text)
aws sqs send-message --queue-url "$QUEUE_URL" --message-body '{"nao":"e-um-envelope"}' \
  --message-attributes 'eventType={DataType=String,StringValue=OrcamentoAprovado}'
```

O alarme `<fila>-dlq-messages` dispara e a mensagem aparece em
`lata-velha-inventory-service-events-dlq`.
