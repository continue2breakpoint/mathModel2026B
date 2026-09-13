# Linux ↔ Windows 虚拟机 robot API 链路：实测说明、故障判据与 Windows 放行操作

> 记录时间：2026-09-13 10:20–10:40（北京时间）
> 适用对象：本机（Linux 宿主机 `KFZPC`）上的 libvirt 虚机 `win10` 里跑官方模拟器 `jammers-simulator.exe`，
> 由 Linux 侧 `mathModel2026B/script/run.py --mode live` 通过 `robot-protocol-v1` 驱动。
> 本文只描述**本机这套联调链路**，不含任何逆向产物。

---

## 0. 一页结论（TL;DR）

| # | 结论 |
| --- | --- |
| 1 | **"TCP 握手成功" 不能证明连到了 Windows。** 只要链路上存在任何中间转发器（qemu `hostfwd`、`netsh portproxy`、`ssh -L`、`socat`），三次握手就由转发器在**本机内核**里完成，之后它再去连下游。下游不通时，客户端照样看到 ESTABLISHED，请求被吞掉、Windows 侧抓不到任何东西。 |
| 2 | 本次故障（10:20 前）就是这个假象：Linux 侧 `127.0.0.1:2026` 的"服务端"其实是 **qemu 为 win10 用户态网卡做的 SLIRP 端口转发**，它把连接投递到 guest 的 `10.0.2.15:2026`，而模拟器**只监听 Windows 回环**，于是请求全部石沉大海。 |
| 3 | 现行方案（已生效）：**在 Windows 内跑一个 ~6.5 KB 的转发进程 `portrelay.exe`**，只绑 `192.168.122.161:2026` → 转发到 `127.0.0.1:2026`，由 SYSTEM 计划任务 `PortRelay` 开机自启（见 §3.7、§5.1）。qemu 用户态网卡只保留 SMB，不再做端口映射。 |
| 4 | 修复后两条路径都实测可用，往返时延 ~1 ms，30 次串行请求 30/30 成功。 |
| 5 | 真正的探活必须发**真实 HTTP 请求**（`GET /` 或 `POST /enter`）。实测：**门控关闭时裸 TCP connect 依旧 100% 成功**（0–3 ms）——它永远区分不出"可用"与"门控关"，也区分不出"链路通"与"只通到转发器"。 |
| 6 | **转发器绝不能绑 `0.0.0.0:2026`**：实测会让模拟器启动时报 `machine-dog API port 2026 is unavailable`（见 `startup.log`），robot API 起不来。`portrelay` 只绑 `192.168.122.161`，模拟器要用的 `127.0.0.1` / `[::1]` / 全地址绑定实测**仍然全部可用**（见 §3.5、§3.7）。 |
| 7 | **Windows 的时区/时钟必须正确**：本机曾因时区为 Pacific Standard Time 使系统 UTC 偏差 **+15 小时**；模拟器据此判断是否已过测试截止时间，偏差可能导致直接拒绝开始测试（见 §3.6）。 |
| 8 | **可以从 Linux 用 WinRM 直接操作 Windows 的 cmd.exe / powershell.exe**（本机 `pwsh` 缺 WSMan 客户端库，改用 pywinrm + NTLM，见 §4.4），改配置、看日志、验端口都不必到 VM 控制台。 |
| 9 | **`request_id` 必须每次动作唯一**：复用同一个 id 的动作会被 HTTP **409** 拒绝（`{"accepted":false}`）。仓库里的 `HttpSimulatorClient` 每次调用都取 `uuid4().hex`，行为正确。 |
| 10 | **完整协议已实测跑通**：`/enter → /measure → /exit` 全部 `accepted:true`，`measure_result`/`svd_deg` 正常返回（见 §3.4）。 |

> **本次在 Windows 实机上做过的改动**（2026-09-13 11:2x–11:3x，经 WinRM 执行，均可回滚）：
> 1. 时区 `Set-TimeZone -Id 'China Standard Time'`，并用 Linux 的正确 UTC 校准系统时钟（见 §3.6）
> 2. 部署 `C:\protableTool\portrelay\portrelay.exe`（6.5 KB，源码 `portrelay.cs`）+ SYSTEM 计划任务 `PortRelay`
>    （开机自启），仅绑 `192.168.122.161:2026` → `127.0.0.1:2026`（见 §3.7、§5.1）
> 3. 删除此前由我加的两条 `netsh interface portproxy` 条目（历史方案，见 §3.5、§5.2）
> 4. 未改动防火墙规则（`PortProxy 2026` 仍为 Allow / 所有配置文件）；未改动 libvirt 域 XML；未启动模拟器

---

## 1. 拓扑与数据流（现行架构）

```
                      ┌──────────────────────── Linux 宿主机 KFZPC ────────────────────────┐
   run.py --mode live ┤  --base-url http://192.168.122.161:2026                          │
                      │  192.168.122.1 (virbr0)                                            │
                      └──────────────────────────┬────────────────────────────────────────┘
                                                 │ virbr0 / e1000e
                      ┌──────────────────────────▼────────────────────────────────────────┐
                      │ libvirt domain `win10` (DESKTOP-VERJ953)                           │
                      │  网卡1 e1000e  network=default  MAC 52:54:00:be:40:07 → .122.161   │
                      │  网卡2 e1000e  qemu user-mode(SLIRP) 10.0.2.15 —— 只用于 SMB 共享  │
                      │                                                                    │
                      │  [portrelay.exe]  LISTEN 192.168.122.161:2026  ──┐                 │
                      │  [Windows 防火墙] 入站放行 TCP 2026               │                 │
                      │  SYSTEM 计划任务 PortRelay（开机自启）            ▼                 │
                      │  [jammers-simulator.exe] LISTEN 127.0.0.1:2026 + [::1]:2026（只回环）│
                      └────────────────────────────────────────────────────────────────────┘
```

