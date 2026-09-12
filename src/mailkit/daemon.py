"""Long-running mailkit service: watchers, API, webhooks, event socket."""

from __future__ import annotations

import os
import signal
import threading
import time

from mailkit.api.http import App, serve_event_socket, serve_forever
from mailkit.doctor import run_doctor, start_doctor_loop
from mailkit.events import EventBus
from mailkit.logutil import setup_logging
from mailkit.models import utcnow
from mailkit.paths import events_socket_path
from mailkit.runtime import open_runtime
from mailkit.service import clear_pid, load_or_create_token, write_pid
from mailkit.supervisor import Supervisor
from mailkit.webhooks import WebhookDispatcher


def run(root=None, *, foreground: bool = True) -> int:
    runtime = open_runtime(root)
    setup_logging(runtime.config.daemon.log_level, json_logs=runtime.config.daemon.json_logs, root=runtime.root)
    write_pid(runtime.root)
    token = load_or_create_token(runtime.root)
    bus = EventBus(runtime.store, retention=runtime.config.daemon.event_retention)
    webhooks = WebhookDispatcher(runtime.store, runtime.vault)
    bus.add_listener(webhooks.enqueue)
    supervisor = Supervisor(runtime, bus)
    app = App(
        runtime=runtime,
        bus=bus,
        supervisor=supervisor,
        webhooks=webhooks,
        token=token,
        started_at=utcnow(),
    )
    stop = threading.Event()

    def handle_stop(*_args):
        stop.set()

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)
    host = runtime.config.daemon.host
    if runtime.config.daemon.allow_remote is False and host not in {"127.0.0.1", "localhost", "::1"}:
        host = "127.0.0.1"
    server = serve_forever(app, host, runtime.config.daemon.port, stop=stop)
    try:
        serve_event_socket(app, events_socket_path(runtime.root), stop)
    except OSError:
        pass
    webhooks.start()
    supervisor.start_all()
    _heartbeat(app, stop)
    run_doctor(runtime.root, repair=runtime.config.daemon.doctor_repair, app=app, runtime=runtime)
    start_doctor_loop(
        app,
        stop,
        interval=float(runtime.config.daemon.doctor_interval or 60),
        repair=runtime.config.daemon.doctor_repair,
    )
    try:
        while not stop.is_set():
            time.sleep(0.3)
    finally:
        supervisor.stop_all()
        webhooks.stop()
        server.shutdown()
        clear_pid(runtime.root, expected_pid=os.getpid())
        runtime.close()
    return 0


def _heartbeat(app: App, stop: threading.Event) -> None:
    def loop():
        from mailkit.ids import new_id
        from mailkit.models import Event

        while not stop.wait(60):
            app.bus.publish(
                Event(
                    id=new_id("evt"),
                    type="service.heartbeat",
                    data={"accounts": list(app.supervisor.status())},
                )
            )

    threading.Thread(target=loop, name="mailkit-heartbeat", daemon=True).start()
