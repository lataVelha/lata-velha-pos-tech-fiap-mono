# RUNBOOK — rollback de deploy

Story 12.3. Aplicável a `os-service`, `inventory-service` e
`billing-service` (o monolito `app` foi removido).

## Quando fazer rollback

- Smoke test pós-deploy falhou (`/actuator/health` != 200 em 10 tentativas).
- Taxa de 5xx > 1% por 5 min após o deploy.
- Erro funcional bloqueante confirmado (ex.: saga quebrada).

## Opção 1 — reverter o pod para a imagem anterior (mais rápido)

```bash
SERVICE=lata-velha-os
NAMESPACE=lata-velha-os

# Ver histórico
kubectl rollout history deployment/$SERVICE -n $NAMESPACE

# Reverter para a revisão anterior
kubectl rollout undo deployment/$SERVICE -n $NAMESPACE

# Acompanhar
kubectl rollout status deployment/$SERVICE -n $NAMESPACE --timeout=5m
```

> Funciona quando o Deployment mantém o histórico (RollingUpdate). O
> Terraform continuará com a imagem nova no state até o próximo apply — por
> isso, em seguida, faça a Opção 2 ou re-deploy da tag corrigida.

## Opção 2 — re-deploy de uma tag anterior (state consistente)

```bash
# lista as tags no ECR
aws ecr describe-images \
  --repository-name lata-velha-os-service \
  --query 'sort_by(imageDetails,&imagePushedAt)[-10:].[imageTags[0],imagePushedAt]' \
  --output table
```

Dispare o workflow do serviço com `image_tag=<tag-anterior>`:

```bash
gh workflow run main.yml \
  -R lataVelha/lata-velha-pos-tech-fiap-os-service \
  -f image_tag=<tag-anterior>
```

O workflow usa `IMAGE_TAG` no build/push (reaproveita a tag) e o Terraform
aplica `TF_VAR_docker_image=<ecr>:<tag>`, deixando o state coerente.

Também é possível rodar localmente:

```bash
cd os-service
TF_VAR_docker_image="<ecr_url>:<tag>" ./terraform/apply.sh --auto --skip-tests
```

## Opção 3 — reverter o commit

```bash
git revert <sha>
git push origin master
```

O pipeline roda normalmente e publica uma nova imagem. É a opção mais lenta.

## Checklist

- [ ] Identificar o deploy/commit que introduziu a regressão.
- [ ] Escolher a opção (1 para mitigar rápido, 2 para consistência).
- [ ] Rodar o smoke test na versão revertida:
      `SERVICE=<svc> NAMESPACE=<ns> bash scripts/smoke-test.sh`.
- [ ] Confirmar `kubectl get deploy -A` sem CrashLoop.
- [ ] Registrar o incidente (commit, tag, horário, responsável).
- [ ] Abrir issue com a causa raiz e o teste que faltou.

## Responsáveis

| Serviço | Owner |
|---|---|
| os-service | time de OS |
| inventory-service | time de estoque |
| billing-service | time de billing |

## Observações

- O rollback de um serviço não afeta os demais (state/namespace/ECR isolados).
- Em caso de DLQ crescendo, siga também `infra/terraform/messaging/RUNBOOK-sqs.md`.
- O ALB opera em catch-all 404 (`app_enabled=false`); não há mais rollback de
  cutover do monolito.
