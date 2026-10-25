from rabbitmq_events import publish_event
from waiting import wait_until


def test_backorder_then_peca_completamente_alocada_unblocks_execucao(
    os_client,
    os_client_mecanico,
    inventory_client,
    rabbitmq_channel,
    seeded_proprietario_id,
    seeded_veiculo_id,
    seeded_servico_id,
):
    quantidade_necessaria = 2

    peca = inventory_client.criar_peca("Correia dentada", "E2E backorder", 120.00)
    peca_id = peca["id"]

    os_resp = os_client.criar_ordem_servico(
        seeded_veiculo_id, seeded_proprietario_id, "E2E: correia rangendo"
    )
    os_id = os_resp["id"]

    os_client_mecanico.iniciar_diagnostico(os_id)
    os_client_mecanico.adicionar_servico(
        os_id,
        [
            {
                "servicoId": seeded_servico_id,
                "pecas": [{"pecaId": peca_id, "quantidade": quantidade_necessaria}],
                "valorMaoDeObra": 180.00,
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

    ordem = wait_until(
        lambda: os_client.get_ordem_servico(os_id),
        lambda o: next(s for s in o["servicos"] if s["id"] == execucao_id)["status"]
        == "AGUARDANDO_PECA",
        timeout=30,
        desc="execucao should be blocked waiting for the backordered part",
    )

    blocked_attempt = os_client_mecanico.iniciar_servico(os_id, execucao_id, raise_on_error=False)
    assert blocked_attempt.status_code >= 400

    inventory_client.entrada_estoque(peca_id, quantidade_necessaria)

    wait_until(
        lambda: os_client.get_ordem_servico(os_id),
        lambda o: next(s for s in o["servicos"] if s["id"] == execucao_id)["status"]
        == "APROVADO",
        timeout=30,
        desc="execucao should unblock back to APROVADO once the part is fully allocated",
    )

    os_client_mecanico.iniciar_servico(os_id, execucao_id)
    os_client_mecanico.finalizar_servico(os_id, execucao_id)

    wait_until(
        lambda: os_client.get_ordem_servico(os_id),
        lambda o: o["status"] == "FINALIZADA",
        timeout=20,
        desc="OS should reach FINALIZADA after the previously-blocked service finishes",
    )

    estoque_final = inventory_client.get_peca_estoque(peca_id)
    assert estoque_final["quantidadeArmazenada"] == 0
    assert estoque_final["quantidadeDisponivel"] == 0

    os_client.retirar_veiculo(os_id)
    ordem = os_client.get_ordem_servico(os_id)
    assert ordem["status"] == "ENTREGUE"


def test_partial_reservation_blocks_until_remaining_stock_arrives(
    os_client,
    os_client_mecanico,
    inventory_client,
    rabbitmq_channel,
    seeded_proprietario_id,
    seeded_veiculo_id,
    seeded_servico_id,
):
    quantidade_necessaria = 2
    estoque_parcial = 1

    peca = inventory_client.criar_peca("Rolamento", "E2E reserva parcial", 95.00)
    peca_id = peca["id"]
    inventory_client.entrada_estoque(peca_id, estoque_parcial)

    os_resp = os_client.criar_ordem_servico(
        seeded_veiculo_id, seeded_proprietario_id, "E2E: reserva parcial"
    )
    os_id = os_resp["id"]

    os_client_mecanico.iniciar_diagnostico(os_id)
    os_client_mecanico.adicionar_servico(
        os_id,
        [
            {
                "servicoId": seeded_servico_id,
                "pecas": [{"pecaId": peca_id, "quantidade": quantidade_necessaria}],
                "valorMaoDeObra": 140.00,
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
        lambda o: next(s for s in o["servicos"] if s["id"] == execucao_id)["status"]
        == "AGUARDANDO_PECA",
        timeout=30,
        desc="partially reserved execucao should be blocked",
    )

    estoque_parcialmente_reservado = inventory_client.get_peca_estoque(peca_id)
    assert estoque_parcialmente_reservado["quantidadeDisponivel"] == 0
    assert estoque_parcialmente_reservado["quantidadeArmazenada"] == estoque_parcial

    inventory_client.entrada_estoque(peca_id, quantidade_necessaria - estoque_parcial)

    wait_until(
        lambda: os_client.get_ordem_servico(os_id),
        lambda o: next(s for s in o["servicos"] if s["id"] == execucao_id)["status"] == "APROVADO",
        timeout=30,
        desc="execucao should unblock once the remaining unit arrives",
    )

    os_client_mecanico.iniciar_servico(os_id, execucao_id)
    os_client_mecanico.finalizar_servico(os_id, execucao_id)

    wait_until(
        lambda: inventory_client.get_peca_estoque(peca_id),
        lambda e: e["quantidadeArmazenada"] == 0,
        timeout=20,
        desc="all reserved units should be deducted on ServiceEnded",
    )
