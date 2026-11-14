from bson import ObjectId
from bson.errors import InvalidId
from pymongo import MongoClient

from support.config import INVENTORY_DB_NAME, INVENTORY_MONGO_URI


def _ids(value):
    try:
        return [value, ObjectId(value)]
    except (InvalidId, TypeError):
        return [value]


class InventoryDb:
    def __init__(self):
        self._client = MongoClient(INVENTORY_MONGO_URI, serverSelectionTimeoutMS=5000)
        self._db = self._client[INVENTORY_DB_NAME]

    def close(self):
        self._client.close()

    def peca(self, peca_id):
        return self._db["PECA"].find_one({"_id": {"$in": _ids(peca_id)}})

    def peca_estoque(self, peca_id):
        return self._db["PECA_ESTOQUE"].find_one({"_id": {"$in": _ids(peca_id)}})

    def estoque_armazenado(self, peca_id):
        doc = self.peca_estoque(peca_id)
        return None if doc is None else doc.get("QUANTIDADE_ARMAZENADA")

    def estoque_disponivel(self, peca_id):
        doc = self.peca_estoque(peca_id)
        return None if doc is None else doc.get("QUANTIDADE_DISPONIVEL")

    def _filtro_alocada(self, execucao_servico_id, peca_id=None):
        filtro = {"EXECUCAO_SERVICO_ID": execucao_servico_id}
        if peca_id is not None:
            filtro["PECA_ID"] = {"$in": _ids(peca_id)}
        return filtro

    def pecas_alocadas(self, execucao_servico_id):
        return list(self._db["PECA_ALOCADA"].find(self._filtro_alocada(execucao_servico_id)))

    def peca_alocada(self, execucao_servico_id, peca_id):
        return self._db["PECA_ALOCADA"].find_one(
            self._filtro_alocada(execucao_servico_id, peca_id))

    def status_alocada(self, execucao_servico_id, peca_id):
        doc = self.peca_alocada(execucao_servico_id, peca_id)
        return None if doc is None else doc.get("STATUS")

    def contar_alocadas(self, execucao_servico_id, peca_id=None):
        return self._db["PECA_ALOCADA"].count_documents(
            self._filtro_alocada(execucao_servico_id, peca_id))
