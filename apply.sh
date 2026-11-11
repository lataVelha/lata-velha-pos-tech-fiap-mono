#!/usr/bin/env bash
#
# apply.sh — orquestra o apply.sh de cada submódulo, na mesma ordem do CI/CD:
#
#   (padrão)   infra bootstrap (VPC+EKS)
#              infra addons (ALB interno + API Gateway + target groups)
#              infra messaging (SNS/SQS/DLQs)
#              infra-db (RDS)
#              lambda (auth CPF + authorizer)
#              app (opcional — DEPLOY_APP=false pula)
#              edge (integração + rotas do API Gateway)
#              os-service
#              inventory-service
#              billing-service
#   --destroy  ordem inversa (edge → billing → inventory → os → app → lambda →
#              infra-db → infra messaging → infra addons → infra bootstrap)
#
# Uso:
#   ./apply.sh                  — pipeline completo, com confirmação interativa
#   ./apply.sh --auto           — pipeline completo, sem confirmação
#   ./apply.sh --skip-tests     — pula os testes Maven (app/os/inventory/billing)
#   ./apply.sh --skip-app       — pula o deploy do app (cutover)
#   ./apply.sh --destroy        — desfaz tudo, com confirmação
#   ./apply.sh --destroy --auto — desfaz tudo, sem confirmação
#
# Variáveis:
#   DEPLOY_APP=false   — equivalente a --skip-app
#   APP_ENABLED=false  — ALB responde 404 para paths não roteados (cutover)
#   APP_ENABLED=true   — app continua como default action do ALB (default)
#
# Rollback de um serviço (sem rebuild): ver docs/RUNBOOK-rollback.md.
# Cutover do app: ver docs/RUNBOOK-cutover-app.md.
#
# Pré-requisitos: ver README.md de cada submódulo (credenciais AWS,
# terraform.tfvars, etc.) — este script só encadeia os apply.sh existentes.

set -Eeuo pipefail

if [[ -t 1 ]]; then
  C_BLUE=$'\033[1;34m'; C_GREEN=$'\033[1;32m'; C_YELLOW=$'\033[1;33m'; C_RED=$'\033[1;31m'; C_DIM=$'\033[2m'; C_RESET=$'\033[0m'
else
  C_BLUE=''; C_GREEN=''; C_YELLOW=''; C_RED=''; C_DIM=''; C_RESET=''
fi

STEP_NAMES=()
STEP_SECONDS=()

# infra/infra-db/lambda só entendem --auto/--destroy; --skip-tests é só dos
# stacks com Maven (app/os/inventory/billing).
COMMON_ARGS=()
APP_ARGS=()
DESTROY=false
AUTO=false
DEPLOY_APP="${DEPLOY_APP:-true}"
APP_ENABLED="${APP_ENABLED:-true}"
for arg in "$@"; do
  case "$arg" in
    --destroy) DESTROY=true; COMMON_ARGS+=("$arg"); APP_ARGS+=("$arg") ;;
    --auto)    AUTO=true; COMMON_ARGS+=("$arg"); APP_ARGS+=("$arg") ;;
    --skip-tests|--skip-test) APP_ARGS+=("$arg") ;;
    --skip-app) DEPLOY_APP=false ;;
    *)
      echo "${C_RED}Flag desconhecida: $arg${C_RESET}"
      echo "Uso: ./apply.sh [--auto] [--skip-tests] [--skip-app] [--destroy]"
      exit 1
      ;;
  esac
done

# O infra addons usa APP_ENABLED para decidir se o app continua como default
# action do ALB (true) ou se paths nao roteados viram 404 (false = cutover).
export APP_ENABLED
export TF_VAR_app_enabled="$APP_ENABLED"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "$DESTROY" == "true" || "$DEPLOY_APP" == "true" ]]; then
  TOTAL_STEPS=10
else
  TOTAL_STEPS=9
fi
CURRENT_STEP=0

