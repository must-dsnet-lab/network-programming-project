import socket

from rover.core.control_target import ControlTarget
from rover.transport.udp_transport import send_target


def main() -> None:
    destination = ("127.0.0.1", 9999)
    target = ControlTarget(speed_mps=2.0, heading_rad=0.5)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sent = send_target(sock, target, destination)
        print(f"sent {sent} bytes from {sock.getsockname()} to {destination}")


if __name__ == "__main__":
    main()
