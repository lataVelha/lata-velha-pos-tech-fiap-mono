import requests


class BillingServiceClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Bearer {token}"

    def aprovar_orcamento(self, os_id: int) -> None:
        resp = self.aprovar_orcamento_raw(os_id)
        resp.raise_for_status()

    def aprovar_orcamento_raw(self, os_id: int) -> requests.Response:
        return self._session.post(f"{self.base_url}/orcamentos/{os_id}/aprovar")

    def reprovar_orcamento(self, os_id: int) -> None:
        resp = self.reprovar_orcamento_raw(os_id)
        resp.raise_for_status()

    def reprovar_orcamento_raw(self, os_id: int) -> requests.Response:
        return self._session.post(f"{self.base_url}/orcamentos/{os_id}/reprovar")

    def get_pagamento_por_os(self, os_id: int) -> dict:
        resp = requests.get(f"{self.base_url}/pagamentos/ordem-servico/{os_id}")
        resp.raise_for_status()
        return resp.json()

    def get_pagamento_por_os_raw(self, os_id: int) -> requests.Response:
        return requests.get(f"{self.base_url}/pagamentos/ordem-servico/{os_id}")

    def webhook(self, payment_id: int, tipo: str = "payment") -> requests.Response:
        return requests.post(
            f"{self.base_url}/pagamentos/webhook",
            params={"type": tipo, "data.id": str(payment_id)},
        )
