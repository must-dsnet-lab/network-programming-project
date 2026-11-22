# 专家 P1 · 控制权状态机与消息草案

[开发路线](development-roadmap.md#protocol-delivery) · [控制权模型](../src/rover/vehicle/control_authority.py) · [验收用例](../tests/test_control_authority.py)

P1 用独立的 `ControlAuthority` 表达车端控制权裁决。状态机和用例可以单独验证，管理报文、连接调度、UDP 准入和租约时钟按 P2–P6 逐步接入。本页的线上接口供后续评审。物理双机与断网验收继续按高级阶段的记录口径执行。

## 1. 身份、会话和授权

| 对象 | 车端约定 |
| --- | --- |
| `ControlSession` | 包含认证后确定的 `client_id`、车端为本条连接生成的 `connection_id`、车端签发并绑定本条连接的 `session_id`。同一账号的两条连接具有各自的会话和身份上下文。 |
| `AuthorityGeneration` | 由 `epoch` 和 `number` 组成。车端每次启动生成新的 UUID `epoch`，每次成功授予控制权递增 `number`。两个字段一起比较。 |
| `AuthorityGrant` | 将一个 `ControlSession` 与一个授权代次绑定。车端任一时刻最多持有一份当前授权。 |

申请资格由已认证且活跃的管理会话获得。使能、归还、转让及续期还要校验当前持有会话与授权代次。UDP 窗口限制单条指令的时间有效性，序号约束同一代次里的重复和倒序。其他已认证会话在界面可显示为待命。

## 2. 状态与事件

| 状态 | 可执行动作与转移 |
| --- | --- |
| `idle` | 无授权。活跃会话调用 `acquire` 可获得新代次，进入 `held`。 |
| `held` | 已持有，尚未使能。当前持有者用 `set_enabled` 使能后进入 `controlling`。再次申请返回 `authority_busy`，保留原授权。 |
| `controlling` | 当前持有者已使能，UDP 仍需经过既有保护和专家代次准入。停用回到 `held`，保留同一授权。 |
| `stopping` | `release`、持有者断开、当前代次的 `lease_expired` 或已受理的 `transfer` 都会立即撤掉授权并进入此状态。仿真步进确认速度为零后，按正在停止的代次提交 `motion_stopped`。无候选则回 `idle`，仍在线的转让候选取得新代次并进入 `held`。 |
| `fault_latched` | `latch_fault` 取消待授候选并清除授权。车端通过独立复位检查后提交 `fault_cleared`，回到 `idle`。 |

运行时接线应在撤权时同步停用控制、清理当期有效目标、短期窗口和序号，并使旧控制密钥失效。转让候选须来自另一条连接上的活跃会话。候选断开时取消待授，车辆继续完成停车。旧持有者迟到的断开事件和不匹配的停车确认均不能完成或清除新授权。

**正常转让。** A 已使能 → A 提交转让给 B → 车端撤销 A 并等待归零 → 仿真步进确认对应代次已停车 → B 以新代次取得待使能授权 → B 使能并应用目标。转让受理的回复只能确认开始停车，B 通过授权查询确认已取得控制权。

**显式复位。** 复位由有恢复权限的操作者通过活跃、绑定在本条连接的管理会话发起。车端独立验证恢复权限、故障已锁存、速度为零且无有效目标，再调用模型的 `fault_cleared`。恢复权限应能覆盖无人持有控制权的锁存场景。复位后保持静止，重新申请、使能并明确应用目标后才可运动。

## 3. 管理与 UDP 接口草案

专家入口显式启用，复用 TLS 校验、应用认证和 TCP 分帧。P2 采用专家管理报文 `version: 2`，专家安全 UDP 拟用 `version: 3`。高级入口使用管理 v1 和安全 UDP v2。管理版本不兼容时结束当前连接，排查时核对两端的启动模式。专家 UDP 候选格式须完整性校验覆盖会话、授权代次、窗口、序号和指令目标，专家授权路径拒绝缺少代次的高级报文。

连接完成 TLS 握手后，经 `authenticate` 提交 `client_id` 和 `credential`。认证成功的连接用 `open_session` 取得各自的 `session_id`，重复打开时复用本连接的会话。打开管理会话不授予控制权。请求使用 UTF-8 JSON，包含整数 `version`、请求类型 `type` 和递增的 `request_id`。车端从认证连接取得操作者身份，并核对请求中的 `session_id` 绑定当前连接。`generation` 携带 `epoch` 和 `number`。下表的回复包含 `version` 和 `type`，回复类型为对应请求类型加 `_result`，并附原编号、布尔值 `accepted` 及 `reason`。成功时 `reason` 为 `null`。

| 请求类型 | 公共字段外的参数 | 受理条件及成功结果 |
| --- | --- | --- |
| `acquire_authority` | `session_id` | 活跃会话在 `idle` 可获新代次，返回 `phase` 和 `generation`。 |
| `set_authority_enabled` | `session_id`、`generation`、`enabled` | 当前持有者在 `held` 或 `controlling` 可切换使能，返回变更后的 `phase`。专家 UDP 接入后，使能成功的回复经当前连接返回该代次的 `control_key`。 |
| `issue_control_window` | `session_id`、`generation` | 使能后的持有者可申请短期控制窗口，返回 `window_id` 和 `lifetime_s`。 |
| `release_authority` | `session_id`、`generation` | 当前持有者进入 `stopping`。回复报告撤权已受理，归零由仿真步进确认。 |
| `transfer_authority` | `session_id`、`generation`、`target_session_id` | 当前持有者选择另一条连接的在线候选。车端按签发的候选会话 ID 查找，并在开始停车时回复 `phase`。 |
| `renew_authority` | `session_id`、`generation` | P5 接入后，当前持有者在到期前可续约。返回此刻计算的剩余时长 `lease_remaining_s`。 |
| `query_authority` | `session_id` | 活跃会话可查询 `phase`、当前授权代次、是否由本会话持有和可执行动作。仅向有转让权限的操作者返回在线候选的 `target_session_id`。 |
| `emergency_stop` | `session_id`、`generation` | 当前持有者可请求急停，车端进入 `fault_latched` 并撤掉授权。车端保护也可自行触发锁存。 |
| `reset_control` | `session_id` | 车端通过本页复位检查后解除故障锁存，返回 `phase: idle`。 |

查询响应仅返回动作所需的状态及受权限限制的候选信息。控制密钥只经 TLS 发给对应获授权连接，授权查询、共享状态、拒绝回复和验收日志中均不包含控制密钥或凭据。

### 3.1 请求顺序与拒绝

- 每条连接由客户端从 `1` 递增分配 `request_id`，同一连接一次处理一条请求。和该连接已处理的最大编号相等时返回 `duplicate_request_id`，低于它时返回 `out_of_order_request_id`。编号通过检查且可关联时即被记录，业务拒绝也保留编号。回复超时后先在可用连接上查状态。原连接不可用时重新认证并查询，再决定是否发出新动作。
- 格式正确的业务拒绝回显请求编号，保持连接可用。P1 裁决原因包括 `authority_busy`、`vehicle_stopping`、`fault_latched`、`not_authority_holder`、`stale_generation`、`invalid_transfer_target`。线上接线需区分认证失败、会话无效、排队不可用及复位不满足条件等额外原因。
- 管理版本可识别且编号有效时，未知动作或无效字段返回带编号的 `management_error`，原因分别为 `unsupported_message_type` 或 `invalid_request`。版本或分帧不兼容、编号无法可靠读取时，结束当前连接并记录原因。连接结尾只清理与该连接实例绑定的状态。

### 3.2 队列与租约边界

网络处理线程将管理事件交给车端仿真线程统一裁决。同一轮先处理急停和故障，再处理租约到期、断开和归还，再处理申请、续期等剩余动作。同一优先级按车端入队顺序裁决。已撤销授权的同一代次不能被后到的续期或使能恢复。

P5 草案以车端单调时钟计算租约，授予时确定到期点。以车端处理时刻判定续期，`now < expires_at` 时才允许续期，等于或超过即到期。后续续期使用当前车端处理时刻重新计算到期点。仿真线程使用当前到期点判定是否调用 `lease_expired`，避免旧到期点在续期后撤销授权。管理保活、查询和 UDP 指令都不自动续期。租约到期和 UDP 看门狗分别计时、分别验收。

## 4. P1 复现

### 4.1 单终端动手测试

在项目根目录打开一个终端，只需要 Python。示例在同一进程中模拟 A、B 两个管理会话和车端仿真步进，用来观察 P1 状态机。TLS 和 UDP 的真实进程联调留到后续接线验证。

Windows PowerShell 使用项目虚拟环境。如果找不到 `rover` 模块，先执行 `$env:PYTHONPATH = 'src'` 再重试。

```powershell
.\.venv\Scripts\python.exe -X utf8 -B -m examples.demo_control_authority
```

Ubuntu 使用以下命令。

```bash
PYTHONPATH=src python3 -B -m examples.demo_control_authority
```

启动时 A 已使能，B 此时申请会收到 `authority_busy`。按一次 Enter，让 A 发起向 B 的交接。车端立即撤销 A 的授权，B 此时提前使能会收到 `vehicle_stopping`。随后输入数字选择分支，重启示例可以验证另一种结果。

| 输入 | 应看到的变化 |
| --- | --- |
| `1` 正常交接 | 仿真步进确认归零后，B 获得代次 2，仍未使能。A 用旧授权使能会收到 `not_authority_holder`。再按 Enter，让 B 使能并应用目标。 |
| `2` 候选 B 离开 | B 的待授资格取消。仿真归零后状态为 `idle`，没有当前授权。 |
| `3` 保护锁存 | 归零后仍保持 `fault_latched`，B 申请时也收到 `fault_latched`。再按 Enter 确认显式复位，车辆保持零速，状态回到 `idle` 且没有当前授权。 |

**用户动手验收。** Windows PowerShell，基线 `544b247` 加 `feat/expert-control-authority` 工作区改动。用户运行上面的命令三次，三个分支各一次。三轮均观察到 A 控制期间 B 的申请返回 `authority_busy`，交接开始后 B 提前使能返回 `vehicle_stopping`。

| 分支 | 实际观察 |
| --- | --- |
| 正常交接 | A 在代次 1 以 1.0 m/s 行驶。归零后 B 获代次 2 并处于 `held`，A 用旧授权使能返回 `not_authority_holder`。用户再次回车，B 进入 `controlling`，速度回到 1.0 m/s。 |
| 候选 B 断开 | 停车后为 `idle`，持有者与代次均为无，速度为 0.0 m/s。 |
| 故障锁存 | 归零后仍为 `fault_latched`，B 申请被拒。明确确认复位后为 `idle`，无人持有控制权，速度仍为 0.0 m/s。 |

### 4.2 自动化回归

在 Windows 项目根目录运行，使用已安装项目的虚拟环境。

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -X utf8 -B -m unittest -q tests.test_control_authority tests.test_control_authority_demo
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -X utf8 -B -m unittest discover -s tests -t . -q
```

Windows 本机预检使用 Python 3.12.10，基线 `544b247` 加 `feat/expert-control-authority` 工作区改动。状态机初版专项 12 项、离屏全量 404 项通过。加入 [交互示例](../examples/demo_control_authority.py) 后，命令行三个分支均跑通，专项 15 项、离屏全量 407 项通过。

专项用例覆盖独占申请、连接归属、启停、转让与候选断开、旧代次事件、撤权后等待归零和故障恢复。用例直接调用状态机，后续的线上报文、队列、计时和物理双机均需对应阶段的独立验收。

## 5. P2 双 TLS 本机联调

`--secure --expert-management` 启用管理 v2。P2 支持两个桌面端各自认证、建立管理会话和查询授权状态。连接线程分别处理 TLS 和读写，车端主循环每轮最多处理一个排队请求，并清理已断开的连接实例。查询回复包含管理会话数、排队耗时和仿真节拍间隔。本节记录 P2 版本的双连接验收。实际控制权与起步在 P3 复现。

| 项目 | P2 行为 |
| --- | --- |
| 并发名额 | 最多两条连接，各有独立的 TLS 握手和协议状态。名额占满时关闭新连接。 |
| 请求编号 | 编号从 1 开始按连接递增，重复和倒退均返回可关联的拒绝。同账号可以建立两个独立会话。 |
| 状态查询 | 以本条连接登记的会话为准。返回 `phase`、`owns_authority`、`online_sessions`、`queue_wait_ms` 和 `tick_interval_ms`。 |
| 超时与清理 | 连接 I/O 超时为 5 秒，单帧拼装上限为 1 秒。队列容量为 2。未开始的操作等待仿真超过 1 秒时取消，分别报告 `simulation_timeout` 或 `queue_full`。断开清理对应会话，其余连接可以继续查询。 |

### 5.1 手动复现

在 Windows 项目根目录开两个 PowerShell 窗口，从同一个工作区运行。已有可用的回环证书与 `local/trusted_clients.json` 可直接复用。

窗口 A · 启动车端：

```powershell
.\.venv\Scripts\python.exe -X utf8 -B -m rover.vehicle.app --secure --expert-management --cert-dir '.\local\certs' --credentials-file '.\local\trusted_clients.json'
```

窗口 B · 运行 [双连接示例](../examples/demo_expert_management.py)：

```powershell
.\.venv\Scripts\python.exe -X utf8 -B -m examples.demo_expert_management
```

默认用同一个 `desktop-a` 账号建立两条经过证书验证的 TLS 连接，凭据在终端隐藏输入。已有另一账号时可添加 `--client-id-b desktop-b` 分别输入。证书在其他目录时，对应替换车端的 `--cert-dir` 和示例的 `--ca-file`。预期输出：

```text
P2 online sessions: A=2 B=2, distinct session IDs
P2 A closed: B=1, B session unchanged
```

## 6. P3 零速交接

P3 将 P1 的裁决接入正式车端主循环。P2 的两条 TLS 连接可同时请求，只有当前持有者可使能、申请窗口或指定交接对象。交接先清掉旧目标、控制密钥、窗口和序号。车端在仿真步进确认零速后才向在线候选授予新代次，新持有者需使能并应用目标。

管理动作使用管理 v2。P3 运动沿用已验证的 UDP v2 格式，依靠车端当前会话、启停状态、新密钥、短窗和序号进行准入。P4 再把代次写入 UDP 报文。专家模式下当前持有者可请求急停，UDP 看门狗触发时同样撤销控制权。处于锁存且车端已观察到零速、无有效目标时，原型的已认证活跃可信账号可明确请求复位。复位回到 `idle`，重新申请授权后才能使能。

### 6.1 本机复现

在 Windows 项目根目录使用两个 PowerShell 窗口。沿用 P2 实验 CA 和账号配置。先启动车端，并用新文件保存车端事件：

```powershell
$p3Log = Join-Path 'local' ('p3-handoff-' + [guid]::NewGuid().ToString('N') + '.csv')
.\.venv\Scripts\python.exe -X utf8 -B -m rover.vehicle.app --secure --expert-management --cert-dir '.\local\certs' --credentials-file '.\local\trusted_clients.json' --event-log $p3Log
```

另一个窗口运行 [交接示例](../examples/demo_expert_handoff.py)，按提示输入登录凭据。默认以 `desktop-a` 建立两条独立会话，也可以加 `--client-id-b desktop-b` 使用另一账号。

```powershell
.\.venv\Scripts\python.exe -X utf8 -B -m examples.demo_expert_handoff
```

预期看到 A 行驶、转让受理、B 接权时速度为零和未使能、B 显式使能后行驶并归还。证书若放在其他目录，同时调整车端的 `--cert-dir` 和示例的 `--ca-file`。如果改过车端端口或遥测地址，示例支持 `--port`、`--control-port`、`--telemetry-bind-host` 和 `--telemetry-port`。

### 6.2 本机证据

2026-11-23，Windows 本机回环，基线 `46a08b2` 加 P3 工作区改动。正式车端进程与两个使用独立测试账号的 TLS 客户端完成交接。车端 CSV 顺序记录了 `stopping` 代次 1 时 1.0 m/s、`held` 代次 2 时 0.0 m/s，以及 B 使能后 1.0 m/s。另一次使能后保持无有效控制报文，验证看门狗撤权锁存，显式复位后仍无人持有。原始车端事件和终端日志归档在本地 `local/p3-tests/`。该证据对应 Windows 本机联调，高级与专家的物理验收按集中试验计划记录。

P3 专项 48 项、Windows 离屏全量回归 453 项通过。专项覆盖同时申请只授予一端、归零前候选端断开、旧持有者迟到关闭、旧材料清理、窗口签发与急停复位。

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -X utf8 -B -m unittest -q tests.test_control_authority tests.test_expert_control tests.test_expert_management_runtime tests.test_expert_management_client tests.test_expert_handoff_demo
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -X utf8 -B -m unittest discover -s tests -t . -q
```

### 5.2 本机证据

2026-11-23 在 Windows 用正式车端进程、隔离实验 CA 和两个临时账号通过本机回环实际验证。第一条连接还在等待握手时，第二条可完成 TLS 和登记。两条已认证连接同时在线、会话不同，两个 UDP 遥测接收端均收到零速数据。关闭 A 后，B 看到在线会话数降为 1，原会话可继续查询。示例入口同样通过。该证据覆盖本机 TLS 与 UDP 遥测。

Windows Python 3.12.10，基线 `fe0ebc0` 加 P2 工作区改动。P2 专项及相关入口回归 44 项、离屏全量 432 项通过。本机 TCP 测试覆盖重复编号、等待超时和队列满，独立的真实 TLS/UDP 联调覆盖上段场景。

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -X utf8 -B -m unittest -q tests.test_expert_management_runtime tests.test_parallel_tls_server tests.test_expert_management_client tests.test_expert_management_demo tests.test_secure_connection tests.test_vehicle_startup
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -X utf8 -B -m unittest discover -s tests -t . -q
```

### 5.3 用户动手验收

2026-11-23 收到用户的 Windows PowerShell 本机运行结果，对应 P2 实现提交 `e00f1c2`。按 5.1 的默认命令建立两条 TLS 连接，两端都查询到 2 个不同会话。示例关闭 A 后，B 查询到在线数降为 1，并继续使用原会话。

```text
P2 online sessions: A=2 B=2, distinct session IDs
P2 A closed: B=1, B session unchanged
```
