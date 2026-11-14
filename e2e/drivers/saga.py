import itertools
from dataclasses import dataclass, field

from support.waiting import wait_until

_payment_ids = itertools.count(900000)


def next_payment_id():
    return next(_payment_ids)


@dataclass
class PecaPedida:
    peca_id: str
    quantidade: int
    estoque_inicial: int


@dataclass
class OsFixture:
    os_id: int
    execucoes: list = field(default_factory=list)
    pecas: dict = field(default_factory=dict)

    @property
    def execucao_id(self):
        return self.execucoes[0]

    @property
    def peca_id(self):
        return next(iter(self.pecas))


class SagaDriver:
    def __init__(self, os_client, os_client_mecanico, inventory_client,
                 billing_client, fake_mp, os_db, billing_db,
                 proprietario_id, veiculo_id):
        self.os_client = os_client
        self.os_client_mecanico = os_client_mecanico
        self.inventory_client = inventory_client
        self.billing_client = billing_client
        self.fake_mp = fake_mp
        self.os_db = os_db
        self.billing_db = billing_db
        self.proprietario_id = proprietario_id
        self.veiculo_id = veiculo_id

    def nova_peca(self, nome, valor=45.00, estoque=0):
        peca = self.inventory_client.criar_peca(nome, "criada pelo e2e", valor)
        peca_id = peca["id"]
        if estoque > 0:
            self.inventory_client.entrada_estoque(peca_id, estoque)
        return peca_id

    def nova_os_aguardando_aprovacao(self, servicos, reclamacao="Barulho ao frear"):
        resposta = self.os_client.criar_ordem_servico(
            self.veiculo_id, self.proprietario_id, reclamacao)
        os_id = resposta["id"]

        self.os_client_mecanico.iniciar_diagnostico(os_id)
        self.os_client_mecanico.adicionar_servico(os_id, [
            {
                "servicoId": servico["servico_id"],
                "valorMaoDeObra": servico["valor_mao_de_obra"],
                "pecas": [
                    {"pecaId": p.peca_id, "quantidade": p.quantidade}
                    for p in servico["pecas"]
                ],
            }
            for servico in servicos
        ])
        self.os_client_mecanico.finalizar_diagnostico(os_id)

        execucoes = wait_until(
            lambda: self.os_db.execucao_servicos(os_id),
            lambda rows: len(rows) == len(servicos),
            desc=f"OS {os_id} should have {len(servicos)} execucoes persisted",
        )
        pecas = {}
        for servico in servicos:
            for p in servico["pecas"]:
                pecas[p.peca_id] = p

        wait_until(
            lambda: self.os_db.status(os_id),
            lambda status: status == "AGUARDANDO_APROVACAO",
            desc=f"OS {os_id} should reach AGUARDANDO_APROVACAO",
        )
        return OsFixture(os_id=os_id, execucoes=[row["id"] for row in execucoes], pecas=pecas)

    def nova_os_sem_servicos(self, reclamacao="Revisao sem reparo"):
        resposta = self.os_client.criar_ordem_servico(
            self.veiculo_id, self.proprietario_id, reclamacao)
        os_id = resposta["id"]
        self.os_client_mecanico.iniciar_diagnostico(os_id)
        self.os_client_mecanico.finalizar_diagnostico(os_id)
        wait_until(
            lambda: self.os_db.status(os_id),
            lambda status: status == "FINALIZADA",
            desc=f"OS {os_id} with no servicos should go straight to FINALIZADA",
        )
        return OsFixture(os_id=os_id)

    def esperar_orcamento(self, os_id, timeout=40):
        return wait_until(
            lambda: self.billing_db.orcamento(os_id),
            lambda row: row is not None,
            timeout=timeout,
            desc=f"billing should create an orcamento for OS {os_id}",
        )

    def aprovar_via_billing(self, fixture):
        self.esperar_orcamento(fixture.os_id)
        self.billing_client.aprovar_orcamento(fixture.os_id)
        return fixture

    def reprovar_via_billing(self, fixture):
        self.esperar_orcamento(fixture.os_id)
        self.billing_client.reprovar_orcamento(fixture.os_id)
        return fixture

    def aprovar_mute(self, fixture, status="APROVADO"):
        self.os_client.aprovacao_orcamento(fixture.os_id, [
            {"execucaoServicoId": execucao_id, "status": status}
            for execucao_id in fixture.execucoes
        ])
        return fixture

    def esperar_execucao(self, execucao_id, status, timeout=40):
        return wait_until(
            lambda: self.os_db.execucao_status(execucao_id),
            lambda actual: actual == status,
            timeout=timeout,
            desc=f"execucao {execucao_id} should reach {status}",
        )

    def esperar_os(self, os_id, status, timeout=40):
        return wait_until(
            lambda: self.os_db.status(os_id),
            lambda actual: actual == status,
            timeout=timeout,
            desc=f"OS {os_id} should reach {status}",
        )

    def executar(self, fixture, execucao_id):
        self.os_client_mecanico.iniciar_servico(fixture.os_id, execucao_id)
        self.os_client_mecanico.finalizar_servico(fixture.os_id, execucao_id)

    def executar_tudo(self, fixture):
        for execucao_id in fixture.execucoes:
            self.executar(fixture, execucao_id)

    def esperar_pagamento(self, os_id, timeout=40):
        return wait_until(
            lambda: self.billing_db.pagamento(os_id),
            lambda row: row is not None,
            timeout=timeout,
            desc=f"billing should create a pagamento for OS {os_id}",
        )

    def pagar(self, fixture, status="approved", status_detail="accredited"):
        pagamento = self.esperar_pagamento(fixture.os_id)
        payment_id = next_payment_id()
        self.fake_mp.program_payment(
            payment_id,
            external_reference=str(pagamento["id"]),
            status=status,
            status_detail=status_detail,
        )
        resposta = self.billing_client.webhook(payment_id)
        resposta.raise_for_status()
        return payment_id, str(pagamento["id"])
