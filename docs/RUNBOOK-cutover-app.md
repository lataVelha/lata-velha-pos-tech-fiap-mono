# RUNBOOK — cutover do `app` e das rotas/ALB

Objetivo: tirar o `app` da borda sem downtime prolongado, passando o
roteamento por path para `os-service` e `inventory-service`, e deixar o
`app` opcional no pipeline (`DEPLOY_APP=false`).

## Desenho final

```
Cliente → API Gateway (stack edge)
                       → ALB (listener :80, regras por path)
                       → os-service        NodePort 30082
                       → inventory-service NodePort 30083
                       → app               NodePort 30080 (só fallback)
```

- `edge` é o dono único da integração + rotas do API Gateway.
- `addons` é o dono do ALB + target groups + listener rules.
- `app`, `os-service` e `inventory-service` **não** criam mais recursos de
  API Gateway/ALB.
- `billing-service` mantém NLB/API Gateway próprios (webhook do Mercado Pago).

## Mapa de paths

| Path | Serviço | Público? |
|---|---|---|
| `/ordens-servico`, `/historico`, `/funcionarios`, `/servicos`, `/proprietarios`, `/veiculos` | os-service | protegido |
| `/auth/login` | os-service | público |
| `/auth/cpf`, `/auth/cpf-openapi.json` | lambda | público |
| `/pecas`, `/pecas/*` | inventory-service | protegido |
| `POST /ordens-servico/{id}/aprovacao-orcamento` | os-service | público |
| `/pagamentos`, `/orcamentos` | billing-service | interno (NLB próprio) |
| qualquer outro | app (até o cutover) / 404 (depois) | — |

## Pré-requisitos

- Terraform >= 1.10, AWS CLI autenticado, `kubectl` no cluster.
- `infra/terraform/addons` aplicado (API Gateway + ALB + VPC Link).
- `lambda` aplicado (authorizer JWT).
- `os-service` e `inventory-service` com a nova versão (sem recursos de
  API Gateway no Terraform) publicados.
- Bucket de state acessível (`lata-velha-tfstate-<account>`).

## Passo 0 — higiene de state (uma vez)

Se algum serviço já aplicou as rotas duplicadas no API Gateway, remova esses
recursos do state **sem** apagar os recursos na AWS (o dono antigo é o app):

```bash
cd os-service/terraform
# Lista o que existe (nao apaga nada na AWS):
terraform state list | grep -E 'aws_apigatewayv2_(integration|route)' || true

# Remove do STATE (a AWS continua intacta; o dono antigo e o app):
terraform state list | grep -E 'aws_apigatewayv2_(integration|route)' | while read -r r; do
  terraform state rm "$r"
done
```

Repita em `inventory-service/terraform`. Não faça isso no `addons`/`edge`.

## Passo 1 — ALB por path (app ainda default)

O `infra/terraform/addons` já cria os target groups/listener rules de
`os` (30082) e `inventory` (30083). Aplique:

```bash
cd infra/terraform
APP_ENABLED=true ./apply.sh --addons-only --auto
```

Valide que o ALB tem as regras:

```bash
aws elbv2 describe-rules \
  --listener-arn "$(terraform -chdir=addons output -raw alb_listener_arn)" \
  --query 'Rules[].Conditions' --output table
```

(O listener ARN também está no output `alb_listener_arn` do addons.)

## Passo 2 — subir os serviços

```bash
cd ../../os-service/terraform        && ./apply.sh --auto --skip-tests
cd ../../inventory-service/terraform && ./apply.sh --auto --skip-tests
```

Confirme os pods e os Services:

```bash
kubectl get deploy,svc -n lata-velha-os
kubectl get deploy,svc -n lata-velha-inventory
```

## Passo 3 — validar o roteamento pelo API Gateway

O app ainda é dono das rotas, mas o API Gateway encaminha tudo ao ALB, que
agora decide por path:

```bash
API=$(aws apigatewayv2 get-apis \
  --query "Items[?Name=='lata-velha-app-api'].ApiEndpoint" --output text)

curl -i "$API/actuator/health"          # os-service (regra health)
curl -i "$API/pecas"                    # inventory (401 sem token = rota chegou)
curl -i "$API/auth/login" -X POST       # os (400/401 = rota chegou)
```

Se `/pecas` ou `/auth/login` retornarem 404/502, pare aqui: a listener rule
ou o NodePort não está correto.

## Passo 4 — remover as rotas antigas do app

Aplique o `app` com o código novo (as rotas saíram do Terraform dele):

```bash
cd ../../app/terraform
./apply.sh --auto --skip-tests
```

O `terraform apply` vai **destruir** `aws_apigatewayv2_integration.alb` e as
rotas do app. A API fica sem rotas por alguns instantes.

## Passo 5 — criar as rotas no edge

```bash
cd ../../infra/terraform/edge
./apply.sh --auto
```

Valide:

```bash
aws apigatewayv2 get-routes \
  --api-id "$(aws apigatewayv2 get-apis --query "Items[?Name=='lata-velha-app-api'].ApiId" --output text)" \
  --query 'Items[].RouteKey' --output table
```

Devem aparecer as rotas públicas + `ANY /` + `ANY /{proxy+}`.

## Passo 6 — desligar o app no ALB

```bash
cd ..   # volta para infra/terraform
APP_ENABLED=false ./apply.sh --addons-only --auto
```

Isso adiciona a listener rule catch-all `/*` → 404 (priority 50000). O app
continua rodando, mas nenhum path não mapeado chega nele.

## Passo 7 — desligar o deploy e destruir o app

No GitHub, em **Settings → Secrets and variables → Actions → Variables** do
mono, crie:

```
DEPLOY_APP=false
APP_ENABLED=false
```

Se o pipeline próprio do repo `infra` também rodar em `master`, crie
`APP_ENABLED=false` **também lá** — senão o `addons.yml` dele reaplica com o
default `true` e remove a regra 404.

Localmente:

```bash
cd ../../app/terraform
./apply.sh --auto --destroy
```

Ou, pelo mono: `./apply.sh --skip-app` (o destroy sempre inclui o app).

## Passo 8 — validação final

```bash
kubectl get deploy -n lata-velha || echo "namespace do app removido"

# fluxo completo (E2E) — a partir da raiz do mono
cd ../..
API_URL="$API" bash e2e/sqs/saga-e2e.sh

# rotas que nao existem mais
curl -i "$API/rota-inexistente"   # 404
```

Checklist:

- [ ] `/ordens-servico` responde os-service
- [ ] `/pecas` responde inventory-service
- [ ] `/auth/login` responde os-service
- [ ] `/auth/cpf` responde lambda
- [ ] app sem pods (ou namespace removido)
- [ ] `deploy-edge` verde no mono
- [ ] resumo do run mostra `deploy-app: skipped`

## Rollback

1. `APP_ENABLED=true` e reaplicar addons (remove o catch-all 404).
2. `DEPLOY_APP=true` e rodar o workflow do app (volta o Deployment).
3. Reaplicar o `app` com o código anterior (rotas voltam ao state dele).
4. Remover o `edge` (`./apply.sh --destroy`) para liberar as rotas antes de
   reaplicar o app, se necessário.

O state do app continua no S3; nada é apagado do ECR.

## Observações

- `os-service` e `inventory-service` são independentes; se um path falhar,
  o fallback do app continua atendendo os demais.
- O `billing-service` não participa deste cutover.
- O cutover de rotas é uma janela curta (passos 4–5). Faça fora de horário
  de uso.
