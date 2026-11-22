import argparse
import ssl
from contextlib import ExitStack
from functools import partial
from getpass import getpass
from pathlib import Path
from time import monotonic, sleep

from rover.desktop import secure_connection


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check two simultaneous expert TLS management connections."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19177)
    parser.add_argument("--server-name", default="localhost")
    parser.add_argument("--ca-file", type=Path, default=Path("local/certs/ca.pem"))
    parser.add_argument("--client-id", default="desktop-a")
    parser.add_argument("--client-id-b", help="second account, defaults to --client-id")
    args = parser.parse_args(argv)

    connect = partial(
        secure_connection.connect_expert_management,
        (args.host, args.port),
        ca_file=args.ca_file,
        server_hostname=args.server_name,
    )

    try:
        client_id_b = args.client_id_b or args.client_id
        credential_a = getpass("Credential A (hidden): ")
        credential_b = credential_a
        if client_id_b != args.client_id:
            credential_b = getpass("Credential B (hidden): ")

        with ExitStack() as connection_a:
            client_a = connection_a.enter_context(
                connect(client_id=args.client_id, credential=credential_a)
            )
            session_a = client_a.open_session()
            with connect(client_id=client_id_b, credential=credential_b) as client_b:
                session_b = client_b.open_session()
                if session_a == session_b:
                    raise ValueError("the two connections must have different session IDs")

                result_a = client_a.query_authority(session_a)
                result_b = client_b.query_authority(session_b)
                if (result_a["online_sessions"], result_b["online_sessions"]) != (2, 2):
                    raise ValueError("both connections must report two online sessions")
                print("P2 online sessions: A=2 B=2, distinct session IDs", flush=True)

                connection_a.close()
                deadline = monotonic() + 3.0
                while client_b.query_authority(session_b)["online_sessions"] != 1:
                    if monotonic() >= deadline:
                        raise TimeoutError("timed out waiting for A to disconnect")
                    sleep(0.05)

                if client_b.open_session() != session_b:
                    raise ValueError("B session changed after A disconnected")
                print("P2 A closed: B=1, B session unchanged", flush=True)

    except ssl.SSLCertVerificationError as error:
        print("REJECT certificate:", error.verify_message)
        return 1
    except PermissionError:
        print("REJECT credential")
        return 1
    except (EOFError, OSError, ValueError) as error:
        print("FAILED:", error)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
