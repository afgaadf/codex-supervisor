# codex-supervisor

**独立于 Codex 的监管程序。信任只能由人经此界面授予。**

## 为什么是独立程序

Codex 自带的 hook 信任有个结构性问题：信任哈希只覆盖「handler 的配置身份」
（事件名 + matcher + command/timeout），**不覆盖脚本内容** —— 也就是说，
被监管的一方可以直接改脚本来改行为、而信任状态不变。我们实测过：改了
`hook_supervisor.ps1`，`trusted_hash` 一字未变。

所以本程序把它做成**独立进程、独立目录**的一个软件，并用**界面**来管信任。

## 它做什么

1. **列出 Codex 的全部 hooks**（读 `~/.codex/hooks.json`）
2. 对每条 handler **按官方算法算当前哈希**，与 `~/.codex/config.toml` 的
   `[hooks.state.*].trusted_hash` 比对 → 显示 **trusted / new / modified**
3. **信任 / 撤销信任**（只在这个界面上点；写接口需要本进程内存里的会话令牌）
4. 每次信任动作 **写审计**（`audit.jsonl`，append-only）
5. 顶部显示 anti-degradation supervisor 的实时状态（`NORMAL/DEGRADED/BLOCKED`）

## 官方标准的精确复刻（这是本程序的地基）

算法来源（openai/codex @ main，2026-10-06 在线核对）：

- `codex-rs/hooks/src/engine/discovery.rs` → `hook_hash()`
- `codex-rs/config/src/fingerprint.rs` → `version_for_toml()`
  `TOML → JSON → 递归按键排序 → 紧凑序列化 → sha256 → "sha256:<hex>"`
- `normalize_command_hook()`：`SessionEnd/Interrupt` 默认 1s / 上限 3s；其它 600s

**交叉验证**：用本程序重算你机器上 `hooks.json` 的 11 条 handler，
与 `config.toml` 已存的 `trusted_hash` **11/11 完全一致**（含 `session_end`
被 clamp 到 3s 的那条）。

## 用法

```
双击桌面「监督者」          # 原生窗口（推荐，无浏览器）
run_app.cmd                # 同上，命令行版
run.cmd                    # 带控制台，方便看报错
```

**界面怎么改**：先读 `设计依据.md` —— 里面是改界面时必须遵守的规则（字体阶梯、
对比度、状态指示器、信息层级），每条都指得到权威出处；指不出出处的不许写成规则。

**快捷键**：`F5` 刷新 · `Ctrl+Tab` 切页 · `Esc` 收进后台。「换外观」按钮切浅色/深色
（不设时跟随 Windows 的深浅色设置，存在 `settings.json`）。

**原生窗口版** = 界面 + 后端 + 一个只读为主的本机小 API
（`http://127.0.0.1:8765/` 仍在，浏览器可打开看同一份数据，当备用视图）。
窗口每次刷新都写 `logs/app.heartbeat`；看门狗 `watchdog.py` 靠它判断窗口还活不活，
心跳过期就自动拉起。点 × 只收进后台，真要退出用窗口上的「退出」按钮
（会写 `logs/app.stopped`，看门狗尊重这个标记、不再重启）。

## 文件

| 文件 | 作用 |
|---|---|
| `supervisor_gui.py` | **原生窗口界面**（PySide6）+ 指令框 + 卡片流（旧 Tk 版已退休到 backup） |
| `supervisor_ui.py` | 后端：监控循环 / 判断循环 / 信任写接口 / 本机 API |
| `codex_trust.py` | 官方哈希引擎 + 信任状态判定（无依赖，纯标准库） |
| `watchdog.py` | 看门狗：心跳过期自动拉起窗口（尊重「退出」标记） |
| `run_app.cmd` / `run.cmd` | 启动器（无控制台 / 带控制台） |
| `audit.jsonl` | 审计（运行后生成） |

## 诚实的边界

- 写接口需要**内存里的会话令牌**（重启即变），并且**只监听 127.0.0.1**；
  但**同一个用户下的 agent 仍有可能**去调本机端口 —— 这是"同权限"问题，
  单靠软件无解。
- **真正的边界**是让被监管者**写不到监管者**：把本程序与
  `~/.codex/anti-degradation/`、`hooks.json`、`config.toml` 一起放进
  **沙箱可写范围之外**。这一步只能由你在 Codex 的沙箱/权限设置里做。