历史形态（10:31–11:33）：宿主机侧曾依赖 qemu `hostfwd`（宿主机 `0.0.0.0:2026`）与 Windows `netsh portproxy`
两条转发链，链路 A/B 都可用；因 §3.5（`0.0.0.0` 抢占回环导致模拟器起不来）与暴露面问题，
已改为上图的单一 `portrelay` 方案（见 §3.7）。

关键配置（实测来源）：

| 项 | 值 | 来源 |
| --- | --- | --- |
| 虚机 | domain `win10`，uuid `ace1da7b-a203-4540-b3c3-ba85c519370c` | `virsh -c qemu:///system list` |
| 网卡1 | `type='network'` → `network=default`/`virbr0`，MAC `52:54:00:be:40:07` → `192.168.122.161` | `virsh domiflist win10`、`/var/lib/libvirt/dnsmasq/virbr0.status` |
| 网卡2 | `qemu:commandline`: `-netdev user,id=mynet.0,hostfwd=tcp::2026-:2026,smb=/home/kfz/share` + `-device e1000e,netdev=mynet.0,id=net1,addr=0x09.0`（hostfwd 待删除，只留 `smb=`） | `virsh dumpxml win10` |
| 模拟器监听 | `127.0.0.1:2026`、`[::1]:2026` | Windows `netstat -ano \| findstr 2026` |
| Windows 转发 | `portrelay.exe 192.168.122.161 2026 127.0.0.1 2026`（SYSTEM 计划任务 `PortRelay`） | `C:\protableTool\portrelay\`、`Get-ScheduledTask PortRelay` |
| 防火墙 | 入站规则 `PortProxy 2026`，TCP 2026 放行（所有配置文件） | `Get-NetFirewallRule` |

**客户端侧**：由于默认的 `http://127.0.0.1:2026` 依赖已被弃用的 qemu 端口映射，
启动策略时必须显式给出地址：

```bash
python3 script/run.py --mode live --base-url http://192.168.122.161:2026
```

---

## 2. 故障判据表（看到什么 = 什么原因）

| 现象 | 真实含义 | 该动哪里 |
| --- | --- | --- |
| connect **超时**，无 SYN-ACK | SYN 被丢弃：目标地址没有监听者且 Windows 防火墙默认入站全拒（如早先直连 `192.168.122.161:2026`） | Windows 侧 portproxy + 防火墙放行 |
| `curl` 退码 **52** `Empty reply from server`、Python 抛 `RemoteDisconnected`，耗时 1–3 ms（**TCP 仍连得上**） | **门控未开**（测试未开始 / 5 秒倒计时 / 测试已结束）。实测指纹，见 §3.3 —— 这是**正常状态，不要动网络** | 在 GUI 里开始一次演练/正式测试 |
| HTTP **409** + `{"accepted":false,...}` | 动作被拒：`request_id` 复用了同一个值（见 §3.4） | 每次动作换新的 `request_id` |
| `Connection reset` 且连 TCP connect 都失败 | 该端口真的没人监听（模拟器没起 / portproxy 没了 / hostfwd 没了） | 查 §4、§5 |
| **握手成功、请求无响应、Windows 侧什么都看不到** | 你连的是**中间转发器**，它的下游不通（本次故障形态）；请求字节会滞留在本机 socket 接收队列里 | 检查转发链的目标是不是 guest 回环（见 §4） |
| 返回 `{"accepted":false,...}`（HTTP 200/400/404） | **链路已通**，属于协议/业务拒绝（路径不对、字段缺失、`robot_id` 不是当前登录队号等） | 改请求，不用改网络 |
| 返回 `{"accepted":true,...}` | 完全可用 | — |

> 反面教材：仓库里 `script/preflight.py::probe_robot_port()` 只做 TCP connect。
> 在"转发器在、下游不通"的状态下它会打印 `robot 127.0.0.1:2026 -> open`，
> 让人以为 `--mode live` 可以直接跑。**判断可用性请用 §4 的真实 HTTP 探测。**

---

## 3. 实测证据（本次）

### 3.1 修复前（10:25–10:31）

```
$ ss -tln | grep 2026
LISTEN 0  1  0.0.0.0:2026  0.0.0.0:*          # backlog=1，属主 uid 非 kfz（= qemu/libvirt-qemu）
$ curl -sv --max-time 5 http://127.0.0.1:2026/
* Established connection to 127.0.0.1 (127.0.0.1 port 2026)
* Request completely sent off
* Operation timed out after 5005 milliseconds with 0 bytes received     # 请求被吞
$ curl -sv --max-time 5 http://192.168.122.161:2026/
*   Trying 192.168.122.161:2026...
* Connection timed out after 5004 milliseconds                          # 连 SYN-ACK 都没有
```

同时在 `/proc/net/tcp` 里能看到：请求字节（79/81 B）**滞留在宿主机那条 socket 的接收队列**、连接停在 `CLOSE_WAIT`——
即数据从未离开这块网卡；`journalctl -u libvirtd` 里也有旁证：

```
qemu-system-x86_64: -netdev user,id=mynet.0,hostfwd=tcp::2026-:2026,smb=/home/kfz/share: ...
```

### 3.2 修复后（10:31–10:37，Windows 内加好 portproxy + 防火墙规则）

```
$ curl -s http://127.0.0.1:2026/          # 路径 A
HTTP/1.1 404 Not Found
{"accepted":false,"real_timestamp_ms":1789266867722,"virtual_time_s":0}   # 模拟器真实响应 ✅
$ curl -s http://192.168.122.161:2026/    # 路径 B
{"accepted":false,"real_timestamp_ms":1789266867723,"virtual_time_s":0}   # ✅
$ POST /enter  {"robot_id":"000000000000",...}   → HTTP 200 {"accepted":false,...}   # 门控开启、业务拒绝
$ POST /enter  {"arena_id":"default"}（缺字段）   → HTTP 400 {"accepted":false,...}
```

串行压测（模拟正式测试的连续请求，路径 A，30 次）：

