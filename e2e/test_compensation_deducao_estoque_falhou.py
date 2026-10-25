from rabbitmq_events import EventWatcher, publish_event
from waiting import wait_until


def test_deduction_failure_emits_compensating_event_without_changing_os_state(
    os_client,
    os_client_mecanico,
    inventory_client,
    rabbitmq_channel,
    rabbitmq_connection_params,
    seeded_proprietario_id,
    seeded_veiculo_id,
    seeded_servico_id,
):
    quantidade_necessaria = 2

    peca = inventory_client.criar_peca("Vela de ignicao", "E2E deducao falhou", 40.00)
    peca_id = peca["id"]
    inventory_client.entrada_estoque(peca_id, 10)

    os_resp = os_client.criar_ordem_servico(
        seeded_veiculo_id, seeded_proprietario_id, "E2E: motor falhando"
    )
    os_id = os_resp["id"]

    os_client_mecanico.iniciar_diagnostico(os_id)
    os_client_mecanico.adicionar_servico(
        os_id,
        [
            {
                "servicoId": seeded_servico_id,
                "pecas": [{"pecaId": peca_id, "quantidade": quantidade_necessaria}],
                "valorMaoDeObra": 90.00,
            }
        ],
    )
    os_client_mecanico.finalizar_diagnostico(os_id)

    ordem = os_client.get_ordem_servico(os_id)
    execucao_id = ordem["servicos"][0]["id"]
    os_client.aprovar(os_id, [{"execucaoServicoId": execucao_id, "status": "APROVADO"}])

    publish_event(
        rabbitmq_channel,
        "OrcamentoAprovado",
        {
            "osId": os_id,
            "itens": [
                {
                    "execucaoServicoId": execucao_id,
                    "pecaId": peca_id,
                    "quantidade": quantidade_necessaria,
                }
            ],
        },
    )

    wait_until(
        lambda: os_client.get_ordem_servico(os_id),
        lambda o: o["status"] == "EM_EXECUCAO",
        timeout=30,
        desc="OS should reach EM_EXECUCAO once inventory reserves the part",
    )

    inventory_client.ajustar_estoque(peca_id, quantidade_armazenada=0, quantidade_disponivel=0)

    os_client_mecanico.iniciar_servico(os_id, execucao_id)

    with EventWatcher(rabbitmq_connection_params) as watcher:
        os_client_mecanico.finalizar_servico(os_id, execucao_id)

        payload = watcher.wait_for(
            "DeducaoEstoqueFalhou",
            predicate=lambda p: p.get("osId") == os_id
            and p.get("execucaoServicoId") == execucao_id,
            timeout=20,
        )
        assert payload.get("motivo")

    ordem = wait_until(
        lambda: os_client.get_ordem_servico(os_id),
        lambda o: o["status"] == "FINALIZADA",
        timeout=20,
        desc="OS should still reach FINALIZADA even though stock deduction failed",
    )
    assert ordem["status"] == "FINALIZADA"