- 因此本程序的定位是：**可信的人机信任闸门 + 监管面板**，不是防越狱的沙箱。

## 界面每一块是什么

打开 `http://127.0.0.1:8765/` 后，从上到下：

1. **红色横幅**：平时不出现。只有「① 判定 BLOCKED」或「② 内部 supervisor 停摆」时才出现。
2. **「这个页面怎么看」卡**：30 秒说明 + 等级含义 + 信任图例。
3. **① 本程序独立判定** — 本程序**自己**读 Codex 的原始记录算出的等级。
   - `Codex 记录文件大小` = transcript 文件的字节数（越大越接近"上下文爆炸"）。
   - `看的会话 / 窗口起点` = 它当前盯的是哪个会话、从什么时候开始算。
   - `本程序看过的事件` = 它自己数出来的原始次数。
4. **② Codex 内部 supervisor 的结论** — Codex 自己算的（对照用）。
   - `分数 score` = 越高越糟；到阈值就升级/阻断。
   - `为什么` = 加分的具体原因。
   - `它上次刷新` = 很久没变 + transcript 还在长 ⇒ 内部那套可能停摆。
5. **③ 事件** — 每个方块是一个动作类型的**原始次数**：
   - 用户提交 / 上下文被压缩 / hook 输入解析失败（盲区）/ 工具报错 /
     执行命令 / 一轮结束（宿主 Stop）/ 会话开始。
6. **④ Hook 信任表** — 最重要的一张表。
   - **trusted** = 已信任且定义没变 → **会执行**。
   - **new** = 新增、从未信任 → **不执行**，需要点「信任」。
   - **modified** = 定义被改过、旧信任失效 → **不执行**，需要重新「信任」。
   - 点「信任」= 用 Codex 官方格式写入信任记录（本程序会写审计）。
7. **⑤ 告警与审计** — 上面出过的异常（等级变化、内部停摆）和你每次的信任/撤销动作。

## 电脑管家（2026-10-07 起）

监督者现在也管整机：`pc_guard.py` 用系统自带 CIM/PowerShell 采集（**不引第三方依赖**）。

- **采集**：OS/CPU/内存/各盘/内存 Top 进程/开机自启/自启未运行的服务/网卡/最近补丁/开机时长
- **体检规则**：内存≥88%(要紧)/≥75%(注意) · 磁盘剩余<10%(要紧)/<20%(注意) · 自启>15 项 · 单进程>1GB · 补丁跨年
- **等级**：score 100 起扣（要紧 −22 / 注意 −8）→ ≥88 NORMAL / ≥70 WATCH / ≥40 DEGRADED / <40 BLOCKED
- **动作白名单**（只有三个，全部写 `pc_audit.jsonl`）：
  `kill_process`（只能在 Top 列表里选 + 手输 PID 二次确认）· `clear_temp`（只清 `%TEMP%` 且 7 天前，默认先 dry-run 出清单）· `open_task_manager`
- **接口**：`GET /api/pc/state`、`POST /api/pc/scan`、`POST /api/pc/action`（后两个要令牌）
- **界面**：左栏「电脑管家」页 —— 管家评分/CPU/内存/C盘 KPI + 体检结果 + Top 进程（带"结束"）+ 开机自启表

依据（T1）：Microsoft Learn「运行和 RunOnce 注册表项」、Microsoft Learn「关于性能计数器」；
psutil 官方文档（本机未安装，仅作能力对照）。详见 `事实核查表.md` #12–14。

## Obsidian 桥（2026-10-06 起）

