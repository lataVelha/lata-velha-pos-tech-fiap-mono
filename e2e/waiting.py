import time


def wait_until(fn, predicate, timeout=20, interval=0.5, desc=""):
    deadline = time.monotonic() + timeout
    last_result = None
    while time.monotonic() < deadline:
        last_result = fn()
        if predicate(last_result):
            return last_result
        time.sleep(interval)
    raise TimeoutError(f"{desc} (last value: {last_result!r})")
