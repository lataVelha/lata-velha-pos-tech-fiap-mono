import time

from rabbitmq_events import publish_event
from waiting import wait_until

SETTLE_SECONDS = 6


def _drive_os_to_approved(
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
                "valorMaoDeObra": 100.00,
            }
        ],
    )
    os_client_mecanico.finalizar_diagnostico(os_id)
    execucao_id = os_client.get_ordem_servico(os_id)["servicos"][0]["id"]
    os_client.aprovar(os_id, [{"execucaoServicoId": execucao_id, "status": "APROVADO"}])
    return os_id, execucao_id


def test_duplicate_orcamento_aprovado_reserves_stock_only_once(
    os_client,
    os_client_mecanico,
    inventory_client,
    rabbitmq_channel,
    seeded_proprietario_id,
    seeded_veiculo_id,
    seeded_servico_id,
):
    quantidade = 2
    estoque_inicial = 5

    peca_id = inventory_client.criar_peca("Filtro de ar", "E2E idempotencia reserva", 60.00)["id"]
    inventory_client.entrada_estoque(peca_id, estoque_inicial)

    os_id, execucao_id = _drive_os_to_approved(
        os_client, os_client_mecanico, seeded_veiculo_id, seeded_proprietario_id,
        seeded_servico_id, peca_id, quantidade, "E2E: idempotencia reserva",
    )

    payload = {
        "osId": os_id,
        "itens": [{"execucaoServicoId": execucao_id, "pecaId": peca_id, "quantidade": quantidade}],
    }
    publish_event(rabbitmq_channel, "OrcamentoAprovado", payload)

    wait_until(
        lambda: os_client.get_ordem_servico(os_id),
        lambda o: o["status"] == "EM_EXECUCAO",
        timeout=30,
        desc="OS should reach EM_EXECUCAO after the first OrcamentoAprovado",
    )
    estoque_apos_primeira = inventory_client.get_peca_estoque(peca_id)
    assert estoque_apos_primeira["quantidadeDisponivel"] == estoque_inicial - quantidade

    publish_event(rabbitmq_channel, "OrcamentoAprovado", payload)
    time.sleep(SETTLE_SECONDS)

    estoque_final = inventory_client.get_peca_estoque(peca_id)
    assert estoque_final["quantidadeDisponivel"] == estoque_inicial - quantidade
    assert estoque_final["quantidadeArmazenada"] == estoque_inicial


def test_duplicate_service_ended_deducts_stock_only_once(
    os_client,
    os_client_mecanico,
    inventory_client,
    rabbitmq_channel,
    seeded_proprietario_id,
    seeded_veiculo_id,
    seeded_servico_id,
):
    quantidade = 2
    estoque_inicial = 6

    peca_id = inventory_client.criar_peca("Bateria", "E2E idempotencia deducao", 400.00)["id"]
    inventory_client.entrada_estoque(peca_id, estoque_inicial)

    os_id, execucao_id = _drive_os_to_approved(
        os_client, os_client_mecanico, seeded_veiculo_id, seeded_proprietario_id,
        seeded_servico_id, peca_id, quantidade, "E2E: idempotencia deducao",
    )

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
        desc="OS should reach EM_EXECUCAO before finishing the service",
    )

    os_client_mecanico.iniciar_servico(os_id, execucao_id)
    os_client_mecanico.finalizar_servico(os_id, execucao_id)

    wait_until(
        lambda: inventory_client.get_peca_estoque(peca_id),
        lambda e: e["quantidadeArmazenada"] == estoque_inicial - quantidade,
        timeout=20,
        desc="stock should be deducted once by the real ServiceEnded",
    )

    publish_event(
        rabbitmq_channel,
        "ServiceEnded",
        {
            "osId": os_id,
            "execucaoServicoId": execucao_id,
            "itens": [{"pecaId": peca_id, "quantidade": quantidade}],
        },
    )

    time.sleep(SETTLE_SECONDS)

    estoque_final = inventory_client.get_peca_estoque(peca_id)
    assert estoque_final["quantidadeArmazenada"] == estoque_inicial - quantidade
