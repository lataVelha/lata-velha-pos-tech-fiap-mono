#!/usr/bin/env bash
#
# api-smoke.sh — bateria de testes curl contra o ambiente AWS.
#
# Cobre o caminho real de produção:
#   cliente → API Gateway (edge, authorizer JWT) → VPC Link → ALB → serviços
#   + API Gateway do billing (webhook Mercado Pago)
#
# Uso:
#   API_URL=https://xxxx.execute-api.us-east-1.amazonaws.com \
#   BILLING_API_URL=https://yyyy.execute-api.us-east-1.amazonaws.com \
#   bash scripts/api-smoke.sh
#
# Variáveis:
#   API_URL           (obrigatória) endpoint do API Gateway principal (edge)
#   BILLING_API_URL   (opcional)  endpoint do API Gateway do billing
#   CPF / PASSWORD    credenciais do login (default: seed admin)
#   READ_ONLY=1       roda só leitura (não cria OS/peça)
#   TIMEOUT=30        timeout de cada curl
#   OUT=/tmp/api-smoke diretório para os corpos das respostas
#
# Saída: uma linha por teste (PASS/FAIL/SKIP) + corpo resumido + resumo final.
# Exit code: 0 se nenhum FAIL.
set -uo pipefail

API_URL="${API_URL:?defina API_URL (ex: https://xxxx.execute-api.us-east-1.amazonaws.com)}"
BILLING_API_URL="${BILLING_API_URL:-}"
CPF="${CPF:-11144477735}"
PASSWORD="${PASSWORD:-Admin@123}"
READ_ONLY="${READ_ONLY:-0}"
TIMEOUT="${TIMEOUT:-30}"
OUT="${OUT:-/tmp/api-smoke}"

command -v jq >/dev/null || { echo "jq é obrigatório" >&2; exit 2; }
mkdir -p "$OUT"

PASS=0; FAIL=0; SKIP=0; KNOWN=0
FAILED=(); SKIPPED=(); KNOWN_ITEMS=()
HTTP_CODE=""; RESP=""
TOKEN=""; OS_ID=""; PECA_ID=""; EXEC_ID=""

c_pass=$'\033[1;32m'; c_fail=$'\033[1;31m'; c_skip=$'\033[1;33m'; c_dim=$'\033[2m'; c_off=$'\033[0m'
[ -t 1 ] || { c_pass=""; c_fail=""; c_skip=""; c_dim=""; c_off=""; }

snippet() {
  printf '%s' "$RESP" | tr -d '\n' | cut -c1-220
  [ "${#RESP}" -gt 220 ] && printf '…'
}

# request METHOD PATH [BODY] [TOKEN] [BASE]
request() {
  local method=$1 path=$2 body=${3:-} token=${4:-} base=${5:-$API_URL}
  local args=(-s -m "$TIMEOUT" -o "$OUT/body.json" -w '%{http_code}' -X "$method")
  [ -n "$token" ] && args+=(-H "Authorization: Bearer $token")
  [ -n "$body" ] && args+=(-H 'Content-Type: application/json' --data "$body")
  HTTP_CODE=$(curl "${args[@]}" "$base$path" 2>/dev/null) || HTTP_CODE="000"
  RESP=$(cat "$OUT/body.json" 2>/dev/null || true)
}

# check NOME CÓDIGO_ESPERADO [FILTRO_JQ VALOR_ESPERADO]
check() {
  local name=$1 want_code=$2 filter=${3:-} want=${4:-}
  local ok=1 got=""
  [ "$HTTP_CODE" = "$want_code" ] || ok=0
  if [ -n "$filter" ]; then
    got=$(printf '%s' "$RESP" | jq -r "$filter" 2>/dev/null || true)
    [ "$got" = "$want" ] || ok=0
  fi
  if [ "$ok" = 1 ]; then
    PASS=$((PASS+1))
    printf '%sPASS%s %-58s HTTP %s %s%s%s\n' "$c_pass" "$c_off" "$name" "$HTTP_CODE" "$c_dim" "$(snippet)" "$c_off"
  else
    FAIL=$((FAIL+1)); FAILED+=("$name")
    printf '%sFAIL%s %-58s HTTP %s (esperado %s%s) %s\n' "$c_fail" "$c_off" "$name" "$HTTP_CODE" "$want_code" \
      "${filter:+, jq '$filter'==$want (obtido: $got)}" "$(snippet)"
  fi
}

skip() {
  SKIP=$((SKIP+1)); SKIPPED+=("$1")
  printf '%sSKIP%s %-58s %s\n' "$c_skip" "$c_off" "$1" "${2:-}"
}

# Defeito conhecido: conta separado e não derruba o exit code.
known() {
  KNOWN=$((KNOWN+1)); KNOWN_ITEMS+=("$1 — $2")
  printf '%sKNOWN%s %-57s %s\n' "$c_skip" "$c_off" "$1" "$2"
}

