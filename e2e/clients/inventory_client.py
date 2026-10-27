import requests


class InventoryServiceClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Bearer {token}"

    def criar_peca(self, nome: str, descricao: str, valor: float) -> dict:
        resp = self._session.post(
            f"{self.base_url}/pecas",
            json={"nome": nome, "descricao": descricao, "valor": valor},
        )
        resp.raise_for_status()
        return resp.json()

    def get_peca_estoque(self, peca_id: int) -> dict:
        resp = self._session.get(f"{self.base_url}/pecas/{peca_id}/estoque")
        resp.raise_for_status()
        return resp.json()

    def entrada_estoque(self, peca_id: int, quantidade: int) -> dict:
        resp = self._session.post(
            f"{self.base_url}/pecas/{peca_id}/estoque/entrada",
            json={"quantidade": quantidade},
        )
        resp.raise_for_status()
        return resp.json()

    def saida_estoque(self, peca_id: int, quantidade: int) -> dict:
        resp = self._session.post(
            f"{self.base_url}/pecas/{peca_id}/estoque/saida",
            json={"quantidade": quantidade},
        )
        resp.raise_for_status()
        return resp.json()

    def ajustar_estoque(self, peca_id: int, quantidade_armazenada: int, quantidade_disponivel: int) -> dict:
        resp = self._session.patch(
            f"{self.base_url}/pecas/{peca_id}/estoque/ajuste",
            json={
                "quantidadeArmazenada": quantidade_armazenada,
                "quantidadeDisponivel": quantidade_disponivel,
            },
        )
        resp.raise_for_status()
        return resp.json()
