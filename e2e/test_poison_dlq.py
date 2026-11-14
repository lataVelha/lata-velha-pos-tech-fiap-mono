import pytest

from drivers.saga import PecaPedida
from support import bus
from support.config import DLQS, SERVICE_QUEUES
from support.waiting import wait_until

pytestmark = pytest.mark.dlq


def test_envelope_malformado_vai_direto_para_as_tres_dlqs(rabbitmq_channel, dlq_baseline):
    bus.publish_raw(rabbitmq_channel, "DiagnosticoFinalizado", b'{"nao-e-um-envelope"')

    for name, queue in DLQS.items():
        wait_until(
            lambda q=queue: bus.queue_depth(q),
            lambda depth, n=name: depth == dlq_baseline[n] + 1,
            timeout=30,
            desc=f"{queue} should receive exactly one poison message",
        )

    for queue in SERVICE_QUEUES.values():
        wait_until(
            lambda q=queue: bus.queue_depth(q),
            lambda depth: depth == 0,
            timeout=30,
            desc=f"{queue} should drain after rejecting the poison message",
        )


def test_servicos_seguem_saudaveis_depois_do_envelope_malformado(
        saga, os_db, inventory_db, seeded_servico_ids):
    peca_id = saga.nova_peca("Peca pos poison e2e", valor=20.00, estoque=2)
    fixture = saga.nova_os_aguardando_aprovacao([{
        "servico_id": seeded_servico_ids[0],
        "valor_mao_de_obra": 40.00,
        "pecas": [PecaPedida(peca_id=peca_id, quantidade=1, estoque_inicial=2)],
    }])
    saga.aprovar_via_billing(fixture)
    saga.esperar_os(fixture.os_id, "EM_EXECUCAO")
    saga.executar_tudo(fixture)
    saga.esperar_os(fixture.os_id, "FINALIZADA")
    saga.pagar(fixture)
    saga.esperar_os(fixture.os_id, "PAGA")
    saga.os_client.retirar_veiculo(fixture.os_id)
    saga.esperar_os(fixture.os_id, "ENTREGUE")


def test_peca_inexistente_para_na_dlq_do_inventory_e_a_os_fica_parada(
        saga, recorder, os_db, billing_db, inventory_db, seeded_servico_ids,
        rabbitmq_channel, dlq_baseline):
    peca_id = saga.nova_peca("Peca dlq inventory e2e", valor=20.00, estoque=2)
    fixture = saga.nova_os_aguardando_aprovacao([{
        "servico_id": seeded_servico_ids[0],
        "valor_mao_de_obra": 40.00,
        "pecas": [PecaPedida(peca_id=peca_id, quantidade=1, estoque_inicial=2)],
    }])
    os_id, execucao_id = fixture.os_id, fixture.execucao_id
    meu = lambda p: p.get("osId") == os_id

    bus.publish_event(rabbitmq_channel, "OrcamentoAprovado", {
        "osId": os_id,
        "valorTotal": 60.00,
        "itens": [{
            "execucaoServicoId": execucao_id,
            "pecaId": "ffffffffffffffffffffffff",
            "quantidade": 1,
        }],
    })
    recorder.wait_for("OrcamentoAprovado", meu)

    saga.esperar_os(os_id, "APROVADA")
    recorder.assert_not_seen("InventoryChecked", meu, settle=4)

    wait_until(
        lambda: bus.queue_depth(DLQS["inventory"]),
        lambda depth: depth == dlq_baseline["inventory"] + 1,
        timeout=40,
        desc="the unprocessable OrcamentoAprovado should land in the inventory DLQ",
    )
    assert bus.queue_depth(DLQS["os"]) == dlq_baseline["os"]
    assert bus.queue_depth(DLQS["billing"]) == dlq_baseline["billing"]

    assert os_db.status(os_id) == "APROVADA"
    assert inventory_db.contar_alocadas(execucao_id) == 0
    assert billing_db.orcamento(os_id)["status"] == "PENDENTE"


def test_indisponibilidade_do_mercado_pago_bloqueia_cobranca_e_a_entrega(
        saga, recorder, os_db, billing_db, fake_mp, seeded_servico_ids,
        rabbitmq_channel, dlq_baseline):
    peca_id = saga.nova_peca("Peca mp fora e2e", valor=20.00, estoque=2)
    fixture = saga.nova_os_aguardando_aprovacao([{
        "servico_id": seeded_servico_ids[0],
        "valor_mao_de_obra": 40.00,
        "pecas": [PecaPedida(peca_id=peca_id, quantidade=1, estoque_inicial=2)],
    }])
    os_id = fixture.os_id
    meu = lambda p: p.get("osId") == os_id

    saga.aprovar_via_billing(fixture)
    saga.esperar_os(os_id, "EM_EXECUCAO")

    fake_mp.fail_preferences(500)

    saga.executar_tudo(fixture)
    recorder.wait_for("OSEnded", meu)
    saga.esperar_os(os_id, "FINALIZADA")

    recorder.assert_not_seen("CobrancaEmitida", meu, settle=6)

    wait_until(
        lambda: bus.queue_depth(DLQS["billing"]),
        lambda depth: depth == dlq_baseline["billing"] + 1,
        timeout=40,
        desc="OSEnded should dead-letter while Mercado Pago is down",
    )

    bloqueada = saga.os_client.retirar_veiculo(os_id, raise_on_error=False)
    assert bloqueada.status_code >= 400, "the payment gate must hold through an outage"
    assert os_db.status(os_id) == "FINALIZADA"

    fake_mp.clear_faults()
    bus.publish_event(rabbitmq_channel, "OSEnded", {"osId": os_id})

    recorder.wait_for("CobrancaEmitida", meu, timeout=40)
    saga.pagar(fixture)
    recorder.wait_for("PaymentDone", meu)
    saga.esperar_os(os_id, "PAGA")

    saga.os_client.retirar_veiculo(os_id)
    saga.esperar_os(os_id, "ENTREGUE")
    assert billing_db.pagamento(os_id)["status"] == "APROVADO"
