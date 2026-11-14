import json
import threading
import time
import uuid
from datetime import datetime, timezone

import pika

from support.config import (
    EXCHANGE,
    RABBITMQ_HOST,
    RABBITMQ_PASSWORD,
    RABBITMQ_PORT,
    RABBITMQ_USER,
)


def connection_params():
    return pika.ConnectionParameters(
        host=RABBITMQ_HOST,
        port=RABBITMQ_PORT,
        credentials=pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASSWORD),
        heartbeat=0,
        blocked_connection_timeout=30,
    )


def _envelope(event_type, payload):
    return {
        "eventId": str(uuid.uuid4()),
        "eventType": event_type,
        "occurredAt": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }


def publish_event(channel, event_type, payload):
    channel.basic_publish(
        exchange=EXCHANGE,
        routing_key=event_type,
        body=json.dumps(_envelope(event_type, payload)),
    )


def publish_raw(channel, routing_key, body):
    channel.basic_publish(exchange=EXCHANGE, routing_key=routing_key, body=body)


def queue_depth(queue):
    connection = pika.BlockingConnection(connection_params())
    try:
        declared = connection.channel().queue_declare(queue=queue, passive=True)
        return declared.method.message_count
    finally:
        if connection.is_open:
            connection.close()


def purge_queue(queue):
    connection = pika.BlockingConnection(connection_params())
    try:
        connection.channel().queue_purge(queue=queue)
    except Exception:
        pass
    finally:
        if connection.is_open:
            connection.close()


class SagaRecorder:
    def __init__(self):
        self._connection = None
        self._channel = None
        self._queue = None
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._events = []

    def __enter__(self):
        self._connection = pika.BlockingConnection(connection_params())
        self._channel = self._connection.channel()
        declared = self._channel.queue_declare(queue="", exclusive=True, auto_delete=True)
        self._queue = declared.method.queue
        self._channel.queue_bind(exchange=EXCHANGE, queue=self._queue, routing_key="#")
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self._connection is not None and self._connection.is_open:
            self._connection.close()
        return False

    def _drain(self):
        while not self._stop.is_set():
            try:
                method, _, body = self._channel.basic_get(queue=self._queue, auto_ack=True)
            except Exception:
                return
            if method is None:
                try:
                    self._connection.sleep(0.05)
                except Exception:
                    return
                continue
            try:
                envelope = json.loads(body)
            except (json.JSONDecodeError, TypeError):
                envelope = {"eventType": None, "payload": None, "raw": body.decode(errors="replace")}
            with self._lock:
                self._events.append(envelope)

    def _snapshot(self):
        with self._lock:
            return list(self._events)

    def matches(self, event_type, predicate=lambda payload: True):
        found = []
        for envelope in self._snapshot():
            if envelope.get("eventType") != event_type:
                continue
            payload = envelope.get("payload") or {}
            try:
                if predicate(payload):
                    found.append(payload)
            except (KeyError, TypeError):
                continue
        return found

    def count(self, event_type, predicate=lambda payload: True):
        return len(self.matches(event_type, predicate))

    def wait_for(self, event_type, predicate=lambda payload: True, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            found = self.matches(event_type, predicate)
            if found:
                return found[0]
            time.sleep(0.1)
        seen = sorted({e.get("eventType") for e in self._snapshot() if e.get("eventType")})
        raise TimeoutError(
            f"no '{event_type}' event matched the predicate within {timeout}s (seen: {seen})"
        )

    def wait_for_count(self, event_type, expected, predicate=lambda payload: True, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.count(event_type, predicate) >= expected:
                return self.matches(event_type, predicate)
            time.sleep(0.1)
        raise TimeoutError(
            f"expected {expected} '{event_type}' events within {timeout}s, "
            f"got {self.count(event_type, predicate)}"
        )

    def assert_not_seen(self, event_type, predicate=lambda payload: True, settle=4):
        time.sleep(settle)
        found = self.matches(event_type, predicate)
        assert not found, f"unexpected '{event_type}' event(s): {found}"

    def assert_count(self, event_type, expected, predicate=lambda payload: True, settle=4):
        time.sleep(settle)
        actual = self.count(event_type, predicate)
        assert actual == expected, (
            f"expected exactly {expected} '{event_type}' event(s), got {actual}"
        )

    def index_of(self, event_type, predicate=lambda payload: True):
        for position, envelope in enumerate(self._snapshot()):
            if envelope.get("eventType") != event_type:
                continue
            payload = envelope.get("payload") or {}
            try:
                if predicate(payload):
                    return position
            except (KeyError, TypeError):
                continue
        raise AssertionError(f"'{event_type}' was never recorded")

    def assert_order(self, first, second, predicate=lambda payload: True):
        assert self.index_of(first, predicate) < self.index_of(second, predicate), (
            f"expected '{first}' to be published before '{second}'"
        )

    def event_types(self):
        return [e.get("eventType") for e in self._snapshot()]
