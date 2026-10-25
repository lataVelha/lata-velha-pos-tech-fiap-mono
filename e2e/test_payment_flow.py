from rabbitmq_events import publish_event
from waiting import wait_until


def test_payment_done_marks_os_as_paga_and_allows_delivery(
    os_client,
    os_client_mecanico,
    inventory_client,
    rabbitmq_channel,
    seeded_proprietario_id,
    seeded_veiculo_id,
    seeded_servico_id,
):
    quantidade = 1

    peca_id = inventory_client.criar_peca("Lampada farol", "E2E fluxo pagamento", 25.00)["id"]
    inventory_client.entrada_estoque(peca_id, 3)

    os_id = os_client.criar_ordem_servico(
        seeded_veiculo_id, seeded_proprietario_id, "E2E: fluxo de pagamento"
    )["id"]
    os_client_mecanico.iniciar_diagnostico(os_id)
    os_client_mecanico.adicionar_servico(
        os_id,
        [
            {
                "servicoId": seeded_servico_id,
                "pecas": [{"pecaId": peca_id, "quantidade": quantidade}],
                "valorMaoDeObra": 120.00,
            }
        ],
    )
    os_client_mecanico.finalizar_diagnostico(os_id)

    execucao_id = os_client.get_ordem_servico(os_id)["servicos"][0]["id"]
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
        desc="OS should reach EM_EXECUCAO",
    )

    os_client_mecanico.iniciar_servico(os_id, execucao_id)
    os_client_mecanico.finalizar_servico(os_id, execucao_id)
    wait_until(
        lambda: os_client.get_ordem_servico(os_id),
        lambda o: o["status"] == "FINALIZADA",
        timeout=20,
        desc="OS should reach FINALIZADA",
    )

    publish_event(rabbitmq_channel, "CobrancaEmitida", {"osId": os_id})
    publish_event(rabbitmq_channel, "PaymentDone", {"osId": os_id})

    wait_until(
        lambda: os_client.get_ordem_servico(os_id),
        lambda o: o["status"] == "PAGA",
        timeout=30,
        desc="OS should become PAGA after PaymentDone",
    )

    os_client.retirar_veiculo(os_id)
    assert os_client.get_ordem_servico(os_id)["status"] == "ENTREGUE"