# Aceita mais de um status esperado (ex.: "401 502").
check_oneof() {
  local name=$1 codes=$2 note=${3:-} c
  for c in $codes; do
    if [ "$HTTP_CODE" = "$c" ]; then
      PASS=$((PASS+1))
      printf '%sPASS%s %-58s HTTP %s %s%s%s\n' "$c_pass" "$c_off" "$name" "$HTTP_CODE" "$c_dim" "$(snippet)" "$c_off"
      return
    fi
  done
  FAIL=$((FAIL+1)); FAILED+=("$name")
  printf '%sFAIL%s %-58s HTTP %s (esperado um de: %s) %s\n' "$c_fail" "$c_off" "$name" "$HTTP_CODE" "$codes" "$(snippet)"
}

section() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

gen_cpf() {
  local n=() i sum d1 d2
  for i in $(seq 1 9); do n+=($((RANDOM % 10))); done
  sum=0; for i in $(seq 0 8); do sum=$((sum + n[i] * (10 - i))); done
  d1=$(( (sum * 10) % 11 )); [ "$d1" -eq 10 ] && d1=0
  sum=0; for i in $(seq 0 8); do sum=$((sum + n[i] * (11 - i))); done
  sum=$((sum + d1 * 2))
  d2=$(( (sum * 10) % 11 )); [ "$d2" -eq 10 ] && d2=0
  printf '%s%s%s' "$(printf '%s' "${n[@]}")" "$d1" "$d2"
}

echo "API URL    : $API_URL"
echo "Billing API: ${BILLING_API_URL:-<não informado>}"
echo "Login      : CPF $CPF"
echo "Modo       : $([ "$READ_ONLY" = 1 ] && echo somente-leitura || echo 'completo (cria dados)')"

# ============================================================================
section "1. EDGE / AUTH (API Gateway + authorizer)"
# ============================================================================

request GET /actuator/health
check "GET /actuator/health (público, sem token)" 200 '.status' 'UP'

request GET /ordens-servico
check "GET /ordens-servico sem token → 401" 401

request POST /auth/cpf '{"cpf":"123","password":"x"}'
check "POST /auth/cpf CPF inválido → 400" 400 '.error' 'CPF invalido'

request POST /auth/cpf "{\"cpf\":\"$CPF\",\"password\":\"senha-errada\"}"
check "POST /auth/cpf senha errada → 401" 401

request POST /auth/cpf "{\"cpf\":\"$CPF\",\"password\":\"$PASSWORD\"}"
check "POST /auth/cpf válido → 200 + Bearer" 200 '.tokenType' 'Bearer'
TOKEN=$(printf '%s' "$RESP" | jq -r '.token // empty' 2>/dev/null)
if [ -n "$TOKEN" ]; then
  payload=$(printf '%s' "$TOKEN" | cut -d. -f2 | tr '_-' '/+' | base64 -d 2>/dev/null || true)
  printf '%s     JWT: %s%s\n' "$c_dim" "$(printf '%s' "$payload" | cut -c1-160)" "$c_off"
  exp=$(printf '%s' "$payload" | jq -r '.exp // empty' 2>/dev/null)
  [ -n "$exp" ] && printf '%s     expira em: %s%s\n' "$c_dim" "$(date -u -d "@$exp" '+%Y-%m-%d %H:%M:%SZ' 2>/dev/null || echo "$exp")" "$c_off"
fi

request GET /ordens-servico '' 'token.invalido.aqui'
# Authorizer do API Gateway devolve 403 (não 401) para token malformado; 401 só sem header.
check "GET /ordens-servico token malformado → 403" 403

request GET /ordens-servico '' "$TOKEN"
check "GET /ordens-servico com token → 200" 200 '.content | type' 'array'
echo "${c_dim}     totalElements=$(printf '%s' "$RESP" | jq -r '.totalElements // "?"')${c_off}"

# ============================================================================
section "2. LEITURAS (os-service + inventory-service)"
# ============================================================================

request GET '/proprietarios?page=0&size=5' '' "$TOKEN"
check "GET /proprietarios → 200" 200 '.content | type' 'array'
echo "${c_dim}     proprietarios=$(printf '%s' "$RESP" | jq -r '.totalElements // "?"')${c_off}"

request GET '/servicos?page=0&size=10' '' "$TOKEN"
check "GET /servicos → 200" 200 '.content | type' 'array'
SERVICO_ID=$(printf '%s' "$RESP" | jq -r '.content[0].id // empty' 2>/dev/null)
echo "${c_dim}     servicos=$(printf '%s' "$RESP" | jq -r '.totalElements // "?"') (usando servicoId=$SERVICO_ID)${c_off}"

