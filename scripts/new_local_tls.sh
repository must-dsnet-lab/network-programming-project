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

set -euo pipefail

OPENSSL="${OPENSSL:-openssl}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="$SCRIPT_DIR/../local/certs"
SERVER_IP=""

usage() {
    sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while getopts ":ho:i:" opt; do
    case "$opt" in
        o) OUT_DIR="$OPTARG" ;;
        i) SERVER_IP="$OPTARG" ;;
        h) usage 0 ;;
        *) echo "未知参数: -$OPTARG" >&2; usage 1 ;;
    esac
done

command -v "$OPENSSL" >/dev/null 2>&1 || {
    echo "找不到 OpenSSL: $OPENSSL" >&2
    exit 1
}

# 服务器证书里的 IP 列表：回环始终在内，另可追加一个具体 IPv4
SERVER_IPS=("127.0.0.1")
if [ -n "$SERVER_IP" ]; then
    if ! printf '%s' "$SERVER_IP" | grep -Eq '^([0-9]{1,3}\.){3}[0-9]{1,3}$'; then
        echo "ServerIp 必须是具体 IPv4 地址，例如 192.168.1.20" >&2
        exit 1
    fi
    if [ "$SERVER_IP" = "0.0.0.0" ]; then
        echo "ServerIp 不能是 0.0.0.0" >&2
        exit 1
    fi
    if [ "$SERVER_IP" != "127.0.0.1" ]; then
        SERVER_IPS+=("$SERVER_IP")
    fi
fi

SAN="DNS:localhost"
for ip in "${SERVER_IPS[@]}"; do
    SAN="$SAN,IP:$ip"
done

# 相对路径按调用者的当前目录解析，避免 cd 到脚本目录后输出位置错乱
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"

for name in ca.key ca.pem ca.srl server.key server.csr server.pem tls.cnf; do
    if [ -e "$OUT_DIR/$name" ]; then
        echo "TLS 材料已存在，请换一个输出目录: $OUT_DIR" >&2
        exit 1
    fi
done

TLS_CNF="$OUT_DIR/tls.cnf"
CA_KEY="$OUT_DIR/ca.key"
CA_CERT="$OUT_DIR/ca.pem"
SERVER_KEY="$OUT_DIR/server.key"
SERVER_CSR="$OUT_DIR/server.csr"
SERVER_CERT="$OUT_DIR/server.pem"

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
    "$OPENSSL" "$@"
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

run_openssl verify -CAfile "$CA_CERT" -purpose sslserver \
    -verify_hostname localhost "$SERVER_CERT"

for ip in "${SERVER_IPS[@]}"; do
    run_openssl verify -CAfile "$CA_CERT" -purpose sslserver \
        -verify_ip "$ip" "$SERVER_CERT"
done

# 只保留车端启动需要读的文件，中间产物（CSR、CA 序号、配置）一并留下便于复核
run_openssl x509 -in "$SERVER_CERT" -noout -dates -ext subjectAltName

echo "TLS files: $OUT_DIR"
echo "接下来创建 $OUT_DIR/../trusted_clients.json，内容形如 {\"desktop-a\": \"test-secret-a\"}"
