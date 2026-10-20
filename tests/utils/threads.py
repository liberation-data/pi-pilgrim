import threading


def run_in_threads(target, count):
    """
    Runs `target` on `count` threads at once, and waits for them all.
    """
    threads = [threading.Thread(target=target) for _ in range(count)]
    for it in threads:
        it.start()
    for it in threads:
        it.join()
