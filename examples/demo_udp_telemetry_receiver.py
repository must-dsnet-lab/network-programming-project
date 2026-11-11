import select
import socket
from time import monotonic

from rover.transport.udp_transport import try_receive_telemetry


def main() -> None:
    address = ("127.0.0.1", 10000)
    next_print_at_s = 0.0
    previous_fault = None

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(address)
        sock.setblocking(False)
        print(f"waiting for telemetry on {address}; Ctrl+C to exit", flush=True)

        try:
            while True:
                ready, _, _ = select.select([sock], [], [], 0.2)
                if not ready:
                    continue

                try:
                    received = try_receive_telemetry(sock)
                except ValueError as exc:
                    print(f"rejected telemetry: {exc}", flush=True)
                    continue

                if received is None:
                    continue

                snapshot, peer = received
                now_s = monotonic()

                if now_s >= next_print_at_s or snapshot.fault_latched != previous_fault:
                    state = snapshot.state
                    print(
                        f"telemetry from {peer}: "
                        f"x={state.x_m:.4f}m y={state.y_m:.4f}m "
                        f"heading={state.heading_rad:.3f}rad "
                        f"speed={state.speed_mps:.1f}m/s "
                        f"fault_latched={snapshot.fault_latched}",
                        flush=True,
                    )
                    next_print_at_s = now_s + 0.25

                previous_fault = snapshot.fault_latched

        except KeyboardInterrupt:
            print("\ntelemetry receiver stopped")


if __name__ == "__main__":
    main()
