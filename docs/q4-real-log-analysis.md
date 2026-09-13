# 问题4 官方日志分析与本地“真实画像”回归

日期：2026-09-13  
对象：`日志4/*.jlog`、历史官方模拟器数据、本地 mock Q4。

---

## 0. 一句话结论

`日志4/*.jlog` 是**服务端可解、参赛端不可解**的行为日志包：本地能稳定还原
`case_code / practice_run_no / 源数画像` 的来源是官方历史数据里的
`*.result.json` 与 `practice-statistics-queue.sqlite3`，而不是日志正文。

我据此搭了两层本地环境：

1. `tools/jlog_inspect.py`：解析日志信封、帧长度、metadata；若你合法持有
   wrap 私钥，可 AES-GCM 解密 + gzip 解压。
2. `_bench_q4_real.py`：把历史官方 p4 的**真实案例画像**（总源数、定向源数）
   映射成可复现的本地 mock 案例，并用同一套 client / strategy 跑回归。

---

## 1. `日志4/*.jlog` 能读出什么

信封格式（已由本地 `jammers-simulator.exe` 的
`packagefmt.WriteEnvelope` / `encryptedFrameWriter` 反汇编确认）：

```text
JMBPLOG1
uint16 envelope_version
uint16 reserved
uint16 header_json_length
header_json
frame*
signature(64B)
```

每帧：

```text
uint32 frame_seq
uint32 plaintext_len
uint32 cipher_len       # 含 16B AES-GCM tag
ciphertext
```

本次五个日志：

| case_code | practice_run_no | created_at_utc | 压缩后长度 | 帧数 | wrap keys |
| --- | --- | --- | ---: | ---: | --- |
| N7NU-DZ5Z-F3A4-F87B | 3450526523680994539 | 2026-09-12T10:55:30.770Z | 65085 | 1 | wrap-backup-2026, wrap-primary-2026 |
| TA5Q-6QJK-H6JK-4AXX | 4213750069266979766 | 2026-09-12T10:54:35.156Z | 250024 | 1 | 同上 |
| HUZT-WD86-QY5N-6WMF | 5776856192837422998 | 2026-09-12T10:53:25.946Z | 99636 | 1 | 同上 |
| RQ8D-EPW6-TYS8-QWMJ | 6720435386824895829 | 2026-09-12T09:55:36.633Z | 118098 | 1 | 同上 |
| 5436-FYKA-KTTV-2SAZ | 8581579197880810122 | 2026-09-12T09:59:16.039Z | 87233 | 1 | 同上 |

正文是 `gzip` + `aes-256-gcm-chunked`；DEK 被 RSA-OAEP-SHA256 包装给
`wrap-backup-2026` / `wrap-primary-2026` 两个**服务端公钥**。本地模拟器只有
公钥，因此没有服务端私钥时无法恢复隐藏坐标、类型、朝向。

```bash
# 只读 metadata
python3 tools/jlog_inspect.py 日志4

# 若日后拿到合法 wrap 私钥
python3 tools/jlog_inspect.py 日志4 --private-key path/to/wrap.pem --out-dir 日志4/decrypted
```

---

## 2. 历史官方 p4 的真实画像

`_scratch_b_full/.../JammersSimulatorData/behavior-logs/*.result.json` 与
`practice-statistics-queue.sqlite3` 是未加密的本地复盘数据，包含：

- `jammer_count`
- `omnidirectional_jammer_count`
- `directional_jammer_count`
- `cleared_jammer_count`
- `measure_accepted_count`
- `virtual_time_us`
- `program_run_duration_ms`

历史五次正式 p4 演练画像如下：

