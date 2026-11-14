# Template de CI/CD dos microsserviços — Story 10.2

Este documento descreve o contrato único dos workflows de `os-service`,
`inventory-service` e `billing-service`. O arquivo de referência é
[`service-ci-cd-template.yml`](./service-ci-cd-template.yml).

## Jobs

| Job | Quando roda | O que faz |
|---|---|---|
| `test` | PR, push e `workflow_call` (exceto destroy e `skip_tests=true`) | `mvn -B test -Dspring.profiles.active=test` |
| `validate` | PR, push e `workflow_call` | `terraform fmt -check -recursive` + `terraform init -backend=false` + `terraform validate` |
| `build` | push em `master`, `workflow_dispatch` e `workflow_call` (sem destroy/dry-run) | autentica na AWS/ECR, garante o repositório ECR via Terraform direcionado e publica a imagem com a tag `image_tag` (default: `github.sha`) |
| `tf-plan` | PR e `workflow_dispatch` com `dry_run=true` | `terraform plan -detailed-exitcode` (nunca aplica) |
| `tf-deploy` | push em `master`, `workflow_dispatch` e `workflow_call` | `terraform init -reconfigure` + `terraform apply -auto-approve` |
| `verify` | após `tf-deploy` | `kubectl rollout status` + smoke test `GET /actuator/health` com até 10 tentativas |
| `destroy` | `workflow_dispatch`/`workflow_call` com `destroy=true` | passa variáveis dummy para validações e roda `terraform destroy` apenas no stack do serviço |

## Identidade que muda por serviço

Cada workflow copia o template e troca apenas a matriz abaixo:

| Item | `os-service` | `inventory-service` | `billing-service` |
|---|---|---|---|
| Repository | `lataVelha/lata-velha-pos-tech-fiap-os-service` | `lataVelha/lata-velha-pos-tech-fiap-inventory-service` | `lataVelha/lata-velha-pos-tech-fiap-billing-service` |
| `ECR_REPO_NAME` | `lata-velha-os-service` | `lata-velha-inventory-service` | `lata-velha-billing` |
| `SERVICE_NAME` | `lata-velha-os` | `lata-velha-inventory` | `lata-velha-billing` |
| `NAMESPACE` | `lata-velha-os` | `lata-velha-inventory` | `lata-velha-billing` |
| State key | `lata-velha/os-service/terraform.tfstate` | `lata-velha/inventory-service/terraform.tfstate` | `lata-velha/billing-service/terraform.tfstate` |
| NodePort | `30082` | `30083` | `30081` |
| Health path | `/actuator/health` | `/actuator/health` | `/actuator/health` |

## Contrato de inputs

```yaml
workflow_call:
  inputs:
    aws_region:  # default: vars.AWS_REGION ou us-east-1
    destroy:     # default: false
    dry_run:     # default: false
    image_tag:   # default: github.sha (rollback por tag)
    skip_tests:  # default: false — o mono passa true (já testou no `validate`)
  secrets:
    AWS_ACCESS_KEY_ID
    AWS_SECRET_ACCESS_KEY
    AWS_SESSION_TOKEN        # opcional (Academy)
    TF_MAIL_USERNAME         # os/inventory
    TF_MAIL_PASSWORD         # os/inventory
    TF_DD_API_KEY            # opcional
    TF_MERCADOPAGO_ACCESS_TOKEN      # billing
    TF_MERCADOPAGO_WEBHOOK_SECRET    # billing (opcional)
```

## Convenções obrigatórias

1. **Checkout sempre do próprio repositório** — nunca de outro serviço.
2. **Concorrência por serviço** (`deploy-os`, `deploy-inventory`, `deploy-billing`) com
   `cancel-in-progress: false`: deploys não se atropelam, mas serviços diferentes não se bloqueiam.
3. **Timeout** por job (20 min serviço; 45 min mono).
4. **ECR próprio**: o build faz um apply direcionado de
   `aws_ecr_repository`/`aws_ecr_lifecycle_policy` antes do push, lê
   `terraform output -raw ecr_repository_url` e publica apenas lá.
5. **Destroy isolado**: `terraform destroy` no stack do serviço, com variáveis dummy
   (`TF_VAR_docker_image`, `TF_VAR_mail_*`, `TF_VAR_mercadopago_access_token`) para satisfazer
   validações; nunca toca no state de outro serviço.
6. **Evidências**: `$GITHUB_STEP_SUMMARY` deve conter commit, imagem, state key, namespace,
   resultado do rollout e status do health.
7. **Sem segredos hardcoded**: apenas `secrets.*`/`vars.*`.

## Destruição

- `workflow_dispatch` com `destroy=true` destrói apenas o stack do serviço.
- O mono chama os workflows em ordem inversa (`edge → billing → inventory →
  lambda → os → messaging → addons → bootstrap`).
- Nenhum workflow de serviço destrói o stack de outro serviço.
