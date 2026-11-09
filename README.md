# network-programming-project
Group project for Network Programming (MUST).

## 当前可运行内容

- 离线车辆运动与分阶段控制演示。
- 控制目标的 JSON 编解码与输入校验。
- 同一台电脑上，两个独立进程通过 UDP 传递控制目标。
- 本地控制超时保护与按真实时间节奏运行的车辆演示。
- 同机持续 UDP 车辆演示：非阻塞接收、超时停车、故障锁存后忽略普通控制。
- 实际车辆状态与故障标志的 JSON 编解码、UDP 传输及独立接收端显示。
- PySide6桌面遥测观察台：地图位置与朝向、状态读数，以及等待/可用/过期提示。

## 学习导图

这两张图分别说明单条消息的收发，以及整个项目的控制与遥测关系。
图源直接保存在下面的 `mermaid` 代码块中，方便修改名称、调整布局和移动文档位置。

### 图 1：一条控制消息怎样到达接收端

```mermaid
flowchart TB
    subgraph TX["发送端：send_target(sock, target, destination)"]
        direction TB
        tx_target["target：ControlTarget 对象"]
        tx_packet["packet：待发送的 JSON 字节"]
        tx_target -->|"encode_target(target)"| tx_packet
    end

    subgraph RX["接收端：receive_target(sock)"]
        direction TB
        rx_packet["packet, peer = sock.recvfrom(...)<br/>取得字节和来源地址"]
        rx_target["target：还原的 ControlTarget 对象<br/>最终返回 (target, peer)"]
        rx_packet -->|"decode_target(packet)"| rx_target
    end

    tx_packet -->|"sock.sendto(packet, destination)<br/>UDP 字节报文"| rx_packet
```

- 从上到下只看一条主线：目标对象 → 编码字节 → UDP 发送与接收 → 还原目标对象。
- 发送端绑定临时来源端口；接收端先绑定 `address = ("127.0.0.1", 9999)` 并等待。`destination` 对应该接收地址。
- `packet` 是 UTF-8 JSON 载荷字节；`peer` 是 Socket 返回的来源地址，不是 JSON 里的字段。
- 此图对应单包演示的阻塞接收。`sent` 是本机发送字节数，不是接收确认；当前演示没有回包。

对应源码：[发送演示](demo_udp_sender.py)、[接收演示](demo_udp_receiver.py)、
[收发函数](udp_transport.py)、[控制编解码](control_codec.py)。

### 图 2：三个终端怎样控制小车并回传状态

```mermaid
flowchart LR
    subgraph SG_C["终端 C：控制发送"]
        direction TB
        c_target["target = ControlTarget(...)"]
        c_send["send_target(sock, target, destination)"]
        c_target --> c_send
    end

    subgraph SG_B["终端 B：车辆仿真与保护"]
        direction TB
        b_receive["try_receive_target(sock)<br/>received = (target, peer) 或 None"]
        b_guard["guard = update_control_guard(...)<br/>输入 incoming；超时先锁存"]
        b_step["state = step_vehicle(...)<br/>使用 guard.last_accepted"]
        b_telemetry["snapshot = TelemetrySnapshot(...)<br/>send_telemetry(telemetry_sock, ...)"]
        b_receive --> b_guard --> b_step --> b_telemetry
    end

    subgraph SG_A["终端 A：遥测接收"]
        direction TB
        a_receive["try_receive_telemetry(sock)<br/>received = (snapshot, peer) 或 None"]
        a_display["state = snapshot.state<br/>显示位置、朝向、速度和故障位"]
        a_receive -->|仅收到合法快照时| a_display
    end

    SG_C -->|destination 对应车端 address：UDP 9999| SG_B
    SG_B -->|telemetry_address 对应显示端 address：UDP 10000| SG_A
```

- C 提供期望目标；B 更新实际 `state` 后，将它与 `guard.fault_latched` 组合成 `snapshot`；A 只显示收到的状态。
- `incoming` 是本轮收到的目标或 `None`。B 在收包前、收包后都检查保护，图中合并展示；超时锁存后，普通新目标仍会被忽略。
- 暂无报文返回 `None`，坏报文抛出 `ValueError`。B 仍继续保护、仿真及按期遥测；A 暂无数据时等待，不伪造全零状态。
- B 用独立的 `telemetry_sock` 发遥测，因此 A 的 `peer` 是车端遥测来源地址，与 B 收到控制时的 `peer` 不同。

