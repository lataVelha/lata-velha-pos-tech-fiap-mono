from drivers.saga import PecaPedida
from support.asserts import assert_conservacao_estoque
from support.waiting import wait_until


def test_estoque_zerado_vira_encomenda_e_desbloqueia_com_entrada(
        saga, recorder, os_db, inventory_db, seeded_servico_ids):
    peca_id = saga.nova_peca("Peca encomenda e2e", valor=40.00, estoque=0)
    fixture = saga.nova_os_aguardando_aprovacao([{
        "servico_id": seeded_servico_ids[0],
        "valor_mao_de_obra": 70.00,
        "pecas": [PecaPedida(peca_id=peca_id, quantidade=2, estoque_inicial=0)],
    }])
    os_id, execucao_id = fixture.os_id, fixture.execucao_id
    meu = lambda p: p.get("osId") == os_id

    saga.aprovar_via_billing(fixture)
    checked = recorder.wait_for("InventoryChecked", meu)
    assert checked["itens"] == [{"execucaoServicoId": execucao_id, "pecasDisponiveis": False}]

    saga.esperar_execucao(execucao_id, "AGUARDANDO_PECA")
    assert os_db.status(os_id) == "EM_EXECUCAO"

    alocada = wait_until(
        lambda: inventory_db.peca_alocada(execucao_id, peca_id),
        lambda doc: doc is not None,
        desc="inventory should record the backordered allocation",
    )
    assert alocada["STATUS"] == "ENCOMENDA"
    assert alocada["QTD_RESERVADA"] == 0
    assert alocada["QTD_ENCOMENDADA"] == 2

    bloqueada = saga.os_client_mecanico.iniciar_servico(os_id, execucao_id, raise_on_error=False)
    assert bloqueada.status_code >= 400

    saga.inventory_client.entrada_estoque(peca_id, 2)
    recorder.wait_for("PecaCompletamenteAlocada",
                      lambda p: p.get("execucaoServicoId") == execucao_id)
    saga.esperar_execucao(execucao_id, "APROVADO")

    desbloqueada = wait_until(
        lambda: inventory_db.peca_alocada(execucao_id, peca_id),
        lambda doc: doc["STATUS"] == "RESERVADA",
        desc="allocation should become RESERVADA once stock arrives",
    )
    assert desbloqueada["QTD_RESERVADA"] == 2
    assert desbloqueada["QTD_ENCOMENDADA"] == 0
    assert inventory_db.estoque_disponivel(peca_id) == 0

    saga.executar(fixture, execucao_id)
    saga.esperar_os(os_id, "FINALIZADA")
    wait_until(
        lambda: inventory_db.status_alocada(execucao_id, peca_id),
        lambda status: status == "INSTALADA",
        desc="allocation should become INSTALADA after the service ended",
    )
    assert inventory_db.estoque_armazenado(peca_id) == 0

    saga.pagar(fixture)
    saga.esperar_os(os_id, "PAGA")
    saga.os_client.retirar_veiculo(os_id)
    saga.esperar_os(os_id, "ENTREGUE")

    assert_conservacao_estoque(inventory_db, peca_id, 2, [execucao_id])


