# Evidências — app opcional + cutover de rotas/ALB

## O que foi implementado

### App opcional

- Mono: `deploy-app` só roda com `vars.DEPLOY_APP != 'false'`.
- `deploy-os-service` não depende mais do app.
- `apply.sh`: `--skip-app` / `DEPLOY_APP=false`; o destroy continua limpando o app.
- `dry_run=true` no mono não dispara nenhum deploy (só `validate`).

### ALB path-based

- `infra/terraform/modules/alb`: target groups + listener rules por serviço
  (`services` / `listener_rules`), com os limites da AWS respeitados
  (5 valores por regra, 6 wildcards).
- `os-service`: `/ordens-servico`, `/historico`, `/funcionarios`, `/servicos`,
  `/proprietarios`, `/veiculos`, `/auth/*`.
- `inventory-service`: `/pecas`.
- `health`: `/actuator/health*` → os-service.
- `app_enabled=false` adiciona a regra catch-all `/*` → 404 (priority 50000).

### Edge (API Gateway)

- Novo stack `infra/terraform/edge` (state `lata-velha/edge/terraform.tfstate`):
  integração HTTP_PROXY com o ALB + rotas públicas + `ANY /` e `ANY /{proxy+}`
  protegidas pela authorizer do lambda.
- Novo workflow reusável `infra/.github/workflows/edge.yml`.
- Mono: `deploy-edge` depois do lambda/app e antes dos serviços;
  `destroy-edge` primeiro no destroy.
- `app`, `os-service` e `inventory-service` deixaram de criar recursos de
  API Gateway/ALB (sem rotas duplicadas).

### Outros

- `app/.github/workflows/main.yml`: smoke test do app via port-forward no
  Service (não depende mais do API Gateway durante o cutover).
- `docs/RUNBOOK-cutover-app.md`: passo a passo + rollback.
- READMEs atualizados (pipeline, `DEPLOY_APP`, `APP_ENABLED`, edge).

## Validações executadas

```bash
terraform fmt -check -recursive infra/terraform app/terraform \
  os-service/terraform inventory-service/terraform          # OK
terraform validate (addons, edge, app, os, inventory)        # OK
actionlint (todos os workflows)                              # OK
python3 yaml.safe_load (workflows)                           # OK
bash -n apply.sh infra/terraform/edge/apply.sh               # OK
```

Não executado (exige AWS/cluster): `terraform plan/apply`, deploy real,
`e2e/sqs/saga-e2e.sh`, smoke tests.

## Pendências operacionais

1. Commitar/pushar `app`, `infra`, `os-service`, `inventory-service`.
2. Bump dos ponteiros dos submódulos no mono.
3. Rodar o roteiro de [`docs/RUNBOOK-cutover-app.md`](../RUNBOOK-cutover-app.md).
4. Definir `DEPLOY_APP=false`/`APP_ENABLED=false` no mono (e `APP_ENABLED=false`
   no repo `infra`, se o pipeline dele rodar em `master`).
