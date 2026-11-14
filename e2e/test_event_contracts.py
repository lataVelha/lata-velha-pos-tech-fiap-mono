from decimal import Decimal

import requests

from drivers.saga import PecaPedida
from support.asserts import assert_valor
from support.config import BILLING_BASE_URL, INVENTORY_BASE_URL, OS_BASE_URL


def test_payloads_consumidos_pelo_billing_mantem_a_forma(
        saga, recorder, billing_db, seeded_servico_ids):
    peca_id = saga.nova_peca("Peca contrato e2e", valor=45.00, estoque=4)
    fixture = saga.nova_os_aguardando_aprovacao([{
        "servico_id": seeded_servico_ids[0],
        "valor_mao_de_obra": 175.50,
        "pecas": [PecaPedida(peca_id=peca_id, quantidade=1, estoque_inicial=4)],
    }])
    os_id, execucao_id = fixture.os_id, fixture.execucao_id
    meu = lambda p: p.get("osId") == os_id

    diagnostico = recorder.wait_for("DiagnosticoFinalizado", meu)
    assert diagnostico["osId"] == os_id
    assert len(diagnostico["itens"]) == 1
    item = diagnostico["itens"][0]
    assert item["execucaoServicoId"] == execucao_id
    assert item["servicoId"] == seeded_servico_ids[0]
    assert_valor(item["valorMaoDeObra"], Decimal("175.50"))
    assert item["pecas"] == [{"pecaId": peca_id, "quantidade": 1}]

    saga.aprovar_via_billing(fixture)
    aprovado = recorder.wait_for("OrcamentoAprovado", meu)
    assert aprovado["osId"] == os_id
    assert_valor(aprovado["valorTotal"], Decimal("220.50"))
    assert aprovado["itens"] == [
        {"execucaoServicoId": execucao_id, "pecaId": peca_id, "quantidade": 1}
    ]

    saga.esperar_os(os_id, "EM_EXECUCAO")
    saga.executar(fixture, execucao_id)

    service_ended = recorder.wait_for(
        "ServiceEnded", lambda p: p.get("execucaoServicoId") == execucao_id)
    assert service_ended["osId"] == os_id
    assert service_ended["itens"] == [{"pecaId": peca_id, "quantidade": 1}]

    os_ended = recorder.wait_for("OSEnded", meu)
    assert os_ended == {"osId": os_id}

    cobranca = recorder.wait_for("CobrancaEmitida", meu)
    assert cobranca == {"osId": os_id}

    saga.pagar(fixture)
    payment_done = recorder.wait_for("PaymentDone", meu)
    assert payment_done == {"osId": os_id}


def test_orcamento_soma_mao_de_obra_com_precos_do_inventory(
        saga, billing_db, seeded_servico_ids):
    peca_caro = saga.nova_peca("Peca caro e2e", valor=200.00, estoque=2)
    peca_barato = saga.nova_peca("Peca barato e2e", valor=5.50, estoque=10)
    fixture = saga.nova_os_aguardando_aprovacao([{
        "servico_id": seeded_servico_ids[0],
        "valor_mao_de_obra": 120.00,
        "pecas": [
            PecaPedida(peca_id=peca_caro, quantidade=1, estoque_inicial=2),
            PecaPedida(peca_id=peca_barato, quantidade=4, estoque_inicial=10),
        ],
    }])
    os_id, execucao_id = fixture.os_id, fixture.execucao_id

    orcamento = saga.esperar_orcamento(os_id)
    assert_valor(orcamento["valor_total"], Decimal("342.00"))

    itens = billing_db.orcamento_itens(os_id)
    assert {(i["execucao_servico_id"], i["peca_id"], i["quantidade"]) for i in itens} == {
        (execucao_id, peca_caro, 1),
        (execucao_id, peca_barato, 4),
    }

    necessarias = saga.os_db.pecas_necessarias(execucao_id)
    assert {(n["peca_id"], n["quantidade"]) for n in necessarias} == {
        (peca_caro, 1),
        (peca_barato, 4),
    }


def test_matriz_de_autorizacao(saga, billing_client_mecanico, os_client_mecanico,
                               seeded_servico_ids):
    peca_id = saga.nova_peca("Peca authz e2e", valor=30.00, estoque=2)
    fixture = saga.nova_os_aguardando_aprovacao([{
        "servico_id": seeded_servico_ids[0],
        "valor_mao_de_obra": 60.00,
        "pecas": [PecaPedida(peca_id=peca_id, quantidade=1, estoque_inicial=2)],
    }])
    os_id = fixture.os_id
    saga.esperar_orcamento(os_id)

    negado = billing_client_mecanico.aprovar_orcamento_raw(os_id)
    assert negado.status_code == 403

    sem_token = requests.post(f"{BILLING_BASE_URL}/orcamentos/{os_id}/aprovar")
    assert sem_token.status_code in (401, 403)

    assert requests.get(f"{INVENTORY_BASE_URL}/pecas/{peca_id}").status_code == 200
    assert requests.get(
        f"{BILLING_BASE_URL}/pagamentos/ordem-servico/{os_id}").status_code in (200, 404)
    assert requests.get(f"{OS_BASE_URL}/actuator/health").status_code == 200

    saga.aprovar_via_billing(fixture)
    saga.esperar_os(os_id, "EM_EXECUCAO")

    atendente_tentando_executar = saga.os_client.iniciar_servico(
        os_id, fixture.execucao_id, raise_on_error=False)
    assert atendente_tentando_executar.status_code == 403
