import argparse
from getpass import getpass
from pathlib import Path
import socket
import ssl
from time import monotonic, sleep

from examples.demo_expert_handoff import _require_result
from rover.core.control_target import ControlTarget
from rover.desktop import secure_connection
from rover.protocol.control_codec import AuthorityGeneration, ControlCommand
from rover.transport.udp_transport import send_expert_control_command, try_receive_telemetry


SEND_PERIOD_S = 0.05
OBSERVATION_TIMEOUT_S = 2.0


class _MotionPublisher:
    def __init__(self, client, session_id, generation, control, address):
        enabled = client.set_authority_enabled(session_id, generation, True)
        _require_result(enabled, "explicit enable")
        self.client = client
        self.session_id = session_id
        self.generation = generation
        self.command_generation = AuthorityGeneration(**generation)
        self.control = control
        self.address = address
        self.key = bytes.fromhex(enabled["control_key"])
        self.seq = 0
        self.window_id = None
        self.window_until = 0.0

    def send(self) -> bool:
        now = monotonic()
        if self.window_id is None or now + SEND_PERIOD_S >= self.window_until:
            window = self.client.issue_control_window(self.session_id, self.generation)
            if not window["accepted"] and window["reason"] in {
                "lease_expired", "vehicle_stopping", "not_authority_holder"
            }:
                return False
            _require_result(window, "window request")
            self.window_id = window["window_id"]
            self.window_until = now + window["lifetime_s"]
            if monotonic() >= self.window_until:
                raise TimeoutError("control window expired before publishing")

        command = ControlCommand(
            self.session_id, self.seq, ControlTarget(1.0, 0.0), self.window_id,
            generation=self.command_generation,
        )
        send_expert_control_command(self.control, command, self.address, key=self.key)
        self.seq += 1
        return True


def _latest_telemetry(telemetry, vehicle_ip):
    latest = None
    for _ in range(8):
        try:
            received = try_receive_telemetry(telemetry)
        except ValueError:
            continue
        if received is None:
            break
        snapshot, peer = received
        if peer[0] == vehicle_ip:
            latest = snapshot
    return latest


def _wait_for_stop(telemetry, vehicle_ip, *, expect_fault: bool) -> None:
    deadline = monotonic() + OBSERVATION_TIMEOUT_S
    while monotonic() < deadline:
        snapshot = _latest_telemetry(telemetry, vehicle_ip)
        if snapshot is not None:
            if snapshot.fault_latched and not expect_fault:
                raise ValueError("UDP watchdog latched before a clean lease stop")
            if snapshot.state.speed_mps == 0.0 and snapshot.fault_latched is expect_fault:
                return
        sleep(SEND_PERIOD_S)
    raise TimeoutError(f"no stopped telemetry with fault_latched={expect_fault}")


def _run_lease_case(client, session_id, control, telemetry, address):
    requested_at = monotonic()
    acquired = client.acquire_authority(session_id)
    _require_result(acquired, "acquire timed authority")
    lease_s = acquired.get("lease_remaining_s")
    if lease_s is None:
        raise ValueError("vehicle does not report a lease on acquisition")
    original_deadline = monotonic() + lease_s
    renew_at = requested_at + lease_s / 2
    observe_until = original_deadline + OBSERVATION_TIMEOUT_S
    generation = acquired["generation"]
    publisher = _MotionPublisher(client, session_id, generation, control, address)
    renewed = False
    beyond_original_deadline = False
    moved = False
    while (now := monotonic()) < observe_until:
        snapshot = _latest_telemetry(telemetry, address[0])
        if snapshot is not None and snapshot.fault_latched:
            raise ValueError("vehicle faulted while publishing valid control")
        moving = snapshot is not None and snapshot.state.speed_mps == 1.0
        moved = moved or moving
        if not renewed and now >= renew_at:
            if not moved:
                raise ValueError("vehicle never moved before renewal")
            renewal = client.renew_authority(session_id, generation)
            _require_result(renewal, "renew authority before expiry")
            renewed = True
            observe_until = monotonic() + renewal["lease_remaining_s"] + OBSERVATION_TIMEOUT_S
        if renewed and not beyond_original_deadline and now >= original_deadline and moving:
            status = client.query_authority(session_id)
            if status["owns_authority"] is not True or status["phase"] != "controlling":
                raise ValueError("renewed authority did not survive the original deadline")
            beyond_original_deadline = True
            print("P5 lease renewed; motion continued past the original deadline", flush=True)
        if not publisher.send():
            if not beyond_original_deadline:
                raise ValueError("control was revoked before proving renewal")
            break
        sleep(SEND_PERIOD_S)
    else:
        raise TimeoutError("vehicle did not revoke control after lease expiry")

    _wait_for_stop(telemetry, address[0], expect_fault=False)
    status = client.query_authority(session_id)
    if status["phase"] != "idle" or status["owns_authority"] is not False:
        raise ValueError("vehicle did not become idle after lease expiry")
    print("P5 lease expired; speed=0.0 m/s, fault_latched=False, TLS session unchanged", flush=True)

    late = client.renew_authority(session_id, generation)
    _require_result(late, "late renewal", reason="lease_expired")
    fresh = client.acquire_authority(session_id)
    _require_result(fresh, "reacquire after expiry")
    if fresh["phase"] != "held" or fresh["generation"] == generation:
        raise ValueError("reacquisition did not grant a fresh, disabled generation")
    released = client.release_authority(session_id, fresh["generation"])
    _require_result(released, "release fresh authority")
    print("P5 late renewal rejected (lease_expired); reacquired a new generation", flush=True)


