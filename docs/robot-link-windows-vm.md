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
| 3 | 解法（已生效）：在 **Windows 内**把外部地址的 2026 转给回环 —— `netsh interface portproxy` + 防火墙放行 TCP 2026。 |
| 4 | 修复后两条路径都实测可用，往返时延 ~1 ms，30 次串行请求 30/30 成功。 |
| 5 | 真正的探活必须发**真实 HTTP 请求**（`GET /` 或 `POST /enter`）。实测：**门控关闭时裸 TCP connect 依旧 100% 成功**（0–3 ms）——它永远区分不出"可用"与"门控关"，也区分不出"链路通"与"只通到转发器"。 |
| 6 | 顺序很重要：**先删 portproxy → 再启动模拟器 → 最后加 portproxy**，否则模拟器可能报"端口被占用"。 |
| 7 | **`request_id` 必须每次动作唯一**：复用同一个 id 的动作会被 HTTP **409** 拒绝（`{"accepted":false}`）。仓库里的 `HttpSimulatorClient` 每次调用都取 `uuid4().hex`，行为正确。 |
| 8 | **完整协议已实测跑通**：`/enter → /measure → /exit` 全部 `accepted:true`，`measure_result`/`svd_deg` 正常返回（见 §3.4）。 |

---

## 1. 本次实际拓扑与数据流

```
                      ┌──────────────────────────── Linux 宿主机 KFZPC ───────────────────────────┐
                      │  192.168.122.1 (virbr0)          10.243.5.44 (wlan0)   0.0.0.0:2026        │
                      │                                                       ↑ qemu SLIRP hostfwd │
  run.py --mode live ─┤──(A) http://127.0.0.1:2026 ─────────────────────────────┘                    │
                      │──(B) http://192.168.122.161:2026 ──┐                                        │
                      └────────────────────────────────────┼────────────────────────────────────────┘
                                                           │ virbr0 / e1000e
                      ┌────────────────────────────────────▼────────────────────────────────────────┐
                      │ libvirt domain `win10` (DESKTOP-VERJ953)                                    │
                      │  网卡1 e1000e  network=default  MAC 52:54:00:be:40:07 → 192.168.122.161    │
                      │  网卡2 e1000e  qemu user-mode(SLIRP) netdev=mynet.0 → 10.0.2.15            │
                      │                                                                            │
                      │  [IP Helper / netsh portproxy] 0.0.0.0:2026  ──┐                            │
                      │  [Windows 防火墙] 入站放行 TCP 2026            │                            │
                      │                                                ▼                            │
                      │  [jammers-simulator.exe] LISTEN 127.0.0.1:2026 + [::1]:2026（只回环！）     │
                      └────────────────────────────────────────────────────────────────────────────┘
```

关键配置（实测来源）：

| 项 | 值 | 来源 |
| --- | --- | --- |
| 虚机 | domain `win10`，uuid `ace1da7b-a203-4540-b3c3-ba85c519370c` | `virsh -c qemu:///system list` |
| 网卡1 | `type='network'` → `network=default`/`virbr0`，MAC `52:54:00:be:40:07` → `192.168.122.161` | `virsh domiflist win10`、`/var/lib/libvirt/dnsmasq/virbr0.status` |
| 网卡2 | `qemu:commandline`: `-netdev user,id=mynet.0,hostfwd=tcp::2026-:2026,smb=/home/kfz/share` + `-device e1000e,netdev=mynet.0,id=net1,addr=0x09.0` | `virsh dumpxml win10` |
| 模拟器监听 | `127.0.0.1:2026`、`[::1]:2026`（PID 8352） | Windows `netstat -ano \| findstr 2026` |
| 端口代理 | `0.0.0.0:2026`（PID 3284 = IP Helper / iphlpsvc） | 同上 |
| 防火墙 | 入站规则 `PortProxy 2026`，TCP 2026 放行 | 本机添加 |

**两条可用路径（本次都验证通过）**

- **A**：`http://127.0.0.1:2026` → qemu SLIRP hostfwd → guest `10.0.2.15:2026` → Windows portproxy → `127.0.0.1:2026` 模拟器
- **B**：`http://192.168.122.161:2026` → virbr0 直连 guest → Windows portproxy → `127.0.0.1:2026` 模拟器

