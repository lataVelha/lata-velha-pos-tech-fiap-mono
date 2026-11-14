# Matriz de secrets e variáveis — CI/CD

Story 12.1. Nenhum valor sensível é versionado: tudo vem de
`Settings → Secrets and variables → Actions` do repositório correspondente.

> **Estado verificado em 2026-11-12** (via `gh secret list`). O mono repassa
> os secrets aos workflows reusáveis; os repos de serviço só precisam dos
> próprios secrets para rodar o pipeline standalone (push no próprio repo).

## Secrets por repositório

| Secret | mono | os-service | inventory-service | billing-service | infra |
|---|---|---|---|---|---|
| `AWS_ACCESS_KEY_ID` | ✅ | ❌ | ❌ | ❌ | ✅ |
| `AWS_SECRET_ACCESS_KEY` | ✅ | ❌ | ❌ | ❌ | ✅ |
| `AWS_SESSION_TOKEN` | ✅ | ❌ | ❌ | ❌ | ✅ |
| `TF_MAIL_USERNAME` | ✅ | ❌ | ❌ | — | — |
| `TF_MAIL_PASSWORD` | ✅ | ❌ | ❌ | — | — |
| `TF_VAR_DD_API_KEY` (mono/infra) | ✅ | ❌ | ❌ | ❌ | ✅ |
| `TF_DD_API_KEY` (nome passado aos serviços) | — | — | — | — | — | — |
| `TF_MERCADOPAGO_ACCESS_TOKEN` | ❌ | — | — | — | ❌ | — |
| `TF_MERCADOPAGO_WEBHOOK_SECRET` | ❌ | — | — | — | ❌ | — |
| `TF_DB_USERNAME` / `TF_DB_PASSWORD` | ✅ | — | — | — | — | — |
| `DD_API_KEY` / `DD_APP_KEY` (observability local) | — | — | — | — | — | usado só no apply local |

**Nomes diferentes:** o mono guarda `TF_VAR_DD_API_KEY` e o workflow repassa
esse valor como `TF_DD_API_KEY` para os/inventory/billing e como
`TF_VAR_dd_api_key` para o `infra` (addons).

## Variables (não sensíveis)

| Variable | Repos | Valor |
|---|---|---|
| `AWS_REGION` | todos | `us-east-1` |
| `CLUSTER_NAME` | mono/os/inventory/billing (opcional) | `lata-velha-eks` |
| `APP_ENABLED` | mono e infra | não configurada (default `false`); `true` = escape hatch de rollback do monolito |

## Regras

1. **Nunca** commitar `.env`, `terraform.tfvars`, chaves ou tokens.
2. Workflows só declaram em `workflow_call.secrets` o que realmente usam.
3. O mono repassa os secrets aos workflows dos serviços; os serviços não
   leem secrets uns dos outros.
4. Logs do Actions mascaram automaticamente qualquer valor de `secrets.*`.
   Não imprima `TF_VAR_*` sensíveis (`terraform output -raw` de valores
   sensíveis deve ser evitado).
5. Sessão AWS expirada (Academy): o primeiro step de cada pipeline roda
   `aws sts get-caller-identity`; a mensagem de erro indica credenciais
   inválidas/expiradas.

## Rotação

| Secret | Frequência | Procedimento |
|---|---|---|
| AWS Academy | por sessão do laboratório | atualizar os 3 secrets no mono + infra (e nos serviços se rodarem standalone) |
| `TF_MAIL_PASSWORD` | ao trocar a app password do Gmail | atualizar em mono (e os/inventory se standalone) |
| `TF_VAR_DD_API_KEY` | a cada 90 dias | Datadog → Organization Settings → API Keys |
| Mercado Pago | ao rotacionar credenciais | painel → Credenciais → atualizar billing + mono |

Após rotacionar, rode `workflow_dispatch` com `dry_run=true` para validar sem
aplicar.

## Bloqueios atuais (2026-11-12)

- AWS expirada: os runs de `build`/deploy falham em
  `configure-aws-credentials` ("Credentials could not be loaded").
- `TF_MERCADOPAGO_ACCESS_TOKEN` ausente no mono e no billing: o deploy do
  billing falha na validação do Terraform (`mercadopago_access_token`).
- os-service/inventory-service/billing-service sem secrets próprios: os
  pipelines standalone falham no build; o caminho do mono funciona se os
  secrets do mono estiverem válidos.