| case_code | 源数 | 定向 | 全向 | 定向比例 | 官方清除 | 官方虚拟时间/s | 官方检测次数 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| KAFK-P4MP-YF32-WHQM | 10 | 10 | 0 | 1.00 | 10/10 | 17863.7 | 881 |
| VZ52-TJUU-FABK-PSCW | 16 | 8 | 8 | 0.50 | 16/16 | 18342.8 | 838 |
| 9W7G-WEAE-9PPW-JWXC | 11 | 9 | 2 | 0.82 | 11/11 | 19540.6 | 913 |
| 9T86-MU58-WMKX-E42B | 13 | 2 | 11 | 0.15 | 13/13 | 13671.0 | 656 |
| BM8Z-7J83-3CGE-88WV | 15 | 14 | 1 | 0.93 | 15/15 | 25661.4 | 1134 |

关键结论：官方 p4 的定向比例并不“默认 50%”，而是可能达到 100%；
本地只生成 `1..n/2` 个定向源会低估难度。

---

## 3. 本地“真实画像”回归环境

`_bench_q4_real.py` 的逻辑：

1. 读取真实画像的 `n_jammers` 与 `n_directional`；
2. 用 `case_code` 做稳定哈希种子，生成**同源数、同定向源数**的本地案例；
3. 通过 `MockSimulator -> HttpSimulatorClient -> Q4Strategy` 运行，和线上同一代码路径；
4. 输出全清率、虚拟时间、路程、检测次数、清除失败次数、墙钟时间。

```bash
# 自动发现历史官方画像
python3 _bench_q4_real.py --discover --list

# 跑 matrix 版 Q4（显式朝向完备布局）
python3 _bench_q4_real.py --discover --param scan_layout=axial

# 对照论文冻结内核
python3 _bench_q4_real.py --discover --strategy paper

# 把日志4 中的未知 case 按历史画像分布生成代理案例
python3 _bench_q4_real.py --jlog-dir 日志4 --all-profiles --param scan_layout=axial
```

---

## 4. 一次可复现的回归结果

盐 `q4-real-v1`，历史五画像，本地代理真值：

| 策略 | 全清 | 虚拟时间中位数/s | 路程中位数/m | 检测次数中位数 | 清失败中位数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `matrix` + `scan_layout=axial` | 2/5 | 12213 | 50692 | 379 | 3 |
| `paper` 冻结内核 | 5/5 | 20262 | 76123 | 877 | 0 |

`matrix` 失败案例的共同特征：定向源比例高（10/10、14/15、9/11、11/13），
失败频道有 2 条近乎共线的读数，可行域 MEC 停在 400m 左右，
横向补测没有把交会角打开，连续 `/clear` 失败。该模式与
`docs/review-response-2026-09-13.md` 中 Q4 seed 6 的已知退化几何一致。

一例诊断（本地代理 `BM8Z` 的 channel 4）：

```text
两条读数方向差 ≈ 0.09°（几乎共线）
可行域 MEC ≈ 425m
clear_failure_count = 3
最终漏清 channel 4
```

论文冻结内核的 31 点轴向网格会走更多路，但上述五类真实画像全部清除。
因此当前最稳妥的工程策略是：

1. **默认保留 paper/axial 作为兜底**，不要用 matrix 的超短路线替代“确保全清”；
2. matrix 只作为快速路径，一旦出现 MEC 停留在 60~500m 且 `_geometry_is_poor`，
   就放弃继续沿射线/小环补测，切换到“能形成大交会角”的探查点；
3. 本地回归至少覆盖 `dir_ratio = 0.15 / 0.5 / 0.82 / 0.93 / 1.0` 五档，
   而不是只测 50% 混合。

---

## 5. 已生成文件

| 文件 | 作用 |
| --- | --- |
| `tools/jlog_inspect.py` | 解析 `.jlog/.psum` 信封；可选 wrap 私钥解密 |
| `_bench_q4_real.py` | 真实画像驱动的本地 Q4 回归与 CSV/JSON 报告 |
| `docs/q4-real-log-analysis.md` | 本文档 |

日志正文解密需要服务端 wrap 私钥；在拿到之前，不要根据 `.jlog` 文件大小
反推隐藏源数量，误差会很大，且会把“压缩后的行为长度”误当成“源数”。
