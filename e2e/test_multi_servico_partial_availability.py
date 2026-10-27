from rabbitmq_events import publish_event
from waiting import wait_until


def _execucao(ordem, execucao_id):
    return next(s for s in ordem["servicos"] if s["id"] == execucao_id)


def test_one_servico_blocked_other_proceeds(
    os_client,
    os_client_mecanico,
    inventory_client,
    rabbitmq_channel,
    seeded_proprietario_id,
    seeded_veiculo_id,
):
    quantidade = 2

    servicos_catalogo = os_client.list_servicos_page(size=2)
    assert len(servicos_catalogo) >= 2, "catálogo precisa de ao menos 2 serviços"
    servico_com_estoque = servicos_catalogo[0]["id"]
    servico_sem_estoque = servicos_catalogo[1]["id"]

    peca_disponivel = inventory_client.criar_peca("Pastilha dianteira", "E2E multi com estoque", 90.00)["id"]
    inventory_client.entrada_estoque(peca_disponivel, 10)
    peca_faltante = inventory_client.criar_peca("Disco de freio", "E2E multi sem estoque", 250.00)["id"]

    os_id = os_client.criar_ordem_servico(
        seeded_veiculo_id, seeded_proprietario_id, "E2E: dois servicos, um sem peca"
    )["id"]
    os_client_mecanico.iniciar_diagnostico(os_id)
    os_client_mecanico.adicionar_servico(
        os_id,
        [
            {
                "servicoId": servico_com_estoque,
                "pecas": [{"pecaId": peca_disponivel, "quantidade": quantidade}],
                "valorMaoDeObra": 150.00,
            },
            {
                "servicoId": servico_sem_estoque,
                "pecas": [{"pecaId": peca_faltante, "quantidade": quantidade}],
                "valorMaoDeObra": 300.00,
            },
        ],
    )
    os_client_mecanico.finalizar_diagnostico(os_id)

    ordem = os_client.get_ordem_servico(os_id)
    assert len(ordem["servicos"]) == 2
    exec_ok = next(s for s in ordem["servicos"] if s["servicoId"] == servico_com_estoque)["id"]
    exec_bloqueado = next(s for s in ordem["servicos"] if s["servicoId"] == servico_sem_estoque)["id"]

    os_client.aprovar(
        os_id,
        [
            {"execucaoServicoId": exec_ok, "status": "APROVADO"},
            {"execucaoServicoId": exec_bloqueado, "status": "APROVADO"},
        ],
    )

    publish_event(
        rabbitmq_channel,
        "OrcamentoAprovado",
        {
            "osId": os_id,
            "itens": [
                {"execucaoServicoId": exec_ok, "pecaId": peca_disponivel, "quantidade": quantidade},
                {"execucaoServicoId": exec_bloqueado, "pecaId": peca_faltante, "quantidade": quantidade},
            ],
        },
    )

    ordem = wait_until(
        lambda: os_client.get_ordem_servico(os_id),
        lambda o: _execucao(o, exec_bloqueado)["status"] == "AGUARDANDO_PECA",
        timeout=30,
        desc="only the servico without stock should be blocked",
    )
    assert _execucao(ordem, exec_ok)["status"] == "APROVADO"

    blocked = os_client_mecanico.iniciar_servico(os_id, exec_bloqueado, raise_on_error=False)
    assert blocked.status_code >= 400

    os_client_mecanico.iniciar_servico(os_id, exec_ok)
    os_client_mecanico.finalizar_servico(os_id, exec_ok)

    ordem = os_client.get_ordem_servico(os_id)
    assert ordem["status"] == "EM_EXECUCAO", "OS must stay open while one servico waits for parts"
    assert _execucao(ordem, exec_ok)["status"] == "FINALIZADO"

    inventory_client.entrada_estoque(peca_faltante, quantidade)

    wait_until(
        lambda: os_client.get_ordem_servico(os_id),
        lambda o: _execucao(o, exec_bloqueado)["status"] == "APROVADO",
        timeout=30,
        desc="blocked servico should unblock once its part arrives",
    )

    os_client_mecanico.iniciar_servico(os_id, exec_bloqueado)
    os_client_mecanico.finalizar_servico(os_id, exec_bloqueado)

    wait_until(
        lambda: os_client.get_ordem_servico(os_id),
        lambda o: o["status"] == "FINALIZADA",
        timeout=20,
        desc="OS should finalize once both servicos are done",
    )

    assert inventory_client.get_peca_estoque(peca_disponivel)["quantidadeArmazenada"] == 10 - quantidade
    assert inventory_client.get_peca_estoque(peca_faltante)["quantidadeArmazenada"] == 0
