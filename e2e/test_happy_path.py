from decimal import Decimal

from drivers.saga import PecaPedida
from support.asserts import (
    assert_conservacao_estoque,
    assert_ladder,
    assert_um_estado_aberto,
    assert_valor,
)
from support.config import DLQS, SERVICE_QUEUES
from support import bus
from support.waiting import wait_until

LADDER_COMPLETA = [
    "RECEBIDA",
    "EM_DIAGNOSTICO",
    "AGUARDANDO_APROVACAO",
    "APROVADA",
    "EM_EXECUCAO",
    "FINALIZADA",
    "PAGA",
    "ENTREGUE",
]


def test_fluxo_completo_um_servico(saga, recorder, os_db, billing_db, inventory_db,
                                   seeded_servico_ids):
    peca_id = saga.nova_peca("Pastilha e2e happy", valor=45.00, estoque=5)
    fixture = saga.nova_os_aguardando_aprovacao([{
        "servico_id": seeded_servico_ids[0],
        "valor_mao_de_obra": 150.00,
        "pecas": [PecaPedida(peca_id=peca_id, quantidade=2, estoque_inicial=5)],
    }])
    os_id = fixture.os_id
    execucao_id = fixture.execucao_id
    meu = lambda p: p.get("osId") == os_id

    recorder.wait_for("DiagnosticoFinalizado", meu)

    orcamento = saga.esperar_orcamento(os_id)
    assert orcamento["status"] == "PENDENTE"
    assert_valor(orcamento["valor_total"], Decimal("240.00"))

    saga.aprovar_via_billing(fixture)
    recorder.wait_for("OrcamentoAprovado", meu)
    inventory_checked = recorder.wait_for("InventoryChecked", meu)
    assert inventory_checked["itens"] == [
        {"execucaoServicoId": execucao_id, "pecasDisponiveis": True}
    ]

    saga.esperar_os(os_id, "EM_EXECUCAO")
    assert os_db.execucao_status(execucao_id) == "APROVADO"
    wait_until(
        lambda: inventory_db.status_alocada(execucao_id, peca_id),
        lambda status: status == "RESERVADA",
        desc="peca should be reserved after InventoryChecked",
    )
    assert inventory_db.estoque_disponivel(peca_id) == 3
    assert inventory_db.estoque_armazenado(peca_id) == 5

    saga.executar(fixture, execucao_id)
    recorder.wait_for("ServiceEnded", lambda p: p.get("execucaoServicoId") == execucao_id)
    recorder.wait_for("OSEnded", meu)
    recorder.assert_not_seen("DeducaoEstoqueFalhou", meu, settle=2)

    saga.esperar_os(os_id, "FINALIZADA")
    wait_until(
        lambda: inventory_db.estoque_armazenado(peca_id),
        lambda armazenada: armazenada == 3,
        desc="stock should be deducted once the service ended",
    )
    assert inventory_db.status_alocada(execucao_id, peca_id) == "INSTALADA"

    recorder.wait_for("CobrancaEmitida", meu)
    pagamento = saga.esperar_pagamento(os_id)
    assert pagamento["status"] == "PENDENTE"
    assert_valor(pagamento["valor"], Decimal("240.00"))
    assert pagamento["checkout_url"] is not None
    assert pagamento["preferencia_id"] is not None

    payment_id, pagamento_uuid = saga.pagar(fixture)
    recorder.wait_for("PaymentDone", meu)
    saga.esperar_os(os_id, "PAGA")

    pago = wait_until(
        lambda: billing_db.pagamento(os_id),
        lambda row: row["status"] == "APROVADO",
        desc="pagamento should be APROVADO after the webhook",
    )
    assert pago["mercado_pago_payment_id"] == payment_id
    assert pago["status_detalhe"] == "accredited"
    assert str(pago["id"]) == pagamento_uuid

    saga.os_client.retirar_veiculo(os_id)
    saga.esperar_os(os_id, "ENTREGUE")

    ordem = os_db.ordem_servico(os_id)
    assert ordem["entregue_em"] is not None
    assert ordem["finalizado_em"] < ordem["entregue_em"]

    assert_ladder(os_db, os_id, LADDER_COMPLETA)
    assert_um_estado_aberto(os_db, os_id, "ENTREGUE")
    assert_conservacao_estoque(inventory_db, peca_id, 5, [execucao_id])

    assert billing_db.contar_orcamentos(os_id) == 1
    assert billing_db.contar_pagamentos(os_id) == 1
    assert inventory_db.contar_alocadas(execucao_id, peca_id) == 1

    recorder.assert_order("ServiceEnded", "OSEnded", meu)


