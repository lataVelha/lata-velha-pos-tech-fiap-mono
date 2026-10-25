from rabbitmq_events import EventWatcher, publish_event
from waiting import wait_until


def _open_os_with_peca(
    os_client, os_client_mecanico, seeded_veiculo_id, seeded_proprietario_id,
    seeded_servico_id, peca_id, quantidade, reclamacao,
):
    os_id = os_client.criar_ordem_servico(seeded_veiculo_id, seeded_proprietario_id, reclamacao)["id"]
    os_client_mecanico.iniciar_diagnostico(os_id)
    os_client_mecanico.adicionar_servico(
        os_id,
        [
            {
                "servicoId": seeded_servico_id,
                "pecas": [{"pecaId": peca_id, "quantidade": quantidade}],
                "valorMaoDeObra": 110.00,
            }
        ],
    )
    os_client_mecanico.finalizar_diagnostico(os_id)
    execucao_id = os_client.get_ordem_servico(os_id)["servicos"][0]["id"]
    os_client.aprovar(os_id, [{"execucaoServicoId": execucao_id, "status": "APROVADO"}])
    return os_id, execucao_id


def test_service_ended_without_any_reservation_refuses_to_deduct(
    os_client,
    os_client_mecanico,
    inventory_client,
    rabbitmq_connection_params,
    seeded_proprietario_id,
    seeded_veiculo_id,
    seeded_servico_id,
):
    quantidade = 2
    estoque_inicial = 5

    peca_id = inventory_client.criar_peca("Palheta", "E2E sem reserva", 35.00)["id"]
    inventory_client.entrada_estoque(peca_id, estoque_inicial)

    os_id, execucao_id = _open_os_with_peca(
        os_client, os_client_mecanico, seeded_veiculo_id, seeded_proprietario_id,
        seeded_servico_id, peca_id, quantidade, "E2E: servico concluido sem reserva",
    )

    with EventWatcher(rabbitmq_connection_params) as watcher:
        os_client_mecanico.iniciar_servico(os_id, execucao_id)
        os_client_mecanico.finalizar_servico(os_id, execucao_id)

        payload = watcher.wait_for(
            "DeducaoEstoqueFalhou",
            predicate=lambda p: p.get("osId") == os_id,
            timeout=20,
        )
        assert payload["execucaoServicoId"] == execucao_id
        assert str(peca_id) in payload["motivo"]

    estoque_final = inventory_client.get_peca_estoque(peca_id)
    assert estoque_final["quantidadeArmazenada"] == estoque_inicial
    assert estoque_final["quantidadeDisponivel"] == estoque_inicial


def test_quantity_drift_between_billing_and_os_is_compensated(
    os_client,
    os_client_mecanico,
    inventory_client,
    rabbitmq_channel,
    rabbitmq_connection_params,
    seeded_proprietario_id,
    seeded_veiculo_id,
    seeded_servico_id,
):
    quantidade_os = 2
    quantidade_billing = 1
    estoque_inicial = 8

    peca_id = inventory_client.criar_peca("Parafuso roda", "E2E divergencia quantidade", 12.00)["id"]
    inventory_client.entrada_estoque(peca_id, estoque_inicial)

    os_id, execucao_id = _open_os_with_peca(
        os_client, os_client_mecanico, seeded_veiculo_id, seeded_proprietario_id,
        seeded_servico_id, peca_id, quantidade_os, "E2E: divergencia de quantidade",
    )

    publish_event(
        rabbitmq_channel,
        "OrcamentoAprovado",
        {
            "osId": os_id,
            "itens": [
                {
                    "execucaoServicoId": execucao_id,
                    "pecaId": peca_id,
                    "quantidade": quantidade_billing,
                }
            ],
        },
    )
    wait_until(
        lambda: os_client.get_ordem_servico(os_id),
        lambda o: o["status"] == "EM_EXECUCAO",
        timeout=30,
        desc="OS should reach EM_EXECUCAO after the under-quantity reservation",
    )

    with EventWatcher(rabbitmq_connection_params) as watcher:
        os_client_mecanico.iniciar_servico(os_id, execucao_id)
        os_client_mecanico.finalizar_servico(os_id, execucao_id)

        payload = watcher.wait_for(
            "DeducaoEstoqueFalhou",
            predicate=lambda p: p.get("osId") == os_id,
            timeout=20,
        )
        assert payload["execucaoServicoId"] == execucao_id
        assert payload["motivo"]

    ordem = wait_until(
        lambda: os_client.get_ordem_servico(os_id),
        lambda o: o["status"] == "FINALIZADA",
        timeout=20,
        desc="OS still finalizes; the deduction failure is compensated, not blocking",
    )
    assert ordem["status"] == "FINALIZADA"
