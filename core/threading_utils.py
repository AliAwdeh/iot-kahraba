import threading


def start_thread(name, target, *args):
    thread = threading.Thread(
        target=target,
        args=args,
        daemon=True,
        name=name
    )
    thread.start()

    print(f"[THREAD] Started {name}")

    return thread
