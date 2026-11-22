import re


_MAGIC = b"RVTR"
_HEADER = _MAGIC + b"\x01"
REGISTRATION_TOKEN_BYTES = 32
REGISTRATION_DATAGRAM_SIZE = len(_HEADER) + REGISTRATION_TOKEN_BYTES
_TOKEN = re.compile(rf"[0-9a-f]{{{2 * REGISTRATION_TOKEN_BYTES}}}")


def is_telemetry_registration(packet: bytes) -> bool:
    """Recognize the packet family so malformed probes stay out of control handling."""
    return packet.startswith(_MAGIC)


def encode_telemetry_registration(token: str) -> bytes:
    if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
        raise ValueError("invalid telemetry registration token")
    return _HEADER + bytes.fromhex(token)


def decode_telemetry_registration(packet: bytes) -> str:
    if len(packet) != REGISTRATION_DATAGRAM_SIZE or not packet.startswith(_HEADER):
        raise ValueError("invalid telemetry registration datagram")
    return packet[len(_HEADER):].hex()
