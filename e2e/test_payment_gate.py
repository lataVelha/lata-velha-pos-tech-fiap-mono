from drivers.saga import PecaPedida
from support.asserts import assert_ladder
from support.waiting import wait_until


def _os_finalizada(saga, servico_id, nome_peca):
    peca_id = saga.nova_peca(nome_peca, valor=30.00, estoque=3)
    fixture = saga.nova_os_aguardando_aprovacao([{
        "servico_id": servico_id,
        "valor_mao_de_obra": 60.00,
        "pecas": [PecaPedida(peca_id=peca_id, quantidade=1, estoque_inicial=3)],
    }])
    saga.aprovar_via_billing(fixture)
    saga.esperar_os(fixture.os_id, "EM_EXECUCAO")
    saga.executar_tudo(fixture)
    saga.esperar_os(fixture.os_id, "FINALIZADA")
    return fixture


def test_os_finalizada_nao_pode_ser_entregue_sem_pagamento(saga, os_db, billing_db,
                                                           seeded_servico_ids):
    fixture = _os_finalizada(saga, seeded_servico_ids[0], "Peca gate nao paga")

    resposta = saga.os_client.retirar_veiculo(fixture.os_id, raise_on_error=False)

    assert resposta.status_code >= 400, (
        "a FINALIZADA OS with work done must not be deliverable before payment"
    )
    ordem = os_db.ordem_servico(fixture.os_id)
    assert ordem["status"] == "FINALIZADA"
    assert ordem["entregue_em"] is None
    assert "ENTREGUE" not in os_db.historico_ladder(fixture.os_id)

    pagamento = saga.esperar_pagamento(fixture.os_id)
    assert pagamento["status"] == "PENDENTE"


def test_os_paga_pode_ser_entregue(saga, recorder, os_db, seeded_servico_ids):
    fixture = _os_finalizada(saga, seeded_servico_ids[0], "Peca gate paga")
    meu = lambda p: p.get("osId") == fixture.os_id

    bloqueada = saga.os_client.retirar_veiculo(fixture.os_id, raise_on_error=False)
    assert bloqueada.status_code >= 400

    saga.pagar(fixture)
    recorder.wait_for("PaymentDone", meu)
    saga.esperar_os(fixture.os_id, "PAGA")

    saga.os_client.retirar_veiculo(fixture.os_id)
    saga.esperar_os(fixture.os_id, "ENTREGUE")

    ordem = os_db.ordem_servico(fixture.os_id)
    assert ordem["entregue_em"] is not None


def test_os_reprovada_pode_ser_entregue_sem_pagamento(saga, recorder, os_db, billing_db,
                                                      inventory_db, seeded_servico_ids):
    peca_id = saga.nova_peca("Peca gate reprovada", valor=30.00, estoque=3)
    fixture = saga.nova_os_aguardando_aprovacao([{
        "servico_id": seeded_servico_ids[0],
        "valor_mao_de_obra": 60.00,
        "pecas": [PecaPedida(peca_id=peca_id, quantidade=1, estoque_inicial=3)],
    }])
    os_id = fixture.os_id
    meu = lambda p: p.get("osId") == os_id

    saga.reprovar_via_billing(fixture)
    recorder.wait_for("OrcamentoReprovado", meu)
    saga.esperar_os(os_id, "REPROVADA")

    recorder.assert_not_seen("OrcamentoAprovado", meu, settle=2)
    recorder.assert_not_seen("InventoryChecked", meu, settle=0)

    saga.os_client.retirar_veiculo(os_id)
    saga.esperar_os(os_id, "ENTREGUE")

    assert billing_db.orcamento(os_id)["status"] == "REPROVADO"
    assert billing_db.contar_pagamentos(os_id) == 0
    assert inventory_db.contar_alocadas(fixture.execucao_id) == 0
    assert inventory_db.estoque_armazenado(peca_id) == 3
    assert inventory_db.estoque_disponivel(peca_id) == 3
    assert_ladder(os_db, os_id, [
        "RECEBIDA", "EM_DIAGNOSTICO", "AGUARDANDO_APROVACAO", "REPROVADA", "ENTREGUE",
    ])


def test_os_sem_servicos_pode_ser_entregue(saga, recorder, os_db, billing_db):
    fixture = saga.nova_os_sem_servicos()
    os_id = fixture.os_id
    meu = lambda p: p.get("osId") == os_id

    recorder.wait_for("OSEnded", meu)
    recorder.assert_not_seen("DiagnosticoFinalizado", meu, settle=2)

    assert billing_db.contar_orcamentos(os_id) == 0

    saga.os_client.retirar_veiculo(os_id)
    saga.esperar_os(os_id, "ENTREGUE")

    assert_ladder(os_db, os_id, ["RECEBIDA", "EM_DIAGNOSTICO", "FINALIZADA", "ENTREGUE"])


def test_pagamento_rejeitado_nao_libera_entrega_e_aprovacao_posterior_libera(
        saga, recorder, os_db, billing_db, seeded_servico_ids):
    fixture = _os_finalizada(saga, seeded_servico_ids[0], "Peca gate rejeitado")
    os_id = fixture.os_id
    meu = lambda p: p.get("osId") == os_id

    saga.pagar(fixture, status="rejected", status_detail="cc_rejected_other_reason")
    rejeitado = wait_until(
        lambda: billing_db.pagamento(os_id),
        lambda row: row["status"] == "REJEITADO",
        desc="pagamento should be REJEITADO after a rejected webhook",
    )
    assert rejeitado["status_detalhe"] == "cc_rejected_other_reason"

    recorder.assert_not_seen("PaymentDone", meu, settle=3)
    assert os_db.status(os_id) == "FINALIZADA"
    bloqueada = saga.os_client.retirar_veiculo(os_id, raise_on_error=False)
    assert bloqueada.status_code >= 400

    saga.pagar(fixture, status="approved")
    recorder.wait_for("PaymentDone", meu)
    saga.esperar_os(os_id, "PAGA")

    aprovado = billing_db.pagamento(os_id)
    assert aprovado["status"] == "APROVADO"
    assert aprovado["atualizado_em"] > rejeitado["atualizado_em"]

    saga.os_client.retirar_veiculo(os_id)
    saga.esperar_os(os_id, "ENTREGUE")
