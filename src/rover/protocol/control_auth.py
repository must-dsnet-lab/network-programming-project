import hmac


def compute_control_mac(payload: bytes, key: bytes) -> str:
    if not key:
        raise ValueError("HMAC key must not be empty")

    return hmac.new(
        key,  # 密钥
        payload,  # 报文字节
        digestmod="sha256",  # 使用的算法
    ).hexdigest()  # HMAC-SHA256 的结果是 64 个十六进制字符


def verify_control_mac(payload: bytes, key: bytes, received_mac: str) -> bool:
    expected_mac = compute_control_mac(payload, key)

    if not isinstance(received_mac, str):
        return False
    if len(received_mac) != 64 or not received_mac.isascii():
        return False

    return hmac.compare_digest(expected_mac, received_mac)
