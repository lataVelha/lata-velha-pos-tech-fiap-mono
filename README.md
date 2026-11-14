# lata-velha-pos-tech-fiap-mono

Repositório mono que agrega, como submódulos git, os quatro componentes do projeto **Lata
Velha** (Pós-Tech FIAP) e orquestra a ordem de deploy entre eles. Cada submódulo tem seu
próprio state Terraform e seu próprio pipeline de CI/CD — este repo não duplica a lógica de
deploy de nenhum, só decide **quando** e **em que ordem** cada um roda.

**Ponto de entrada único da aplicação em produção:** existe **um só** API Gateway (repo
`infra`) na frente de tudo. O ALB é **interno** (sem IP público) — todo tráfego externo,
incluindo o login por CPF (`POST /auth/cpf`, servido pelo repo `lambda`), passa pelo API
Gateway. Ver [`infra/README.md`](https://github.com/lataVelha/lata-velha-pos-tech-fiap-infra)
para os detalhes da arquitetura.

## Sumário

- [Submódulos](#submódulos)
- [Ordem do pipeline](#ordem-do-pipeline)
- [Como clonar](#como-clonar)
- [Execução local (`apply.sh`)](#execução-local-applysh)
- [Ambiente de desenvolvimento local (serviços separados)](#ambiente-de-desenvolvimento-local-serviços-separados)
- [CI/CD (GitHub Actions)](#cicd-github-actions)

---

## Submódulos

| Repo | O que provisiona/faz |
| --- | --- |
| [`infra`](https://github.com/lataVelha/lata-velha-pos-tech-fiap-infra) | VPC, EKS, ECR, ALB interno (target groups/listener rules por path), API Gateway (único ponto de entrada), Cluster Autoscaler e o stack `edge` (integração + rotas) |
| [`lambda`](https://github.com/lataVelha/lata-velha-pos-tech-fiap-lambda) | `auth-cpf` (`POST /auth/cpf`, lendo o RDS do os-service) + `jwt-authorizer`, anexadas ao API Gateway do `infra` |
| [`os-service`](https://github.com/lataVelha/lata-velha-pos-tech-fiap-os-service) | Domínio de Ordem de Serviço + `authentication`, com ECR/state/namespace/RDS próprios (`lata-velha-os`, NodePort 30082) e pipeline autônomo |
| [`inventory-service`](https://github.com/lataVelha/lata-velha-pos-tech-fiap-inventory-service) | Domínio de Peça/Estoque/PecaAlocada, com ECR/state/namespace próprios (`lata-velha-inventory`, NodePort 30083) e pipeline autônomo |
| [`billing-service`](https://github.com/lataVelha/lata-velha-pos-tech-fiap-billing-service) | Cobrança integrada ao Mercado Pago, com pipeline autônomo (`lata-velha-billing`, NodePort 30081) |

`os-service` e `inventory-service` compartilham código genérico (exceções, logging, paginação,
e-mail) via o submódulo git `shared-kernel` (repo
[`lata-velha-pos-tech-fiap-shared`](https://github.com/lataVelha/lata-velha-pos-tech-fiap-shared))
dentro de cada um — não é uma lib publicada ainda, o `pom.xml` de cada serviço só adiciona o
`src/main/java` do submódulo como source root extra (`build-helper-maven-plugin`).

## Ordem do pipeline

```
infra (bootstrap) ──┬──‖ infra (addons)    ALB + API GW
  VPC + EKS + ECR   └──‖ infra messaging   SNS/SQS/DLQs
                             │
              ┌──────────────┴───────────────┐
              │                              │
        lambda → edge                os-service ‖ inventory-service ‖ billing-service
      (authorizer + rotas)           (workloads, em paralelo)
```

Blocos com state Terraform próprio:

1. `addons ‖ messaging` — dependem só do bootstrap (VPC/subnets/EKS).
2. `os-service ‖ inventory-service ‖ billing-service` — cada workload lê só o
   state de messaging; `os-service` e `billing-service` provisionam o próprio
   RDS e o `inventory-service` sobe MongoDB no cluster.
3. `lambda` depende do `os-service` (a `auth-cpf` lê o RDS dele) e do `addons`
   (API Gateway). O `edge` fecha a borda depois do `lambda`, porque lê o
   `jwt_authorizer_id` + o state do addons.

> **Folga de nodes (margem, não causa):** quatro rollouts simultâneos com
> `maxUnavailable: 0` pedem pods de surge que não caberiam no teto antigo de 4
> nodes `t3.small`. O bootstrap passou a usar `node_max_size = 8`,
> `node_min_size = 2` e `node_desired_size = 3`. A falha de 617s vista no
> `os-service` **não** era capacidade: era colisão de Flyway/schema no RDS
> compartilhado do legado. O os-service agora tem **RDS próprio**
> (`terraform/rds.tf`, database `lata_velha_os`), então o problema não volta.

O `edge` é o dono único da integração e das rotas do API Gateway; o ALB
roteia por path para os serviços e responde **404** (catch-all) para paths
não roteados — o monolito e o `infra-db` foram removidos. O
`billing-service` mantém NLB/API Gateway próprios para o webhook do Mercado
Pago.

Cada serviço tem **repositório, ECR, state Terraform, namespace e NodePort
únicos**:

| Serviço | State | Namespace / Deployment | NodePort | ECR |
|---|---|---|---|---|
| os-service | `lata-velha/os-service/terraform.tfstate` | `lata-velha-os` / `lata-velha-os` | 30082 | `lata-velha-os-service` |
| inventory-service | `lata-velha/inventory-service/terraform.tfstate` | `lata-velha-inventory` / `lata-velha-inventory` | 30083 | `lata-velha-inventory-service` |
| billing-service | `lata-velha/billing-service/terraform.tfstate` | `lata-velha-billing` / `lata-velha-billing` | 30081 | `lata-velha-billing` |

Os serviços Postgres (`os-service` e `billing-service`) têm **RDS próprio** (database-per-service).
O `inventory-service` usa MongoDB, provisionado no próprio cluster
(`k8s/mongodb-deployment.yaml` + `k8s/mongodb-service.yaml`). A lambda `auth-cpf` consulta o
RDS do `os-service`; não existe mais `infra-db` nem deploy do `app` monolito.

O stack `edge` tem state próprio (`lata-velha/edge/terraform.tfstate`) e não é um serviço: ele
gerencia a integração/rotas do API Gateway e é aplicado depois do `lambda`.

O `infra` roda em duas etapas (`bootstrap` e `addons`) porque os providers `kubectl`/`helm` do
`addons` precisam do endpoint do EKS já existindo — não dá pra criar o cluster e configurar esses
providers na mesma apply. Isso é estrutural (sempre vai precisar de duas chamadas Terraform), mas
`addons` **não depende de nenhum outro repo** — ele cria o "casco" do API Gateway (API + VPC
Link + Stage, sem nenhuma rota), o ALB e os target groups/listener rules path-based dos serviços,
e roda logo em seguida do bootstrap. Quem anexa a integração e as rotas nesse Gateway é o stack
`edge` (públicas + catch-all protegido), depois do `lambda` (authorizer); o `lambda` anexa apenas
`POST /auth/cpf`. O `billing-service` mantém seu próprio NLB/API Gateway para o webhook. Detalhes em
[`infra/README.md`](https://github.com/lataVelha/lata-velha-pos-tech-fiap-infra#por-que-dois-módulos-terraform-separados-bootstrap-e-addons).

## Como clonar

Como este repositório usa submódulos, clone-o já trazendo os submódulos:

```bash
git clone --recurse-submodules https://github.com/lataVelha/lata-velha-pos-tech-fiap-mono.git
```

Se já tiver clonado sem essa flag, inicialize os submódulos manualmente:

```bash
git submodule update --init --recursive
```

Para atualizar os submódulos posteriormente:

```bash
git submodule update --remote --recursive
```

## Execução local (`apply.sh`)

Pré-requisito: `aws configure` (ou `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_SESSION_TOKEN`
no ambiente), Terraform >= 1.10, Docker, Java 21, Node 20 — os pré-requisitos de cada submódulo
combinados. Cada repo tem seu `terraform.tfvars.example` — copie e edite antes do primeiro apply
(ver o `README.md` de cada um).

`./apply.sh` na raiz chama o `apply.sh` de cada submódulo, na ordem do
[pipeline](#ordem-do-pipeline) acima:

```bash
./apply.sh                    # pipeline completo, com confirmação interativa em cada etapa
./apply.sh --auto             # pipeline completo, sem confirmação
./apply.sh --skip-tests       # pula os testes Maven (os/inventory/billing)
./apply.sh --destroy          # desfaz tudo na ordem inversa, com confirmação
./apply.sh --destroy --auto   # desfaz tudo, sem confirmação
```

O ALB opera em modo catch-all 404 (`app_enabled=false`, default): paths que
não casam com nenhuma listener rule viram 404. A var `APP_ENABLED=true`
existe apenas como escape hatch de rollback manual do monolito.

Rollback de um serviço sem rebuild: ver [`docs/RUNBOOK-rollback.md`](docs/RUNBOOK-rollback.md).

## Ambiente de desenvolvimento local (serviços separados)

Este `docker-compose.yml` na raiz sobe **os serviços já separados** —
`os-service`, `inventory-service` e `billing-service` — junto com os bancos de
cada um (Postgres para os/billing, MongoDB para inventory) e um broker de
eventos (RabbitMQ), pra testar a topologia localmente. Não inclui a infra AWS.

**Pré-requisito:** Docker + submódulos inicializados recursivamente (precisa do
`shared-kernel` dentro de `os-service/` e `inventory-service/`):

```bash
git submodule update --init --recursive
```

**Subir tudo:**

```bash
cp .env.example .env   # opcional — e-mail e Mercado Pago funcionam sem preencher
docker compose up --build -d
docker compose ps      # espera todos ficarem "healthy"
```

| Serviço | Swagger | Banco |
| --- | --- | --- |
| `os-service` | http://localhost:8080/swagger-ui.html | `postgres-os:5432/lata_velha_os` (host `5432`) |
| `inventory-service` | http://localhost:8082/swagger-ui.html | `postgres-inventory:5432/lata_velha_inventory` (host `5434`) |
| `billing-service` | http://localhost:8081/swagger-ui.html | `postgres-billing:5432/lata_velha_billing` (host `5433`) |
| RabbitMQ (broker de eventos, dev) | management UI em http://localhost:15672 (guest/guest) | AMQP na porta `5672` |

**RabbitMQ vs SQS:** RabbitMQ aqui é só pro ambiente de dev/local — em produção o adapter
correspondente (quando os serviços passarem a consumi-lo) é SQS. Ver o scaffolding de eventos
no submódulo `shared-kernel`.

**Parar tudo:**

```bash
docker compose down       # mantém os dados dos bancos (volumes)
docker compose down -v    # remove também os volumes (bancos zerados no próximo up)
```

## CI/CD (GitHub Actions)

O workflow deste repo (`.github/workflows/main.yml`) não reimplementa o deploy de cada
submódulo — ele **chama o workflow reusável** de cada um (`uses:
lataVelha/<repo>/.github/workflows/<arquivo>.yml@master`), na ordem do
[pipeline](#ordem-do-pipeline) (os stacks independentes em paralelo), encadeados via `needs:`:

1. `deploy-infra-bootstrap` → `infra/.github/workflows/bootstrap.yml`
2. `deploy-infra-addons` → `infra/.github/workflows/addons.yml`  ┐
3. `deploy-infra-messaging` → `infra/.github/workflows/messaging.yml`  ┘ paralelos
4. `deploy-os-service` → `os-service/.github/workflows/main.yml`  ┐
5. `deploy-inventory-service` → `inventory-service/.github/workflows/main.yml`  ├ workloads
6. `deploy-billing-service` → `billing-service/.github/workflows/main.yml`  ┘ em paralelo
7. `deploy-lambda` → `lambda/.github/workflows/main.yml` (espera 2 **e** 4)
8. `deploy-edge` → `infra/.github/workflows/edge.yml` (depois do lambda)

A validação roda em todo push e PR, em três jobs de matriz independentes:
`validate-terraform` (um job por stack, `terraform validate` sem state/AWS — inclui
`infra/terraform/messaging` e `infra/terraform/edge`), `validate-java` (um job por serviço:
`mvn test` do os-service, inventory-service e billing-service) e `validate-lambda` (pytest) — sem aplicar nada. O job
agregador `validate` só consolida os resultados (e é ele que os `deploy-*`/`summary` referenciam
em `needs`). Os jobs `deploy-*` rodam em push pra `master` (aqui e em cada submódulo) **ou** via
disparo manual (`workflow_dispatch`) — os jobs `destroy-*` só rodam via `workflow_dispatch` com
`destroy: true` (nunca automático). `dry_run=true` executa apenas a validação; nenhum deploy é
disparado.

Template e contrato dos workflows: [`docs/service-ci-cd-template.md`](docs/service-ci-cd-template.md).

**Pré-requisito:** cada um dos 5 repos submódulo precisa permitir ser chamado de fora, em
**Settings → Actions → General → Access** (liberar para a organização/conta `lataVelha` ou para
este repo especificamente) — como `lataVelha` é uma conta pessoal (não organização), essa
liberação nem sempre é suficiente sozinha; os repos submódulo estão públicos, o que também
resolve o acesso cross-repo. Sem isso, os jobs `deploy-*`/`destroy-*` falham com "workflow was
not found" ao tentar carregar o workflow do outro repo.

**Destroy:** cada submódulo também aceita um input `destroy` no próprio `workflow_call` (mesmo
padrão do `--destroy` que os `apply.sh` locais já tinham) — o job `workflow_dispatch` com
`destroy: true` chama os workflows reusáveis na ordem inversa:
`edge → billing → inventory → lambda → os → infra messaging → infra (addons) →
infra (bootstrap)`. A lambda é destruída antes do os-service porque lê o state dele.
