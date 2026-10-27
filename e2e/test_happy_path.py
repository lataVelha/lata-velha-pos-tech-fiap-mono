from rabbitmq_events import EventWatcher, publish_event
from waiting import wait_until


def test_happy_path_full_os_lifecycle(
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
    estoque_inicial = 5

    peca = inventory_client.criar_peca("Pastilha de freio", "E2E happy path", 80.00)
    peca_id = peca["id"]
    inventory_client.entrada_estoque(peca_id, estoque_inicial)

    os_resp = os_client.criar_ordem_servico(
        seeded_veiculo_id, seeded_proprietario_id, "E2E: barulho ao frear"
    )
    os_id = os_resp["id"]
    assert os_resp["status"] == "RECEBIDA"

    os_client_mecanico.iniciar_diagnostico(os_id)
    os_client_mecanico.adicionar_servico(
        os_id,
        [
            {
                "servicoId": seeded_servico_id,
                "pecas": [{"pecaId": peca_id, "quantidade": quantidade_necessaria}],
                "valorMaoDeObra": 150.00,
            }
        ],
    )
    os_client_mecanico.finalizar_diagnostico(os_id)

    ordem = os_client.get_ordem_servico(os_id)
    assert ordem["status"] == "AGUARDANDO_APROVACAO"
    execucao_id = ordem["servicos"][0]["id"]

    os_client.aprovar(os_id, [{"execucaoServicoId": execucao_id, "status": "APROVADO"}])

    with EventWatcher(rabbitmq_connection_params) as watcher:
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
            desc="OS should reach EM_EXECUCAO after inventory confirms availability",
        )

        ordem = os_client.get_ordem_servico(os_id)
        execucao = next(s for s in ordem["servicos"] if s["id"] == execucao_id)
        assert execucao["status"] == "APROVADO"

        os_client_mecanico.iniciar_servico(os_id, execucao_id)
        os_client_mecanico.finalizar_servico(os_id, execucao_id)

        wait_until(
            lambda: os_client.get_ordem_servico(os_id),
            lambda o: o["status"] == "FINALIZADA",
            timeout=20,
            desc="OS should reach FINALIZADA once the last service is finished",
        )

        watcher.assert_not_seen(
            "DeducaoEstoqueFalhou",
            predicate=lambda p: p.get("osId") == os_id,
            timeout=5,
        )

    estoque_final = wait_until(
        lambda: inventory_client.get_peca_estoque(peca_id),
        lambda e: e["quantidadeArmazenada"] == estoque_inicial - quantidade_necessaria,
        timeout=20,
        desc="stock should reflect the real deduction triggered by ServiceEnded",
    )
    assert estoque_final["quantidadeDisponivel"] == estoque_inicial - quantidade_necessaria

    os_client.retirar_veiculo(os_id)
    ordem = os_client.get_ordem_servico(os_id)
    assert ordem["status"] == "ENTREGUE"
