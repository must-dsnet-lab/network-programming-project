# 非 Windows 环境复现指南

**基线** `main` · `e4d1b59` · 实测环境 macOS 26.0.1 / Apple M2 / Python 3.14.8 / OpenSSL 3.5.9 · 实测日期 2026-11-22

README 的 Windows 命令全部以 PowerShell 和 `.\.venv\Scripts\python.exe` 给出，那是 Windows 上的权威路径；README 另有一部分 Linux 车端命令以 bash 给出。本文件补齐 macOS 与 Linux 的完整路径，供协议层开发、编解码回归和跨系统联调查阅。Windows 上仍以 [README](../README.md) 为准，两者命令不同但验收条件相同。

## 1. 实测状态

| 环节 | macOS 26.0.1 / Apple M2 | Linux | Windows |
| --- | --- | --- | --- |
| 协议层回归（不装 Qt） | 实测 `Ran 363 tests` 全部通过（40 个非 GUI 模块） | 未实测 | 未记录（Windows 侧只有含 Qt 的全量回归） |
| 全量回归（装 PySide6 后） | 实测 `Ran 392 tests` 全部通过 | 未实测 | 392 项通过（[README](../README.md) 记录，Python 3.12.4） |
| 车端安全模式启动 | 实测通过，TLS 1.3 握手与证书链校验通过 | 未实测 | 已验收 |
| 证书准备 | 实测通过（[new_local_tls.sh](../scripts/new_local_tls.sh)，OpenSSL 3.5.9） | 同一脚本 | 已验收（`.ps1`） |
| 桌面 GUI | PySide6 6.12.0 安装成功；cocoa 平台创建真实窗口通过；29 项 `test_desktop_*` 通过；**未做人工点击验证** | 未实测 | 已验收 |

两处数字差异说明：

