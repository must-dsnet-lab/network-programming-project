# 非 Windows 环境复现指南

**基线** `main` · `e4d1b59` · 对标 [README 第 7 章](../README.md) 的 Windows 双终端流程

README 的正式复现命令全部以 PowerShell 和 `.\.venv\Scripts\python.exe` 给出，这是 Windows 上的权威路径。本文件补齐 macOS 与 Linux 的等价路径，供协议层开发、编解码回归和跨系统联调查阅。Windows 上仍以 [README](../README.md) 为准，两者命令不同但验收条件相同。

## 1. 实测状态

| 环节 | macOS 26 / Apple M2 | Linux | Windows |
| --- | --- | --- | --- |
| 全量回归（无 GUI） | 实测 `Ran 365 tests` 通过，2 项因缺 PySide6 报错 | 未实测 | 387 项通过（README 记录） |
| 车端安全模式启动 | 实测通过，TLS 1.3 握手与证书链校验通过 | 未实测 | 已验收 |
| 证书准备 | 实测通过（[new_local_tls.sh](../scripts/new_local_tls.sh)） | 同一脚本 | 已验收（`.ps1`） |
| 桌面 GUI | 未实测（本机未装 PySide6） | 未实测 | 已验收 |

本表只记录实际跑过的命令与结果。未实测项在提交证据时标为「待补」，不并入已验收结论。

## 2. 一次性准备

### 2.1 Python 与依赖

要求 Python ≥ 3.12（`pyproject.toml`）。本机实测使用 conda Python 3.14.8，协议层未安装任何第三方包也能直接运行。

```bash
cd network-programming-project

# 只做协议层开发与回归：不需要安装，靠 PYTHONPATH 指向 src
PYTHONPATH=src python3 -B -m unittest discover -s tests -t . -q

# 需要 GUI 时再建虚拟环境
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

`requirements.txt` 只含 `PySide6==6.12.0`。PySide6 在 macOS 上只发布 `cp310-abi3` 的 `macosx_14_0_universal2` wheel（要求 macOS 14 以上）。abi3 对 3.12+ 的解释器前向兼容，但本机未实际安装验证，装不上时按 [2.4](#24-pySide6-装不上时) 处理。

### 2.2 证书

[new_local_tls.sh](../scripts/new_local_tls.sh) 与 PowerShell 版等价：生成实验 CA、签发服务器证书、校验证书链/名称/SAN/有效期，有效期为 CA 365 天、服务器证书 90 天。

```bash
# 本机回环：SAN = DNS:localhost, IP:127.0.0.1
bash scripts/new_local_tls.sh

# 双机联调：额外把一个局域网 IPv4 写进 SAN
bash scripts/new_local_tls.sh -o local/certs-lan -i 192.168.1.20
```

脚本在目录已存在 TLS 材料时拒绝覆盖，不做自动续期。默认输出 `local/certs`，与示例工具的默认 `--ca-file local/certs/ca.pem` 对齐。

### 2.3 可信客户端

`local/trusted_clients.json` 是客户端 ID 到凭据字符串的 JSON 映射：

```json
{
  "desktop-a": "test-secret-a"
}
```

`local/` 已被根目录 `.gitignore` 排除。提交前用 `git check-ignore -v local/certs/server.key` 复核私钥不在版本库里。

### 2.4 PySide6 装不上时

按顺序试：

1. 确认 macOS ≥ 14、Python ≥ 3.12；
2. 改用 Python 3.12 或 3.13 的独立环境（wheel 的 abi3 分支最稳）；
3. 跳过 GUI，只用车端与示例客户端验证协议：`python -m rover.vehicle.app` 不依赖 PySide6。

## 3. 三条验证路径

### 3.1 协议与回归（不需要 GUI）

```bash
PYTHONPATH=src python3 -B -m unittest discover -s tests -t . -q
```

专项测试按影响范围选取，例如只改管理协议时：

```bash
PYTHONPATH=src python3 -B -m unittest -v tests.test_management_codec tests.test_tcp_records \
  tests.test_tcp_transport tests.test_management_connection tests.test_control_codec \
  tests.test_windowed_control_codec tests.test_telemetry_codec
```

带 GUI 的测试在无显示器环境需要 `QT_QPA_PLATFORM=offscreen`：

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src python3 -B -m unittest discover -s tests -t . -q
```

