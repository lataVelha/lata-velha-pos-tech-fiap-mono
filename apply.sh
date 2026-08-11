#!/usr/bin/env bash
#
# apply.sh — orquestra o apply.sh de cada submódulo, na ordem documentada
# nos READMEs (infra bootstrap → infra addons → infra-db → lambda → app):
#
#   (padrão)   [1/5] infra bootstrap (VPC+EKS+ECR) → [2/5] infra addons
#              (ALB interno + API Gateway vazio + autoscaler) → [3/5] infra-db
#              (RDS) → [4/5] lambda (auth CPF + authorizer, anexa no API
#              Gateway) → [5/5] app (deploy, anexa integração ALB + rotas)
#   --destroy  ordem inversa
#
# O API Gateway (infra addons) não depende de mais nenhum repo — cria só o
# "casco" (API + VPC Link + Stage, sem rotas), por isso roda logo após o
# bootstrap. Quem anexa rota nele é o próprio lambda (POST /auth/cpf +
# authorizer) e o próprio app (integração ALB + rotas públicas/protegidas),
# cada um via terraform_remote_state — nenhum precisa que o infra saiba
# que eles existem.
#
# Uso:
#   ./apply.sh                  — pipeline completo, com confirmação interativa
#   ./apply.sh --auto           — pipeline completo, sem confirmação
#   ./apply.sh --skip-tests     — pula os testes Maven do app
#   ./apply.sh --destroy        — desfaz tudo, com confirmação
#   ./apply.sh --destroy --auto — desfaz tudo, sem confirmação
#
# Pré-requisitos: ver README.md de cada submódulo (credenciais AWS,
# terraform.tfvars, etc.) — este script só encadeia os apply.sh existentes.

set -Eeuo pipefail

# --------------------------- saída no terminal ------------------------------
if [[ -t 1 ]]; then
  C_BLUE=$'\033[1;34m'; C_GREEN=$'\033[1;32m'; C_YELLOW=$'\033[1;33m'; C_RED=$'\033[1;31m'; C_DIM=$'\033[2m'; C_RESET=$'\033[0m'
else
  C_BLUE=''; C_GREEN=''; C_YELLOW=''; C_RED=''; C_DIM=''; C_RESET=''
fi

STEP_NAMES=()
STEP_SECONDS=()

# infra, infra-db e lambda só entendem --auto/--destroy; --skip-tests é só do app.
COMMON_ARGS=()
APP_ARGS=()
DESTROY=false
AUTO=false
for arg in "$@"; do
  case "$arg" in
    --destroy) DESTROY=true; COMMON_ARGS+=("$arg"); APP_ARGS+=("$arg") ;;
    --auto)    AUTO=true; COMMON_ARGS+=("$arg"); APP_ARGS+=("$arg") ;;
    --skip-tests|--skip-test) APP_ARGS+=("$arg") ;;
    *)
      echo "${C_RED}Flag desconhecida: $arg${C_RESET}"
      echo "Uso: ./apply.sh [--auto] [--skip-tests] [--destroy]"
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOTAL_STEPS=5
CURRENT_STEP=0

on_error() {
  local exit_code=$?
  local last_idx=$((${#STEP_NAMES[@]} - 1))
  local failing_step="${STEP_NAMES[$last_idx]:-?}"
  echo
  echo "${C_RED}✗ Pipeline interrompido no passo ${CURRENT_STEP}/${TOTAL_STEPS} (${failing_step}), código de saída ${exit_code}.${C_RESET}"
  # STEP_SECONDS só ganha uma entrada quando o passo TERMINA — se o passo
  # que falhou ainda não tem entrada em STEP_SECONDS, os dois arrays têm
  # tamanhos diferentes aqui, e é isso que decide o que já concluiu.
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
  echo "${C_YELLOW}  MONO REPO — Destruindo TUDO (infra + infra-db + lambda + app)${C_RESET}"
else
  echo "  MONO REPO — Deploy completo (infra + infra-db + lambda + app)"
fi
[[ "$AUTO" == "true" ]] && echo "${C_DIM}  (--auto: sem confirmação em cada etapa)${C_RESET}"
echo "${C_BLUE}════════════════════════════════════════════════════════════${C_RESET}"

if [[ "$DESTROY" == "true" ]]; then
  run_step "app (deploy)"              "$SCRIPT_DIR/app/terraform/apply.sh"       "${APP_ARGS[@]}"
  run_step "lambda (auth CPF)"         "$SCRIPT_DIR/lambda/terraform/apply.sh"    "${COMMON_ARGS[@]}"
  run_step "infra-db (RDS)"            "$SCRIPT_DIR/infra-db/terraform/apply.sh"  "${COMMON_ARGS[@]}"
  run_step "infra addons"              "$SCRIPT_DIR/infra/terraform/apply.sh"     "${COMMON_ARGS[@]}" --addons-only
  run_step "infra bootstrap"           "$SCRIPT_DIR/infra/terraform/apply.sh"     "${COMMON_ARGS[@]}" --bootstrap-only
else
  run_step "infra bootstrap"           "$SCRIPT_DIR/infra/terraform/apply.sh"     "${COMMON_ARGS[@]}" --bootstrap-only
  run_step "infra addons"              "$SCRIPT_DIR/infra/terraform/apply.sh"     "${COMMON_ARGS[@]}" --addons-only
  run_step "infra-db (RDS)"            "$SCRIPT_DIR/infra-db/terraform/apply.sh"  "${COMMON_ARGS[@]}"
  run_step "lambda (auth CPF)"         "$SCRIPT_DIR/lambda/terraform/apply.sh"    "${COMMON_ARGS[@]}"
  run_step "app (deploy)"              "$SCRIPT_DIR/app/terraform/apply.sh"       "${APP_ARGS[@]}"
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
