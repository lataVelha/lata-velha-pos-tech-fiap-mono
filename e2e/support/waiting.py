import time


def wait_until(fn, predicate, timeout=30, interval=0.5, desc=""):
    deadline = time.monotonic() + timeout
    last_result = None
    while True:
        last_result = fn()
        if predicate(last_result):
            return last_result
        if time.monotonic() > deadline:
            raise TimeoutError(f"{desc} (last value: {last_result!r})")
        time.sleep(interval)


def settle(seconds=4):
    time.sleep(seconds)