### 3.2 本机单机闭环（车端 + 桌面）

两个终端都在项目根目录。车端：

```bash
PYTHONPATH=src python3 -B -m rover.vehicle.app \
  --secure --cert-dir ./local/certs \
  --credentials-file ./local/trusted_clients.json \
  --event-log ./local/events/$(date +%Y%m%d-%H%M%S).csv
```

桌面端（终端里启动，凭据通过 `getpass` 隐藏输入，不能在 IDE 的输出面板里等）：

```bash
PYTHONPATH=src python3 -B -m rover.desktop.app \
  --secure --vehicle-host 127.0.0.1 \
  --ca-file ./local/certs/ca.pem \
  --client-id desktop-a
```

只验证 TLS 与管理握手、不启 GUI 时，用示例客户端：

```bash
PYTHONPATH=src python3 -B -m examples.demo_secure_connection --ca-file ./local/certs/ca.pem
```

测管理 RTT 与重连计时：

```bash
PYTHONPATH=src python3 -B -m examples.demo_management_metrics \
  --ca-file ./local/certs/ca.pem --output ./local/a-tests/rtt-$(date +%H%M%S).csv
```

### 3.3 双机（Linux 车端 + macOS 桌面）

车端在 Linux 上绑定局域网地址，遥测指向桌面端：

```bash
PYTHONPATH=src python3 -B -m rover.vehicle.app \
  --secure --cert-dir ./local/certs-lan \
  --credentials-file ./local/trusted_clients.json \
  --bind-host 192.168.1.20 --telemetry-host 192.168.1.10
```

桌面端连过去，证书 SAN 必须包含 `192.168.1.20`，否则 TLS 校验失败：

```bash
PYTHONPATH=src python3 -B -m rover.desktop.app \
  --secure --vehicle-host 192.168.1.20 \
  --ca-file ./local/certs-lan/ca.pem --client-id desktop-a
```

端口默认：管理 `19177/TCP`、控制 `9999/UDP`、遥测 `10000/UDP`、观察者 `10001/UDP`。跨机时确认这两个 UDP 端口在两端防火墙放行。

## 4. 常见故障

| 症状 | 原因 | 处理 |
| --- | --- | --- |
| `ModuleNotFoundError: No module named 'rover'` | 没设 `PYTHONPATH` | 命令前加 `PYTHONPATH=src`，或在 venv 里 `pip install -e .` |
| `ModuleNotFoundError: No module named 'PySide6'` | 未装 GUI 依赖 | 只跑车端可忽略；跑桌面或 `test_desktop_*` 需安装 |
| `ssl.SSLCertVerificationError: certificate verify failed` | `--ca-file` 与车端 `--cert-dir` 不是同一套证书 | 两边指向同一次脚本输出 |
| `hostname '...' doesn't match` | 连接地址不在证书 SAN 里 | 重签证书时用 `-i <该地址>`，或让桌面端 `--server-name localhost` 走回环 |
| `[Errno 48] Address already in use` | 上一次进程没退干净 | `lsof -i :19177` 后结束进程 |
| 桌面端卡在输入提示不动 | `getpass` 在非终端环境读不到输入 | 在终端里直接启动 |
| Linux 上桌面端起不来、报 Qt 平台插件错误 | 无显示器 | `QT_QPA_PLATFORM=offscreen`，或用 X11 转发 |

## 5. 证据采集

- 车端事件日志：`--event-log <新文件>`，CSV，一次运行一个新文件，历史记录不被覆盖。
- 管理 RTT 与重连计时：`examples/demo_management_metrics.py --output <CSV>`，同时给出样本数、P50/P95。
- 实验归档沿用 `local/a-tests/`、`local/n-tests/` 的编号，原始 CSV 与配置留在本地，不进版本库。
- 报告中标注实际命令、操作系统、Python 版本、Git 提交与连接方式（本机回环 / VMware / 物理双机）。

## 6. 已知限制

- 桌面 GUI 与非 Windows 全量回归尚未实测，本文档的 GUI 命令按源码参数整理，未经运行验证。
- `scripts/new_local_tls.sh` 只覆盖首次准备，不处理证书续期与轮换；续期需要重新生成并同步两端的信任文件。
- TLS 是单向认证：车端出示证书，桌面端用 `client_id/credential` 做应用层认证，桌面端不提供客户端证书。
