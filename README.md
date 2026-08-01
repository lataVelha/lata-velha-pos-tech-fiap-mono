# lata-velha-pos-tech-fiap-mono

## Sumário

Repositório mono que agrega, como submódulos git, os componentes do projeto Lata Velha (Pós Tech FIAP):

- **app** — aplicação principal
- **lambda** — funções serverless (AWS Lambda)
- **infra** — infraestrutura como código
- **infra-db** — infraestrutura de banco de dados

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
