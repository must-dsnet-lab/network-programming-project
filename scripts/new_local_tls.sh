#!/usr/bin/env bash
# 生成实验 CA → 签发服务器证书 → 校验证书链、名称与有效期
# 与 scripts/new_local_tls.ps1 等价的 macOS / Linux 版本
#
# 用法:
#   ./scripts/new_local_tls.sh                          # 输出到 local/certs，只含 127.0.0.1
#   ./scripts/new_local_tls.sh -o local/certs-vm        # 指定输出目录
#   ./scripts/new_local_tls.sh -o local/certs-vm -i 192.168.1.20   # 追加一个局域网 IPv4
#
# 默认有效期与 PowerShell 版一致：CA 365 天，服务器证书 90 天。
# 目录中已有 TLS 材料时拒绝覆盖（本脚本只负责首次准备，不做续期）。
# 材料先在临时目录生成并自检，全部通过后才落到输出目录；中途失败不会留下半套材料。
#
# 依赖 OpenSSL 3.0 或更高（用到 3.x 的 -noenc 参数，以及 1.1.1+ 的 -verify_ip）。

set -euo pipefail

OPENSSL="${OPENSSL:-openssl}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="$SCRIPT_DIR/../local/certs"
SERVER_IP=""
WORK_DIR=""

usage() {
    sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

fail() {
    echo "$1" >&2
    exit 1
}

while getopts ":ho:i:" opt; do
    case "$opt" in
        o) OUT_DIR="$OPTARG" ;;
        i) SERVER_IP="$OPTARG" ;;
        h) usage 0 ;;
        *) echo "未知参数: -$OPTARG" >&2; usage 1 ;;
    esac
done

# ---- 前置检查：都在写任何文件之前完成 ----

command -v "$OPENSSL" >/dev/null 2>&1 || fail "找不到 OpenSSL: $OPENSSL"

OPENSSL_VERSION="$("$OPENSSL" version 2>/dev/null || true)"
case "$OPENSSL_VERSION" in
    LibreSSL*)
        fail "需要 OpenSSL 3.0 或更高，当前是 LibreSSL: $OPENSSL_VERSION
macOS 自带的 /usr/bin/openssl 就是 LibreSSL。先安装 OpenSSL 3（例如 brew install openssl@3），
再用 OPENSSL=/opt/homebrew/opt/openssl@3/bin/openssl 重跑，或把它放进 PATH 靠前的位置。"
        ;;
    "OpenSSL "*)
        OPENSSL_MAJOR="${OPENSSL_VERSION#OpenSSL }"
        OPENSSL_MAJOR="${OPENSSL_MAJOR%%.*}"
        if ! printf '%s' "$OPENSSL_MAJOR" | grep -Eq '^[0-9]+$' || [ "$OPENSSL_MAJOR" -lt 3 ]; then
            fail "需要 OpenSSL 3.0 或更高，当前版本: $OPENSSL_VERSION"
        fi
        ;;
    *)
        fail "无法识别 OpenSSL 版本输出: ${OPENSSL_VERSION:-<空>}"
        ;;
esac

# IPv4 校验：四段十进制、每段 0-255、不带前导零（带前导零在部分工具里会被当作八进制）
is_ipv4() {
    local ip="$1" octet
    printf '%s' "$ip" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$' || return 1
    local IFS=.
    for octet in $ip; do
        if [ "${#octet}" -gt 1 ] && [ "${octet#0}" != "$octet" ]; then
            return 1
        fi
        [ "$octet" -le 255 ] || return 1
    done
    return 0
}

# 服务器证书里的 IP 列表：回环始终在内，另可追加一个具体 IPv4
SERVER_IPS=("127.0.0.1")
if [ -n "$SERVER_IP" ]; then
    if ! is_ipv4 "$SERVER_IP"; then
        fail "ServerIp 必须是具体 IPv4 地址（四段 0-255、不带前导零），例如 192.168.1.20
收到: $SERVER_IP"
    fi
    case "$SERVER_IP" in
        0.0.0.0) fail "ServerIp 不能是 0.0.0.0，它不是可连接的具体地址" ;;
        255.255.255.255) fail "ServerIp 不能是广播地址 255.255.255.255" ;;
    esac
    if [ "$SERVER_IP" != "127.0.0.1" ]; then
        SERVER_IPS+=("$SERVER_IP")
    fi