| 指标 | 值 |
| --- | --- |
| 成功率 | 30/30 |
| 中位时延 | 1.1 ms |
| p90 | 1.2 ms |
| 最大 | 8.5 ms |

两条路径时延对比（各 10 次）：A 中位 1.2 ms / B 中位 1.0 ms —— **差异可忽略，用默认的 A 即可**。

### 3.3 门控开/关的实测指纹（本次演练结束前后连续采样）

模拟器由 `internal/portguard` 门控：端口一直 LISTEN，但**测试未开始时新连接会被直接关闭、投递不到 HTTP 处理器**
（题面附件 2 §1.5："非测试期间、5 秒倒计时期间以及测试结束后，机器狗接口未开放，连接可能直接失败"）。
本次演练结束的瞬间被后台探针完整记录到（两条路径行为一致）：

| 时刻 | 路径 A `127.0.0.1:2026` | 路径 B `192.168.122.161:2026` | 裸 TCP connect |
| --- | --- | --- | --- |
| 10:39:02（测试进行中） | `HTTP 404` + `{"accepted":false,...}` 2 ms | `HTTP 404` + JSON 1 ms | connect_ok 0 ms |
| 10:39:22（**测试刚结束**） | `RemoteDisconnected`（无任何 HTTP 响应）2 ms | `RemoteDisconnected` 1 ms | connect_ok 0 ms |
| 10:39:42 | `RemoteDisconnected` 3 ms | `RemoteDisconnected` 3 ms | connect_ok 0 ms |
| 10:40:02 | `RemoteDisconnected` 2 ms | `RemoteDisconnected` 1 ms | connect_ok 0 ms |

也就是说：

- **门控关闭的指纹 = TCP 连得上、1–3 ms 内被对端关闭、没有任何 HTTP 响应**（`curl` 退码 52 / Python `RemoteDisconnected`）。
  这**不是网络故障**，去 GUI 里开始测试即可。
- **裸 TCP connect 在两种状态下都是 `connect_ok 0 ms`** —— 它既区分不出门控，也区分不出"链路只通到转发器"。
  这就是 `script/preflight.py::probe_robot_port()` 会给出误导性结论的根本原因。

### 3.4 端到端烟测：`/enter → /measure → /exit`（10:39，演练内执行，不消耗正式次数）

修复后第一次真正的协议级验证（同一演练窗口内）：

```
# 第一次：三个动作复用了同一个 request_id
/enter    -> HTTP 200  {"accepted":true,"real_timestamp_ms":...,"virtual_time_s":0,
                        "max_virtual_duration_s":360000,"max_real_duration_s":1200,
                        "remaining_real_duration_s":71}                     ✅
/measure  -> HTTP 409  {"accepted":false,...}                               ❌
/exit     -> HTTP 409  {"accepted":false,...}                               ❌

# 第二次：每个动作都用全新 uuid4
/measure ch1 @(0,0)   -> HTTP 200 {"accepted":true,"virtual_time_s":5,
                                   "measure_result":"direction","svd_deg":287.57}   ✅
/measure ch3 @(50,0)  -> HTTP 200 {"accepted":true,"virtual_time_s":21,
                                   "measure_result":"no_signal"}                    ✅
/exit                 -> HTTP 200 {"accepted":true,"virtual_time_s":21,
                                   "exit_reason":"user_exit"}                       ✅
```

结论与注意：

1. **`request_id` 必须每个动作唯一**。复用同一个 id 的动作返回 **HTTP 409**（`{"accepted":false}`，响应体里不带原因）。
   本次证据是连续 2 次 409（复用）对 3 次 200（全新 uuid），下次演练可再复核一次 A/B。
   → 仓库 `framework/src/mathmodel2026b/client.py` 的 `_base_payload()` 每次调用都 `uuid4().hex`，**行为正确**；
   自己写临时脚本探测时千万别图省事复用一个 id。
2. `/enter` 的响应里带 `remaining_real_duration_s`，可以用来判断当前演练/正式测试还剩多少真实时间
   （本次 71 秒，随后门控即关闭，见 §3.3）。
3. `/enter` 成功后**必须** `/exit`；被拒（如 409）时机器人会留在场内，下一次动作要用新的 id 重试 `/exit`。
4. 烟测放在**演练**里做，不消耗正式次数：
   ```bash
   curl -s -X POST http://127.0.0.1:2026/enter -H 'Content-Type: application/json' \
     -d "{\"arena_id\":\"default\",\"robot_id\":\"<当前登录队号>\",\"request_id\":\"$(uuidgen | tr -d -)\"}"
   curl -s -X POST http://127.0.0.1:2026/measure -H 'Content-Type: application/json' \
     -d "{\"arena_id\":\"default\",\"robot_id\":\"<当前登录队号>\",\"request_id\":\"$(uuidgen | tr -d -)\",\"position\":{\"x\":0,\"y\":0},\"channel\":1}"
   curl -s -X POST http://127.0.0.1:2026/exit -H 'Content-Type: application/json' \
     -d "{\"arena_id\":\"default\",\"robot_id\":\"<当前登录队号>\",\"request_id\":\"$(uuidgen | tr -d -)\"}"
   ```
   > 上面是 10:39 当时的原始记录（走的是已弃用的 qemu 端口映射）。
   > **现行架构下请把地址换成 `http://192.168.122.161:2026`**（见 §1、§3.7）。

### 3.5 端口归属冲突：portproxy 绑 `0.0.0.0` 会让模拟器再也起不来（实测）

时间线（2026-09-13）：

| 时刻 | 事件 |
| --- | --- |
| 10:31 | 加 portproxy `0.0.0.0:2026 → 127.0.0.1:2026`。此时模拟器**已在运行**并占着 `127.0.0.1:2026`，两者共存，链路可用（§3.2 的 404+JSON 就是此时测到的） |
| 10:42:37 | 演练结束后重启模拟器，`JammersSimulatorData/startup.log` 记录：**`machine-dog API port 2026 is unavailable`** —— GUI 起来了，但 robot API 没监听（`netstat` 里只剩 portproxy 的 `0.0.0.0:2026`） |
| 11:09 | VM 重启；portproxy 随 `iphlpsvc` 自动恢复，**继续占着 `0.0.0.0:2026`** |
| 11:21 | 远程检查确认：模拟器未运行，且 `127.0.0.1:2026` 无法绑定 |

