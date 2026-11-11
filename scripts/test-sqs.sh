#!/usr/bin/env bash
#
# test-sqs.sh — sobe o LocalStack (se necessário) e roda os testes de
# integração SNS/SQS (Story 15.2).
#
# Uso: ./scripts/test-sqs.sh [serviço]
#   serviço default: os-service (shared-kernel é o mesmo nos três)

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVICE="${1:-os-service}"

if ! docker info >/dev/null 2>&1; then
  echo "Docker não está rodando." >&2
  exit 1
fi

docker compose -f "$ROOT/docker-compose.test.yml" up -d

echo "Aguardando LocalStack..."
for _ in $(seq 1 30); do
  if curl -sf http://localhost:4566/_localstack/health >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

export LOCALSTACK_ENDPOINT="${LOCALSTACK_ENDPOINT:-http://localhost:4566}"

cd "$ROOT/$SERVICE"
./mvnw -B test -Dspring.profiles.active=test \
  -Dtest='LocalStackSqsIT' \
  -Dsurefire.failIfNoSpecifiedTests=false
