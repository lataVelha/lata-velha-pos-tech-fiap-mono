import psycopg2
import psycopg2.extras

from support.config import OS_DB


class OsDb:
    def __init__(self):
        self._connection = psycopg2.connect(**OS_DB)
        self._connection.autocommit = True

    def close(self):
        if not self._connection.closed:
            self._connection.close()

    def _rows(self, sql, params):
        with self._connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]

    def ordem_servico(self, os_id):
        rows = self._rows(
            """
            SELECT id, status, criado_em, iniciado_em, finalizado_em, entregue_em,
                   atendente_inicio_id, mecanico_responsavel_id
              FROM ordem_servico
             WHERE id = %s
            """,
            (os_id,),
        )
        return rows[0] if rows else None

    def status(self, os_id):
        row = self.ordem_servico(os_id)
        return row["status"] if row else None

    def execucao_servicos(self, os_id):
        return self._rows(
            """
            SELECT id, servico_id, status_servico, valor_mao_de_obra, iniciado_em, terminado_em
              FROM execucao_servico
             WHERE os_id = %s
             ORDER BY id
            """,
            (os_id,),
        )

    def execucao_status(self, execucao_servico_id):
        rows = self._rows(
            "SELECT status_servico FROM execucao_servico WHERE id = %s",
            (execucao_servico_id,),
        )
        return rows[0]["status_servico"] if rows else None

    def pecas_necessarias(self, execucao_servico_id):
        return self._rows(
            """
            SELECT id, peca_id, quantidade
              FROM peca_necessaria
             WHERE execucao_servico_id = %s
             ORDER BY peca_id
            """,
            (execucao_servico_id,),
        )

    def historico_rows(self, os_id):
        return self._rows(
            """
            SELECT estado_os, data_inicio, data_fim
              FROM historico_estado_os
             WHERE os_id = %s
             ORDER BY data_inicio, id
            """,
            (os_id,),
        )

    def historico_ladder(self, os_id):
        return [row["estado_os"] for row in self.historico_rows(os_id)]

    def historico_abertos(self, os_id):
        return [row for row in self.historico_rows(os_id) if row["data_fim"] is None]
