import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8090

_lock = threading.Lock()
_state = {
    "preferences": [],
    "payments": {},
    "preferences_fault": None,
}

_PAYMENT_PATH = re.compile(r"^/v1/payments/(\d+)$")
_CONTROL_PAYMENT_PATH = re.compile(r"^/__control__/payments/(\d+)$")


def _reset():
    with _lock:
        _state["preferences"] = []
        _state["payments"] = {}
        _state["preferences_fault"] = None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print("fake-mercadopago %s" % (fmt % args), flush=True)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length == 0:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path == "/checkout/preferences":
            return self._criar_preferencia()
        if self.path == "/__control__/reset":
            _reset()
            return self._send(200, {"ok": True})
        if self.path == "/__control__/faults":
            body = self._body()
            with _lock:
                _state["preferences_fault"] = body.get("preferencesStatus")
            return self._send(200, {"ok": True})
        match = _CONTROL_PAYMENT_PATH.match(self.path)
        if match:
            return self._programar_pagamento(int(match.group(1)))
        self._body()
        return self._send(404, {"message": "not found: %s" % self.path})

    def do_GET(self):
        if self.path == "/__control__/preferences":
            with _lock:
                return self._send(200, {"preferences": list(_state["preferences"])})
        if self.path == "/__control__/health":
            return self._send(200, {"status": "UP"})
        match = _PAYMENT_PATH.match(self.path)
        if match:
            return self._buscar_pagamento(int(match.group(1)))
        return self._send(404, {"message": "not found: %s" % self.path})

    def _criar_preferencia(self):
        body = self._body()
        with _lock:
            fault = _state["preferences_fault"]
        if fault:
            return self._send(int(fault), {"message": "fault injected by the e2e suite"})

        external_reference = body.get("external_reference")
        with _lock:
            _state["preferences"].append(external_reference)
            numero = len(_state["preferences"])
        return self._send(201, {
            "id": "pref-%d" % numero,
            "init_point": "http://fake-mercadopago:%d/checkout/%d" % (PORT, numero),
            "sandbox_init_point": "http://fake-mercadopago:%d/sandbox/checkout/%d" % (PORT, numero),
            "external_reference": external_reference,
        })

    def _programar_pagamento(self, payment_id):
        body = self._body()
        with _lock:
            _state["payments"][payment_id] = {
                "id": payment_id,
                "status": body.get("status", "approved"),
                "status_detail": body.get("statusDetail", "accredited"),
                "external_reference": body.get("externalReference"),
            }
        return self._send(200, {"ok": True})

    def _buscar_pagamento(self, payment_id):
        with _lock:
            programado = _state["payments"].get(payment_id)
        if programado is None:
            return self._send(404, {"message": "payment %d was not programmed" % payment_id})
        return self._send(200, programado)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("fake-mercadopago listening on %d" % PORT, flush=True)
    server.serve_forever()