对应源码：[周期控制发送](demo_udp_stream_sender.py)、[车辆运行环](demo_udp_vehicle_loop.py)、
[遥测接收显示](demo_udp_telemetry_receiver.py)、[保护状态](control_guard.py)、[遥测快照与编解码](telemetry_codec.py)。

### 后续怎样维护导图

- 改变量或函数名时，编辑图中文字即可；`c_target`、`b_step` 等节点 ID 只用于连线，不必跟着业务变量改名。Python 改名后需同步图中文字，图不会自动重构代码。
- 移动整个小节或代码块即可调整图在 README 中的位置；第二张图的 `flowchart LR` 是横排，改成 `flowchart TB` 可竖排，由 Mermaid 自动布局。
- 源码链接使用仓库相对路径，不绑定本机盘符或 PNG 存放目录。后续疑难点可以按同样的小标题和图源格式继续添加。

## 运行环境

Windows PowerShell、Python 3.12。终端演示和核心模型使用 Python 标准库；
桌面界面开发使用 PySide6，版本记录在 `requirements.txt`。

以下命令均在仓库根目录执行。

## 创建环境与运行测试

首次使用时创建虚拟环境：

```powershell
py -3.12 -m venv .venv
```

安装桌面界面依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

运行测试：

```powershell
.\.venv\Scripts\python.exe -m unittest -v
```

当前共 63 项测试，通过时显示 `OK`。运行演示另按下文进行多终端验收。

## 离线演示

```powershell
.\.venv\Scripts\python.exe demo_simulation.py
.\.venv\Scripts\python.exe demo_control.py
```

## 同机 UDP 收发演示

打开两个终端，分别进入仓库根目录。

终端 A 先启动接收端：

```powershell
.\.venv\Scripts\python.exe demo_udp_receiver.py
```

看到 `waiting for UDP` 后保持运行，在终端 B 启动发送端：

```powershell
.\.venv\Scripts\python.exe demo_udp_sender.py
```

发送端会打印发送字节数、来源地址和目标地址。接收端应打印来源地址，以及：

```text
ControlTarget(speed_mps=2.0, heading_rad=0.5)
```

接收端绑定 `127.0.0.1:9999`；发送端绑定端口 `0`，
由操作系统分配临时源端口。两端打印的来源端口应一致。

`127.0.0.1` 用于同一台电脑内通信。接收端收到一条消息后退出；
再次演示时，先重新启动接收端。等待期间可按 `Ctrl+C` 结束。

## UDP 控制目标驱动车辆模型

两个终端均进入仓库根目录。

终端 A 启动车辆演示接收端：

```powershell
.\.venv\Scripts\python.exe demo_udp_vehicle.py
```

看到等待提示后，终端 B 运行发送端：

```powershell
.\.venv\Scripts\python.exe demo_udp_sender.py
```

初始车辆状态由接收端通过 `VehicleState()` 创建，位置位于原点。
发送端提供目标速度与绝对朝向。

应用目标后，以 `0.02` 秒为步长推进 50 步，共 1 秒模拟时间；
轨迹包含初始时刻，共 51 个状态。

发送速度 `2.0 m/s`、朝向 `0.5 rad` 时，关键输出为：

```text
states=51
start: x=0.0000m y=0.0000m
end: sim_t=1.00s x=1.7552m y=0.9589m
```

这是收到一条消息后的离线模拟，1 秒模拟时间可以很快计算完成。

## 控制超时与定时运行演示

在一个终端中运行：

```powershell
.\.venv\Scripts\python.exe demo_watchdog_loop.py
```

脚本在启动时构造一次速度为 `2.0 m/s`、朝向为 `0 rad` 的已接受目标，
随后以 `0.02 s` 固定模拟步长循环更新车辆，并使用单调时钟计时与等待。
控制记录不再更新时，第一次检查到距接受时刻已达 `0.5 s` 就返回停车目标；
约 `1 s` 后结束演示。输出包括速度变化及最终位置。