`script/run.py` 默认就是 A（`DEFAULT_ROBOT_URL = http://127.0.0.1:2026`），无需加参数。

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
# 只读、无副作用：能拿到 JSON 就说明链路通
curl -sv --max-time 5 http://127.0.0.1:2026/
# 业务级探测：用错误 robot_id，被拒绝也不产生任何状态（正确队号请用 login-jammers 配置里的值）
curl -s -X POST http://127.0.0.1:2026/enter -H 'Content-Type: application/json' \
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

---

## 5. Windows 放行行为：怎么改、怎么查、怎么撤

模拟器自身**硬绑 127.0.0.1/[::1]**（题面要求，`netstat` 里只会看到这两个回环地址），
所以"让别的机器/宿主机能访问"这件事**完全由 portproxy + 防火墙两件事决定**，与模拟器设置里的"端口号"无关。

### 5.1 端口代理（netsh interface portproxy，需要管理员 CMD/PowerShell）

```cmd
:: 查看现状（两行都在才说明配置生效）
netsh interface portproxy show all
netsh interface portproxy show v4tov4

:: 新增（当前采用：宿主机两条路径都能进）
netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=2026 connectaddress=127.0.0.1 connectport=2026

:: 只想服务 qemu SLIRP 转发（路径 A），不暴露给 192.168.122.0/24 时用这条代替上面那条：
netsh interface portproxy add v4tov4 listenaddress=10.0.2.15 listenport=2026 connectaddress=127.0.0.1 connectport=2026

:: 删除（改配置前先删，见 §6 顺序）
netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=2026

:: 依赖服务：IP Helper
sc query iphlpsvc
sc config iphlpsvc start= auto
net start iphlpsvc
```

要点：

- portproxy 配置**持久化在注册表**（`HKLM\SYSTEM\CurrentControlSet\Services\PortProxy`），重启后仍在，但
  **`iphlpsvc` 必须在运行**，否则 `netstat` 里那条 `0.0.0.0:2026` 会消失。
- 绑 `0.0.0.0` = 该端口在虚机**所有网卡地址**上都可连（含 SLIRP 的 `10.0.2.15` 与 virbr0 的 `192.168.122.161`）。
- 绑 `10.0.2.15` = 只有路径 A 可用，暴露面更小。
- 环境变化（换端口、换网段）后 `ipconfig` 确认地址再改。

### 5.2 防火墙放行（当前规则）

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

### 5.3 网卡网络类别（虚拟网卡默认常被判为"公用网络"）

默认"公用网络"配置文件**入站全拒**。若发现规则加了仍不通，先看类别：

```powershell
Get-NetConnectionProfile | ft InterfaceAlias,InterfaceIndex,NetworkCategory,IPv4Connectivity
# 改"专用"（注意 InterfaceAlias 用上面查到的名字，中文系统可能是"以太网 2"）
Set-NetConnectionProfile -InterfaceAlias "Ethernet 2" -NetworkCategory Private
```

### 5.4 确认是"防火墙丢包"而不是别的问题

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

## 6. 启动 / 重启 SOP（顺序错了会踩坑）

1. **删掉 portproxy**：`netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=2026`
   （`0.0.0.0:2026` 被 IP Helper 占着时，模拟器启动可能报"端口被占用"）
2. 启动模拟器 GUI，`netstat -ano | findstr :2026` 确认**只有** `127.0.0.1:2026` 与 `[::1]:2026`
3. 重新加回 portproxy（§5.1）并确认防火墙规则在（§5.2）
4. **真实 HTTP 探测**：`curl -s --max-time 5 http://127.0.0.1:2026/` 期望拿到 `{"accepted":false,...}`
   （此时门控可能还没开，见第 5 步）
5. 在 GUI 里**开始演练/正式测试**（门控开启）
6. Linux 侧跑：`cd mathModel2026B && python3 script/run.py --mode live`
   - 用非默认端口时：`--base-url http://127.0.0.1:<port>`，或先用 `preflight.py --robot-port <port>`
   - 环境变量：`JAMMERS_ROBOT_PORT=<port>`（`preflight.py` / `run_practice_online.py` 都读它）

