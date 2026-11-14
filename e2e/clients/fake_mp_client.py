import requests


class FakeMercadoPagoClient:
    def __init__(self, base_url: str):
        self.base_url = base_url

    def reset(self) -> None:
        resp = requests.post(f"{self.base_url}/__control__/reset", timeout=5)
        resp.raise_for_status()

    def program_payment(self, payment_id: int, external_reference: str,
                        status: str = "approved", status_detail: str = "accredited") -> None:
        resp = requests.post(
            f"{self.base_url}/__control__/payments/{payment_id}",
            json={
                "status": status,
                "statusDetail": status_detail,
                "externalReference": external_reference,
            },
            timeout=5,
        )
        resp.raise_for_status()

    def fail_preferences(self, status: int = 500) -> None:
        resp = requests.post(
            f"{self.base_url}/__control__/faults",
            json={"preferencesStatus": status},
            timeout=5,
        )
        resp.raise_for_status()

    def clear_faults(self) -> None:
        resp = requests.post(
            f"{self.base_url}/__control__/faults",
            json={"preferencesStatus": None},
            timeout=5,
        )
        resp.raise_for_status()

    def preferences(self) -> list:
        resp = requests.get(f"{self.base_url}/__control__/preferences", timeout=5)
        resp.raise_for_status()
        return resp.json()["preferences"]
