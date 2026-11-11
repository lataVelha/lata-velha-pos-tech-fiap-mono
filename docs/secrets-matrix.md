# Matriz de secrets e variáveis — CI/CD

Story 12.1. Nenhum valor sensível é versionado: tudo vem de
`Settings → Secrets and variables → Actions` do repositório correspondente.

## Secrets por repositório

| Secret | mono | app | os-service | inventory-service | billing-service | infra |
|---|---|---|---|---|---|---|
| `AWS_ACCESS_KEY_ID` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `AWS_SECRET_ACCESS_KEY` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `AWS_SESSION_TOKEN` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `TF_MAIL_USERNAME` | ✅ | ✅ | ✅ | ✅ | — | — |
| `TF_MAIL_PASSWORD` | ✅ | ✅ | ✅ | ✅ | — | — |
| `TF_DD_API_KEY` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ (como `TF_VAR_dd_api_key`) |
| `TF_MERCADOPAGO_ACCESS_TOKEN` | ✅ | — | — | — | ✅ | — |
| `TF_MERCADOPAGO_WEBHOOK_SECRET` | ✅ | — | — | — | ✅ | — |
| `TF_DB_USERNAME` / `TF_DB_PASSWORD` | ✅ | — | — | — | — | — |
| `DD_API_KEY` / `DD_APP_KEY` (observability local) | — | — | — | — | — | usado só no apply local |
| `TF_VAR_dd_api_key` (nome legado do repo infra) | ✅ | — | — | — | — | ✅ |

## Variables (não sensíveis)

| Variable | Repos | Valor |
|---|---|---|
| `AWS_REGION` | todos | `us-east-1` |
| `CLUSTER_NAME` | mono/os/inventory/billing (opcional) | `lata-velha-eks` |

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
| AWS Academy | por sessão do laboratório | atualizar os 3 secrets nos 7 repos |
| `TF_MAIL_PASSWORD` | ao trocar a app password do Gmail | atualizar em app/os/inventory |
| `TF_DD_API_KEY` | a cada 90 dias | Datadog → Organization Settings → API Keys |
| Mercado Pago | ao rotacionar credenciais | painel → Credenciais → atualizar billing + mono |

Após rotacionar, rode `workflow_dispatch` com `dry_run=true` para validar sem
aplicar.
