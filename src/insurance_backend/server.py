import threading
import time
from collections.abc import Generator
from contextlib import contextmanager

import uvicorn
from fastapi import FastAPI


def bound_port(server: uvicorn.Server) -> int | None:
    for http_server in server.servers:
        for socket in http_server.sockets:
            return int(socket.getsockname()[1])
    return None


def start_server(app: FastAPI) -> tuple[uvicorn.Server, threading.Thread, int]:
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=0,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    port = None
    while time.monotonic() < deadline:
        if server.started:
            port = bound_port(server)
            if port is not None:
                return server, thread, port
        time.sleep(0.01)
    raise RuntimeError("uvicorn did not bind an ephemeral port")


def stop_server(server: uvicorn.Server, thread: threading.Thread) -> None:
    server.should_exit = True
    thread.join(timeout=5)


@contextmanager
def serving(app: FastAPI) -> Generator[int]:
    server, thread, port = start_server(app)
    try:
        yield port
    finally:
        stop_server(server, thread)
