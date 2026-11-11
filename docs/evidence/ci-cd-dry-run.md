# Evidências — dry-run e sumários do pipeline (Story 12.4)

## O que foi implementado

- Input `dry_run` no mono: com `true`, **nenhum** job de deploy é disparado
  (bootstrap/addons/messaging/infra-db/lambda/app/edge/serviços); roda só o
  `validate`.
- Inputs `dry_run` e `image_tag` nos workflows dos três serviços.
- `tf-plan` com `terraform plan -detailed-exitcode` em PR e em
  `workflow_dispatch` com `dry_run=true` (nunca aplica).
- `$GITHUB_STEP_SUMMARY` em:
  - `validate` (implícito no log do Actions);
  - `tf-deploy` (imagem + state key);
  - `verify` (`scripts/smoke-test.sh` publica namespace, deployment, URL e
    status);
  - `destroy` (serviço destruído);
  - `edge` (API Gateway, rotas aplicadas);
  - `summary` do mono (commit, evento, ref, flags e resultados de todos os
    jobs, inclusive `deploy-app: skipped` e `deploy-edge`).
- `docs/RUNBOOK-rollback.md` documenta o re-deploy por `image_tag`.
- `docs/RUNBOOK-cutover-app.md` documenta o cutover do app/rotas/ALB.

## Como validar sem aplicar

```bash
# PR que altera workflow/terraform
gh pr create --title "ci: dry run" --body "validação"
# o job tf-plan roda e falha com exit code 2 quando há diff

# dry-run manual no mono
gh workflow run main.yml -f dry_run=true
```

## Sumário esperado (deploy)

| campo | valor |
|---|---|
| commit | `<sha>` |
| imagem | `<ecr>:<sha>` |
| state key | `lata-velha/<serviço>/terraform.tfstate` |
| namespace | `lata-velha-<serviço>` |
| rollout | `deployment "<svc>" successfully rolled out` |
| health | `HTTP 200` |

## Sumário esperado (destroy)

| campo | valor |
|---|---|
| operação | `destroy` |
| ordem | `billing → inventory → os → app → lambda → infra-db → infra-messaging → addons → bootstrap` |
| recursos removidos | listados no log do `terraform destroy` |

## Auditoria

Cada run do Actions mantém: ator, commit, inputs (`destroy`, `dry_run`,
`image_tag`) e artefatos de plan. O sumário do mono registra os resultados dos
jobs encadeados.
