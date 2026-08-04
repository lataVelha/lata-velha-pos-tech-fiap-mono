#!/usr/bin/env bash
#
# apply.sh — orquestra o apply.sh de cada submódulo, na ordem documentada
# nos READMEs (infra → infra-db → app):
#
#   (padrão)   [1/3] infra (VPC+EKS+ECR+ALB) → [2/3] infra-db (RDS) → [3/3] app (deploy)
#   --destroy  [1/3] app → [2/3] infra-db → [3/3] infra   (ordem inversa)
#
# lambda não tem apply.sh e é ignorado.
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

set -euo pipefail

# infra e infra-db só entendem --auto/--destroy; --skip-tests é só do app.
COMMON_ARGS=()
APP_ARGS=()
DESTROY=false
for arg in "$@"; do
  case "$arg" in
    --destroy) DESTROY=true; COMMON_ARGS+=("$arg"); APP_ARGS+=("$arg") ;;
    --auto)    COMMON_ARGS+=("$arg"); APP_ARGS+=("$arg") ;;
    --skip-tests|--skip-test) APP_ARGS+=("$arg") ;;
    *)
      echo "Flag desconhecida: $arg"
      echo "Uso: ./apply.sh [--auto] [--skip-tests] [--destroy]"
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

run_step() {
  local label="$1" script="$2"; shift 2
  echo
  echo "==> $label ($script)"
  ( cd "$(dirname "$script")" && "./$(basename "$script")" "$@" )
}

if [[ "$DESTROY" == "true" ]]; then
  run_step "app (deploy)"    "$SCRIPT_DIR/app/terraform/apply.sh"       "${APP_ARGS[@]}"
  run_step "infra-db (RDS)"  "$SCRIPT_DIR/infra-db/terraform/apply.sh"  "${COMMON_ARGS[@]}"
  run_step "infra (base)"    "$SCRIPT_DIR/infra/terraform/apply.sh"     "${COMMON_ARGS[@]}"
else
  run_step "infra (base)"    "$SCRIPT_DIR/infra/terraform/apply.sh"     "${COMMON_ARGS[@]}"
  run_step "infra-db (RDS)"  "$SCRIPT_DIR/infra-db/terraform/apply.sh"  "${COMMON_ARGS[@]}"
  run_step "app (deploy)"    "$SCRIPT_DIR/app/terraform/apply.sh"       "${APP_ARGS[@]}"
fi

echo
echo "==> Concluído."