- 392 与 387：387 来自 [开发路线](development-roadmap.md)（GUI 合并前的基线 `eb87428`，本机复跑为 387 项）；合并后是 392 项，本机 392 含 PR #29 新增的 5 项桌面测试。
- 363 与 392：不装 Qt 时 `test_desktop_control`、`test_desktop_map` 两个模块导入即失败，全量 discover 会报 2 个 error（365 项）；只跑非 GUI 模块则 363 项全部通过。命令见 [3.1](#31-协议与回归不需要-gui)。

本表只记录实际跑过的命令与结果。未实测项在提交证据时标为「待补」，不并入已验收结论。

## 2. 一次性准备

### 2.1 Python 与依赖

要求 Python ≥ 3.12（`pyproject.toml`）。本机实测使用 conda Python 3.14.8。

```bash
cd network-programming-project

# 只做协议层开发与回归：不需要安装任何依赖，靠 PYTHONPATH 指向 src
# 注意：这里不能跑全量 discover，未装 Qt 时两个桌面模块导入即失败（见 3.1）
PYTHONPATH=src python3 -B -m unittest \
  $(ls tests/test_*.py | grep -v desktop | sed 's#tests/#tests.#; s#\.py$##')

# 需要 GUI 时再建虚拟环境
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

`requirements.txt` 只含 `PySide6==6.12.0`。macOS 上该版本只发布 `pyside6-6.12.0-cp310-abi3-macosx_14_0_universal2.whl`（abi3 对 3.12+ 的解释器前向兼容，要求 macOS ≥ 14）。本机实测在 macOS 26.0.1 + Python 3.14.8 下安装成功：`du -sh .venv` 为 1.4 GB（其中 `PySide6` 占 1.2 GB），wheel 下载量约 200 MB。装完用下面两条确认：

```bash
.venv/bin/python -c "import PySide6; print(PySide6.__version__)"    # 期望 6.12.0
PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -c "import rover.desktop.app; print('ok')"
```

第二条必须带 `PYTHONPATH=src`；在 venv 里执行过 `pip install -e .` 之后 `rover` 已在导入路径上，这个前缀才可以省略。

### 2.2 证书

[new_local_tls.sh](../scripts/new_local_tls.sh) 与 PowerShell 版等价：生成实验 CA、签发服务器证书、校验证书链/名称/SAN/有效期，CA 365 天、服务器证书 90 天。

```bash
# 本机回环：SAN = DNS:localhost, IP:127.0.0.1
bash scripts/new_local_tls.sh

# 双机联调：额外把一个局域网 IPv4 写进 SAN
bash scripts/new_local_tls.sh -o local/certs-lan -i 192.168.1.20
```

脚本必须用 `bash` 执行（用到 bash 数组与 `set -o pipefail`；Linux 上的 `/bin/sh` 是 dash，会直接报错退出）。它在**生成任何 TLS 材料之前**完成三项检查，任何一项不过都直接退出、不留下半套材料：

1. **OpenSSL 版本**：低于 3.0 或检测到 LibreSSL 时拒绝执行（脚本用到 3.x 的 `-noenc`）。macOS 自带的 `/usr/bin/openssl` 是 LibreSSL，需要装 OpenSSL 3 并用 `OPENSSL=` 指过去，或让它排在 PATH 前面。
2. **IPv4 格式**：`-i` 必须是四段 0-255、不带前导零的具体地址（`192.168.01.20` 这类带前导零的写法会被拒，避免被当成八进制解读）。
3. **输出目录**：目录内已有 TLS 材料时拒绝覆盖，不做自动续期。

生成过程在输出目录旁边的临时目录里进行，链校验、主机名校验和每个 SAN IP 都通过后，才整体搬到输出目录；中途失败临时目录会被清理掉。私钥落盘后权限为 `600`。

### 2.3 可信客户端

`local/trusted_clients.json` 是客户端 ID 到凭据字符串的 JSON 映射，车端启动时**必须**存在（缺失会 `vehicle startup failed: [Errno 2]`）：

```bash
mkdir -p local
cat > local/trusted_clients.json <<'JSON'
{
  "desktop-a": "test-secret-a"
}
JSON
```

`local/` 已被根目录 `.gitignore` 排除。提交前用 `git check-ignore -v local/certs/server.key` 复核私钥不在版本库里。

**跨机时桌面端只需要复制 `ca.pem` 这一个公开文件**，供其校验车端证书；`ca.key`、`server.key` 留在车端机器上，不要复制过去。

### 2.4 PySide6 装不上时

按顺序试：

1. 确认 macOS ≥ 14、Python ≥ 3.12；
2. 改用 Python 3.12 或 3.13 的独立环境（wheel 的 abi3 分支最稳）；
3. 跳过 GUI，只用车端与示例客户端验证协议：`PYTHONPATH=src python3 -B -m rover.vehicle.app` 不依赖 PySide6，见 [3.1](#31-协议与回归不需要-gui)。

## 3. 三条验证路径

### 3.1 协议与回归（不需要 GUI）

下面所有命令都在**项目根目录**执行（`ls tests/` 能列出来的那个目录），换到别的目录会 `Ran 0 tests`。

**未装 Qt 时**只跑非 GUI 模块，40 个模块共 363 项：

```bash
PYTHONPATH=src python3 -B -m unittest \
  $(ls tests/test_*.py | grep -v desktop | sed 's#tests/#tests.#; s#\.py$##')
```

直接把全量 discover 跑在无 Qt 环境上会报 2 个 error（`test_desktop_control`、`test_desktop_map` 导入失败），这不是协议层的问题。

**装好 Qt 之后**跑全量，392 项：

```bash
PYTHONPATH=src .venv/bin/python -B -m unittest discover -s tests -t . -q
```

带 GUI 的测试在无显示器环境需要 `QT_QPA_PLATFORM=offscreen`：

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -B -m unittest discover -s tests -t . -q
```

专项测试按影响范围选取，例如只改管理协议时：

```bash
PYTHONPATH=src python3 -B -m unittest -v tests.test_management_codec tests.test_tcp_records \
  tests.test_tcp_transport tests.test_management_connection tests.test_control_codec \
  tests.test_windowed_control_codec tests.test_telemetry_codec
```

### 3.2 本机单机闭环（车端 + 桌面）

两个终端都在项目根目录，环境前置（[2.1](#21-python-与依赖)、[2.2](#22-证书)、[2.3](#23-可信客户端)）都做完。**首次启动前先建好日志目录**，否则车端会因为打不开事件日志文件直接退出：

```bash
mkdir -p local/events
```

同时确认 `local/trusted_clients.json` 已按 [2.3](#23-可信客户端) 建好，缺失同样会在启动阶段报 `[Errno 2]`。

车端：

```bash
PYTHONPATH=src python3 -B -m rover.vehicle.app \
  --secure --cert-dir ./local/certs \
  --credentials-file ./local/trusted_clients.json \
  --event-log ./local/events/$(date +%Y%m%d-%H%M%S).csv
```

桌面端（终端里启动，凭据通过 `getpass` 隐藏输入，不能在 IDE 的输出面板里等）。GUI 依赖装在 `.venv` 里，所以这里用 `.venv/bin/python`，不能用系统 `python3`：

```bash
PYTHONPATH=src .venv/bin/python -B -m rover.desktop.app \
  --secure --vehicle-host 127.0.0.1 \
  --ca-file ./local/certs/ca.pem \
  --client-id desktop-a
```

本机回环时遥测监听地址默认就是 `127.0.0.1`，可以不写 `--telemetry-bind-host`。

只验证 TLS 与管理握手、不启 GUI 时，用示例客户端（同样会 `getpass` 提示输入凭据，输 2.3 里配的 `test-secret-a`）：

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

桌面端把车端生成的公开 `ca.pem` 复制到自己的 `local/certs-lan/ca.pem`（只复制这一个文件，私钥不要拿过去），然后连过去。**双机时 `--telemetry-bind-host` 必须写成桌面端自己的局域网地址**，不能留默认的 `127.0.0.1` —— 车端是把遥测 UDP 包发到这个地址的，绑在回环上就收不到：

```bash
PYTHONPATH=src .venv/bin/python -B -m rover.desktop.app \
  --secure --vehicle-host 192.168.1.20 \
  --telemetry-bind-host 192.168.1.10 --telemetry-port 10000 \
  --ca-file ./local/certs-lan/ca.pem --client-id desktop-a
```

两个参数分工容易混：

- `--vehicle-host` 决定**连到哪个地址**（TCP 管理连接与 UDP 控制都发往这里）；
- `--server-name` 只决定**用哪个名字做证书校验**（SNI 与 hostname 校验），不改变连接地址；不写时回落到 `--vehicle-host`，两者都不写时才用 `localhost`（`src/rover/desktop/app.py:653`）。

所以连 `192.168.1.20` 时，最省事的做法是签发证书时把该 IP 写进 SAN（`-i 192.168.1.20`），让缺省的 `--server-name` 直接用 IP 通过校验。如果证书里只有 `DNS:localhost` 而你必须连 IP，可以单独写 `--server-name localhost` 让它按证书上的名字校验——这时连接地址仍然是 `--vehicle-host`。

端口默认：管理 `19177/TCP`、控制 `9999/UDP`、遥测 `10000/UDP`、观察者 `10001/UDP`。跨机时确认这些端口在两端防火墙放行。

## 4. 常见故障

| 症状 | 原因 | 处理 |
| --- | --- | --- |
| `ModuleNotFoundError: No module named 'rover'` | 没设 `PYTHONPATH` | 命令前加 `PYTHONPATH=src`，或在 venv 里 `pip install -e .` |
| `ModuleNotFoundError: No module named 'PySide6'` | 未装 GUI 依赖 | 只跑车端可忽略；跑桌面或 `test_desktop_*` 需安装 |
| `vehicle startup failed: [Errno 2] No such file or directory: '.../local/events/xxx.csv'` | 事件日志目录不存在，车端不会自动创建 | `mkdir -p local/events` 后重启 |
| `需要 OpenSSL 3.0 或更高，当前是 LibreSSL` | macOS 自带的 `/usr/bin/openssl` 是 LibreSSL | 装 OpenSSL 3，用 `OPENSSL=<路径> bash scripts/new_local_tls.sh`，或调整 PATH |
| `ServerIp 必须是具体 IPv4 地址` | `-i` 写错或带前导零 | 核对四段地址，例如 `192.168.1.20` |
| `ssl.SSLCertVerificationError: certificate verify failed` | `--ca-file` 与车端 `--cert-dir` 不是同一套证书 | 两边指向同一次脚本输出 |
| `certificate verify failed: Hostname mismatch` / `IP address mismatch` | 证书 SAN 里没有实际连接用的名字或地址 | 重签证书时用 `-i <该地址>`；或按 [3.3](#33-双机linux-车端--macos-桌面) 用 `--server-name` 单独指定校验名（连接地址仍由 `--vehicle-host` 决定） |
| 双机时桌面端收不到遥测 | `--telemetry-bind-host` 留了默认的 `127.0.0.1` | 改成桌面端自己的局域网地址 |
| `[Errno 48] Address already in use` | 上一次进程没退干净 | `lsof -i :19177` 后结束进程 |
| 桌面端卡在输入提示不动 | `getpass` 在非终端环境读不到输入 | 在终端里直接启动 |
| Linux 上桌面端起不来、报 Qt 平台插件错误 | 无显示器 | `QT_QPA_PLATFORM=offscreen`，或用 X11 转发 |

## 5. 证据采集

- 车端事件日志：`--event-log <新文件>`，CSV，一次运行一个新文件，历史记录不被覆盖；父目录必须已存在。
- 管理 RTT 与重连计时：`examples/demo_management_metrics.py --output <CSV>`，同时给出样本数、P50/P95。
- 实验归档沿用 `local/a-tests/`、`local/n-tests/` 的编号，原始 CSV 与配置留在本地，不进版本库。
- 报告中标注实际命令、操作系统版本（本机 `macOS 26.0.1`，`sw_vers -productVersion` 可复核）、Python 版本、OpenSSL 版本、Git 提交与连接方式（本机回环 / VMware / 物理双机）。

## 6. 已知限制

- 桌面 GUI 只验证到「PySide6 装成功 + cocoa 平台能创建可见窗口 + 29 项桌面测试通过」，**没有做人工点击与端到端操作验证**；Linux 与 Windows 上的 GUI 未实测。
- 双机路径（Linux 车端 + macOS 桌面）按源码参数与默认值整理，未在本机实测。
- `scripts/new_local_tls.sh` 只覆盖首次准备，不处理证书续期与轮换；续期需要重新生成并同步两端的信任文件。
- TLS 是单向认证：车端出示证书，桌面端用 `client_id/credential` 做应用层认证，桌面端不提供客户端证书。