def test_estoque_parcial_bloqueia_ate_chegar_o_restante(
        saga, recorder, os_db, inventory_db, seeded_servico_ids):
    peca_id = saga.nova_peca("Peca parcial e2e", valor=40.00, estoque=1)
    fixture = saga.nova_os_aguardando_aprovacao([{
        "servico_id": seeded_servico_ids[0],
        "valor_mao_de_obra": 70.00,
        "pecas": [PecaPedida(peca_id=peca_id, quantidade=3, estoque_inicial=1)],
    }])
    os_id, execucao_id = fixture.os_id, fixture.execucao_id
    meu = lambda p: p.get("osId") == os_id

    saga.aprovar_via_billing(fixture)
    checked = recorder.wait_for("InventoryChecked", meu)
    assert checked["itens"][0]["pecasDisponiveis"] is False
    saga.esperar_execucao(execucao_id, "AGUARDANDO_PECA")

    parcial = wait_until(
        lambda: inventory_db.peca_alocada(execucao_id, peca_id),
        lambda doc: doc is not None and doc["STATUS"] == "PARCIAL",
        desc="allocation should be PARCIAL with partial stock",
    )
    assert parcial["QTD_RESERVADA"] == 1
    assert parcial["QTD_ENCOMENDADA"] == 2
    assert inventory_db.estoque_disponivel(peca_id) == 0
    assert inventory_db.estoque_armazenado(peca_id) == 1

    saga.inventory_client.entrada_estoque(peca_id, 2)
    recorder.wait_for("PecaCompletamenteAlocada",
                      lambda p: p.get("execucaoServicoId") == execucao_id)
    saga.esperar_execucao(execucao_id, "APROVADO")

    completa = inventory_db.peca_alocada(execucao_id, peca_id)
    assert completa["STATUS"] == "RESERVADA"
    assert completa["QTD_RESERVADA"] == 3
    assert completa["QTD_ENCOMENDADA"] == 0
    assert inventory_db.estoque_disponivel(peca_id) == 0

    saga.executar(fixture, execucao_id)
    saga.esperar_os(os_id, "FINALIZADA")
    wait_until(
        lambda: inventory_db.estoque_armazenado(peca_id),
        lambda armazenada: armazenada == 0,
        desc="all three reserved units should be deducted",
    )
    assert_conservacao_estoque(inventory_db, peca_id, 3, [execucao_id])


def test_disponibilidade_parcial_entre_dois_servicos(
        saga, recorder, os_db, inventory_db, seeded_servico_ids):
    peca_ok = saga.nova_peca("Peca multi disponivel", valor=20.00, estoque=5)
    peca_faltante = saga.nova_peca("Peca multi faltante", valor=20.00, estoque=0)
    fixture = saga.nova_os_aguardando_aprovacao([
        {
            "servico_id": seeded_servico_ids[0],
            "valor_mao_de_obra": 50.00,
            "pecas": [PecaPedida(peca_id=peca_ok, quantidade=1, estoque_inicial=5)],
        },
        {
            "servico_id": seeded_servico_ids[1],
            "valor_mao_de_obra": 50.00,
            "pecas": [PecaPedida(peca_id=peca_faltante, quantidade=1, estoque_inicial=0)],
        },
    ])
    os_id = fixture.os_id
    exec_ok, exec_bloqueado = fixture.execucoes
    meu = lambda p: p.get("osId") == os_id

    saga.aprovar_via_billing(fixture)
    checked = recorder.wait_for("InventoryChecked", meu, timeout=40)
    disponibilidade = {item["execucaoServicoId"]: item["pecasDisponiveis"]
                       for item in checked["itens"]}
    assert disponibilidade == {exec_ok: True, exec_bloqueado: False}

    saga.esperar_execucao(exec_bloqueado, "AGUARDANDO_PECA")
    assert os_db.execucao_status(exec_ok) == "APROVADO"

    recusada = saga.os_client_mecanico.iniciar_servico(os_id, exec_bloqueado, raise_on_error=False)
    assert recusada.status_code >= 400

    saga.executar(fixture, exec_ok)
    recorder.wait_for("ServiceEnded", lambda p: p.get("execucaoServicoId") == exec_ok)
    saga.esperar_execucao(exec_ok, "FINALIZADO")

    assert os_db.status(os_id) == "EM_EXECUCAO"
    recorder.assert_not_seen("OSEnded", meu, settle=3)

    saga.inventory_client.entrada_estoque(peca_faltante, 1)
    recorder.wait_for("PecaCompletamenteAlocada",
                      lambda p: p.get("execucaoServicoId") == exec_bloqueado)
    saga.esperar_execucao(exec_bloqueado, "APROVADO")

    saga.executar(fixture, exec_bloqueado)
    recorder.wait_for("OSEnded", meu)
    saga.esperar_os(os_id, "FINALIZADA")

    assert_conservacao_estoque(inventory_db, peca_ok, 5, [exec_ok])
    assert_conservacao_estoque(inventory_db, peca_faltante, 1, [exec_bloqueado])