复现证据（WinRM 在 portproxy 绑 `0.0.0.0` 时执行绑定测试）：

```
bind 127.0.0.1:2026 -> 失败: An attempt was made to access a socket in a way forbidden by its access permissions
```

即 WSAEACCES。原因：Windows 里一旦 `0.0.0.0:P` 被占，后续对具体地址的同端口绑定会失败；
而 IP Helper 是**带 SO_REUSEADDR** 的，所以它能压在已在监听的具体地址之上——
**这个方向不对称**：`先模拟器、后 portproxy` 能共存，`先 portproxy、后模拟器` 就直接废掉模拟器。
（`0.0.0.0` 这个绑定还会在每次开机由 `iphlpsvc` 抢先建立，所以重启后必然踩坑。）

**过渡修正**（曾生效，现已由 §3.7 的 `portrelay` 取代，备选方案见 §5.2）：portproxy 分别绑 `10.0.2.15` 与 `192.168.122.161`，把回环让给模拟器。

| 校验项 | 修正后实测 |
| --- | --- |
| 绑定 `127.0.0.1:2026` | 可绑定 ✅ |
| 绑定 `[::1]:2026` | 可绑定 ✅ |
| 绑定 `0.0.0.0:2026` | 可绑定 ✅ |
| `netsh interface portproxy show v4tov4` | `10.0.2.15:2026` 与 `192.168.122.161:2026` 各一条 |
| Linux 路径 A `127.0.0.1:2026`（模拟器未启动） | curl 退码 52（Empty reply），2.0 s——连接到达 guest 后被关闭 ✅ |
| Linux 路径 B `192.168.122.161:2026`（同上） | curl 退码 56（reset），2.0 s ✅ |

### 3.6 时钟/时区：曾经的隐藏地雷（已修复）

WinRM 读到的原始状态：

```
Zone  : Pacific Standard Time (-08:00；当时 DST 生效为 -07:00)
Local : 2026-09-13 11:24:54 -07:00     ← 显示值"碰巧"等于北京时间
UTC   : 2026-09-13 18:24:54            ← 真实 UTC 是 03:24:54，偏差 +15 小时
w32tm : Source: Local CMOS Clock，Leap Indicator: 3(not synchronized)
```

成因：宿主 RTC 存的是**本地时间（北京时间）**，Windows 按其默认语义把 RTC 当"本地时间"读取，
但 guest 时区却是 PST → 系统 UTC = RTC + 7h = 北京时间 + 7h，凭空多出 15 小时。

为什么危险：模拟器用系统时间判断"是否已过测试开始截止时间"（`test_start_deadline = 2026-09-13T09:30:00Z`，
见 `/api/v1/status`）。UTC 多 15 小时时它会认为早就截止，**正式测试可能直接无法开始**。

修复（已生效）：

```powershell
Set-TimeZone -Id 'China Standard Time'          # 把时区改对
# 再用外部基准校一次系统时钟（本次由 Linux 侧传入正确 UTC；也可 w32tm /resync）
$ref = [DateTime]::Parse('<Linux 的 UTC ISO>', [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::AdjustToUniversal -bor [Globalization.DateTimeStyles]::AssumeUniversal)
Set-Date -Date ([TimeZoneInfo]::ConvertTimeFromUtc($ref, [TimeZoneInfo]::Local))
```

修复后：`Zone = China Standard Time (+08:00)`、`Local = 2026-09-13 11:25 +08:00`、`UTC = 2026-09-13 03:25`（与 Linux 相差 <5 s）。
因为宿主 RTC 是本地时间、guest 时区也是 +08:00，**重启后时间自洽**（Windows 关机时按本地时间写回 RTC）。

遗留：`w32tm` 仍为 `Local CMOS Clock`（未同步 NTP），长期会缓慢漂移；正式测试前用 §4.4 复查一次即可。

### 3.7 现行架构：Windows 侧轻量转发 `portrelay`（取代 portproxy 与 qemu 端口映射）

**动机**：不再依赖 `netsh interface portproxy`（绑 `0.0.0.0` 会抢占回环，见 §3.5；条目还持久化在注册表里、不易察觉），
也不再让 qemu 用户态网卡做端口映射（那会在宿主机上凭空多出一个 `0.0.0.0:2026` 监听，暴露面大）。
qemu 用户态网卡从此只保留 SMB 文件共享。

**实现**：`C:\protableTool\portrelay\portrelay.exe`——C# 写的极简 TCP 转发，`csc.exe` 编译，**6656 字节，零外部依赖**；
调用形式 `portrelay.exe <监听地址> <监听端口> <目标地址> <目标端口>`，只绑指定地址（不绑 `0.0.0.0`），
每个连接两条线程做双向 `CopyTo`；由 SYSTEM 计划任务 `PortRelay`（触发条件：开机）托管，日志写同目录 `portrelay.log`。

**实测验证**（2026-09-13 11:33–11:36）：

| 步骤 | 结果 |
| --- | --- |
| 编译 | `csc.exe /nologo /target:exe` → `portrelay.exe` = **6656 字节** ✅ |
| 临时上游 `127.0.0.1:12026` + relay 指向它，**Windows 本地**自测 | `GET /selftest` → `{"marker":"TEST-UPSTREAM",...}` ✅ |
| **从 Linux** `curl http://192.168.122.161:2026/relaytest` | `{"accepted":false,"marker":"TEST-UPSTREAM","first":"GET /relaytest HTTP/1.1"}`，1.5–4.7 ms ✅ |
| 切到正式目标 `127.0.0.1:2026` 后 `netstat` | `192.168.122.161:2026 LISTENING 1164`（SYSTEM 会话 0）✅ |
| `netsh interface portproxy show v4tov4` | 已清空 ✅ |
| 端口共存（relay 占着 `192.168.122.161:2026`） | 绑 `127.0.0.1:2026` ✅ / `[::1]:2026` ✅ / `0.0.0.0:2026` ✅（模拟器可正常启动） |
| 从 Linux `curl http://192.168.122.161:2026/`（模拟器尚未启动） | 2.05 s 后连接被关闭（curl 退码 56）——请求确实到达了 relay ✅ |

