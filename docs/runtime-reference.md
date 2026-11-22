# 图 10 · 高级版运行函数索引

`main@74dbee0` · `--secure`。展开模块可追到源码里的具体调用和回调。

[可缩放总图](../reference_image/diagrams/10-advanced-runtime.svg) · [项目首页](../README.md)

| 运行路径 | 展开节点 |
| --- | --- |
| 工程目录 → 桌面和车端入口 | [项目目录](#project-tree) · [桌面启动与线程](#desktop-entry) · [车端启动与单轮仿真](#vehicle-tick) |
| 桌面事件 ⇢ 控制后台 | [Qt 入口](#desktop-entry) · [SecureControlWorker 后台循环](#desktop-worker) |
| 控制后台 ⇄ 车端 TLS 线程 ⇢ 车端仿真循环 | [连接与报文](#management-exchange) · [线程交接](#management-mailbox) · [会话操作](#management-actions) |
| 控制后台 ⇒ 车端控制 socket → 本地裁决和仿真 | [UDP 控制发包](#udp-control-send) · [UDP 控制收包](#udp-control-receive) · [仿真步进](#vehicle-tick) |
| 车端快照 ⇒ 桌面窗口及独立观察进程 | [遥测出口](#udp-telemetry) · [桌面遥测与绘制](#telemetry-desktop) · [观察端](#telemetry-observer) |
| 停车、复位与收尾 | [故障与恢复分支](#fault-and-recovery) · [实验入口及回归](#experiment-tools) |

`→` 调用或当前步骤交给下一步骤 · `⇢` 事件、定时器或跨线程交接 · `⇄` TLS 请求和回复 · `⇒` UDP 数据报。图中端口为默认值。

<a id="project-tree"></a>

## 项目目录

<details>
<summary>展开目录和生产入口</summary>

~~~text
Project/
├── README.md
├── pyproject.toml / requirements.txt
├── src/rover/
│   ├── core/
│   │   ├── control_target.py         ControlTarget / apply_target()
│   │   ├── vehicle_model.py          VehicleState / advance()
│   │   └── simulation.py             run_steps()
│   ├── desktop/
│   │   ├── app.py                    main() / DesktopConsole
│   │   ├── control_worker.py         SecureControlWorker
│   │   ├── secure_connection.py      connect_management()
│   │   ├── management_client.py      ManagementClient
│   │   ├── secure_control.py         SecureControlSession
│   │   ├── desktop_map.py            VehicleMap
│   │   ├── map_geometry.py           world_to_screen()
│   │   └── trajectory.py             TrajectoryHistory
│   ├── protocol/
│   │   ├── management_codec.py       管理请求及回复
│   │   ├── control_codec.py          控制目标、指令和窗口化数据报
│   │   ├── control_auth.py           compute_control_mac() / verify_control_mac()
│   │   └── telemetry_codec.py        TelemetrySnapshot
│   ├── transport/
│   │   ├── tcp_transport.py          send_record() / iter_records()
│   │   ├── tcp_records.py            TcpRecordBuffer
│   │   ├── udp_transport.py          控制及遥测数据报收发
│   │   ├── udp_control_outage.py     断流实验
│   │   ├── udp_control_faults.py     丢包、延迟及顺序实验
│   │   └── udp_control_order.py      PacketOrderPolicy
│   └── vehicle/
│       ├── app.py                    cli() / main()
│       ├── management_runtime.py     management_listener()
│       ├── management_connection.py  serve_management_connection()
│       ├── management_session.py     handle_* 管理操作
│       ├── control_guard.py          ControlGuardState / update_*_guard()
│       ├── control_window.py         ControlWindow / issue_control_window()
│       ├── control_watchdog.py       AcceptedTarget / target_for_tick()
│       └── vehicle_runtime.py        step_vehicle()
├── examples/                         观察端、实验计时、汇总
├── scripts/new_local_tls.ps1
├── tests/
├── docs/
├── reference_image/diagrams/
└── local/                            本地证书、凭据及原始实验记录
~~~

[桌面入口](../src/rover/desktop/app.py#L577) · [车端入口](../src/rover/vehicle/app.py#L252) · [观察端入口](../examples/demo_udp_telemetry_receiver.py#L8)

</details>

<a id="desktop-entry"></a>

## 桌面启动与线程

<details>
<summary>Qt 主线程 · 初始化、信号与状态</summary>

~~~text
desktop.app.main()
├── getpass() → partial(connect_management, ...) → SecureControlWorker(...)
│   └── __init__() → Thread(target=worker._run)
├── QApplication → DesktopConsole.__init__()
│   ├── 绑定非阻塞 UDP 遥测 socket
│   ├── QTimer.timeout (20 ms) ⇢ poll_telemetry()
│   ├── QTimer.timeout (50 ms) ⇢ poll_secure_status() → worker.status()
│   └── worker.start() → Thread.start() ⇢ worker._run()
└── window.show() → QApplication.exec()

start_button.clicked ⇢ DesktopConsole.start_control()
├── ControlTarget(speed_mps, radians(heading)) → _secure_motion_blocked()
├── worker.apply_target(target) → Condition.notify() ⇢ _run_loop()
└── poll_secure_status() → 刷新工作线程状态和按钮
~~~

[DesktopConsole](../src/rover/desktop/app.py#L39) · [start_control](../src/rover/desktop/app.py#L295) · [poll_secure_status](../src/rover/desktop/app.py#L387)

</details>

<a id="desktop-worker"></a>

<details>
<summary>控制后台线程 · _run、_run_loop 与发送取消</summary>

~~~text
SecureControlWorker._run()
├── self._connect() → connect_management() → 已认证的 ManagementClient
├── 创建非阻塞控制 UDP socket → SecureControlSession(...)
├── session.open() → client.open_session() → 保存会话 ID 和控制密钥
├── _run_loop()
│   ├── 管理动作待处理 → _perform_action()
│   ├── 无目标 → session.stop_sending()（先前正在发送时）
│   │            仍在空闲期间 → client.open_session() → 核对原会话 ID
│   ├── 有目标 → session.start(...) / session.send(...)
│   │            send_gate = _send_gate(generation) → 发包前检查本地发送意图
│   └── 更新 WorkerStatus ⇢ Qt 的 poll_secure_status() 轮询
└── 退出上下文 → 释放 UDP / TLS socket → 更新 WorkerStatus
~~~

[后台运行入口](../src/rover/desktop/control_worker.py#L128) · [发送与保活循环](../src/rover/desktop/control_worker.py#L184) · [桌面会话](../src/rover/desktop/secure_control.py#L19)

</details>

<a id="tls-management"></a>

## 蓝色 · TLS 管理

<a id="management-exchange"></a>

<details>
<summary>连接、鉴权、分帧与回包</summary>

~~~text
桌面 connect_management()
├── ssl.create_default_context(cafile=...) → TCP create_connection()
├── SSLContext.wrap_socket(..., server_hostname=...)
└── ManagementClient.authenticate() → _exchange()

ManagementClient._exchange(request, encode, decode)
├── encode(request) → send_record() → encode_record() → SSLSocket.sendall()
├── next(self._records, None) → iter_records()
│   ├── _recv_chunk() → SSLSocket.recv()
│   ├── TcpRecordBuffer.feed() → decode_record() → _validate_payload_length()
│   └── 遇到 EOF → TcpRecordBuffer.finish()
└── decode(payload) → 核对 reply.request_id → 返回结果

桌面 ManagementClient ⇄ TLS / TCP 19177 ⇄ 车端 serve_management_connection()
车端 run_worker() → listener.accept() → SSLContext.wrap_socket(server_side=True)
└── serve_management_connection()
    ├── iter_records() → decode_authenticate_request() → verify_client_credential()
    ├── encode_authentication_result() → send_record()
    ├── 已认证的后续记录 → decode_session_request() → 对应管理回调
    └── 回调返回 → 对应 encode_*() → send_record()
~~~

[桌面 TLS](../src/rover/desktop/secure_connection.py#L11) · [ManagementClient](../src/rover/desktop/management_client.py#L32) · [读写记录](../src/rover/transport/tcp_transport.py) · [记录缓冲区](../src/rover/transport/tcp_records.py) · [车端分派](../src/rover/vehicle/management_connection.py#L32)

</details>

<a id="management-mailbox"></a>

<details>
<summary>跨线程交接 · Queue、Future 与车端 guard</summary>

~~~text
vehicle.app.cli() → 创建 management_listener(...) 上下文 → main(management=...)
vehicle.app.main() → with management as process_management
                    → Queue(maxsize=1) + Thread(target=run_worker)
                    → yield process_one 作为 process_management

TLS 线程
serve_management_connection() → on_*() → submit(apply)
├── pending.put_nowait((apply, future)) ⇢ 交给车端仿真线程
└── future.result() 等待回包数据 → 回调返回后编码响应

车端仿真线程
vehicle.app.main() → process_management(guard)（即 process_one）
├── pending.get_nowait() → apply(guard) → (next_guard, reply)
├── future.set_result(reply) ⇢ 唤醒 TLS 线程
├── return next_guard → 车端下一步继续使用该 guard
└── apply() 异常 → future.set_exception(error) + 返回原 guard
~~~

[TLS 线程及队列](../src/rover/vehicle/management_runtime.py#L22) · [仿真循环注入 process_management](../src/rover/vehicle/app.py#L29)

</details>

<a id="management-actions"></a>

<details>
<summary>管理动作 · 客户端方法、线上的消息和车端处理函数</summary>

| 桌面方法 | 请求 ⇄ 结果 | 车端回调和实际状态操作 |
| --- | --- | --- |
| `authenticate()` | `AuthenticateRequest` ⇄ `AuthenticationResult` | `serve_management_connection()` → `verify_client_credential()` |
| `open_session()` | `OpenSessionRequest` ⇄ `SessionOpened` | `on_open_session()` ⇢ `handle_open_session()` → `open_control_session()`（新会话时生成密钥） |
| `enable_session()` | `EnableSessionRequest` ⇄ `SessionEnableResult` | `on_enable_session()` ⇢ `update_control_guard()` → `handle_enable_session()` → `enable_control_session()` |
| `issue_control_window()` | `IssueControlWindowRequest` ⇄ `ControlWindowResult` | `on_issue_control_window()` ⇢ `handle_issue_control_window()` → `update_control_guard()`，批准后调用 `issue_control_window()` |
| `emergency_stop()` | `EmergencyStopRequest` ⇄ `EmergencyStopResult` | `on_emergency_stop()` ⇢ `handle_emergency_stop()` → 清目标、使能和窗口，写入锁存 |
| `reset_control()` | `ResetControlRequest` ⇄ `ResetControlResult` | `on_reset_control()` ⇢ `handle_reset_control()` → `close_control_session()`，清除锁存和连接绑定 |
| 管理连接结束 | 已认证连接的清理回调 | `on_close_connection()` ⇢ `handle_connection_closed()` → `close_control_session()`（拥有当前会话时） |

[消息与编解码](../src/rover/protocol/management_codec.py) · [车端操作](../src/rover/vehicle/management_session.py) · [图 9 管理请求](../README.md#management-request-flow)

</details>

<a id="udp-control"></a>

## 铜色 · UDP 控制

<a id="udp-control-send"></a>

<details>
<summary>桌面出口 · 控制窗口、发送时序和 HMAC</summary>

~~~text
SecureControlWorker._run_loop()
├── 首包或重新开始 → SecureControlSession.start(target)
│   └── open() → _renew_window() ⇄ issue_control_window()
│       → client.enable_session() ⇄ 车端 → send(target)
└── 后续发送 → SecureControlSession.send(target)
    ├── 到达续签时刻 → _renew_window() ⇄ issue_control_window()
    ├── ControlCommand(session_id, seq, target, window_id)
    └── with send_gate → send_windowed_control_command()
        ├── encode_windowed_command()
        │   ├── encode_command() / _validate_window_id()
        │   └── _encode_control_fields() → compute_control_mac() → 组装数据报
        └── UDP sendto() ⇒ 车端 UDP 9999 → 发送返回后递增 seq
~~~

[会话启动和发送](../src/rover/desktop/secure_control.py#L60) · [UDP 发送](../src/rover/transport/udp_transport.py#L129) · [v2 编码](../src/rover/protocol/control_codec.py#L205)

</details>

<a id="udp-control-receive"></a>

<details>
<summary>车端入口 · UDP 接收、HMAC 和准入</summary>

~~~text
vehicle.app.main()
├── try_receive_windowed_control_command(sock, key=guard.control_key)
│   ├── recvfrom() → 无数据时返回 None
│   ├── 已收包但没有活动密钥 → NoActiveControlKeyError
│   └── decode_windowed_command()
│       ├── UTF-8 / JSON → _encode_control_fields()
│       ├── verify_control_mac() → compute_control_mac() / hmac.compare_digest()
│       └── 版本、类型、窗口字段 → decode_command() → ControlCommand
└── update_windowed_command_guard_with_reason(guard, now_s, incoming)
    ├── update_control_guard() → 检查超时和锁存
    ├── control_window_rejection_reason() → 核对当前窗口
    └── _apply_session_command() → 核对使能、会话、序号
        └── 接受时写入 guard.last_accepted 和 guard.last_seq
~~~

[UDP 接收](../src/rover/transport/udp_transport.py#L139) · [v2 解码](../src/rover/protocol/control_codec.py#L222) · [准入裁决](../src/rover/vehicle/control_guard.py#L172) · [窗口判断](../src/rover/vehicle/control_window.py#L47) · [HMAC 实现](../src/rover/protocol/control_auth.py)

</details>

<a id="vehicle-tick"></a>

## 紫色 · 车端仿真

<details>
<summary>CLI 注入管理回调、循环顺序和车辆步进</summary>

~~~text
vehicle.app.cli()
├── load_trusted_credentials() → 准备 management_listener() 上下文
└── main(..., management=...)
    ├── 初始化 ControlGuardState / VehicleState
    ├── 进入管理上下文 → process_management → 绑定控制、遥测 socket
    └── 每轮
        1. monotonic() → process_management(guard) → update_control_guard()
        2. try_receive_windowed_control_command() → 至多一个控制数据报
        3. monotonic() → update_windowed_command_guard_with_reason()
        4. step_vehicle(state, guard.last_accepted, now_s, dt_s)
           ├── target_for_tick() → 选用当前目标，或者保持朝向、目标速度置零
           ├── apply_target() → VehicleState
           └── advance() → 新 VehicleState
        5. 遥测到期 → TelemetrySnapshot(state, guard.fault_latched)
        6. 输出事件 → 可选 record() → 车端 CSV
        7. sleep() → 补齐步长剩余时间
~~~

[车端 CLI](../src/rover/vehicle/app.py#L252) · [仿真循环](../src/rover/vehicle/app.py#L29) · [单步仿真](../src/rover/vehicle/vehicle_runtime.py#L6) · [目标选择](../src/rover/vehicle/control_watchdog.py#L17) · [车辆运动](../src/rover/core/vehicle_model.py)

</details>

<a id="udp-telemetry"></a>

## 青绿色 · UDP 遥测

~~~text
vehicle.app.main() → TelemetrySnapshot(state, fault_latched)
└── send_telemetry() → encode_telemetry() → UDP sendto()
    ├── ⇒ 桌面 UDP 10000 → DesktopConsole.poll_telemetry()
    └── ⇒ 可选观察端 UDP 10001 → demo_udp_telemetry_receiver.main()
~~~

[遥测传输](../src/rover/transport/udp_transport.py#L61) · [遥测编解码](../src/rover/protocol/telemetry_codec.py)

<a id="telemetry-desktop"></a>

<details>
<summary>桌面接收、轨迹和 Qt 绘制</summary>

~~~text
QTimer.timeout ⇢ DesktopConsole.poll_telemetry()
├── try_receive_telemetry() → recvfrom() → decode_telemetry()
├── 更新最近有效接收时刻
├── VehicleMap.set_state()
│   ├── TrajectoryHistory.record()
│   └── QWidget.update() ⇢ VehicleMap.paintEvent()
│       ├── TrajectoryHistory.points()
│       └── screen_point() → world_to_screen()
└── 刷新最近快照读数及遥测状态
~~~

[遥测轮询](../src/rover/desktop/app.py#L491) · [地图绘制](../src/rover/desktop/desktop_map.py) · [轨迹](../src/rover/desktop/trajectory.py)

</details>

<a id="telemetry-observer"></a>

<details>
<summary>只读观察进程</summary>

~~~text
demo_udp_telemetry_receiver.main()
└── UDP bind() → select.select()
    └── try_receive_telemetry() → recvfrom() → decode_telemetry() → print()
~~~

[观察端](../examples/demo_udp_telemetry_receiver.py#L8) · [部署和验收](../README.md#vmware-lan-acceptance)

</details>

<a id="fault-and-recovery"></a>

## 停车与恢复

<details>
<summary>停车、过期、急停、复位及连接关闭</summary>

| 事件 | 函数关系 |
| --- | --- |
| 正常停车 | `stop_motion()` → 本地已有活动目标时 `worker.apply_target(ControlTarget(0, heading))` ⇢ 周期发送零速目标 |
| 停止发包 | `stop_control()` → `worker.stop_sending()` ⇢ 后台停止新发包 → 车端 `update_control_guard()` 检查使能后首包或最后有效包的超时，`target_for_tick()` 选择零速 |
| 遥测过期 | `poll_telemetry()` → `_set_telemetry_control_suspended(True)` → `stop_control()` → 同一停发路径 |
| 急停 | `request_emergency_stop()` → `worker.emergency_stop()` → `_queue_action()` ⇢ `_perform_action()` → `session.stop_sending()` → `client.emergency_stop()` ⇄ `handle_emergency_stop()` |
| 显式复位 | `request_reset()` → `worker.reset_control()` → `_queue_action()` ⇢ `_perform_action()` → `session.reset_control()` ⇄ `handle_reset_control()` → 成功时 `_clear_session()` → `session.open()` |
| 窗口关闭 | `closeEvent()` → `worker.close()` → `wait_closed(0)` 检查后台，尚未退出时由 `poll_secure_status()` 再触发关闭 |
| TLS 连接结束 | `serve_management_connection()` 的 `finally` → `on_close_connection()` ⇢ `handle_connection_closed()` → 拥有当前会话时执行 `close_control_session()`，保留原有锁存 |
| 车端进程退出 | `vehicle.app.main()` 的 `finally` → 有活动会话时 `close_control_session()` → 退出管理上下文 → 设置停止事件、取消待办 Future、`worker.join()` |

[Qt 操作入口](../src/rover/desktop/app.py#L337) · [后台动作](../src/rover/desktop/control_worker.py#L157) · [车端看门狗](../src/rover/vehicle/control_guard.py#L26) · [车端会话](../src/rover/vehicle/management_session.py)

</details>

<a id="experiment-tools"></a>

## 实验入口及回归

<details>
<summary>展开独立进程、离线分析和相关测试</summary>

| 工具或模块 | 入口和关键调用 | 对应代码 |
| --- | --- | --- |
| N0–N3 中继 | `main()` → `relay_controls()` → 顺序实验由 `PacketOrderPolicy.receive()` / `flush_due()` 调度 | [故障中继](../src/rover/transport/udp_control_faults.py) · [顺序策略](../src/rover/transport/udp_control_order.py) · [测试](../tests/test_udp_control_faults.py) |
| N4/N5 中继 | `main()` → 启动 `_read_requests()` 线程和 `relay_controls()` → 队列传递触发信号 | [断流中继](../src/rover/transport/udp_control_outage.py) · [测试](../tests/test_udp_control_outage.py) |
| A10 管理计时 | `main()` → `measure_management()` → `print_summary()` | [计时入口](../examples/demo_management_metrics.py) · [测试](../tests/test_management_metrics.py) |
| 离线汇总 | `main()` → `summarize_trials()` → `_summarize_vehicle()` / `_summarize_relay()` | [汇总入口](../examples/summarize_network_trials.py) · [测试](../tests/test_network_trial_summary.py) |
| 管理和保护 | 记录分派、线程交接、车端准入、急停及复位 | [分派](../tests/test_management_connection.py) · [运行时](../tests/test_management_runtime.py) · [准入](../tests/test_windowed_control_guard.py) · [急停](../tests/test_emergency_stop.py) · [复位](../tests/test_control_reset.py) |
| 桌面和遥测 | 发送取消、窗口状态、数据回传和轨迹 | [后台](../tests/test_control_worker.py) · [窗口](../tests/test_desktop_control.py) · [遥测](../tests/test_udp_telemetry.py) · [轨迹](../tests/test_trajectory.py) |

[实验部署](../README.md#control-outage-lab) · [验收矩阵](../README.md#advanced-acceptance-status) · [验收补充](verification.md)

</details>
