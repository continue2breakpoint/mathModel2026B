# portrelay —— Windows 侧极简 TCP 转发

用途：官方模拟器 `jammers-simulator.exe` 只监听 **Windows 回环**（`127.0.0.1:2026` / `[::1]:2026`，题面要求），
而策略代码跑在 Linux 宿主机上。`portrelay` 在 Windows 里把宿主机发往
`192.168.122.161:2026` 的连接转发到 `127.0.0.1:2026`。

替代了两个更"重"的方案：

- `netsh interface portproxy`：绑 `0.0.0.0:2026` 会抢占回环，导致模拟器启动时
  `startup.log` 报 `machine-dog API port 2026 is unavailable`（详见
  `docs/robot-link-windows-vm.md` §3.5），且条目持久化在注册表里不易察觉；
- qemu 用户态网卡的 `hostfwd` 端口映射：会在宿主机上多出一个 `0.0.0.0:2026` 监听，暴露面大。
  该网卡现在只用于 SMB 文件共享。

## 编译（Windows 自带 .NET Framework，无需安装任何东西）

```powershell
& "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /target:exe `
  /out:'C:\protableTool\portrelay\portrelay.exe' portrelay.cs
```

产物约 **6.6 KB**。

## 运行

```
portrelay.exe <监听地址> <监听端口> <目标地址> <目标端口>
portrelay.exe 192.168.122.161 2026 127.0.0.1 2026
```

只绑定指定地址（**不绑 `0.0.0.0`**），因此不会与模拟器绑定的 `127.0.0.1` / `[::1]` 冲突。
日志写同目录 `portrelay.log`。绑定失败会每 5 秒重试（应对开机时地址还没就绪）。

## 用计划任务托管（开机自启）

```powershell
$act   = New-ScheduledTaskAction -Execute 'C:\protableTool\portrelay\portrelay.exe' `
           -Argument '192.168.122.161 2026 127.0.0.1 2026'
$trg   = New-ScheduledTaskTrigger -AtStartup
$princ = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$set   = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
           -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName 'PortRelay' -Action $act -Trigger $trg -Principal $princ -Settings $set -Force
Start-ScheduledTask -TaskName 'PortRelay'
```

> ⚠️ 不要用 `Start-Process` 从 WinRM/远程会话里直接起常驻进程：命令一返回进程就被回收。
> 常驻进程一律交给计划任务。

## 前置条件

- Windows 防火墙放行入站 TCP 2026（本机规则名 `PortProxy 2026`，Allow / 所有配置文件）
- 客户端侧使用 `--base-url http://192.168.122.161:2026`

## testsrv.cs

验证用的临时"上游"（在 `127.0.0.1:<port>` 上返回固定 JSON 响应），用来在不启动模拟器的情况下
验证 `portrelay` 是否真的把流量送到了目标端口。平时不需要运行。