---

## 7. 正式测试前 checklist

- [ ] Windows `netstat` 里 `0.0.0.0:2026`(portproxy) 与 `127.0.0.1:2026`(模拟器) 同时在
- [ ] 防火墙规则启用：`Get-NetFirewallRule -DisplayName "PortProxy 2026" | ft Enabled,Profile,Action`
- [ ] GUI 里测试处于进行中（门控开）→ 此时 `curl http://127.0.0.1:2026/` 应返回 **JSON**
      （若返回 `Empty reply`/`RemoteDisconnected`，说明门控还没开，见 §3.3，**不是网络问题**）
- [ ] **先在演练里跑一遍 §3.4 的 30 秒烟测**（`/enter → /measure → /exit`，每个动作换新 `request_id`），
      全 `accepted:true` 再开正式测试
- [ ] `--mode live` 跑起来后，模拟器界面上的统计数据有变化（确认真有请求在走）
- [ ] 记下 `/enter` 返回的 `remaining_real_duration_s`，据此安排正式测试的节奏
- [ ] 演练/正式测试**中途不要改端口**（模拟器端口一改，portproxy、hostfwd、防火墙规则三处全部失效）
- [ ] 不要同时起本地 mock：`python3 -m mathmodel2026b.mock.server --port 2026` 会因宿主机 2026 被 qemu 占用而失败

---

## 8. 已知坑与注意事项

| 坑 | 现象 | 处理 |
| --- | --- | --- |
| `smb=/home/kfz/share` 写死在 `qemu:commandline` | 目录不存在时 **qemu 直接启动失败**（`Error accessing shared directory`），虚机起不来 | 保证 `/home/kfz/share` 存在，或用 `virsh edit win10` 去掉 `smb=` |
| `net1` 占 `addr=0x09.0` | 若把 qxl-vga 也放到 `0x1` 会报 `PCI: slot 1 function 0 not available ... in use by e1000e,id=net1` | 改 XML 时避开已用 `addr` |
| 宿主机 2026 被 qemu hostfwd 占用 | 本地 mock 起不来；`ssh -R 127.0.0.1:2026:...` 也会 `remote port forwarding failed` | 换端口（`JAMMERS_ROBOT_PORT`），或在 XML 里改/去掉 hostfwd |
| 宿主机 `0.0.0.0:2026` 暴露面 | hostfwd 默认监听所有网卡（含 wlan0 `10.243.5.44`、tailscale），同网段机器可访问该接口 | 需要收紧就把 XML 改成 `hostfwd=tcp:127.0.0.1:2026-:2026`（`virsh edit win10` 后重启域） |
| 中间转发器给的"假通" | connect 成功但无响应（见 §2/§3.1） | 一律用真实 HTTP 探测判活 |
| 门控（portguard） | 非测试期间连接被直接关闭 / 无 HTTP 响应 | 先在 GUI 开始测试，再排查网络 |

---

## 9. 附：本页涉及的配置位置速查

| 内容 | 位置 / 命令 |
| --- | --- |
| 虚机 XML（含 custom-argv 的 hostfwd） | `sudo virsh -c qemu:///system edit win10` |
| 虚机网络接口 | `virsh -c qemu:///system domiflist win10` |
| libvirt DHCP 租约（确认 guest IP/主机名） | `/var/lib/libvirt/dnsmasq/virbr0.status` |
| 宿主机谁占了端口 | `sudo ss -tlnp 'sport = :2026'` |
| Windows portproxy 配置 | `netsh interface portproxy show all`；注册表 `HKLM\SYSTEM\CurrentControlSet\Services\PortProxy` |
| Windows 防火墙规则 | `netsh advfirewall firewall show rule name="PortProxy 2026" verbose` |
| Windows 防火墙拦截日志 | `%systemroot%\system32\LogFiles\Firewall\pfirewall.log` |
| 客户端默认地址 | `framework/src/mathmodel2026b/client.py`（`http://127.0.0.1:2026`）、`script/run.py`（`DEFAULT_ROBOT_URL`） |