on_error() {
  local exit_code=$?
  local last_idx=$((${#STEP_NAMES[@]} - 1))
  local failing_step="${STEP_NAMES[$last_idx]:-?}"
  echo
  echo "${C_RED}✗ Pipeline interrompido no passo ${CURRENT_STEP}/${TOTAL_STEPS} (${failing_step}), código de saída ${exit_code}.${C_RESET}"
  if ((${#STEP_SECONDS[@]} > 0)); then
    echo "${C_DIM}  Passos concluídos antes de falhar:${C_RESET}"
    local i
    for ((i = 0; i < ${#STEP_SECONDS[@]}; i++)); do
      printf '  %s✓%s %-28s %s(%ss)%s\n' "$C_GREEN" "$C_RESET" "${STEP_NAMES[$i]}" "$C_DIM" "${STEP_SECONDS[$i]}" "$C_RESET"
    done
  fi
  exit "$exit_code"
}
trap on_error ERR

run_step() {
  CURRENT_STEP=$((CURRENT_STEP + 1))
  local label="$1" script="$2"; shift 2
  local start_ts elapsed
  STEP_NAMES+=("$label")
  echo
  echo "${C_BLUE}==> [${CURRENT_STEP}/${TOTAL_STEPS}] ${label}${C_RESET} ${C_DIM}(${script})${C_RESET}"
  start_ts=$(date +%s)
  ( cd "$(dirname "$script")" && "./$(basename "$script")" "$@" )
  elapsed=$(( $(date +%s) - start_ts ))
  STEP_SECONDS+=("$elapsed")
  echo "${C_GREEN}✓ [${CURRENT_STEP}/${TOTAL_STEPS}] ${label} concluído${C_RESET} ${C_DIM}(${elapsed}s)${C_RESET}"
}

echo "${C_BLUE}════════════════════════════════════════════════════════════${C_RESET}"
if [[ "$DESTROY" == "true" ]]; then
  echo "${C_YELLOW}  MONO REPO — Destruindo TUDO (ordem inversa: edge → … → bootstrap)${C_RESET}"
else
  echo "  MONO REPO — Deploy completo (infra → app opcional → edge → serviços)"
fi
[[ "$AUTO" == "true" ]] && echo "${C_DIM}  (--auto: sem confirmação em cada etapa)${C_RESET}"
[[ "$DEPLOY_APP" == "false" ]] && echo "${C_DIM}  (app desligado: DEPLOY_APP=false)${C_RESET}"
echo "${C_BLUE}════════════════════════════════════════════════════════════${C_RESET}"

if [[ "$DESTROY" == "true" ]]; then
  run_step "edge (rotas API GW)"         "$SCRIPT_DIR/infra/terraform/edge/apply.sh"         "${COMMON_ARGS[@]}"
  run_step "billing-service"             "$SCRIPT_DIR/billing-service/terraform/apply.sh"   "${APP_ARGS[@]}"
  run_step "inventory-service"           "$SCRIPT_DIR/inventory-service/terraform/apply.sh" "${APP_ARGS[@]}"
  run_step "os-service"                  "$SCRIPT_DIR/os-service/terraform/apply.sh"        "${APP_ARGS[@]}"
  run_step "app (deploy)"                "$SCRIPT_DIR/app/terraform/apply.sh"               "${APP_ARGS[@]}"
  run_step "lambda (auth CPF)"           "$SCRIPT_DIR/lambda/terraform/apply.sh"            "${COMMON_ARGS[@]}"
  run_step "infra-db (RDS)"              "$SCRIPT_DIR/infra-db/terraform/apply.sh"          "${COMMON_ARGS[@]}"
  run_step "infra messaging"             "$SCRIPT_DIR/infra/terraform/messaging/apply.sh"   "${COMMON_ARGS[@]}"
  run_step "infra addons"                "$SCRIPT_DIR/infra/terraform/apply.sh"             "${COMMON_ARGS[@]}" --addons-only
  run_step "infra bootstrap"             "$SCRIPT_DIR/infra/terraform/apply.sh"             "${COMMON_ARGS[@]}" --bootstrap-only
else
  run_step "infra bootstrap"             "$SCRIPT_DIR/infra/terraform/apply.sh"             "${COMMON_ARGS[@]}" --bootstrap-only
  run_step "infra addons"                "$SCRIPT_DIR/infra/terraform/apply.sh"             "${COMMON_ARGS[@]}" --addons-only
  run_step "infra messaging"             "$SCRIPT_DIR/infra/terraform/messaging/apply.sh"   "${COMMON_ARGS[@]}"
  run_step "infra-db (RDS)"              "$SCRIPT_DIR/infra-db/terraform/apply.sh"          "${COMMON_ARGS[@]}"
  run_step "lambda (auth CPF)"           "$SCRIPT_DIR/lambda/terraform/apply.sh"            "${COMMON_ARGS[@]}"
  if [[ "$DEPLOY_APP" == "true" ]]; then
    run_step "app (deploy)"              "$SCRIPT_DIR/app/terraform/apply.sh"               "${APP_ARGS[@]}"
  fi
  run_step "edge (rotas API GW)"         "$SCRIPT_DIR/infra/terraform/edge/apply.sh"         "${COMMON_ARGS[@]}"
  run_step "os-service"                  "$SCRIPT_DIR/os-service/terraform/apply.sh"        "${APP_ARGS[@]}"
  run_step "inventory-service"           "$SCRIPT_DIR/inventory-service/terraform/apply.sh" "${APP_ARGS[@]}"
  run_step "billing-service"             "$SCRIPT_DIR/billing-service/terraform/apply.sh"   "${APP_ARGS[@]}"
fi

TOTAL_SECONDS=0
for s in "${STEP_SECONDS[@]}"; do TOTAL_SECONDS=$((TOTAL_SECONDS + s)); done

echo
echo "${C_GREEN}════════════════════════════════════════════════════════════${C_RESET}"
echo "${C_GREEN}  ✓ Concluído — ${#STEP_NAMES[@]}/${TOTAL_STEPS} passos em ${TOTAL_SECONDS}s${C_RESET}"
echo "${C_GREEN}════════════════════════════════════════════════════════════${C_RESET}"
for i in "${!STEP_NAMES[@]}"; do
  printf '  %s✓%s %-28s %s(%ss)%s\n' "$C_GREEN" "$C_RESET" "${STEP_NAMES[$i]}" "$C_DIM" "${STEP_SECONDS[$i]}" "$C_RESET"
done
