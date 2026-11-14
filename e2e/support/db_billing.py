import psycopg2
import psycopg2.extras

from support.config import BILLING_DB


class BillingDb:
    def __init__(self):
        self._connection = psycopg2.connect(**BILLING_DB)
        self._connection.autocommit = True

    def close(self):
        if not self._connection.closed:
            self._connection.close()

    def _rows(self, sql, params):
        with self._connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]

    def orcamento(self, os_id):
        rows = self._rows(
            """
            SELECT id, ordem_servico_id, valor_total, email_cliente, status,
                   criado_em, atualizado_em
              FROM orcamento
             WHERE ordem_servico_id = %s
            """,
            (os_id,),
        )
        return rows[0] if rows else None

    def contar_orcamentos(self, os_id):
        rows = self._rows(
            "SELECT count(*) AS total FROM orcamento WHERE ordem_servico_id = %s",
            (os_id,),
        )
        return rows[0]["total"]

    def orcamento_itens(self, os_id):
        return self._rows(
            """
            SELECT i.execucao_servico_id, i.peca_id, i.quantidade
              FROM orcamento_item i
              JOIN orcamento o ON o.id = i.orcamento_id
             WHERE o.ordem_servico_id = %s
             ORDER BY i.execucao_servico_id, i.peca_id
            """,
            (os_id,),
        )

    def pagamento(self, os_id):
        rows = self._rows(
            """
            SELECT id, ordem_servico_id, valor, descricao, email_pagador, status,
                   preferencia_id, checkout_url, mercado_pago_payment_id,
                   status_detalhe, criado_em, atualizado_em
              FROM pagamento
             WHERE ordem_servico_id = %s
            """,
            (os_id,),
        )
        return rows[0] if rows else None

    def contar_pagamentos(self, os_id):
        rows = self._rows(
            "SELECT count(*) AS total FROM pagamento WHERE ordem_servico_id = %s",
            (os_id,),
        )
        return rows[0]["total"]
