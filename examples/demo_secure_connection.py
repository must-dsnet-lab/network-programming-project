import argparse
import ssl
from getpass import getpass
from pathlib import Path

from rover.desktop.secure_connection import connect_management


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check a secure management connection."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19177)
    parser.add_argument("--server-name", default="localhost")
    parser.add_argument("--ca-file", type=Path, default=Path("local/certs/ca.pem"))
    parser.add_argument("--client-id", default="desktop-a")
    args = parser.parse_args()

    try:
        credential = getpass("Credential (hidden): ")
        with connect_management(
            (args.host, args.port),
            ca_file=args.ca_file,
            server_hostname=args.server_name,
            client_id=args.client_id,
            credential=credential,
        ) as client:
            print("Client: authenticated", flush=True)

            first = client.open_session()
            second = client.open_session()

            print("First session:", first)
            print("Repeated session:", second)

            if first.session_id != second.session_id:
                raise ValueError("same connection returned different session IDs")
            print("Client: session reused")

    except ssl.SSLCertVerificationError as error:
        print("REJECT certificate:", error.verify_message)
        return 1
    except PermissionError:
        print("REJECT credential")
        return 1
    except (OSError, ValueError) as error:
        print("FAILED:", error)
        return 1

    print("Client: connection closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
