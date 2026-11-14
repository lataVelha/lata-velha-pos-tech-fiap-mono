from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

OS_BASE_URL = "http://localhost:8080"
BILLING_BASE_URL = "http://localhost:8081"
INVENTORY_BASE_URL = "http://localhost:8082"
FAKE_MP_BASE_URL = "http://localhost:8090"

RABBITMQ_HOST = "localhost"
RABBITMQ_PORT = 5672
RABBITMQ_USER = "guest"
RABBITMQ_PASSWORD = "guest"

EXCHANGE = "lata-velha.events"

SERVICE_QUEUES = {
    "os": "lata-velha-os-service.events",
    "inventory": "lata-velha-inventory-service.events",
    "billing": "lata-velha-billing-service.events",
}
DLQS = {name: f"{queue}.dlq" for name, queue in SERVICE_QUEUES.items()}

OS_DB = {
    "host": "localhost",
    "port": 5432,
    "dbname": "lata_velha_os",
    "user": "admin",
    "password": "admin123",
}
BILLING_DB = {
    "host": "localhost",
    "port": 5433,
    "dbname": "lata_velha_billing",
    "user": "admin",
    "password": "admin123",
}
INVENTORY_MONGO_URI = "mongodb://admin:admin123@localhost:27017/lata_velha_inventory?authSource=admin"
INVENTORY_DB_NAME = "lata_velha_inventory"

USERS = {
    "admin": ("admin@latavelha.com", "Admin@123"),
    "atendente": ("atendente@latavelha.com", "Atend@123"),
    "mecanico": ("mecanico@latavelha.com", "Mecan@123"),
}
