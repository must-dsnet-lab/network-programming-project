#生成实验 CA → 签发服务器证书 → 校验名称和有效期
param(
    [string]$OpenSsl = "openssl",
    [string]$OutputDirectory = "",
    [string]$ServerIp
)

$ErrorActionPreference = "Stop"
[void](Get-Command -Name $OpenSsl -CommandType Application -ErrorAction Stop)
$serverIps = @("127.0.0.1")
if ($PSBoundParameters.ContainsKey("ServerIp")) {
    $parsedIp = $null
    if (
        -not [System.Net.IPAddress]::TryParse($ServerIp, [ref]$parsedIp) -or
        $parsedIp.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork -or
        $parsedIp.ToString() -cne $ServerIp -or
        $ServerIp -eq "0.0.0.0"
    ) {
        throw "ServerIp must be a specific IPv4 address, such as 192.168.1.20."
    }

    if ($ServerIp -ne "127.0.0.1") {
        $serverIps += $ServerIp
    }
}
$subjectAltName = "DNS:localhost," + (($serverIps | ForEach-Object { "IP:$_" }) -join ",")
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $PSScriptRoot "..\local\certs"
}
$OutputDirectory = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputDirectory)

foreach ($name in "ca.key", "ca.pem", "ca.srl", "server.key", "server.csr", "server.pem", "tls.cnf") {
    if (Test-Path -LiteralPath (Join-Path $OutputDirectory $name)) {
        throw "TLS files already exist. Choose a new OutputDirectory: $OutputDirectory"
    }
}
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null

$config = Join-Path $OutputDirectory "tls.cnf"
$caKey = Join-Path $OutputDirectory "ca.key"
$caCert = Join-Path $OutputDirectory "ca.pem"
$serverKey = Join-Path $OutputDirectory "server.key"
$serverCsr = Join-Path $OutputDirectory "server.csr"
$serverCert = Join-Path $OutputDirectory "server.pem"

@"
[req]
distinguished_name = dn

[dn]

[ca_ext]
basicConstraints = critical,CA:TRUE
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash

[server_ext]
basicConstraints = critical,CA:FALSE
subjectAltName = $subjectAltName
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
"@ | Set-Content -LiteralPath $config -Encoding ascii

function Invoke-TlsOpenSsl {
    param([string[]]$Arguments)

    & $OpenSsl @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "OpenSSL failed with exit code $LASTEXITCODE"
    }
}

Invoke-TlsOpenSsl -Arguments @(
    "req", "-x509", "-newkey", "rsa:2048", "-noenc", "-sha256",
    "-keyout", $caKey, "-out", $caCert, "-days", "365",
    "-subj", "/CN=Rover Local Lab CA", "-config", $config, "-extensions", "ca_ext"
)
Invoke-TlsOpenSsl -Arguments @(
    "req", "-new", "-newkey", "rsa:2048", "-noenc", "-sha256",
    "-keyout", $serverKey, "-out", $serverCsr,
    "-subj", "/CN=localhost", "-config", $config
)
Invoke-TlsOpenSsl -Arguments @(
    "x509", "-req", "-in", $serverCsr, "-CA", $caCert, "-CAkey", $caKey,
    "-CAcreateserial", "-out", $serverCert, "-days", "90", "-sha256",
    "-extfile", $config, "-extensions", "server_ext"
)
Invoke-TlsOpenSsl -Arguments @(
    "verify", "-CAfile", $caCert, "-purpose", "sslserver",
    "-verify_hostname", "localhost", $serverCert
)
foreach ($ip in $serverIps) {
    Invoke-TlsOpenSsl -Arguments @(
        "verify", "-CAfile", $caCert, "-purpose", "sslserver",
        "-verify_ip", $ip, $serverCert
    )
}
Invoke-TlsOpenSsl -Arguments @(
    "x509", "-in", $serverCert, "-noout", "-dates", "-ext", "subjectAltName"
)
Write-Output ("TLS files: " + $OutputDirectory)