from concurrent.futures import CancelledError, Future
from contextlib import contextmanager
from pathlib import Path
from queue import Empty, Queue
import socket
import ssl
from threading import Event, Lock, Thread
from rover.vehicle.management_session import handle_emergency_stop, handle_reset_control
from rover.vehicle.management_connection import serve_management_connection
from time import monotonic

from rover.vehicle.control_guard import update_control_guard
from rover.vehicle.management_session import (
    handle_connection_closed,
    handle_enable_session,
    handle_issue_control_window,
    handle_open_session,
)


@contextmanager
def management_listener(
    cert_dir: Path,
    trusted_credentials: dict[str, bytes],
    address: tuple[str, int] = ("127.0.0.1", 19177),
):
    """Run one TLS worker. The caller runs process_one on the simulation thread."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_dir / "server.pem", cert_dir / "server.key")
    pending = Queue(maxsize=1)  # 网络线程把任务塞进去，主线程把任务取出来
    stopped = Event()  # 只要调用 stopped.set()，所有在跑循环的线程都会停止
    admission = Lock()  # 防止网络线程和主线程同时往队列里塞东西或关停程序

    # 把后台网络线程收到的任务，安全地塞给主线程去执行，然后原地死等主线程的返回结果
    def submit(operation):
        result = Future()
        with admission:  # 防止系统正在关停时，还有线程硬要往里塞任务
            if stopped.is_set():
                raise CancelledError()
            pending.put_nowait((operation, result))  # 塞任务到Queue
        while not stopped.is_set():
            try:
                return result.result(timeout=0.2)  # 每次等待主线程结果最多0.2s
            except TimeoutError:
                if result.done():
                    return result.result()
        raise CancelledError()

    def on_open_session(connection, request):
        def apply(guard):
            guard, next_connection, reply = handle_open_session(
                guard, connection, request
            )
            return guard, (next_connection, reply)

        return submit(apply)

    def on_enable_session(connection, request):
        def apply(guard):
            now_s = monotonic()
            guard = update_control_guard(guard, now_s)
            try:
                guard = handle_enable_session(
                    guard,
                    connection,
                    request.session_id,
                    now_s=now_s,
                )
            except ValueError:
                return guard, False
            return guard, True

        return submit(apply)

    def on_issue_control_window(connection, request):
        def apply(guard):
            return handle_issue_control_window(
                guard, connection, request, now_s=monotonic()
            )

        return submit(apply)

    def on_emergency_stop(connection, request):
        def apply(guard):
            return handle_emergency_stop(guard, connection, request)

        return submit(apply)

    def on_reset_control(connection, request):
        def apply(guard):
            guard, next_connection, reply = handle_reset_control(
                guard, connection, request
            )
            return guard, (next_connection, reply)

        return submit(apply)

    def on_close_connection(connection):
        def apply(guard):
            return handle_connection_closed(guard, connection), None

        submit(apply)

    def process_one(guard):
        try:
            operation, future = pending.get_nowait()
        except Empty:
            return guard

        try:
            next_guard, reply = operation(guard)
        except Exception as error:
            future.set_exception(error)
            return guard

        future.set_result(reply)
        return next_guard

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(address)
        listener.listen(1)
        listener.settimeout(0.2)

        def run_worker():
            while not stopped.is_set():
                try:
                    raw, _ = listener.accept()
                except TimeoutError:
                    continue

                with raw:
                    if stopped.is_set():
                        return
                    raw.settimeout(5.0)
                    try:
                        with context.wrap_socket(raw, server_side=True) as tls:
                            if stopped.is_set():
                                return
                            serve_management_connection(
                                tls,
                                trusted_credentials,
                                on_open_session,
                                on_close_connection,
                                enable_session=on_enable_session,
                                issue_control_window=on_issue_control_window,
                                emergency_stop=on_emergency_stop,
                                reset_control=on_reset_control,
                            )
                    except CancelledError:
                        return
                    except (OSError, ValueError) as error:
                        if not stopped.is_set():
                            print(f"Management connection ended: {error}", flush=True)

        worker = Thread(target=run_worker, name="management-tls")
        worker.start()
        try:
            print(f"Management TLS listening on {listener.getsockname()}", flush=True)
            yield process_one
        finally:
            with admission:
                stopped.set()
                try:
                    _, future = pending.get_nowait()
                except Empty:
                    pass
                else:
                    future.cancel()
            worker.join()