> ⚠️ **用 `Start-Process` 从 WinRM 会话里起的进程会随该次 WinRM 调用结束被回收**（实测：命令一返回进程就消失，
> Linux 侧表现为连接超时）。常驻进程一律交给**计划任务**托管。

**客户端侧的变化**：默认的 `http://127.0.0.1:2026` 走的是宿主机上那个 qemu hostfwd，端口映射取消后即失效，
所以从 Linux 跑策略要显式指定地址：

```bash
cd mathModel2026B
python3 script/run.py --mode live --base-url http://192.168.122.161:2026
```

（`preflight.py` 的探测主机名硬编码为 `127.0.0.1`，只传 `--robot-port` 改不了地址；要一起探测就直接
`curl -s -X POST http://192.168.122.161:2026/enter ...`，见 §4.2。）

---

## 4. 诊断命令（可直接复制）

### 4.1 Linux 侧（三条定性）

```bash
sudo ss -tlnp 'sport = :2026'          # 谁在监听：出现 qemu-system-x86_64 说明是 SLIRP 转发器
ss -tln | grep 2026                    # 无 sudo 也能看 bind 形态（0.0.0.0 / 127.0.0.1）
virsh -c qemu:///system list
virsh -c qemu:///system domiflist win10
virsh -c qemu:///system dumpxml win10 | sed -n '/qemu:commandline/,/\/qemu:commandline/p'
sudo tcpdump -ni any -c 20 'tcp port 2026'   # 客户端请求时抓：只在 lo 上出现 = 包没出宿主机
```

### 4.2 真实 HTTP 探活（**唯一可信的判活方式**）

```bash
# 只读、无副作用：能拿到 JSON 就说明链路通（现行地址是 VM 的 LAN IP，见 §3.7）
curl -sv --max-time 5 http://192.168.122.161:2026/
# 业务级探测：用错误 robot_id，被拒绝也不产生任何状态（正确队号请用 login-jammers 配置里的值）
curl -s -X POST http://192.168.122.161:2026/enter -H 'Content-Type: application/json' \
     -d '{"arena_id":"default","robot_id":"000000000000","request_id":"probe"}'
# 期望：HTTP 200 + {"accepted":false,...}
```

### 4.3 Windows 侧（VM 内，管理员）

```powershell
netstat -ano | findstr :2026
Get-NetTCPConnection -LocalPort 2026 -State Listen | ft LocalAddress,LocalPort,OwningProcess
Get-Process -Id <PID> | ft Id,ProcessName,Path      # 8352≈模拟器；3284≈svchost(iphlpsvc)
Get-NetConnectionProfile | ft InterfaceAlias,NetworkCategory   # 虚拟网卡常被判为 Public
Get-NetFirewallProfile | ft Name,Enabled,DefaultInboundAction
```

### 4.4 从 Linux 用 WinRM 直接操作 Windows（cmd.exe / powershell.exe）

**前提**：VM 上 WinRM 监听 5985（`curl -s -o /dev/null -w '%{http_code}' http://192.168.122.161:5985/wsman` 返回 **405** 即正常）。

Linux 侧 `pwsh` **默认不带 WSMan 客户端库**（`libpsl-omi.so` 缺失、无 `PSWSMan` 模块，`New-PSSession -ComputerName` 用不了，补装需要 root），
所以用 **pywinrm** 走 WSMan：

```bash
mkdir -p ~/_winrm && cd ~/_winrm
python3 -m pip install --target ./pylibs "pywinrm[ntlm]"

# 连（NTLM，坑：工作站不在域里，Kerberos 不可用；Basic 默认未开）
python3 - <<'PY'
import sys; sys.path.insert(0, "./pylibs")
import winrm
s = winrm.Session("http://192.168.122.161:5985/wsman",
                  auth=("kfzovw", "<密码>"), transport="ntlm")
print(s.run_cmd("hostname").std_out)                       # 走 cmd.exe
print(s.run_ps("[DateTime]::UtcNow").std_out)              # 走 powershell.exe
PY
```

落库的助手脚本（本次使用，位于工作区 `_probe2026/`，**非 git 仓库**）：

| 文件 | 作用 |
| --- | --- |
| `_probe2026/wr.py` | `python3 wr.py cmd "<命令>"` / `wr.py ps "<脚本>"` / `wr.py probe`（身份+端口+portproxy） |
| `_probe2026/remote_ps.py` | 把本地 `.ps1` 分块上传到 VM 执行并取回输出（`python3 remote_ps.py xxx.ps1 [参数]`） |
| `_probe2026/winrm.env` | 主机/端口/账号密码（600 权限，勿提交） |

远程执行连踩四个坑，助手里都处理了：

1. **WinRS 命令行长度上限（约 8k）**：`run_ps` 会把脚本 base64 成 `-EncodedCommand`，长脚本必失败 → 分块（每块 1400 字符）上传到临时文件再执行。
2. **默认 `ExecutionPolicy = Restricted`**：`& script.ps1` 报 `cannot be loaded because running scripts is disabled` → 执行前 `Set-ExecutionPolicy -Scope Process Bypass -Force`。
3. **编码**：PS 5.1 对**无 BOM** 的 `.ps1` 按 ANSI 解码，中文会变乱码 → 上传字节时加 UTF-8 BOM；取回输出改为 base64（纯 ASCII）后在 Linux 解码，彻底绕开代码页问题。
4. **改系统时钟会顶掉 WinRM 会话**：WSMan 用时间算超时，时钟一跳跃，当前会话的 cleanup 会返回 `HTTP 400`（重连即可；命令本身多半已生效）。

