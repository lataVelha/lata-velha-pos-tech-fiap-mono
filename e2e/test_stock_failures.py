from drivers.saga import PecaPedida
from support.waiting import settle, wait_until


def test_estoque_removido_apos_reserva_emite_deducao_falhou(
        saga, recorder, os_db, billing_db, inventory_db, seeded_servico_ids):
    peca_id = saga.nova_peca("Peca sabotada e2e", valor=25.00, estoque=2)
    fixture = saga.nova_os_aguardando_aprovacao([{
        "servico_id": seeded_servico_ids[0],
        "valor_mao_de_obra": 45.00,
        "pecas": [PecaPedida(peca_id=peca_id, quantidade=2, estoque_inicial=2)],
    }])
    os_id, execucao_id = fixture.os_id, fixture.execucao_id
    meu = lambda p: p.get("osId") == os_id

    saga.aprovar_via_billing(fixture)
    saga.esperar_os(os_id, "EM_EXECUCAO")
    wait_until(
        lambda: inventory_db.status_alocada(execucao_id, peca_id),
        lambda status: status == "RESERVADA",
        desc="peca should be reserved before the stock is removed",
    )

    saga.inventory_client.ajustar_estoque(peca_id, 0, 0)

    saga.executar(fixture, execucao_id)
    falha = recorder.wait_for("DeducaoEstoqueFalhou",
                              lambda p: p.get("execucaoServicoId") == execucao_id)
    assert falha["motivo"]

    saga.esperar_os(os_id, "FINALIZADA")
    recorder.wait_for("OSEnded", meu)
    recorder.wait_for("CobrancaEmitida", meu)

    alocada = inventory_db.peca_alocada(execucao_id, peca_id)
    assert alocada["STATUS"] == "RESERVADA"
    assert alocada["QTD_INSTALADA"] == 0
    assert inventory_db.estoque_armazenado(peca_id) == 0

    pagamento = saga.esperar_pagamento(os_id)
    assert pagamento["status"] == "PENDENTE"
    assert os_db.status(os_id) == "FINALIZADA"


def test_deducao_sem_reserva_e_recusada(saga, recorder, os_db, billing_db,
                                        inventory_db, seeded_servico_ids):
    peca_id = saga.nova_peca("Peca sem reserva e2e", valor=25.00, estoque=5)
    fixture = saga.nova_os_aguardando_aprovacao([{
        "servico_id": seeded_servico_ids[0],
        "valor_mao_de_obra": 45.00,
        "pecas": [PecaPedida(peca_id=peca_id, quantidade=2, estoque_inicial=5)],
    }])
    os_id, execucao_id = fixture.os_id, fixture.execucao_id
    meu = lambda p: p.get("osId") == os_id

    saga.aprovar_mute(fixture)
    saga.esperar_os(os_id, "APROVADA")

    recorder.assert_not_seen("OrcamentoAprovado", meu, settle=3)
    recorder.assert_not_seen("InventoryChecked", meu, settle=0)
    assert inventory_db.contar_alocadas(execucao_id) == 0

    saga.executar(fixture, execucao_id)
    falha = recorder.wait_for("DeducaoEstoqueFalhou",
                              lambda p: p.get("execucaoServicoId") == execucao_id)
    assert str(peca_id) in falha["motivo"]

    saga.esperar_os(os_id, "FINALIZADA")

    assert inventory_db.contar_alocadas(execucao_id) == 0
    assert inventory_db.estoque_armazenado(peca_id) == 5
    assert inventory_db.estoque_disponivel(peca_id) == 5

    assert billing_db.orcamento(os_id)["status"] == "PENDENTE"
    pagamento = saga.esperar_pagamento(os_id)
    assert pagamento["status"] == "PENDENTE"


def test_aprovacao_mute_seguida_de_aprovacao_billing_cria_reserva_fantasma(
        saga, recorder, os_db, inventory_db, seeded_servico_ids):
    peca_id = saga.nova_peca("Peca fantasma e2e", valor=25.00, estoque=4)
    fixture = saga.nova_os_aguardando_aprovacao([{
        "servico_id": seeded_servico_ids[0],
        "valor_mao_de_obra": 45.00,
        "pecas": [PecaPedida(peca_id=peca_id, quantidade=1, estoque_inicial=4)],
    }])
    os_id, execucao_id = fixture.os_id, fixture.execucao_id
    meu = lambda p: p.get("osId") == os_id

    saga.aprovar_mute(fixture)
    saga.esperar_os(os_id, "APROVADA")
    saga.executar(fixture, execucao_id)
    saga.esperar_os(os_id, "FINALIZADA")
    assert inventory_db.contar_alocadas(execucao_id) == 0

    saga.billing_client.aprovar_orcamento(os_id)
    recorder.wait_for("OrcamentoAprovado", meu)

    fantasma = wait_until(
        lambda: inventory_db.peca_alocada(execucao_id, peca_id),
        lambda doc: doc is not None,
        desc="billing approval creates an allocation even after the work finished",
    )
    assert fantasma["QTD_INSTALADA"] == 0
    assert inventory_db.estoque_disponivel(peca_id) == 3
    assert os_db.status(os_id) == "FINALIZADA"