def _run_watchdog_case(client, session_id, control, telemetry, address):
    acquired = client.acquire_authority(session_id)
    _require_result(acquired, "acquire authority for watchdog check")
    generation = acquired["generation"]
    publisher = _MotionPublisher(client, session_id, generation, control, address)
    deadline = monotonic() + OBSERVATION_TIMEOUT_S
    while monotonic() < deadline:
        if not publisher.send():
            raise ValueError("control was revoked before watchdog check")
        snapshot = _latest_telemetry(telemetry, address[0])
        if snapshot is not None and snapshot.state.speed_mps == 1.0 and not snapshot.fault_latched:
            break
        sleep(SEND_PERIOD_S)
    else:
        raise TimeoutError("vehicle did not move before watchdog check")

    requested_at = monotonic()
    renewed = client.renew_authority(session_id, generation)
    _require_result(renewed, "renew while preparing the watchdog check")
    _wait_for_stop(telemetry, address[0], expect_fault=True)
    if monotonic() >= requested_at + renewed["lease_remaining_s"]:
        raise ValueError("watchdog stop was not observed before the renewed lease deadline")
    status = client.query_authority(session_id)
    if status["phase"] != "fault_latched" or status["owns_authority"] is not False:
        raise ValueError("watchdog did not revoke authority and latch a fault")
    print("P5 UDP watchdog; speed=0.0 m/s, fault_latched=True before lease deadline, TLS session unchanged", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check expert authority lease and UDP watchdog on a simulated vehicle.")
    parser.add_argument("--scenario", choices=("both", "lease", "watchdog"), default="both")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19177, help="vehicle TLS management port")
    parser.add_argument("--control-port", type=int, default=9999)
    parser.add_argument("--telemetry-bind-host", default="127.0.0.1")
    parser.add_argument("--telemetry-port", type=int, default=10000)
    parser.add_argument("--server-name", default="localhost")
    parser.add_argument("--ca-file", type=Path, default=Path("local/certs/ca.pem"))
    parser.add_argument("--client-id", default="desktop-a")
    args = parser.parse_args(argv)

    try:
        credential = getpass("Credential (hidden): ")
        address = (socket.gethostbyname(args.host), args.control_port)
        with (
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as telemetry,
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as control,
        ):
            telemetry.bind((args.telemetry_bind_host, args.telemetry_port))
            telemetry.setblocking(False)
            with secure_connection.connect_expert_management(
                (args.host, args.port), ca_file=args.ca_file, server_hostname=args.server_name,
                client_id=args.client_id, credential=credential,
            ) as client:
                session_id = client.open_session()
                if args.scenario in {"both", "lease"}:
                    _run_lease_case(client, session_id, control, telemetry, address)
                if args.scenario in {"both", "watchdog"}:
                    _run_watchdog_case(client, session_id, control, telemetry, address)
    except ssl.SSLCertVerificationError as error:
        print("REJECT certificate:", error.verify_message)
        return 1
    except (EOFError, OSError, ValueError) as error:
        print("FAILED:", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