---

## 5. Windows 侧接入：转发器、放行与排查

模拟器自身**硬绑 127.0.0.1/[::1]**（题面要求，`netstat` 里只会看到这两个回环地址），
所以"让宿主机能访问"这件事由**转发器 + 防火墙**两件事决定，与模拟器设置里的"端口号"无关。

### 5.1 现行方案：`portrelay`（SYSTEM 计划任务）

| 文件 / 对象 | 说明 |
| --- | --- |
| `C:\protableTool\portrelay\portrelay.exe` | 转发器本体，**6656 字节**，`csc.exe` 编译，零外部依赖 |
| `C:\protableTool\portrelay\portrelay.cs` | 源码（工作区副本 `_probe2026/portrelay.cs`） |
| `C:\protableTool\portrelay\portrelay.log` | 启动 / 绑定失败 / 上游连接失败日志 |
| `C:\protableTool\portrelay\testsrv.exe` | 验证用临时上游（5120 字节，平时不运行，可删） |
| 计划任务 `PortRelay` | 开机以 **SYSTEM** 启动 `portrelay.exe 192.168.122.161 2026 127.0.0.1 2026` |

日常运维（可在 VM 里执行，也可经 §4.4 的 WinRM 远程执行）：

```powershell
# 状态
Get-ScheduledTask -TaskName PortRelay | Select-Object TaskName,State
Get-Process portrelay | Select-Object Id,SessionId,StartTime
netstat -ano | findstr ':2026'
Get-Content 'C:\protableTool\portrelay\portrelay.log' -Tail 20

# 重启
Stop-ScheduledTask -TaskName PortRelay ; Start-ScheduledTask -TaskName PortRelay

# 改目标（例如模拟器端口改成 2030）
$act = New-ScheduledTaskAction -Execute 'C:\protableTool\portrelay\portrelay.exe' `
         -Argument '192.168.122.161 2026 127.0.0.1 2030'
Set-ScheduledTask -TaskName PortRelay -Action $act
Stop-ScheduledTask -TaskName PortRelay ; Start-ScheduledTask -TaskName PortRelay

# 停用 / 启用 / 卸载
Disable-ScheduledTask -TaskName PortRelay ; Enable-ScheduledTask -TaskName PortRelay
Stop-ScheduledTask -TaskName PortRelay ; Unregister-ScheduledTask -TaskName PortRelay -Confirm:$false
Get-Process portrelay -ErrorAction SilentlyContinue | Stop-Process -Force

# 改源码后重新编译
& "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /target:exe `
  /out:'C:\protableTool\portrelay\portrelay.exe' 'C:\protableTool\portrelay\portrelay.cs'
```

> 想改任务名或安装路径：`Unregister-ScheduledTask` 后用新名字 `Register-ScheduledTask` 重建即可，与转发器本身无关。

### 5.2 备选方案：`netsh interface portproxy`（历史方案，含陷阱）

```cmd
:: 查看现状
netsh interface portproxy show all
netsh interface portproxy show v4tov4

:: 历史方案（现已弃用，仅作参考；若确要用，只绑具体地址，把 127.0.0.1/[::1] 留给模拟器）
::   · 10.0.2.15        服务 qemu SLIRP 路径（路径 A）
::   · 192.168.122.161  服务 virbr0 直连路径（路径 B）
netsh interface portproxy add v4tov4 listenaddress=10.0.2.15       listenport=2026 connectaddress=127.0.0.1 connectport=2026
netsh interface portproxy add v4tov4 listenaddress=192.168.122.161 listenport=2026 connectaddress=127.0.0.1 connectport=2026

:: ⚠️ 不要用这条：它会把回环端口一起占住，模拟器再启动就会报
::    startup.log: "machine-dog API port 2026 is unavailable"（见 §3.5）
:: netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=2026 connectaddress=127.0.0.1 connectport=2026

:: 删除（例如改绑地址前）
netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0          listenport=2026
netsh interface portproxy delete v4tov4 listenaddress=10.0.2.15        listenport=2026
netsh interface portproxy delete v4tov4 listenaddress=192.168.122.161 listenport=2026

:: 依赖服务：IP Helper（当前：Running / Automatic）
sc query iphlpsvc
sc config iphlpsvc start= auto
net start iphlpsvc
```

> 接口地址会变（换网段、SLIRP 参数改动）时，先 `ipconfig` 确认，再按上面两条重建。

要点：

- portproxy 配置**持久化在注册表**（`HKLM\SYSTEM\CurrentControlSet\Services\PortProxy`），重启后仍在，但
  **`iphlpsvc` 必须在运行**，否则 `netstat` 里那条 `0.0.0.0:2026` 会消失。
- 绑 `0.0.0.0` = 该端口在虚机**所有网卡地址**上都可连（含 SLIRP 的 `10.0.2.15` 与 virbr0 的 `192.168.122.161`）。
- 绑 `10.0.2.15` = 只有路径 A 可用，暴露面更小。
- 环境变化（换端口、换网段）后 `ipconfig` 确认地址再改。

### 5.3 防火墙放行（当前规则）

```cmd
:: 现状
netsh advfirewall firewall show rule name="PortProxy 2026" verbose

:: 新增（等价于当前已加的那条；-Profile 省略 = 所有配置文件都放行）
netsh advfirewall firewall add rule name="PortProxy 2026" dir=in action=allow protocol=TCP localport=2026

