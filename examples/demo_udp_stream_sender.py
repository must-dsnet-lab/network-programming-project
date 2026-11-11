import socket
from time import sleep

from rover.core.control_target import ControlTarget
from rover.transport.udp_transport import send_target


def main() -> None:
    destination = ("127.0.0.1", 9999)
    target = ControlTarget(speed_mps=2.0, heading_rad=0.5)
    count = 20
    interval_s = 0.1

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        print(
            f"stream from {sock.getsockname()} to {destination}",
            flush=True,  # 立刻把文字推送到屏幕上，避免在缓冲区里憋着
        )

        for index in range(count):
            sent = send_target(sock, target, destination)
            print(f"sent {index + 1}/{count}: {sent} bytes", flush=True)

            if index + 1 < count:
                sleep(interval_s)

    print("sender finished")


if __name__ == "__main__":
    main()
