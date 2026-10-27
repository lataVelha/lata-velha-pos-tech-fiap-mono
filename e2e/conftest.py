import os
import subprocess
import time
from pathlib import Path

import pika
import pytest
import requests

from clients.inventory_client import InventoryServiceClient
from clients.os_client import OsServiceClient, login

REPO_ROOT = Path(__file__).resolve().parent.parent

OS_BASE_URL = "http://localhost:8080"
INVENTORY_BASE_URL = "http://localhost:8082"
BILLING_BASE_URL = "http://localhost:8081"

RABBITMQ_HOST = "localhost"
RABBITMQ_PORT = 5672
RABBITMQ_USER = "guest"
RABBITMQ_PASSWORD = "guest"


@pytest.fixture(scope="session", autouse=True)
def ensure_stack_running():
    if os.environ.get("E2E_MANAGE_COMPOSE", "1") != "0":
        subprocess.run(["docker", "compose", "up", "-d"], cwd=REPO_ROOT, check=True)

    deadline = time.monotonic() + 180
    for name, base_url in (
        ("os-service", OS_BASE_URL),
        ("inventory-service", INVENTORY_BASE_URL),
        ("billing-service", BILLING_BASE_URL),
    ):
        while True:
            try:
                resp = requests.get(f"{base_url}/actuator/health", timeout=3)
                if resp.ok and resp.json().get("status") == "UP":
                    break
            except requests.RequestException:
                pass
            if time.monotonic() > deadline:
                raise TimeoutError(f"{name} never reported healthy at {base_url}/actuator/health")
            time.sleep(2)


@pytest.fixture(scope="session")
def admin_token():
    return login(OS_BASE_URL, "admin@latavelha.com", "Admin@123")


@pytest.fixture(scope="session")
def atendente_token():
    return login(OS_BASE_URL, "atendente@latavelha.com", "Atend@123")


@pytest.fixture(scope="session")
def mecanico_token():
    return login(OS_BASE_URL, "mecanico@latavelha.com", "Mecan@123")


@pytest.fixture()
def os_client(atendente_token):
    return OsServiceClient(OS_BASE_URL, atendente_token)


@pytest.fixture()
def os_client_mecanico(mecanico_token):
    return OsServiceClient(OS_BASE_URL, mecanico_token)


@pytest.fixture()
def inventory_client(admin_token):
    return InventoryServiceClient(INVENTORY_BASE_URL, admin_token)


@pytest.fixture()
def seeded_proprietario_id(os_client):
    return os_client.list_proprietarios()[0]["id"]


@pytest.fixture()
def seeded_veiculo_id(os_client):
    return os_client.list_veiculos()[0]["id"]


@pytest.fixture()
def seeded_servico_id(os_client):
    return os_client.list_servicos()[0]["id"]


@pytest.fixture()
def rabbitmq_connection_params():
    return pika.ConnectionParameters(
        host=RABBITMQ_HOST,
        port=RABBITMQ_PORT,
        credentials=pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASSWORD),
    )


@pytest.fixture()
def rabbitmq_channel(rabbitmq_connection_params):
    connection = pika.BlockingConnection(rabbitmq_connection_params)
    channel = connection.channel()
    yield channel
    if connection.is_open:
        connection.close()
