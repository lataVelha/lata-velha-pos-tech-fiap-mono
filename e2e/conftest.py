import os
import subprocess
import time

import pika
import pytest
import requests

from clients.billing_client import BillingServiceClient
from clients.fake_mp_client import FakeMercadoPagoClient
from clients.inventory_client import InventoryServiceClient
from clients.os_client import OsServiceClient, login
from drivers.saga import SagaDriver
from support import bus
from support.config import (
    BILLING_BASE_URL,
    DLQS,
    FAKE_MP_BASE_URL,
    INVENTORY_BASE_URL,
    OS_BASE_URL,
    REPO_ROOT,
    USERS,
)
from support.db_billing import BillingDb
from support.db_inventory import InventoryDb
from support.db_os import OsDb


def pytest_configure(config):
    config.addinivalue_line("markers", "dlq: asserts on dead-letter queue depth; run serially")


@pytest.fixture(scope="session", autouse=True)
def ensure_stack_running():
    if os.environ.get("E2E_MANAGE_COMPOSE", "1") != "0":
        subprocess.run(["docker", "compose", "up", "-d"], cwd=REPO_ROOT, check=True)

    deadline = time.monotonic() + 240
    for name, base_url in (
        ("os-service", f"{OS_BASE_URL}/actuator/health"),
        ("inventory-service", f"{INVENTORY_BASE_URL}/actuator/health"),
        ("billing-service", f"{BILLING_BASE_URL}/actuator/health"),
        ("fake-mercadopago", f"{FAKE_MP_BASE_URL}/__control__/health"),
    ):
        while True:
            try:
                resp = requests.get(base_url, timeout=3)
                if resp.ok and resp.json().get("status") == "UP":
                    break
            except requests.RequestException:
                pass
            if time.monotonic() > deadline:
                raise TimeoutError(f"{name} never reported healthy at {base_url}")
            time.sleep(2)

    for queue in DLQS.values():
        bus.purge_queue(queue)


@pytest.fixture(scope="session")
def admin_token():
    return login(OS_BASE_URL, *USERS["admin"])


@pytest.fixture(scope="session")
def atendente_token():
    return login(OS_BASE_URL, *USERS["atendente"])


@pytest.fixture(scope="session")
def mecanico_token():
    return login(OS_BASE_URL, *USERS["mecanico"])


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
def billing_client(atendente_token):
    return BillingServiceClient(BILLING_BASE_URL, atendente_token)


@pytest.fixture()
def billing_client_mecanico(mecanico_token):
    return BillingServiceClient(BILLING_BASE_URL, mecanico_token)


@pytest.fixture()
def fake_mp():
    client = FakeMercadoPagoClient(FAKE_MP_BASE_URL)
    client.clear_faults()
    yield client
    client.clear_faults()


@pytest.fixture(scope="session")
def os_db():
    db = OsDb()
    yield db
    db.close()


@pytest.fixture(scope="session")
def billing_db():
    db = BillingDb()
    yield db
    db.close()


@pytest.fixture(scope="session")
def inventory_db():
    db = InventoryDb()
    yield db
    db.close()


@pytest.fixture()
def seeded_proprietario_id(os_client):
    return os_client.list_proprietarios()[0]["id"]


@pytest.fixture()
def seeded_veiculo_id(os_client):
    return os_client.list_veiculos()[0]["id"]


@pytest.fixture()
def seeded_servico_ids(os_client):
    servicos = os_client.list_servicos_page(size=5)
    assert len(servicos) >= 2, "seed data must provide at least two servicos"
    return [servico["id"] for servico in servicos]


@pytest.fixture()
def saga(os_client, os_client_mecanico, inventory_client, billing_client, fake_mp,
         os_db, billing_db, seeded_proprietario_id, seeded_veiculo_id):
    return SagaDriver(
        os_client=os_client,
        os_client_mecanico=os_client_mecanico,
        inventory_client=inventory_client,
        billing_client=billing_client,
        fake_mp=fake_mp,
        os_db=os_db,
        billing_db=billing_db,
        proprietario_id=seeded_proprietario_id,
        veiculo_id=seeded_veiculo_id,
    )


@pytest.fixture()
def recorder():
    with bus.SagaRecorder() as rec:
        yield rec


@pytest.fixture()
def rabbitmq_channel():
    connection = pika.BlockingConnection(bus.connection_params())
    channel = connection.channel()
    yield channel
    if connection.is_open:
        connection.close()


@pytest.fixture()
def dlq_baseline():
    return {name: bus.queue_depth(queue) for name, queue in DLQS.items()}