:: PowerShell 等价写法（推荐，粒度更清楚：只放行 libvirt 网段）
New-NetFirewallRule -DisplayName "PortProxy 2026" -Direction Inbound -Action Allow `
  -Protocol TCP -LocalPort 2026 -Profile Any -RemoteAddress 192.168.122.0/24

:: 收紧 / 改范围：只允许 libvirt 网段
Set-NetFirewallRule  -DisplayName "PortProxy 2026" -RemoteAddress 192.168.122.0/24
netsh advfirewall firewall set rule name="PortProxy 2026" new remoteip=192.168.122.0/24

:: 只对"专用网络"生效（先确保虚拟网卡被判定为 Private，见 5.3）
Set-NetFirewallRule -DisplayName "PortProxy 2026" -Profile Private

:: 临时停用 / 恢复
Disable-NetFirewallRule -DisplayName "PortProxy 2026" ; Enable-NetFirewallRule -DisplayName "PortProxy 2026"

:: 删除
Remove-NetFirewallRule -DisplayName "PortProxy 2026"
netsh advfirewall firewall delete rule name="PortProxy 2026"
```

### 5.4 网卡网络类别（虚拟网卡默认常被判为"公用网络"）

默认"公用网络"配置文件**入站全拒**。若发现规则加了仍不通，先看类别：

```powershell
Get-NetConnectionProfile | ft InterfaceAlias,InterfaceIndex,NetworkCategory,IPv4Connectivity
# 改"专用"（注意 InterfaceAlias 用上面查到的名字，中文系统可能是"以太网 2"）
Set-NetConnectionProfile -InterfaceAlias "Ethernet 2" -NetworkCategory Private
```

### 5.5 确认是"防火墙丢包"而不是别的问题

```powershell
# 打开被丢弃包的日志
Set-NetFirewallProfile -Profile Domain,Public,Private -LogBlocked True -LogAllowed False `
  -LogFileName "$env:systemroot\system32\LogFiles\Firewall\pfirewall.log"
Get-Content "$env:systemroot\system32\LogFiles\Firewall\pfirewall.log" -Tail 40
# 日志出现 DROP ... dst-port:2026 → 规则没匹配上（多半是配置文件/网段/端口写错）
```

临时关防火墙做二分（**诊断完立刻开回来**）：

```cmd
netsh advfirewall set allprofiles state off
netsh advfirewall set allprofiles state on
```

注意：**防火墙只可能造成"超时"或"RST"，绝不会造成"握手成功但无响应"**。
看到后者请去查转发链，不要在 Windows 防火墙里绕圈。

---

## 6. 启动 / 重启 SOP

转发器是常驻的（SYSTEM 计划任务，开机自启），模拟器随时启停都不冲突，**不需要每次清理端口**。

1. 确认 `portrelay` 在跑：`Get-ScheduledTask -TaskName PortRelay | ft TaskName,State`；
   `netstat -ano | findstr :2026` 应有一条 `192.168.122.161:2026`（portrelay，SYSTEM）
2. 启动模拟器 GUI（`C:\zWindowsUtility\Jammers-simulator-full\jammers-simulator-full.exe`）
3. `netstat -ano | findstr :2026` 现在应有 **3 条**：`127.0.0.1:2026`、`[::1]:2026`（模拟器）+ `192.168.122.161:2026`（portrelay）
   —— 若只有 portrelay 那条，去 `JammersSimulatorData\startup.log` 看是不是又报 `port 2026 is unavailable`
4. 在 GUI 里**开始演练/正式测试**（门控开启）
5. **真实 HTTP 探测**：`curl -s --max-time 5 http://192.168.122.161:2026/` → 期望 `{"accepted":false,...}`
6. 先在演练里跑 §3.4 的 30 秒烟测，再跑：
   `cd mathModel2026B && python3 script/run.py --mode live --base-url http://192.168.122.161:2026`
   - 换过端口时：portrelay 的**目标端口**（§5.1）和这里的 `--base-url` 要一起改
   - 环境变量 `JAMMERS_ROBOT_PORT` 只能改端口，改不了主机名
7. （可选）彻底去掉宿主机上遗留的 qemu 端口映射：`sudo virsh edit win10`，把
   `-netdev user,id=mynet.0,hostfwd=tcp::2026-:2026,smb=/home/kfz/share`
   改成 `-netdev user,id=mynet.0,smb=/home/kfz/share`（只留 SMB），重启域后宿主机不再有 `0.0.0.0:2026` 监听

**万一还是遇到端口冲突**：`Stop-ScheduledTask PortRelay` → 启模拟器 → 确认回环两条在 → `Start-ScheduledTask PortRelay`。

---

## 7. 正式测试前 checklist

- [ ] 时钟正确：`Get-TimeZone` = China Standard Time；`[DateTime]::UtcNow` 与 Linux `date -u` 相差 < 10 s（§3.6）
- [ ] 计划任务 `PortRelay` = Running；`netstat` 里有 `192.168.122.161:2026`（portrelay），且**没有** `0.0.0.0:2026`
- [ ] 无残留 portproxy：`netsh interface portproxy show v4tov4` 为空
- [ ] Windows `netstat` 里同时有 模拟器的 `127.0.0.1:2026`/`[::1]:2026` 与 portrelay 的 `192.168.122.161:2026`
- [ ] `JammersSimulatorData\startup.log` 最后几行**没有** `machine-dog API port 2026 is unavailable`
- [ ] 防火墙规则启用：`Get-NetFirewallRule -DisplayName "PortProxy 2026" | ft Enabled,Action,Profile`
- [ ] GUI 里测试处于进行中（门控开）→ 此时 `curl http://192.168.122.161:2026/` 应返回 **JSON**
      （若返回 `Empty reply`/`RemoteDisconnected`，说明门控还没开，见 §3.3，**不是网络问题**）
- [ ] **先在演练里跑一遍 §3.4 的 30 秒烟测**（`/enter → /measure → /exit`，每个动作换新 `request_id`），
      全 `accepted:true` 再开正式测试
- [ ] 启动命令带上地址：`python3 script/run.py --mode live --base-url http://192.168.122.161:2026`
- [ ] `--mode live` 跑起来后，模拟器界面上的统计数据有变化（确认真有请求在走）
- [ ] 记下 `/enter` 返回的 `remaining_real_duration_s`，据此安排正式测试的节奏
- [ ] 演练/正式测试**中途不要改端口**（模拟器端口一改，portproxy、hostfwd、防火墙规则三处全部失效）
- [ ] 不要同时起本地 mock：`python3 -m mathmodel2026b.mock.server --port 2026` 会因宿主机 2026 被 qemu 占用而失败

