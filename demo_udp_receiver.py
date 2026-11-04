import socket

from udp_transport import receive_target


def main() -> None:
    address = ("127.0.0.1", 9999)

    # with 会在离开代码块时关闭 Socket
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(address)
        print(f"waiting for UDP on {address}", flush=True)

        try:
            target, peer = receive_target(sock)
        except ValueError as exc:
            print(f"rejected: {exc}")
            return

        print(f"received from {peer}")
        print(target)


if __name__ == "__main__":
    main()
