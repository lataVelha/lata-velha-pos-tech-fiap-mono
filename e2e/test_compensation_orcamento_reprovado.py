from rabbitmq_events import publish_event
from waiting import wait_until


def test_orcamento_reprovado_finalizes_os_as_reprovada(
    os_client,
    os_client_mecanico,
    inventory_client,
    rabbitmq_channel,
    seeded_proprietario_id,
    seeded_veiculo_id,
    seeded_servico_id,
):
    peca = inventory_client.criar_peca("Amortecedor", "E2E orcamento reprovado", 300.00)
    peca_id = peca["id"]
    inventory_client.entrada_estoque(peca_id, 5)

    os_resp = os_client.criar_ordem_servico(
        seeded_veiculo_id, seeded_proprietario_id, "E2E: suspensao batendo"
    )
    os_id = os_resp["id"]

    os_client_mecanico.iniciar_diagnostico(os_id)
    os_client_mecanico.adicionar_servico(
        os_id,
        [
            {
                "servicoId": seeded_servico_id,
                "pecas": [{"pecaId": peca_id, "quantidade": 1}],
                "valorMaoDeObra": 200.00,
            }
        ],
    )
    os_client_mecanico.finalizar_diagnostico(os_id)

    ordem = os_client.get_ordem_servico(os_id)
    assert ordem["status"] == "AGUARDANDO_APROVACAO"

    publish_event(rabbitmq_channel, "OrcamentoReprovado", {"osId": os_id})

    ordem = wait_until(
        lambda: os_client.get_ordem_servico(os_id),
        lambda o: o["status"] == "REPROVADA",
        timeout=20,
        desc="OS should become REPROVADA after consuming OrcamentoReprovado",
    )
    assert ordem["status"] == "REPROVADA"

    execucao_id = ordem["servicos"][0]["id"]
    bad = os_client.aprovar_raw(os_id, [{"execucaoServicoId": execucao_id, "status": "APROVADO"}])
    assert bad.status_code >= 400
