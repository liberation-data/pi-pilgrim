import threading


def run_in_threads(target, count):
    """
    Runs `target` on `count` threads, started together, and waits for them all.
    """
    ready = threading.Barrier(count)

    def run():
        ready.wait()
        target()

    threads = [threading.Thread(target=run) for _ in range(count)]
    for it in threads:
        it.start()
    for it in threads:
        it.join()