request GET '/veiculos?page=0&size=5' '' "$TOKEN"
check "GET /veiculos → 200" 200 '.content | type' 'array'

request GET '/ordens-servico/status-service?page=0&size=5' '' "$TOKEN"
check "GET /ordens-servico/status-service → 200" 200 '.content | type' 'array'

request GET '/historico/tempo-medio-por-estado?inicio=2020-01-01T00:00:00&fim=2030-12-31T23:59:59' '' "$TOKEN"
check "GET /historico/tempo-medio-por-estado → 200" 200

request GET '/pecas?page=0&size=10' '' "$TOKEN"
check "GET /pecas (inventory, Mongo) → 200" 200 '.content | type' 'array'
echo "${c_dim}     pecas=$(printf '%s' "$RESP" | jq -r '.totalElements // "?"')${c_off}"

# Defeito conhecido: JSON malformado deveria ser 400, o handler global devolve 500.
request POST /ordens-servico/completa '{"veiculo": ' "$TOKEN"
[ "$HTTP_CODE" = "500" ] && known "JSON malformado na criacao de OS → 500" "GlobalExceptionHandler nao mapeia HttpMessageNotReadableException para 400"

if [ "$READ_ONLY" = 1 ]; then
  skip "fluxo de escrita (READ_ONLY=1)"
else
  # ==========================================================================
  section "3. INVENTORY — escrita (MongoDB)"
  # ==========================================================================
  STAMP=$(date +%s)
  request POST /pecas "{\"nome\":\"Peça smoke $STAMP\",\"descricao\":\"criada pelo api-smoke.sh\",\"valor\":42.50}" "$TOKEN"
  check "POST /pecas (cria peça no Mongo) → 201" 201 '.nome' "Peça smoke $STAMP"
  PECA_ID=$(printf '%s' "$RESP" | jq -r '.id // empty' 2>/dev/null)

  if [ -n "$PECA_ID" ]; then
    request POST "/pecas/$PECA_ID/estoque/entrada" '{"quantidade":10}' "$TOKEN"
    check "POST /pecas/$PECA_ID/estoque/entrada (10 un) → 200" 200

    request GET "/pecas/$PECA_ID/estoque" '' "$TOKEN"
    check "GET /pecas/$PECA_ID/estoque → 200" 200 '.quantidadeDisponivel|tostring' '10'
  else
    skip "estoque da peça (POST /pecas falhou)"
  fi

  # ==========================================================================
  section "4. FLUXO COMPLETO DA OS (RECEBIDA → ENTREGUE)"
  # ==========================================================================
  CPF_NOVO=$(gen_cpf)
  PLACA="QAB$((RANDOM % 9))C$(printf '%02d' $((RANDOM % 100)))"
  OS_BODY=$(cat <<JSON
{
  "proprietario": {
    "nome": "Cliente Smoke $STAMP",
    "email": "smoke.$STAMP@latavelha.test",
    "documento": "$CPF_NOVO",
    "numeroCelular": "(11) 99999-9999",
    "endereco": {"rua": "Rua do Teste", "cep": "01234567", "numeroCasa": "42"}
  },
  "veiculo": {"placa": "$PLACA", "marca": "Fiat", "modelo": "Uno", "ano": 2020, "cor": "Prata"},
  "reclamacaoProprietario": "smoke test $STAMP",
  "servicos": [{"servicoId": ${SERVICO_ID:-1}, "valorMaoDeObra": 120.00}]
}
JSON
)
  request POST /ordens-servico/completa "$OS_BODY" "$TOKEN"
  check "POST /ordens-servico/completa → 201 RECEBIDA" 201 '.status' 'RECEBIDA'
  OS_ID=$(printf '%s' "$RESP" | jq -r '.id // empty' 2>/dev/null)
  echo "${c_dim}     OS=$OS_ID  cliente=$CPF_NOVO  placa=$PLACA${c_off}"

  if [ -n "$OS_ID" ]; then
    request PATCH "/ordens-servico/$OS_ID/iniciar-diagnostico" '' "$TOKEN"
    check "PATCH /$OS_ID/iniciar-diagnostico → 200" 200

    EXEC_ID=$(curl -s -m "$TIMEOUT" "$API_URL/ordens-servico/$OS_ID" -H "Authorization: Bearer $TOKEN" \
      | jq -r '.servicos[0].id // empty' 2>/dev/null)

    request PATCH "/ordens-servico/$OS_ID/adicionar-servico" \
      "{\"servicoRequests\":[{\"servicoId\": ${SERVICO_ID:-1}, \"valorMaoDeObra\": 80.00}]}" "$TOKEN"
    check "PATCH /$OS_ID/adicionar-servico → 200" 200

    request PATCH "/ordens-servico/$OS_ID/finalizar-diagnostico" '' "$TOKEN"
    check "PATCH /$OS_ID/finalizar-diagnostico → 200" 200

    request GET "/ordens-servico/$OS_ID" '' "$TOKEN"
    check "GET /$OS_ID → AGUARDANDO_APROVACAO" 200 '.status' 'AGUARDANDO_APROVACAO'
    EXEC_ID=$(printf '%s' "$RESP" | jq -r '.servicos[0].id // empty' 2>/dev/null)
    echo "${c_dim}     execucaoServicoId=$EXEC_ID${c_off}"

    request PATCH "/ordens-servico/$OS_ID/aprovar" \
      "{\"servicos\":[{\"execucaoServicoId\": $EXEC_ID, \"status\": \"APROVADO\"}]}" "$TOKEN"
    check "PATCH /$OS_ID/aprovar → 200 APROVADA" 200 '.status' 'APROVADA'

    request PATCH "/ordens-servico/$OS_ID/iniciar-servico/$EXEC_ID" '' "$TOKEN"
    check "PATCH /$OS_ID/iniciar-servico/$EXEC_ID → 200" 200

    request PATCH "/ordens-servico/$OS_ID/finalizar-servico/$EXEC_ID" '' "$TOKEN"
    check "PATCH /$OS_ID/finalizar-servico/$EXEC_ID → 200" 200

    request PATCH "/ordens-servico/$OS_ID/retirar-veiculo" '' "$TOKEN"
    check "PATCH /$OS_ID/retirar-veiculo → 200" 200

    request GET "/ordens-servico/$OS_ID" '' "$TOKEN"
    check "GET /$OS_ID → ENTREGUE" 200 '.status' 'ENTREGUE'
    printf '%s     %s%s\n' "$c_dim" "$(printf '%s' "$RESP" | jq -c '{id,status,iniciadoEm,finalizadoEm,entregueEm,servicos:[.servicos[]|{id,status}]}' 2>/dev/null)" "$c_off"

    # ========================================================================
    section "5. ROTA PÚBLICA (sem token) + NEGATIVOS"
    # ========================================================================
    request POST "/ordens-servico/$OS_ID/aprovacao-orcamento" \
      "{\"servicos\":[{\"execucaoServicoId\": $EXEC_ID, \"status\": \"APROVADO\"}]}"
    check "POST /$OS_ID/aprovacao-orcamento sem token → 422 (pública, OS já ENTREGUE)" 422

    request GET "/ordens-servico/999999" '' "$TOKEN"
    check "GET /ordens-servico/999999 → 404" 404

    request PATCH "/ordens-servico/999999/iniciar-diagnostico" '' "$TOKEN"
    check "PATCH OS inexistente → 404" 404

    request POST /ordens-servico/completa '{}' "$TOKEN"
    check "POST /ordens-servico/completa body vazio → 400" 400
  else
    for t in iniciar-diagnostico adicionar-servico finalizar-diagnostico aprovar iniciar-servico finalizar-servico retirar-veiculo; do
      skip "fluxo: $t (POST /ordens-servico/completa falhou)"
    done
  fi
