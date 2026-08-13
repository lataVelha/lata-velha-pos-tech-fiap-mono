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
- [CI/CD (GitHub Actions)](#cicd-github-actions)

---

## Submódulos

| Repo | O que provisiona/faz |
| --- | --- |
| [`infra`](https://github.com/lataVelha/lata-velha-pos-tech-fiap-infra) | VPC, EKS, ECR, ALB interno, API Gateway (único ponto de entrada), Cluster Autoscaler |
| [`infra-db`](https://github.com/lataVelha/lata-velha-pos-tech-fiap-infra-db) | RDS PostgreSQL |
| [`lambda`](https://github.com/lataVelha/lata-velha-pos-tech-fiap-lambda) | Login por CPF (`auth-cpf`) e a lambda authorizer, anexadas ao API Gateway do `infra` |
| [`app`](https://github.com/lataVelha/lata-velha-pos-tech-fiap) | Aplicação Spring Boot (DDD) + deploy dela no cluster (Deployment/Service/ConfigMap/Secret/HPA/PDB) |

## Ordem do pipeline

```
infra (bootstrap)  →  infra (addons)  →  infra-db  →  lambda  →  app
  VPC+EKS+ECR           ALB interno +        RDS        auth-cpf +    deploy da
                        API Gateway vazio                authorizer    aplicação
```

O `infra` roda em duas etapas (`bootstrap` e `addons`) porque os providers `kubectl`/`helm` do
`addons` precisam do endpoint do EKS já existindo — não dá pra criar o cluster e configurar esses
providers na mesma apply. Isso é estrutural (sempre vai precisar de duas chamadas Terraform), mas
`addons` **não depende de nenhum outro repo** — ele cria só o "casco" do API Gateway (API + VPC
Link + Stage, sem nenhuma rota) e roda logo em seguida do bootstrap, no mesmo disparo. Quem anexa
rota nesse Gateway é o próprio `lambda` (`POST /auth/cpf` + authorizer) e o próprio `app`
(integração com o ALB + rotas públicas/protegidas), cada um lendo o `api_id` via
`terraform_remote_state` — nenhum deles precisa que o `infra` saiba que eles existem. Detalhes em
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
./apply.sh --skip-tests       # pula os testes Maven do app
./apply.sh --destroy          # desfaz tudo, ordem inversa, com confirmação
./apply.sh --destroy --auto   # desfaz tudo, sem confirmação
```

## CI/CD (GitHub Actions)

O workflow deste repo (`.github/workflows/main.yml`) não reimplementa o deploy de cada
submódulo — ele **chama o workflow reusável** de cada um (`uses:
lataVelha/<repo>/.github/workflows/<arquivo>.yml@master`), na ordem do
[pipeline](#ordem-do-pipeline), encadeados via `needs:`:

1. `deploy-infra-bootstrap` → `infra/.github/workflows/bootstrap.yml`
2. `deploy-infra-addons` → `infra/.github/workflows/addons.yml`
3. `deploy-infra-db` → `infra-db/.github/workflows/main.yml`
4. `deploy-lambda` → `lambda/.github/workflows/main.yml`
5. `deploy-app` → `app/.github/workflows/main.yml`

Um job `validate` roda em todo push e PR: checkout com os submódulos fixados no commit do mono
repo, `terraform validate` de todos e testes (Maven do `app`, pytest do `lambda`) — sem aplicar
nada. Os jobs `deploy-*` rodam em push pra `master` (aqui e em cada submódulo) **ou** via
disparo manual (`workflow_dispatch`) — os jobs `destroy-*` só rodam via `workflow_dispatch`
com `destroy: true` (nunca automático).

**Pré-requisito:** cada um dos 4 repos submódulo precisa permitir ser chamado de fora, em
**Settings → Actions → General → Access** (liberar para a organização/conta `lataVelha` ou para
este repo especificamente) — como `lataVelha` é uma conta pessoal (não organização), essa
liberação nem sempre é suficiente sozinha; os 4 repos submódulo estão públicos, o que também
resolve o acesso cross-repo. Sem isso, os jobs `deploy-*`/`destroy-*` falham com "workflow was
not found" ao tentar carregar o workflow do outro repo.

**Destroy:** cada submódulo também aceita um input `destroy` no próprio `workflow_call` (mesmo
padrão do `--destroy` que os `apply.sh` locais já tinham) — o job `workflow_dispatch` com
`destroy: true` chama os mesmos 5 workflows reusáveis do deploy, só que com `destroy: true`, na
ordem inversa da **nova** topologia de dependência: `app → lambda → infra-db → infra (addons) →
infra (bootstrap)` (app e lambda dependem do `api_id` que o `addons` criou, então têm que ser
destruídos antes dele).
