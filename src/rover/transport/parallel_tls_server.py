"""Accept a bounded number of independent TLS connections."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import logging
from math import isfinite
from pathlib import Path
import socket
import ssl
from threading import Event, Lock, Thread, current_thread


LOGGER = logging.getLogger(__name__)


@contextmanager
def parallel_tls_server(
    cert_dir: Path,
    address: tuple[str, int],
    handle_connection: Callable[[ssl.SSLSocket], None],
    *,
    max_connections: int = 2,
    io_timeout_s: float = 5.0,
) -> Iterator[tuple[str, int]]:
    """Yield the bound address; reject additional connections when all slots are busy.

    Closing the context interrupts socket I/O and waits for handlers to return.
    Handlers that wait for work outside the socket must provide their own wakeup.
    """
    if type(max_connections) is not int or max_connections < 1:
        raise ValueError("max_connections must be a positive integer")
    if not isfinite(io_timeout_s) or io_timeout_s <= 0:
        raise ValueError("io_timeout_s must be positive and finite")

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_dir / "server.pem", cert_dir / "server.key")
    stopping = Event()
    lock = Lock()
    active: dict[Thread, socket.socket] = {}

    def report_connection_end(message: str) -> None:
        if not stopping.is_set():
            LOGGER.warning(message)

    def serve(raw: socket.socket) -> None:
        worker = current_thread()
        try:
            with lock:
                if stopping.is_set():
                    return
                raw.settimeout(io_timeout_s)
                # Wrapping transfers descriptor ownership. Track the TLS socket before
                # the potentially blocking handshake so shutdown can interrupt it.
                tls = context.wrap_socket(raw, server_side=True, do_handshake_on_connect=False)
                active[worker] = tls
            tls.do_handshake()
            if not stopping.is_set():
                handle_connection(tls)
        except TimeoutError:
            report_connection_end("TLS management connection timed out")
        except ValueError:
            report_connection_end("TLS management connection ended after an invalid message")
        except OSError:
            report_connection_end("TLS management connection ended after an I/O error")
        finally:
            with lock:
                active.pop(worker).close()
                raw.close()

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(address)
        listener.listen(max_connections)
        listener.settimeout(0.1)

        def accept_connections() -> None:
            while not stopping.is_set():
                try:
                    raw, _ = listener.accept()
                except TimeoutError:
                    continue
                except OSError:
                    if stopping.is_set():
                        return
                    raise

                with lock:
                    if stopping.is_set() or len(active) >= max_connections:
                        raw.close()
                        continue
                    worker = Thread(target=serve, args=(raw,), name="management-tls-client")
                    active[worker] = raw
                    try:
                        worker.start()
                    except RuntimeError:
                        active.pop(worker).close()
                        raise

        acceptor = Thread(target=accept_connections, name="management-tls-accept")
        acceptor.start()
        try:
            yield listener.getsockname()
        finally:
            stopping.set()
            listener.close()
            acceptor.join()
            with lock:
                clients = tuple(active.items())
                for _, client in clients:
                    try:
                        # Wake the I/O without clearing SSLSocket's TLS state
                        # on this thread while its worker may still be using it.
                        socket.socket.shutdown(client, socket.SHUT_RDWR)
                    except OSError:
                        pass
            for worker, _ in clients:
                worker.join()
