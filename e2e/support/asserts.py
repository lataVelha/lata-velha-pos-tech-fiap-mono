from decimal import Decimal


def assert_ladder(os_db, os_id, esperado):
    ladder = os_db.historico_ladder(os_id)
    assert ladder == esperado, f"status ladder for OS {os_id} was {ladder}, expected {esperado}"


def assert_ladder_contains_in_order(os_db, os_id, esperado):
    ladder = os_db.historico_ladder(os_id)
    posicao = -1
    for estado in esperado:
        assert estado in ladder[posicao + 1:], (
            f"expected {estado} after position {posicao} in ladder {ladder}"
        )
        posicao = ladder.index(estado, posicao + 1)


def assert_um_estado_aberto(os_db, os_id, estado):
    abertos = os_db.historico_abertos(os_id)
    assert len(abertos) == 1, f"expected exactly one open historico row, got {abertos}"
    assert abertos[0]["estado_os"] == estado, (
        f"open historico row is {abertos[0]['estado_os']}, expected {estado}"
    )


def assert_conservacao_estoque(inventory_db, peca_id, armazenada_inicial, execucoes):
    instalada_total = 0
    reservada_total = 0
    for execucao_id in execucoes:
        doc = inventory_db.peca_alocada(execucao_id, peca_id)
        if doc is None:
            continue
        instalada_total += doc.get("QTD_INSTALADA") or 0
        reservada_total += doc.get("QTD_RESERVADA") or 0

    armazenada = inventory_db.estoque_armazenado(peca_id)
    disponivel = inventory_db.estoque_disponivel(peca_id)

    assert armazenada == armazenada_inicial - instalada_total, (
        f"peca {peca_id}: armazenada {armazenada}, "
        f"expected {armazenada_inicial} - {instalada_total} installed"
    )
    assert disponivel == armazenada_inicial - reservada_total, (
        f"peca {peca_id}: disponivel {disponivel}, "
        f"expected {armazenada_inicial} - {reservada_total} reserved"
    )
    assert armazenada >= 0 and disponivel >= 0, (
        f"peca {peca_id}: negative stock (armazenada={armazenada}, disponivel={disponivel})"
    )


def assert_valor(actual, expected):
    assert Decimal(str(actual)) == Decimal(str(expected)), (
        f"expected {expected}, got {actual}"
    )
