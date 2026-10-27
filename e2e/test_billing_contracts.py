from rabbitmq_events import EventWatcher, publish_event
from waiting import wait_until


def test_diagnostico_finalizado_and_os_ended_payload_shapes(
    os_client,
    os_client_mecanico,
    inventory_client,
    rabbitmq_channel,
    rabbitmq_connection_params,
    seeded_proprietario_id,
    seeded_veiculo_id,
    seeded_servico_id,
):
    quantidade = 1
    valor_mao_de_obra = 175.50

    peca_id = inventory_client.criar_peca("Fluido de freio", "E2E contratos billing", 45.00)["id"]
    inventory_client.entrada_estoque(peca_id, 4)

    with EventWatcher(rabbitmq_connection_params) as watcher:
        os_id = os_client.criar_ordem_servico(
            seeded_veiculo_id, seeded_proprietario_id, "E2E: contratos do billing"
        )["id"]
        os_client_mecanico.iniciar_diagnostico(os_id)
        os_client_mecanico.adicionar_servico(
            os_id,
            [
                {
                    "servicoId": seeded_servico_id,
                    "pecas": [{"pecaId": peca_id, "quantidade": quantidade}],
                    "valorMaoDeObra": valor_mao_de_obra,
                }
            ],
        )
        os_client_mecanico.finalizar_diagnostico(os_id)

        diagnostico = watcher.wait_for(
            "DiagnosticoFinalizado",
            predicate=lambda p: p.get("osId") == os_id,
            timeout=20,
        )

        execucao_id = os_client.get_ordem_servico(os_id)["servicos"][0]["id"]

        assert diagnostico["osId"] == os_id
        assert len(diagnostico["itens"]) == 1
        item = diagnostico["itens"][0]
        assert item["execucaoServicoId"] == execucao_id
        assert item["servicoId"] == seeded_servico_id
        assert float(item["valorMaoDeObra"]) == valor_mao_de_obra
        assert item["pecas"] == [{"pecaId": peca_id, "quantidade": quantidade}]

        os_client.aprovar(os_id, [{"execucaoServicoId": execucao_id, "status": "APROVADO"}])
        publish_event(
            rabbitmq_channel,
            "OrcamentoAprovado",
            {
                "osId": os_id,
                "itens": [{"execucaoServicoId": execucao_id, "pecaId": peca_id, "quantidade": quantidade}],
            },
        )
        wait_until(
            lambda: os_client.get_ordem_servico(os_id),
            lambda o: o["status"] == "EM_EXECUCAO",
            timeout=30,
            desc="OS should reach EM_EXECUCAO before executing",
        )

        os_client_mecanico.iniciar_servico(os_id, execucao_id)
        os_client_mecanico.finalizar_servico(os_id, execucao_id)

        service_ended = watcher.wait_for(
            "ServiceEnded",
            predicate=lambda p: p.get("osId") == os_id,
            timeout=20,
        )
        assert service_ended["execucaoServicoId"] == execucao_id
        assert service_ended["itens"] == [{"pecaId": peca_id, "quantidade": quantidade}]

        os_ended = watcher.wait_for(
            "OSEnded",
            predicate=lambda p: p.get("osId") == os_id,
            timeout=20,
        )
        assert os_ended == {"osId": os_id}