def test_fluxo_completo_dois_servicos(saga, recorder, os_db, billing_db, inventory_db,
                                      seeded_servico_ids):
    peca_a = saga.nova_peca("Disco e2e dois", valor=100.00, estoque=4)
    peca_b = saga.nova_peca("Pivor e2e dois", valor=70.00, estoque=4)
    fixture = saga.nova_os_aguardando_aprovacao([
        {
            "servico_id": seeded_servico_ids[0],
            "valor_mao_de_obra": 80.00,
            "pecas": [PecaPedida(peca_id=peca_a, quantidade=1, estoque_inicial=4)],
        },
        {
            "servico_id": seeded_servico_ids[1],
            "valor_mao_de_obra": 20.00,
            "pecas": [PecaPedida(peca_id=peca_b, quantidade=2, estoque_inicial=4)],
        },
    ])
    os_id = fixture.os_id
    exec_a, exec_b = fixture.execucoes
    meu = lambda p: p.get("osId") == os_id

    orcamento = saga.esperar_orcamento(os_id)
    assert_valor(orcamento["valor_total"], Decimal("340.00"))
    assert len(billing_db.orcamento_itens(os_id)) == 2

    saga.aprovar_via_billing(fixture)
    checked = recorder.wait_for("InventoryChecked", meu)
    assert len(checked["itens"]) == 2
    assert all(item["pecasDisponiveis"] for item in checked["itens"])

    saga.esperar_os(os_id, "EM_EXECUCAO")

    saga.executar(fixture, exec_a)
    recorder.wait_for("ServiceEnded", lambda p: p.get("execucaoServicoId") == exec_a)
    assert os_db.status(os_id) == "EM_EXECUCAO"
    recorder.assert_not_seen("OSEnded", meu, settle=3)

    saga.executar(fixture, exec_b)
    recorder.wait_for("ServiceEnded", lambda p: p.get("execucaoServicoId") == exec_b)
    recorder.wait_for("OSEnded", meu)
    saga.esperar_os(os_id, "FINALIZADA")

    recorder.assert_count("OSEnded", 1, meu, settle=3)
    recorder.assert_count("CobrancaEmitida", 1, meu, settle=0)

    saga.pagar(fixture)
    saga.esperar_os(os_id, "PAGA")
    saga.os_client.retirar_veiculo(os_id)
    saga.esperar_os(os_id, "ENTREGUE")

    assert billing_db.contar_pagamentos(os_id) == 1
    assert_conservacao_estoque(inventory_db, peca_a, 4, [exec_a])
    assert_conservacao_estoque(inventory_db, peca_b, 4, [exec_b])
    assert_ladder(os_db, os_id, LADDER_COMPLETA)


def test_higiene_do_barramento_apos_fluxo_completo(saga, os_db, seeded_servico_ids,
                                                   dlq_baseline):
    peca_id = saga.nova_peca("Amortecedor e2e higiene", valor=30.00, estoque=2)
    fixture = saga.nova_os_aguardando_aprovacao([{
        "servico_id": seeded_servico_ids[0],
        "valor_mao_de_obra": 10.00,
        "pecas": [PecaPedida(peca_id=peca_id, quantidade=1, estoque_inicial=2)],
    }])
    saga.aprovar_via_billing(fixture)
    saga.esperar_os(fixture.os_id, "EM_EXECUCAO")
    saga.executar_tudo(fixture)
    saga.esperar_os(fixture.os_id, "FINALIZADA")
    saga.pagar(fixture)
    saga.esperar_os(fixture.os_id, "PAGA")
    saga.os_client.retirar_veiculo(fixture.os_id)
    saga.esperar_os(fixture.os_id, "ENTREGUE")

    segunda = saga.os_client.retirar_veiculo(fixture.os_id, raise_on_error=False)
    assert segunda.status_code >= 400
    assert os_db.status(fixture.os_id) == "ENTREGUE"
    assert len(os_db.historico_abertos(fixture.os_id)) == 1

    for name, queue in SERVICE_QUEUES.items():
        depth = wait_until(
            lambda q=queue: bus.queue_depth(q),
            lambda d: d == 0,
            timeout=30,
            desc=f"main queue {queue} should drain to zero",
        )
        assert depth == 0

    for name, queue in DLQS.items():
        assert bus.queue_depth(queue) == dlq_baseline[name], (
            f"{queue} grew during a clean happy path"
        )
