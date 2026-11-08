# network-programming-project
Group project for Network Programming (MUST).

## 当前可运行内容

- 离线车辆运动与分阶段控制演示。
- 控制目标的 JSON 编解码与输入校验。
- 同一台电脑上，两个独立进程通过 UDP 传递控制目标。
- 本地控制超时保护与按真实时间节奏运行的车辆演示。
- 同机持续 UDP 车辆演示：非阻塞接收、超时停车、故障锁存后忽略普通控制。

## 运行环境

Windows PowerShell、Python 3.12。当前代码仅使用 Python 标准库。

以下命令均在仓库根目录执行。

## 创建环境与运行测试

首次使用时创建虚拟环境：

```powershell
py -3.12 -m venv .venv
```

运行测试：

```powershell
.\.venv\Scripts\python.exe -m unittest -v
```

当前共 45 项测试，通过时显示 `OK`。

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
会话与序号、新鲜度窗口和遥测将在后续实现。

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
