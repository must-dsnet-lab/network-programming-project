"""Entry point for the Windows desktop bundle."""

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] != ["--vehicle"]:
        from rover.desktop.app import main as desktop_main

        return desktop_main(args or ["--launcher"])

    # The bundled child only runs the local simulation. The full vehicle
    # service remains available through the repository's vehicle CLI.
    parser = argparse.ArgumentParser(description="Run the local rover simulation.", allow_abbrev=False)
    parser.add_argument("--bind-host", choices=["127.0.0.1"], required=True)
    parser.add_argument("--telemetry-host", choices=["127.0.0.1"], required=True)
    parser.add_argument("--telemetry-port", type=int, required=True)
    parser.add_argument("--auto-control-port", action="store_true", required=True)
    local = parser.parse_args(args[1:])
    if not 1 <= local.telemetry_port <= 65535:
        parser.error("--telemetry-port must be between 1 and 65535")

    from rover.vehicle.app import cli as vehicle_cli

    vehicle_cli([
        "--bind-host", local.bind_host,
        "--telemetry-host", local.telemetry_host,
        "--telemetry-port", str(local.telemetry_port),
        "--auto-control-port",
    ])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
