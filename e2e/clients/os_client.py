import requests


class OsServiceClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Bearer {token}"

    def criar_ordem_servico(self, veiculo_id: int, proprietario_id: int, reclamacao: str) -> dict:
        resp = self._session.post(
            f"{self.base_url}/ordens-servico",
            json={
                "veiculoId": veiculo_id,
                "proprietarioId": proprietario_id,
                "reclamacaoProprietario": reclamacao,
            },
        )
        resp.raise_for_status()
        return resp.json()

    def iniciar_diagnostico(self, os_id: int) -> None:
        resp = self._session.patch(f"{self.base_url}/ordens-servico/{os_id}/iniciar-diagnostico")
        resp.raise_for_status()

    def adicionar_servico(self, os_id: int, servicos: list[dict]) -> None:
        resp = self._session.patch(
            f"{self.base_url}/ordens-servico/{os_id}/adicionar-servico",
            json={"servicoRequests": servicos},
        )
        resp.raise_for_status()

    def finalizar_diagnostico(self, os_id: int) -> None:
        resp = self._session.patch(f"{self.base_url}/ordens-servico/{os_id}/finalizar-diagnostico")
        resp.raise_for_status()

    def aprovar(self, os_id: int, decisoes: list[dict]) -> dict:
        resp = self.aprovar_raw(os_id, decisoes)
        resp.raise_for_status()
        return resp.json()

    def aprovar_raw(self, os_id: int, decisoes: list[dict]) -> requests.Response:
        return self._session.patch(
            f"{self.base_url}/ordens-servico/{os_id}/aprovar",
            json={"servicos": decisoes},
        )

    def reprovar(self, os_id: int) -> None:
        resp = self._session.patch(f"{self.base_url}/ordens-servico/{os_id}/reprovar")
        resp.raise_for_status()

    def iniciar_servico(self, os_id: int, execucao_servico_id: int, raise_on_error: bool = True):
        resp = self._session.patch(
            f"{self.base_url}/ordens-servico/{os_id}/iniciar-servico/{execucao_servico_id}"
        )
        if raise_on_error:
            resp.raise_for_status()
        return resp

    def finalizar_servico(self, os_id: int, execucao_servico_id: int) -> None:
        resp = self._session.patch(
            f"{self.base_url}/ordens-servico/{os_id}/finalizar-servico/{execucao_servico_id}"
        )
        resp.raise_for_status()

    def retirar_veiculo(self, os_id: int) -> None:
        resp = self._session.patch(f"{self.base_url}/ordens-servico/{os_id}/retirar-veiculo")
        resp.raise_for_status()

    def get_ordem_servico(self, os_id: int) -> dict:
        resp = self._session.get(f"{self.base_url}/ordens-servico", params={"id": os_id})
        resp.raise_for_status()
        content = resp.json()["content"]
        if not content:
            raise ValueError(f"OS {os_id} not found")
        return content[0]

    def aprovacao_orcamento(self, os_id: int, decisoes: list[dict]) -> dict:
        resp = requests.post(
            f"{self.base_url}/ordens-servico/{os_id}/aprovacao-orcamento",
            json={"servicos": decisoes},
        )
        resp.raise_for_status()
        return resp.json()

    def list_proprietarios(self) -> list[dict]:
        resp = self._session.get(f"{self.base_url}/proprietarios", params={"page": 0, "size": 1})
        resp.raise_for_status()
        return resp.json()["content"]

    def list_veiculos(self) -> list[dict]:
        resp = self._session.get(f"{self.base_url}/veiculos", params={"page": 0, "size": 1})
        resp.raise_for_status()
        return resp.json()["content"]

    def list_servicos(self) -> list[dict]:
        return self.list_servicos_page(size=1)

    def list_servicos_page(self, size: int) -> list[dict]:
        resp = self._session.get(f"{self.base_url}/servicos", params={"page": 0, "size": size})
        resp.raise_for_status()
        return resp.json()["content"]


def login(base_url: str, username: str, password: str) -> str:
    resp = requests.post(f"{base_url}/auth/login", json={"username": username, "password": password})
    resp.raise_for_status()
    return resp.json()["token"]
