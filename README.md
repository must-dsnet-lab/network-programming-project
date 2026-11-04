# network-programming-project
Group project for Network Programming (MUST).

## 当前可运行内容

- 离线车辆运动与分阶段控制演示。
- 控制目标的 JSON 编解码与输入校验。
- 同一台电脑上，两个独立进程通过 UDP 传递控制目标。

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

当前共 33 项测试，通过时显示 `OK`。

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