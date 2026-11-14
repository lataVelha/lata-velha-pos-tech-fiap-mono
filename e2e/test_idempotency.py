from drivers.saga import PecaPedida
from support.bus import publish_event
from support.waiting import settle, wait_until


def test_orcamento_aprovado_duplicado_reserva_apenas_uma_vez(
        saga, recorder, os_db, inventory_db, seeded_servico_ids, rabbitmq_channel):
    peca_id = saga.nova_peca("Peca dup aprovado e2e", valor=25.00, estoque=5)
    fixture = saga.nova_os_aguardando_aprovacao([{
        "servico_id": seeded_servico_ids[0],
        "valor_mao_de_obra": 45.00,
        "pecas": [PecaPedida(peca_id=peca_id, quantidade=2, estoque_inicial=5)],
    }])
    os_id, execucao_id = fixture.os_id, fixture.execucao_id
    meu = lambda p: p.get("osId") == os_id

    saga.aprovar_via_billing(fixture)
    saga.esperar_os(os_id, "EM_EXECUCAO")
    wait_until(
        lambda: inventory_db.estoque_disponivel(peca_id),
        lambda disponivel: disponivel == 3,
        desc="two units should be reserved once",
    )
    alocada_antes = inventory_db.peca_alocada(execucao_id, peca_id)

    publish_event(rabbitmq_channel, "OrcamentoAprovado", {
        "osId": os_id,
        "valorTotal": 95.00,
        "itens": [{
            "execucaoServicoId": execucao_id,
            "pecaId": peca_id,
            "quantidade": 2,
        }],
    })
    recorder.wait_for_count("OrcamentoAprovado", 2, meu)
    settle(5)

    assert inventory_db.contar_alocadas(execucao_id, peca_id) == 1
    alocada_depois = inventory_db.peca_alocada(execucao_id, peca_id)
    assert alocada_depois["QTD_RESERVADA"] == alocada_antes["QTD_RESERVADA"]
    assert inventory_db.estoque_disponivel(peca_id) == 3
    assert inventory_db.estoque_armazenado(peca_id) == 5
    assert os_db.status(os_id) == "EM_EXECUCAO"


def test_service_ended_duplicado_deduz_apenas_uma_vez(
        saga, recorder, os_db, inventory_db, seeded_servico_ids, rabbitmq_channel):
    peca_id = saga.nova_peca("Peca dup service e2e", valor=25.00, estoque=6)
    fixture = saga.nova_os_aguardando_aprovacao([{
        "servico_id": seeded_servico_ids[0],
        "valor_mao_de_obra": 45.00,
        "pecas": [PecaPedida(peca_id=peca_id, quantidade=2, estoque_inicial=6)],
    }])
    os_id, execucao_id = fixture.os_id, fixture.execucao_id

    saga.aprovar_via_billing(fixture)
    saga.esperar_os(os_id, "EM_EXECUCAO")
    saga.executar(fixture, execucao_id)
    saga.esperar_os(os_id, "FINALIZADA")

    wait_until(
        lambda: inventory_db.estoque_armazenado(peca_id),
        lambda armazenada: armazenada == 4,
        desc="stock should be deducted once",
    )
    assert inventory_db.status_alocada(execucao_id, peca_id) == "INSTALADA"

    publish_event(rabbitmq_channel, "ServiceEnded", {
        "osId": os_id,
        "execucaoServicoId": execucao_id,
        "itens": [{"pecaId": peca_id, "quantidade": 2}],
    })
    recorder.wait_for_count("ServiceEnded", 2,
                            lambda p: p.get("execucaoServicoId") == execucao_id)
    recorder.assert_not_seen("DeducaoEstoqueFalhou",
                             lambda p: p.get("execucaoServicoId") == execucao_id, settle=5)

    alocada = inventory_db.peca_alocada(execucao_id, peca_id)
    assert alocada["STATUS"] == "INSTALADA"
    assert alocada["QTD_INSTALADA"] == 2
    assert inventory_db.estoque_armazenado(peca_id) == 4


def test_webhook_duplicado_publica_payment_done_apenas_uma_vez(
        saga, recorder, os_db, billing_db, seeded_servico_ids):
    peca_id = saga.nova_peca("Peca dup webhook e2e", valor=25.00, estoque=3)
    fixture = saga.nova_os_aguardando_aprovacao([{
        "servico_id": seeded_servico_ids[0],
        "valor_mao_de_obra": 45.00,
        "pecas": [PecaPedida(peca_id=peca_id, quantidade=1, estoque_inicial=3)],
    }])
    os_id = fixture.os_id
    meu = lambda p: p.get("osId") == os_id

    saga.aprovar_via_billing(fixture)
    saga.esperar_os(os_id, "EM_EXECUCAO")
    saga.executar_tudo(fixture)
    saga.esperar_os(os_id, "FINALIZADA")

    pagamento = saga.esperar_pagamento(os_id)
    payment_id, _ = saga.pagar(fixture)
    recorder.wait_for("PaymentDone", meu)
    saga.esperar_os(os_id, "PAGA")

    aprovado = wait_until(
        lambda: billing_db.pagamento(os_id),
        lambda row: row["status"] == "APROVADO",
        desc="pagamento should be APROVADO",
    )

    for _ in range(3):
        resposta = saga.billing_client.webhook(payment_id)
        assert resposta.status_code < 500

    recorder.assert_count("PaymentDone", 1, meu, settle=5)

    depois = billing_db.pagamento(os_id)
    assert depois["status"] == "APROVADO"
    assert depois["atualizado_em"] == aprovado["atualizado_em"]
    assert depois["mercado_pago_payment_id"] == payment_id
    assert os_db.historico_ladder(os_id).count("PAGA") == 1


def test_diagnostico_finalizado_duplicado_cria_um_unico_orcamento(
        saga, recorder, billing_db, seeded_servico_ids, rabbitmq_channel):
    peca_id = saga.nova_peca("Peca dup diagnostico e2e", valor=30.00, estoque=3)
    fixture = saga.nova_os_aguardando_aprovacao([{
        "servico_id": seeded_servico_ids[0],
        "valor_mao_de_obra": 60.00,
        "pecas": [PecaPedida(peca_id=peca_id, quantidade=1, estoque_inicial=3)],
    }])
    os_id, execucao_id = fixture.os_id, fixture.execucao_id
    meu = lambda p: p.get("osId") == os_id

    orcamento = saga.esperar_orcamento(os_id)

    publish_event(rabbitmq_channel, "DiagnosticoFinalizado", {
        "osId": os_id,
        "itens": [{
            "execucaoServicoId": execucao_id,
            "servicoId": seeded_servico_ids[0],
            "valorMaoDeObra": 999.00,
            "pecas": [{"pecaId": peca_id, "quantidade": 1}],
        }],
    })
    recorder.wait_for_count("DiagnosticoFinalizado", 2, meu)
    settle(5)

    assert billing_db.contar_orcamentos(os_id) == 1
    depois = billing_db.orcamento(os_id)
    assert depois["valor_total"] == orcamento["valor_total"]
    assert depois["atualizado_em"] == orcamento["atualizado_em"]
    assert len(billing_db.orcamento_itens(os_id)) == 1
