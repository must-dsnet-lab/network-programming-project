import argparse
import socket
import ssl
from functools import partial
from getpass import getpass
from pathlib import Path
from time import monotonic, sleep

from rover.core.control_target import ControlTarget
from rover.desktop import secure_connection
from rover.desktop.expert_management_client import ExpertManagementClient
from rover.protocol.control_codec import AuthorityGeneration, ControlCommand
from rover.protocol.telemetry_codec import MAX_TELEMETRY_MESSAGE_BYTES, decode_telemetry
from rover.transport.udp_transport import send_expert_control_command


def _require_result(reply: dict, action: str, *, reason: str | None = None) -> None:
    if reply["accepted"] is not (reason is None) or reply.get("reason") != reason:
        raise ValueError(f"unexpected result for {action}: {reply.get('reason')}")


def _send_motion(
    client: ExpertManagementClient,
    session_id: str,
    generation: dict,
    sock: socket.socket,
    address: tuple[str, int],
) -> None:
    enabled = client.set_authority_enabled(session_id, generation, True)
    _require_result(enabled, "explicit enable")
    key = bytes.fromhex(enabled["control_key"])

    requested_at = monotonic()
    window = client.issue_control_window(session_id, generation)
    _require_result(window, "window request")
    if monotonic() - requested_at >= window["lifetime_s"]:
        raise TimeoutError("control window expired before the motion command could be sent")

    command = ControlCommand(
        session_id=session_id,
        seq=0,
        target=ControlTarget(1.0, 0.0),
        window_id=window["window_id"],
        generation=AuthorityGeneration(epoch=generation["epoch"], number=generation["number"]),
    )
    send_expert_control_command(sock, command, address, key=key)


def _wait_for_speed(sock: socket.socket, vehicle_ip: str, speed_mps: float) -> None:
    deadline = monotonic() + 2.0
    while (remaining := deadline - monotonic()) > 0:
        sock.settimeout(remaining)
        try:
            packet, peer = sock.recvfrom(MAX_TELEMETRY_MESSAGE_BYTES + 1)
        except TimeoutError:
            break
        if peer[0] != vehicle_ip:
            continue
        try:
            telemetry = decode_telemetry(packet)
        except ValueError:
            continue
        if telemetry.fault_latched:
            raise ValueError("vehicle telemetry reported a latched fault")
        if telemetry.state.speed_mps == speed_mps:
            return
    raise TimeoutError(f"timed out waiting for telemetry speed={speed_mps:.1f} m/s")


def _wait_until_held(client: ExpertManagementClient, session_id: str) -> dict:
    deadline = monotonic() + 2.0
    while monotonic() < deadline:
        reply = client.query_authority(session_id)
        if reply["owns_authority"] is True and reply["phase"] == "held":
            return reply["generation"]
        if reply["phase"] == "fault_latched":
            raise ValueError("a latched fault prevented authority transfer")
        sleep(0.02)
    raise TimeoutError("timed out waiting for B to hold authority")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check an expert TLS handoff with UDP v3 control.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19177, help="vehicle TLS management port")
    parser.add_argument("--control-port", type=int, default=9999)
    parser.add_argument("--telemetry-bind-host", default="127.0.0.1")
    parser.add_argument("--telemetry-port", type=int, default=10000)
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

        vehicle_ip = socket.gethostbyname(args.host)
        address = (vehicle_ip, args.control_port)
        with (
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as telemetry,
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as control,
        ):
            telemetry.bind((args.telemetry_bind_host, args.telemetry_port))
            with (
                connect(client_id=args.client_id, credential=credential_a) as client_a,
                connect(client_id=client_id_b, credential=credential_b) as client_b,
            ):
                session_a = client_a.open_session()
                session_b = client_b.open_session()
                if session_a == session_b:
                    raise ValueError("A and B must have distinct sessions")

                acquired = client_a.acquire_authority(session_a)
                _require_result(acquired, "A acquire authority")
                generation_a = acquired["generation"]
                competing = client_b.acquire_authority(session_b)
                _require_result(competing, "competing request from B", reason="authority_busy")
                print("P4 A holds authority; B's competing request was rejected", flush=True)

                _send_motion(client_a, session_a, generation_a, control, address)
                _wait_for_speed(telemetry, vehicle_ip, 1.0)
                transfer = client_a.transfer_authority(session_a, generation_a, session_b)
                _require_result(transfer, "A transfer to B")
                print("P4 A observed speed=1.0 m/s; transfer accepted", flush=True)

                generation_b = _wait_until_held(client_b, session_b)
                if generation_a == generation_b:
                    raise ValueError("B needs a new authority generation")
                _wait_for_speed(telemetry, vehicle_ip, 0.0)
                old_window = client_a.issue_control_window(session_a, generation_a)
                _require_result(old_window, "A window after transfer", reason="not_authority_holder")
                print("P4 speed=0.0 m/s; B holds a new generation with control disabled", flush=True)

                _send_motion(client_b, session_b, generation_b, control, address)
                _wait_for_speed(telemetry, vehicle_ip, 1.0)
                released = client_b.release_authority(session_b, generation_b)
                _require_result(released, "B release authority")
                print("P4 B explicitly enabled control, observed speed=1.0 m/s and released", flush=True)

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
