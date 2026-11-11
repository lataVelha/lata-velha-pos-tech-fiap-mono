#!/usr/bin/env bash
#
# saga-e2e.sh — E2E da saga completa contra AWS real (Story 15.3).
#
# Requer:
#   - AWS CLI autenticado na conta do ambiente
#   - serviços deployados (mono pipeline verde)
#   - API Gateway público em $API_URL
#   - filas do infra-messaging aplicadas
#
# Fluxo:
#   POST /ordens-servico → diagnóstico → aprovação → execução →
#   pagamento (webhook) → retirada, verificando os eventos em cada fila.
#
# Uso: API_URL=https://xxx.execute-api.us-east-1.amazonaws.com ./saga-e2e.sh
#
# Saída: 0 = saga completa; !=0 = passo que falhou (com fila/DLQ inspecionada).

set -Eeuo pipefail

API_URL="${API_URL:?defina API_URL do API Gateway}"
REGION="${AWS_REGION:-us-east-1}"
PROJECT="${PROJECT_NAME:-lata-velha}"
TIMEOUT="${TIMEOUT:-300}"

if [[ -t 1 ]]; then
  C_GREEN=$'\033[1;32m'; C_RED=$'\033[1;31m'; C_BLUE=$'\033[1;34m'; C_RESET=$'\033[0m'
else
  C_GREEN=''; C_RED=''; C_BLUE=''; C_RESET=''
fi

step() { echo "${C_BLUE}==> $*${C_RESET}"; }
ok()   { echo "${C_GREEN}✓ $*${C_RESET}"; }

queue_url() {
  aws sqs get-queue-url --queue-name "${PROJECT}-$1-service-events" \
    --region "$REGION" --query QueueUrl --output text
}

dlq_url() {
  aws sqs get-queue-url --queue-name "${PROJECT}-$1-service-events-dlq" \
    --region "$REGION" --query QueueUrl --output text
}

queue_depth() {
  aws sqs get-queue-attributes --queue-url "$1" \
    --attribute-names ApproximateNumberOfMessages \
    --query 'Attributes.ApproximateNumberOfMessages' --output text
}

assert_queue_empty() {
  local name="$1" url
  url=$(queue_url "$name")
  local depth
  depth=$(queue_depth "$url")
  if [[ "$depth" != "0" ]]; then
    echo "${C_RED}✗ fila $name ainda tem $depth mensagens${C_RESET}" >&2
    return 1
  fi
  ok "fila $name drenada"
}

assert_dlq_empty() {
  local name="$1" url depth
  url=$(dlq_url "$name")
  depth=$(queue_depth "$url")
  if [[ "$depth" != "0" ]]; then
    echo "${C_RED}✗ DLQ de $name tem $depth mensagens — inspecione com RUNBOOK-sqs.md${C_RESET}" >&2
    return 1
  fi
  ok "DLQ de $name vazia"
}

wait_until() {
  local description="$1"; shift
  local deadline=$(( $(date +%s) + TIMEOUT ))
  while (( $(date +%s) < deadline )); do
    if "$@"; then
      ok "$description"
      return 0
    fi
    sleep 10
  done
  echo "${C_RED}✗ timeout aguardando: $description${C_RESET}" >&2
  return 1
}

api() {
  local method="$1" path="$2" body="${3:-}"
  if [[ -n "$body" ]]; then
    curl -sf -X "$method" "$API_URL$path" -H 'Content-Type: application/json' -d "$body"
  else
    curl -sf -X "$method" "$API_URL$path"
  fi
}

step "Criando OS"
OS_ID=$(api POST /ordens-servico '{
  "proprietario": {"nome":"E2E Saga","documento":"35949343069","email":"e2e@example.com","numeroCelular":"11999999999"},
  "veiculo": {"placa":"E2E1A23","marca":"Fiat","modelo":"Uno","ano":2020,"cor":"Branco"},
  "reclamacaoProprietario":"E2E saga"
}' | jq -r '.id // .osId')
[[ -n "$OS_ID" && "$OS_ID" != "null" ]] || { echo "falha ao criar OS"; exit 1; }
ok "OS $OS_ID criada"

step "Iniciando diagnóstico"
api POST "/iniciar-diagnostico/$OS_ID" >/dev/null
ok "diagnóstico iniciado"

step "Adicionando serviço e finalizando diagnóstico"
api POST "/adicionar-servico/$OS_ID" '{"servicoId":1,"pecas":[{"pecaId":1,"quantidade":1}]}' >/dev/null || true
api POST "/finalizar-diagnostico/$OS_ID" >/dev/null
wait_until "billing consumiu DiagnosticoFinalizado" bash -c "[[ \$(aws sqs get-queue-attributes --queue-url '$(queue_url billing)' --attribute-names ApproximateNumberOfMessages --query 'Attributes.ApproximateNumberOfMessages' --output text) == 0 ]]"

step "Aprovando orçamento"
api PATCH "/ordens-servico/$OS_ID/aprovar" '{}' >/dev/null || api POST "/aprovar-os/$OS_ID" '{}' >/dev/null || true
wait_until "inventory consumiu OrcamentoAprovado" bash -c "[[ \$(aws sqs get-queue-attributes --queue-url '$(queue_url inventory)' --attribute-names ApproximateNumberOfMessages --query 'Attributes.ApproximateNumberOfMessages' --output text) == 0 ]]"

step "Executando serviços"
api POST "/iniciar-servico/$OS_ID" '{"servicoId":1}' >/dev/null || true
api POST "/finalizar-servico/$OS_ID" '{"servicoId":1}' >/dev/null || true

step "Pagamento (webhook Mercado Pago)"
api POST /pagamentos "$(jq -n --arg os "$OS_ID" '{ordemServicoId: ($os|tonumber), valor: 250.00, descricao: "E2E saga"}')" >/dev/null || true
ok "pagamento solicitado"

step "Retirando veículo"
api POST "/ordens-servico/$OS_ID/retirar" >/dev/null || true

step "Validando estado final e filas"
STATUS=$(api GET "/ordens-servico/$OS_ID" | jq -r '.status')
echo "status final: $STATUS"
for q in os inventory billing; do
  assert_queue_empty "$q"
  assert_dlq_empty "$q"
done

if [[ "$STATUS" != "ENTREGUE" ]]; then
  echo "${C_RED}✗ status final esperado ENTREGUE, obtido $STATUS${C_RESET}" >&2
  exit 1
fi

ok "Saga completa: OS $OS_ID ENTREGUE"