- `accepted_at_s` 记录最后一次接受有效控制的时刻，决定控制是否超时。
- `started_at_s` 记录演示启动时刻，决定是否达到总演示时长。
- `wall_t` 是实际经过时间，`sim_t` 是累计模拟时间；调度会使两者不同。

验收时观察速度从 `2.0` 变为 `0.0`，且停车记录与最终记录的 `x` 相同。
具体停车时刻、模拟总时长和终点位置以实际输出为准，不要求恰好为 `0.500 s`、
`1.000 s` 或 `1.000 m`。调度延迟可能推迟超时检查，此演示不提供硬实时保证。

本脚本使用本地构造的控制目标验证运行节奏与超时行为；实际 UDP 收发见下方演示。

## 持续 UDP 车辆与故障锁存演示

两个终端均进入仓库根目录。终端 A 启动持续接收端：

```powershell
.\.venv\Scripts\python.exe demo_udp_vehicle_loop.py
```

看到等待提示后，终端 B 运行原单包发送器：

```powershell
.\.venv\Scripts\python.exe demo_udp_sender.py
```

首次目标被 `accepted`，车辆开始运动。停止发送后，接收端首次检查到距最后一次接受
有效控制已达 `0.5 s` 时停车，打印 `fault_latched=True`。
此时在 B 再运行一次发送器，应显示 `ignored`，速度仍为零，坐标与停车记录相同。

接收端每轮最多尝试接收一个报文，无数据立即继续；无效报文打印 `rejected`，
仍会执行本轮保护和运动更新。每步模拟推进 `0.02 s`，循环使用单调时钟安排等待。

`wall_t` 从接收端启动时累计，包含首次发送前的等待；坐标是在本轮推进后打印，
因此首次接受目标的日志已包含一次模拟位移。日志时间有舍入及调度误差，不表示硬实时保证。

按 `Ctrl+C` 结束接收端。本阶段故障后通过人工重启接收端重新演示，
普通控制包不能解除锁存。当前仅为本机教学原型，显式使能、身份认证、
会话与序号和新鲜度窗口将在后续实现。遥测回传见下方三终端演示。

## 周期发送与控制时效刷新

先重启上述持续接收端，清除上一轮的故障锁存；然后在另一个终端运行：

```powershell
.\.venv\Scripts\python.exe demo_udp_stream_sender.py
```

发送器使用同一个 Socket，每隔约 `0.1 s` 发送一次相同控制目标，共20次，
首末发送约相隔 `1.9 s`。控制台的 `1/20` 等数字只表示发送进度，不是报文协议序号。

每次目标被接受都会更新接受时间，因此持续发送期间，车辆应保持运动超过
首次接受后的 `0.5 s`；发送结束后，接收端按最后一次接受时间计算超时并停车锁存。
验收时比较接收端首条、末条接受记录及停车记录，不能仅凭发送字节数认定已送达。

## 三终端控制与遥测回传

控制发送器将目标发往车端 `127.0.0.1:9999`；车端使用另一个 UDP Socket，
将实际状态发往遥测接收端 `127.0.0.1:10000`。三个程序均在同一台电脑运行。
遥测包含位置、绝对朝向、实际速度和 `fault_latched`，来自车端本轮更新后的状态。

三个终端均进入仓库根目录。先结束旧车辆程序，按以下顺序启动。

终端 A，启动遥测接收端：

```powershell
.\.venv\Scripts\python.exe demo_udp_telemetry_receiver.py
```

终端 B，启动持续车辆程序：

```powershell
.\.venv\Scripts\python.exe demo_udp_vehicle_loop.py
```

终端 C，发送 20 条周期控制：

```powershell
.\.venv\Scripts\python.exe demo_udp_stream_sender.py
```

按以下步骤验收，并保留 A、B 对应同一次车辆运行的日志：

1. 发送控制前，A 应显示原点、零速度和 `fault_latched=False`。
   从未接受控制时，启动等待本身不会触发当前故障锁存。