`Obsidian Vault\.obsidian\plugins\supervisor-bridge\` —— 把库接给监督者：
全库索引 / 实时事件流 / 规矩体检（断链·缺 frontmatter·收件箱堆积·未提交 git）/ 状态面板，
并执行监督者下发的**受控写命令**（只有 create_note / append_note / move_note，**不删除**）。
监管者判据见 `rubrics/obsidian-vault.md`；依据见 `事实核查表.md` #9–11。
### 电脑管家 v2（2026-10-07）—— 采样曲线 / 告警历史 / 启动项禁用

| 新增 | 说明 |
|---|---|
| **采样曲线** | 后台线程每 5 分钟记一点（CPU/内存/C盘），落 `logs/pc_history.jsonl`；界面「走势」card 自己画折线（不引绘图库） |
| **告警历史** | 只在**刚越过阈值**时记一条（内存 75/88、CPU 90、C盘低于 10%），落 `logs/pc_alerts.jsonl`，界面「告警历史」可看 |
| **启动项禁用/恢复** | 禁用 = **先把原值写进 `startup_backups.json`，再从 Run 键删除**；恢复 = 按备份写回。HKLM 项要管理员，失败会明说 |
| **界面** | 电脑管家页新增：走势图 · 告警历史 · 启动项表（每行「禁用」按钮）· 「已禁用（可一键恢复）」区 |

**权限边界（没变）**：改系统状态一律二次确认（手输 PID / 输入「禁用」「恢复」「清理」），全部写 `pc_audit.jsonl`；
`clear_temp` 的"真删"分支只清 TEMP 目录里 7 天前的文件。

依据：Microsoft Learn《运行和 RunOnce 注册表项》（Run 键里的**值**就是开机跑的命令）；
"备份-再删"是本项目设计（**非权威**，为的是随时可恢复）。见 `事实核查表.md` #12。
### 电脑管家 v3（2026-10-07）—— 垃圾清理 / 服务 / 启动文件夹 / 温度

| 新增 | 说明 | 实测 |
|---|---|---|
| **垃圾扫描** | 分类统计：用户临时文件、回收站、Edge/Chrome 缓存、下载目录大文件、Windows 更新缓存 | 可清理 **478.1 MB**（临时 463.1 MB / 774 个文件；Chrome 缓存 14.4 MB）；下载目录 6 个大于 200MB 的文件**只列不动** |
| **清理白名单** | 只有 user_temp / browser_cache 真能清（recycle_bin 本机实测不可用，不提供按钮） | 临时文件预览 774 个 / 463.1 MB |
| **启动文件夹项** | 「移动」到 startup_folder_backup（不删），可原样移回 | 表格里每个文件夹项都有「禁用」 |
| **服务** | 自启但没在跑的服务清单 + sc.exe start（要管理员，失败明说） | 本机 7 个 |
| **温度** | 试读 MSAcpi_ThermalZoneTemperature / Win32_TemperatureProbe | **本机读数空** → 界面写「本机可能不支持」，不编数字 |

对应接口：GET /api/pc/junk|services|temps，POST /api/pc/junk-clean、/api/pc/service、/api/pc/startup-folder。
依据见 `事实核查表.md` #15-18。
### 监督者的学习（2026-10-07 起）

- `learn.py`：把「你划掉责令改正 / 你拒绝提案」记成**教训**（`lessons.jsonl`），
  下一轮判分时随提示词一起喂回去（`judge._lessons_block`），同类不再重复报。
- **自主学习**：扫日志找规律（同一规则被划 ≥2 次 / 同一文件改 ≥4 次 / 同一工具错 ≥5 次）
  → 写成**候选教训**（`learn_candidates.json`），**必须你采纳才生效**。
- 指标：`误报率 = 你划掉的 ÷ 它报的`（`learn.metrics()`），界面「学习与成长」页可看、可采纳/丢弃。
- 接口：`GET /api/learn/state`、`POST /api/learn/scan|decide`（decide 要令牌）。
- 界面：所有专业名词**下方**都给了大白话（`GLOSSARY`，同一份用于「怎么看 → 术语表」）。


## 开发与测试（2026-10-07 起）

- **版本控制**：本目录已是 git 仓库（`git log` 可查历史）。`.gitignore` 已排除运行数据、日志、密钥（`plugin_token.txt`）、大文件与备份目录。
- **单元测试**：用标准库 `unittest`，**零第三方依赖**（跟本程序一贯的取舍一致）。
  - 一键跑：`tests\run-tests.cmd`，或在本目录执行 `python -m unittest discover -s tests -v`
  - 现覆盖：GUI 控件生命周期（防 `libshiboken: Internal C++ object ... already deleted`）、`codex_trust` 哈希契约。
- **改动约定**（依据见知识库《成熟公司软件工程心得（对管家系统的适用）》）：
  1. 每步一个自洽的小提交（Google《Small CLs》）。
  2. 改完先跑测试再提交（Fowler《Continuous Integration》）。
  3. 注意：改 `supervisor_gui.py` **要重启窗口才生效**（`fresh()` 只热重载别的模块）。