def test_quantidade_acima_da_reserva_nao_deduz_estoque(
        saga, recorder, os_db, inventory_db, seeded_servico_ids, rabbitmq_channel):
    from support.bus import publish_event

    peca_id = saga.nova_peca("Peca drift alto e2e", valor=25.00, estoque=5)
    fixture = saga.nova_os_aguardando_aprovacao([{
        "servico_id": seeded_servico_ids[0],
        "valor_mao_de_obra": 45.00,
        "pecas": [PecaPedida(peca_id=peca_id, quantidade=2, estoque_inicial=5)],
    }])
    os_id, execucao_id = fixture.os_id, fixture.execucao_id

    saga.aprovar_via_billing(fixture)
    saga.esperar_os(os_id, "EM_EXECUCAO")
    wait_until(
        lambda: inventory_db.status_alocada(execucao_id, peca_id),
        lambda status: status == "RESERVADA",
        desc="two units should be reserved",
    )

    publish_event(rabbitmq_channel, "ServiceEnded", {
        "osId": os_id,
        "execucaoServicoId": execucao_id,
        "itens": [{"pecaId": peca_id, "quantidade": 5}],
    })

    falha = recorder.wait_for("DeducaoEstoqueFalhou",
                              lambda p: p.get("execucaoServicoId") == execucao_id)
    assert "Quantidade reservada insuficiente" in falha["motivo"]

    settle(4)
    assert inventory_db.estoque_armazenado(peca_id) == 5
    alocada = inventory_db.peca_alocada(execucao_id, peca_id)
    assert alocada["QTD_INSTALADA"] == 0
    assert alocada["STATUS"] == "RESERVADA"


def test_reentrega_de_service_ended_parcial_nao_deduz_duas_vezes(
        saga, recorder, inventory_db, seeded_servico_ids, rabbitmq_channel):
    from support.bus import publish_event

    peca_id = saga.nova_peca("Peca drift baixo e2e", valor=25.00, estoque=5)
    fixture = saga.nova_os_aguardando_aprovacao([{
        "servico_id": seeded_servico_ids[0],
        "valor_mao_de_obra": 45.00,
        "pecas": [PecaPedida(peca_id=peca_id, quantidade=3, estoque_inicial=5)],
    }])
    os_id, execucao_id = fixture.os_id, fixture.execucao_id

    saga.aprovar_via_billing(fixture)
    saga.esperar_os(os_id, "EM_EXECUCAO")
    wait_until(
        lambda: inventory_db.status_alocada(execucao_id, peca_id),
        lambda status: status == "RESERVADA",
        desc="three units should be reserved",
    )

    parcial = {
        "osId": os_id,
        "execucaoServicoId": execucao_id,
        "itens": [{"pecaId": peca_id, "quantidade": 1}],
    }
    publish_event(rabbitmq_channel, "ServiceEnded", parcial)
    wait_until(
        lambda: inventory_db.peca_alocada(execucao_id, peca_id)["QTD_INSTALADA"],
        lambda instalada: instalada == 1,
        desc="the partial quantity should be installed once",
    )
    assert inventory_db.estoque_armazenado(peca_id) == 4

    publish_event(rabbitmq_channel, "ServiceEnded", parcial)
    publish_event(rabbitmq_channel, "ServiceEnded", parcial)
    recorder.wait_for_count("ServiceEnded", 3,
                            lambda p: p.get("execucaoServicoId") == execucao_id)
    settle(5)

    alocada = inventory_db.peca_alocada(execucao_id, peca_id)
    assert alocada["QTD_INSTALADA"] == 1
    assert inventory_db.estoque_armazenado(peca_id) == 4
    recorder.assert_not_seen("DeducaoEstoqueFalhou",
                             lambda p: p.get("execucaoServicoId") == execucao_id, settle=0)


def test_reserva_nao_e_liberada_quando_orcamento_e_reprovado_depois(
        saga, recorder, inventory_db, seeded_servico_ids, rabbitmq_channel):
    from support.bus import publish_event

    peca_id = saga.nova_peca("Peca sem liberacao e2e", valor=25.00, estoque=3)
    fixture = saga.nova_os_aguardando_aprovacao([{
        "servico_id": seeded_servico_ids[0],
        "valor_mao_de_obra": 45.00,
        "pecas": [PecaPedida(peca_id=peca_id, quantidade=2, estoque_inicial=3)],
    }])
    os_id, execucao_id = fixture.os_id, fixture.execucao_id

    saga.aprovar_via_billing(fixture)
    saga.esperar_os(os_id, "EM_EXECUCAO")
    wait_until(
        lambda: inventory_db.estoque_disponivel(peca_id),
        lambda disponivel: disponivel == 1,
        desc="two units should be reserved",
    )

    publish_event(rabbitmq_channel, "OrcamentoReprovado", {"osId": os_id})
    recorder.wait_for("OrcamentoReprovado", lambda p: p.get("osId") == os_id)

    recorder.assert_not_seen("PecaCompletamenteAlocada",
                             lambda p: p.get("execucaoServicoId") == execucao_id, settle=4)
    assert inventory_db.estoque_disponivel(peca_id) == 1
    assert inventory_db.status_alocada(execucao_id, peca_id) == "RESERVADA"