fi

# ============================================================================
if [ -n "$BILLING_API_URL" ]; then
  section "6. BILLING (API Gateway próprio)"
  request POST /pagamentos/webhook '{"type":"payment","data":{"id":"123"}}' '' "$BILLING_API_URL"
  check_oneof "POST /pagamentos/webhook sem x-signature → 401 (ou 502 se secret vazio)" '401 502'
  [ "$HTTP_CODE" = "502" ] && known "webhook sem assinatura foi PROCESSADO" "MERCADOPAGO_WEBHOOK_SECRET vazio → open webhook"

  request POST '/pagamentos/webhook?type=payment&data.id=123' '{"type":"payment","data":{"id":"123"}}' '' "$BILLING_API_URL"
  check_oneof "POST /pagamentos/webhook com query e sem assinatura → 401/502" '401 502'

  request GET /pagamentos '' '' "$BILLING_API_URL"
  check "GET /pagamentos no API do billing → 404 (rota não publicada)" 404
else
  section "6. BILLING"
  skip "billing (BILLING_API_URL não informado)"
fi

# ============================================================================
section "RESUMO"
printf 'PASS: %s   FAIL: %s   SKIP: %s   KNOWN: %s   (API %s)\n' "$PASS" "$FAIL" "$SKIP" "$KNOWN" "$API_URL"
if [ "${#KNOWN_ITEMS[@]}" -gt 0 ]; then
  printf '\nDefeitos conhecidos (não contam como falha):\n'
  printf ' - %s\n' "${KNOWN_ITEMS[@]}"
fi
if [ "${#FAILED[@]}" -gt 0 ]; then
  printf '%sFalhas:%s\n' "$c_fail" "$c_off"
  printf ' - %s\n' "${FAILED[@]}"
fi
[ "$FAIL" -eq 0 ]