2. 周期发送期间，B 显示 `accepted`，A 显示位置变化、速度 `2.0 m/s`、
   朝向 `0.5 rad` 和 `fault_latched=False`。
3. 停止发送后，B 按最后一次有效控制接受时间检查超时并停车；
   A 随后显示零速度、`fault_latched=True`，后续坐标保持不变。
   最终坐标随实际循环调度变化，不固定要求某个数值。
4. 保持 B 运行，再在 C 运行一次周期发送器；B 应显示 `ignored`，
   A 仍显示锁定停车，不能因普通新控制自动恢复。
5. 保持 B 运行，只在 A 按 `Ctrl+C` 退出并重新运行接收端；
   A 应再次收到当前车辆状态。重启显示端不清除车端故障。

若 B 的车辆程序已经退出，只重启 A 时会停留在等待提示：此时没有车端继续发送，
A 不会自行生成车辆状态，需要重新启动 B。当前虚拟车进程每次启动都会创建新的原点状态，
与仅重启 A 并保留车端状态不同。若 A 一直运行而 B 退出，旧日志只是最后一次收到的状态；
当前终端演示尚无遥测过期提示，后续控制台阶段补充。

车端仍按 `0.02 s` 固定模拟步长推进；遥测目标发送周期为 `0.05 s`（20 Hz），
每轮最多尝试一次，落后时跳过积压。实际发送节奏受系统调度影响，不提供硬实时保证。
遥测发送异常只在发送支路记录，错误日志最多每秒一次，后续车辆保护循环继续执行。

A 的普通状态日志最多约每秒四行，收到故障标志变化时立即显示；
因此显示行数不能用来测量实际遥测发送频率。A 打印的来源地址属于车端专用遥测 Socket，
与 C 的控制发送端口不同；程序重启后临时来源端口可能变化。

暂时没有可读遥测时，接收接口返回 `None`，不会伪造全零状态；
无效报文抛出 `ValueError`，该报文已经从接收队列取走，接收程序可继续处理后续报文。

## 桌面遥测观察台

本机三终端演示中，A改用桌面窗口；B仍运行车端，C仍发送控制。
先关闭旧的命令行遥测接收器或其他观察台，避免同时占用10000端口；
重新演示时也要先结束旧车端，清除上一次故障锁存。

终端A启动窗口：

```powershell
.\.venv\Scripts\python.exe demo_desktop_console.py
```

随后终端B运行`demo_udp_vehicle_loop.py`，终端C运行`demo_udp_stream_sender.py`。
二者完整命令见上方三终端演示。窗口展示车端通过UDP回传的状态，不自行推进车辆模型。

2026-11-09用户在本机完成以下六项人工验收，并提供等待、原点、锁定和过期截图：

1. 只启动窗口：显示“等待遥测”，没有车辆，读数为占位符。
2. 启动B：原点出现车辆，显示“遥测可用”。
3. C周期发送：车辆向右上方移动，位置、朝向、速度读数更新。
4. C结束：车端超时停车，“保护”显示“已锁定”，遥测仍可用。
5. 结束B：约1.0秒未收到合法遥测后显示“遥测过期”，保留最后位置和读数。
6. 窗口可正常拖动、调整大小和关闭。

界面等待/可用/过期来自本地接收情况，保护锁存来自车端回传的`fault_latched`，
两者独立。过期时读数只代表最近一次收到的状态，不能证明车辆此刻仍在该位置或具有该速度。
`telemetry_timeout_s=1.0`只决定界面何时提示过期，与车端0.5秒控制超时保护不同；
当前合法报文表示通过格式和字段校验，认证、会话及网络新鲜度机制仍待后续接入。

`QTimer`目标每20毫秒调用`poll_telemetry()`；每次最多尝试8包，
有合法状态时更新地图，无新包时仍检查接收间隔。坏包不刷新`last_received_s`。
定时器和实际显示频率受调度影响；这组人工验收不代替自动测试、跨机或性能验收。

地图逻辑在[desktop_map.py](desktop_map.py)，收包与显示状态在
[demo_desktop_console.py](demo_desktop_console.py)，坐标转换在
[map_geometry.py](map_geometry.py)。当前地图显示最近状态，运行轨迹与控制输入随后接入。