---

## 8. 已知坑与注意事项

| 坑 | 现象 | 处理 |
| --- | --- | --- |
| **转发器绑 `0.0.0.0:2026`** | 模拟器启动时 `startup.log` 报 `machine-dog API port 2026 is unavailable`，robot API 不监听；重启后 `iphlpsvc`/转发器抢先占位，必然复现 | 只绑具体地址：`portrelay` 就只绑 `192.168.122.161`（§5.1）；历史 portproxy 方案见 §5.2 |
| **从 WinRM 用 `Start-Process` 起常驻进程** | 命令一返回进程就被回收，Linux 侧表现为连接超时 | 常驻进程交给计划任务托管（`Register-ScheduledTask`，见 §3.7、§5.1） |
| 客户端仍用默认 `127.0.0.1:2026` | 端口映射取消后该地址不再指向模拟器（宿主机上的 qemu hostfwd 打不到 guest 回环） | 跑策略时加 `--base-url http://192.168.122.161:2026`（§3.7） |
| **Windows 时区/时钟不对** | 系统 UTC 与真实 UTC 差 15 小时（曾为 PST），模拟器误判"已过测试截止时间" | `Set-TimeZone -Id 'China Standard Time'` + 校时（§3.6） |
| `smb=/home/kfz/share` 写死在 `qemu:commandline` | 目录不存在时 **qemu 直接启动失败**（`Error accessing shared directory`），虚机起不来 | 保证 `/home/kfz/share` 存在，或用 `virsh edit win10` 去掉 `smb=` |
| `net1` 占 `addr=0x09.0` | 若把 qxl-vga 也放到 `0x1` 会报 `PCI: slot 1 function 0 not available ... in use by e1000e,id=net1` | 改 XML 时避开已用 `addr` |
| 宿主机 2026 被 qemu hostfwd 占用 | 本地 mock 起不来；`ssh -R 127.0.0.1:2026:...` 也会 `remote port forwarding failed` | 换端口（`JAMMERS_ROBOT_PORT`），或在 XML 里改/去掉 hostfwd |
| 宿主机 `0.0.0.0:2026` 暴露面 | hostfwd 默认监听所有网卡（含 wlan0 `10.243.5.44`、tailscale），同网段机器可访问该接口 | 需要收紧就把 XML 改成 `hostfwd=tcp:127.0.0.1:2026-:2026`（`virsh edit win10` 后重启域） |
| 中间转发器给的"假通" | connect 成功但无响应（见 §2/§3.1） | 一律用真实 HTTP 探测判活 |
| 门控（portguard） | 非测试期间连接被直接关闭 / 无 HTTP 响应 | 先在 GUI 开始测试，再排查网络 |
| 用 WinRM 跑长脚本 | `The command line is too long.` | 分块上传到临时文件再执行（§4.4） |
| 用 WinRM 跑 `.ps1` | `running scripts is disabled on this system` | 先 `Set-ExecutionPolicy -Scope Process Bypass -Force` |
| WinRM 返回中文乱码 | 英文版 Windows 控制台代码页 + 无 BOM 的 `.ps1` | 上传加 UTF-8 BOM，取回走 base64（§4.4） |
| 改系统时钟后 WinRM 报 `HTTP 400` | WSMan 用时间算超时，时钟跳跃使当前会话失效 | 重连即可，命令通常已生效 |
| 模拟器 exe 所在目录名含零宽字符 | `C:\zW\u200cindowsUtility\...`，手敲/复制路径容易失败 | 用 `Get-ChildItem 'C:\' -Filter 'jammers-simulator*.exe' -Recurse -Depth 3` 定位 |

---

## 9. 附：本页涉及的配置位置速查

| 内容 | 位置 / 命令 |
| --- | --- |
| 虚机 XML（含 custom-argv 的 hostfwd） | `sudo virsh -c qemu:///system edit win10` |
| 虚机网络接口 | `virsh -c qemu:///system domiflist win10` |
| libvirt DHCP 租约（确认 guest IP/主机名） | `/var/lib/libvirt/dnsmasq/virbr0.status` |
| 宿主机谁占了端口 | `sudo ss -tlnp 'sport = :2026'` |
| **Windows 侧转发器** | `C:\protableTool\portrelay\`（`portrelay.exe` / `portrelay.cs` / `portrelay.log` / `testsrv.exe`），SYSTEM 计划任务 `PortRelay` |
| Windows portproxy（历史方案，现已清空） | `netsh interface portproxy show all`；注册表 `HKLM\SYSTEM\CurrentControlSet\Services\PortProxy` |
| Windows 防火墙规则 | `netsh advfirewall firewall show rule name="PortProxy 2026" verbose` |
| Windows 防火墙拦截日志 | `%systemroot%\system32\LogFiles\Firewall\pfirewall.log` |
| **模拟器启动日志（排端口冲突首选）** | `C:\zW\u200cindowsUtility\Jammers-simulator-full\JammersSimulatorData\startup.log` |
| 模拟器行为日志 / 上报队列 | 同目录 `behavior-logs\`、`behavior-runs\`、`*-statistics-queue.sqlite3`、`upload-queue.sqlite3` |
| 模拟器可执行文件 | `C:\zW\u200cindowsUtility\Jammers-simulator-full\jammers-simulator-full.exe` |
| WinRM 远端执行助手（本次使用） | `_probe2026/wr.py`、`_probe2026/remote_ps.py`、凭据 `_probe2026/winrm.env`（非 git 仓库） |
| 客户端默认地址 | `framework/src/mathmodel2026b/client.py`（`http://127.0.0.1:2026`）、`script/run.py`（`DEFAULT_ROBOT_URL`） |
