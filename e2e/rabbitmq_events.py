import json
import time
import uuid
from datetime import datetime, timezone

import pika

EXCHANGE = "lata-velha.events"


def _envelope(event_type: str, payload: dict) -> dict:
    return {
        "eventId": str(uuid.uuid4()),
        "eventType": event_type,
        "occurredAt": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }


def publish_event(channel: "pika.channel.Channel", event_type: str, payload: dict) -> None:
    channel.basic_publish(
        exchange=EXCHANGE,
        routing_key=event_type,
        body=json.dumps(_envelope(event_type, payload)),
    )


class EventWatcher:
    def __init__(self, connection_params: "pika.ConnectionParameters"):
        self._connection_params = connection_params
        self._connection = None
        self._channel = None
        self._queue_name = None

    def __enter__(self):
        self._connection = pika.BlockingConnection(self._connection_params)
        self._channel = self._connection.channel()
        result = self._channel.queue_declare(queue="", exclusive=True, auto_delete=True)
        self._queue_name = result.method.queue
        self._channel.queue_bind(exchange=EXCHANGE, queue=self._queue_name, routing_key="#")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._connection is not None and self._connection.is_open:
            self._connection.close()

    def wait_for(self, event_type: str, predicate=lambda payload: True, timeout: float = 15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(deadline - time.monotonic(), 0)
            method_frame, _header_frame, body = self._channel.basic_get(queue=self._queue_name)
            if method_frame is None:
                time.sleep(min(0.2, remaining) if remaining > 0 else 0)
                continue
            self._channel.basic_ack(method_frame.delivery_tag)
            envelope = json.loads(body)
            if envelope.get("eventType") == event_type and predicate(envelope.get("payload", {})):
                return envelope["payload"]
        raise TimeoutError(f"no '{event_type}' event matched the predicate within {timeout}s")

    def assert_not_seen(self, event_type: str, predicate=lambda payload: True, timeout: float = 3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            method_frame, _header_frame, body = self._channel.basic_get(queue=self._queue_name)
            if method_frame is None:
                time.sleep(0.2)
                continue
            self._channel.basic_ack(method_frame.delivery_tag)
            envelope = json.loads(body)
            if envelope.get("eventType") == event_type and predicate(envelope.get("payload", {})):
                raise AssertionError(f"unexpected '{event_type}' event: {envelope}")