fi

SAN="DNS:localhost"
for ip in "${SERVER_IPS[@]}"; do
    SAN="$SAN,IP:$ip"
done

# 相对路径按调用者的当前目录解析，避免 cd 到脚本目录后输出位置错乱
PARENT_DIR="$(dirname "$OUT_DIR")"
mkdir -p "$PARENT_DIR"
PARENT_DIR="$(cd "$PARENT_DIR" && pwd)"
OUT_DIR="$PARENT_DIR/$(basename "$OUT_DIR")"

if [ -e "$OUT_DIR" ] && [ ! -d "$OUT_DIR" ]; then
    fail "输出路径已存在且不是目录: $OUT_DIR"
fi

MATERIALS=(ca.key ca.pem ca.srl server.key server.csr server.pem tls.cnf)
for name in "${MATERIALS[@]}"; do
    if [ -e "$OUT_DIR/$name" ]; then
        fail "TLS 材料已存在，请换一个输出目录: $OUT_DIR"
    fi
done

# ---- 在临时目录里生成，全部通过后再落地 ----

WORK_DIR="$(mktemp -d "$PARENT_DIR/.tls-build.XXXXXX")"
cleanup() {
    if [ -n "${WORK_DIR:-}" ] && [ -d "$WORK_DIR" ]; then
        rm -rf "$WORK_DIR"
    fi
}
trap cleanup EXIT

TLS_CNF="$WORK_DIR/tls.cnf"
CA_KEY="$WORK_DIR/ca.key"
CA_CERT="$WORK_DIR/ca.pem"
SERVER_KEY="$WORK_DIR/server.key"
SERVER_CSR="$WORK_DIR/server.csr"
SERVER_CERT="$WORK_DIR/server.pem"

cat >"$TLS_CNF" <<EOF
[req]
distinguished_name = dn

[dn]

[ca_ext]
basicConstraints = critical,CA:TRUE
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash

[server_ext]
basicConstraints = critical,CA:FALSE
subjectAltName = $SAN
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
EOF

run_openssl() {
    "$OPENSSL" "$@" || fail "OpenSSL 命令失败: $OPENSSL $*"
}

run_openssl req -x509 -newkey rsa:2048 -noenc -sha256 \
    -keyout "$CA_KEY" -out "$CA_CERT" -days 365 \
    -subj "/CN=Rover Local Lab CA" -config "$TLS_CNF" -extensions ca_ext

run_openssl req -new -newkey rsa:2048 -noenc -sha256 \
    -keyout "$SERVER_KEY" -out "$SERVER_CSR" \
    -subj "/CN=localhost" -config "$TLS_CNF"

run_openssl x509 -req -in "$SERVER_CSR" -CA "$CA_CERT" -CAkey "$CA_KEY" \
    -CAcreateserial -out "$SERVER_CERT" -days 90 -sha256 \
    -extfile "$TLS_CNF" -extensions server_ext

# ---- 自检：链、用途、名称、每个 SAN 里的 IP 都要过 ----

run_openssl verify -CAfile "$CA_CERT" -purpose sslserver \
    -verify_hostname localhost "$SERVER_CERT"

for ip in "${SERVER_IPS[@]}"; do
    run_openssl verify -CAfile "$CA_CERT" -purpose sslserver \
        -verify_ip "$ip" "$SERVER_CERT"
done

chmod 600 "$CA_KEY" "$SERVER_KEY"

# ---- 自检全过，才把材料落到输出目录 ----

if [ -d "$OUT_DIR" ]; then
    mv "$WORK_DIR"/* "$OUT_DIR"/
    rmdir "$WORK_DIR"
else
    mv "$WORK_DIR" "$OUT_DIR"
fi
WORK_DIR=""

"$OPENSSL" x509 -in "$OUT_DIR/server.pem" -noout -dates -ext subjectAltName

echo "TLS files: $OUT_DIR"
echo "已生成的私钥权限为 600（仅本人可读）。"
echo "接下来创建 $OUT_DIR/../trusted_clients.json，内容形如 {\"desktop-a\": \"test-secret-a\"}"
echo "桌面端只需要 $OUT_DIR/ca.pem，不要复制 ca.key / server.key。"
