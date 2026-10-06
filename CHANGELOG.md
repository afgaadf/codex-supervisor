# 变更日志

本文件记录「管家」的重要变更。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本 2.0.0](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.6.1] - 2026-10-07

### Fixed
- **Obsidian 插件在真实知识库上会噪声淹没（真机安装时实测发现）**：本机 `20_附件/原始资料`
  有 **63791 个文件（1.35 GB）**，插件把它当成普通内容，
  报出「63845 个孤立附件 / 1286 篇缺 frontmatter / 1572 处断链」。修复后同一真库为
  **277 个孤立附件 / 30 篇缺 frontmatter / 16 处断链**。
  - 缺 frontmatter、断链改为只统计知识区（`20_附件` / `30_模板` / `90_归档` 不算）。
  - 新增设置项 `attachmentIgnoreFolders`（默认 `["20_附件/原始资料"]`）。
  - 孤立附件列表上限 `ORPHAN_ATTACHMENT_MAX_LIST=50`，超出只报数量。
- 插件版本 0.1.0 → **0.1.1**（manifest / main.js / versions.json 同步）。

### Added
- `plugins/obsidian/selftest.js`：插件自带 Node 自测（含"大归档不淹没报告"回归用例）。
- `tests/test_obsidian_plugin_selftest.py`：用 Node 跑该自测（无 node 自动跳过）。

### Tests
- 新增 3 条测试；全量 **109 条全绿**。

## [0.6.0] - 2026-10-07

### Added
- **Codex 插件**（`plugins/codex/`）：一个 skill（`SKILL.md`，让 Codex 在声称完成前先自查）
  + 一个纯标准库 **stdio MCP 服务器**（`mcp_server.py`），暴露 3 个工具：
  `supervisor_status`（当前分数/等级/维护模式）、`failure_modes`（失败模式库）、
  `check_text`（对一段文本做确定性自检）。附 `install.py` 幂等安装器。
- **Obsidian 插件**（`plugins/obsidian/`）：标准社区插件（manifest/main.js/styles.css/
  versions.json），体检改在 Obsidian 进程内跑（事件驱动 + 5 秒节流），结果写成报告给管家。
- **管家界面显示两个插件**：Codex 页显示 Codex 监督插件状态，Obsidian 页显示管家插件
  状态并带「装进知识库」按钮（`plugins_status.py`）。
- 补完欠账 A11 的 6 个确定性检查器：`codex.unbounded_retry`、`codex.no_library_lookup`、
  `obsidian.canvas_drift`、`obsidian.bases_property_mismatch`、`obsidian.property_type_drift`、
  `obsidian.frontmatter_schema_drift`（读正文的 frontmatter / `.canvas` / `.base`）。
- 打包随带 `plugins/`；`Supervisor.spec` 增加 `checkers`/`failure_modes`/`plugins_status`
  隐藏导入。

### Fixed
- 属性类型漂移不再把「空值」当成一种类型；块列表（`key:` + 缩进 `- `）不再误判为「空」；
  多行标量（`key: |`）不再误判为「空」。

### Tests
- 新增 `tests/test_plugins_status.py`（15 条）与 `tests/test_checkers_content.py`（16 条）；
  全量 **106 条全绿**。

### Known
- Obsidian 插件只有替身 mock 验证，尚未在真实 Obsidian 里跑过（欠账 A12）。
- Codex 插件尚未在真实 Codex 会话里注册/调用过（欠账 A13）。

## [0.5.0] - 2026-10-07

### Added
- 失败模式库从 28 项扩到 **60 项**（Codex 30 · Obsidian 30），并给每条补上
  可机读的 source / severity / auto 字段。
- 新增 **`checkers.py` 自动检查器**：把 `auto=yes` 的条目从"提示词"升级为
  **真能跑的确定性检测**（只读、可解释、带证据行）。
  - Codex 侧：完成声明无动作、改了不验证、假装验证、工具报错当成功、
    重复绕圈、自证完成、无证据的肯定断言、大文件全量读等。
  - Obsidian 侧：断链、改名未修链接、缺 frontmatter、孤立笔记、收件箱未分流、
    收件箱堆积、孤立附件、未提交等，可直接读 `vault_index.json` 跑。
- 自动检查结果并入每轮判据（规则标签带【自动】），确定性证据优先。
- 界面「帮助」页失败模式库卡片新增"检测力"行（可自动检出 / 需人工判 计数）。

### Sources
- Obsidian：官方 Help 文档（canvas / bases / properties / aliases / links / tags /
  attachments / templates / daily-notes，2026-10-07 访问，均为 T1）。
- Codex：官方 developers 文档本机仍返回 403；相关条目继续明确标为
  **项目经验（非权威）**，不冒充官方规则。

### Fixed
- 纯锚点链接 `[[#xxx]]` 不再误判为断链。
- 工具用文件（AGENTS.md / CLAUDE.md / maintenance_prompt.md）不再被算作
  缺 frontmatter 或孤立笔记。

### Tests
- 新增失败模式库与检查器测试；全量测试保持全绿。

## [0.4.0] - 2026-10-07

### Added
- 新增可机读失败模式库 `rubrics/_failure_modes.json`：Codex 14 项、Obsidian 14 项。
- `judge` 每轮根据文本自动筛选最多 8 条相关失败模式，注入判断提示词。
- 界面「帮助」页新增失败模式库卡片。
- 失败模式覆盖完成声明、测试缺失、危险操作、范围扩大、上下文失忆、来源伪造、
  断链、缺属性、重复笔记、孤立笔记、MOC、附件位置、链接修复和未提交等。

### Sources
- Obsidian：官方 Help 文档（Internal links / Properties / Attachments / Backlinks）。
- Codex：官方 developers 文档 2026-10-07 直连返回 403；相关条目明确标为
  **项目经验（非权威）**，不冒充官方规则。

### Tests
- 新增 `tests/test_failure_modes.py`；共 54 条全绿。

## [0.3.1] - 2026-10-07

### Changed
- JSONL 尾部读取改为按块增量读取，不再把大日志全量载入内存。
- 判断循环、告警/审计页和历史记录页统一使用增量读取。
- `latest_turn` 优先读取 transcript 末尾 4 MiB；尾部不足时保留全量回退。

### Tests
- 新增 `tests/test_jsonl_tail.py`；共 51 条全绿。

## [0.3.0] - 2026-10-07

### Added
- 依赖锁定：`requirements.txt` 与 `requirements-dev.txt`。
- Windows CI：自动运行 47 条单元测试；手动工作流可构建未签名 MSIX。
- 公开工程文档：`PRIVACY.md`、`SECURITY.md`、`THIRD-PARTY-NOTICES.md`。
- GitHub 公开仓库与 `v0.2.3` 预发布。

### Changed
- 公开文档、预览和脚本移除本机绝对路径，改用 `%USERPROFILE%` 或通用占位符。
- 版本升级到 `0.3.0`。

### Security
- 公开前完成常见 token / API key / 私钥 / 密码模式扫描，无命中。
- 正式 Release 仍待用户申请 CA 代码签名证书。
## [0.2.3] - 2026-10-07

### Fixed
- Windows 通知改为「单工作线程 + 单常驻 PowerShell 助手 + 有界队列」；
  不再每条通知新起一个 PowerShell 进程。

### Tests
- 新增通知载荷编码与队列上限测试；共 47 条全绿。

## [0.2.2] - 2026-10-07

### Fixed
- 界面看板从 2 秒全量销毁/重建控件降为 10 秒，并在最小化时跳过重建，避免刷新本身拖垮桌面。
- 现场验证：重启新窗口后连续 60 秒采样，窗口最大约 5% CPU，通知 PowerShell 子进程为 0。

### Tests
- GUI 冒烟测试锁定刷新间隔不少于 10 秒；共 45 条全绿。

## [0.2.1] - 2026-10-07

### Fixed
- 修复「弹窗 + 卡顿」：窗口检测到独立后台管家活着时，过去仍会启动 monitor / brain / judge
  三套循环，导致同一轮被两个进程重复判定、重复通知；现在先探测，后台活着时窗口只做看板。
- 违规通知按「规则集合 + 数量」做 15 分钟去重，同一签名窗口内只弹一次；窗口长度是
  **项目约定（非权威）**，不是 Windows 规定。
- 判定入口增加同一进程内互斥，避免自动循环与「立即判定」按钮并发写入重复记录。

### Added
- MSIX 路线：一次 PyInstaller 构建两个 exe（窗口 + 后台管家），新增
  `packaging\msix\AppxManifest.xml.template`、`build_msix.py`、本地测试证书脚本和
  `uap5:StartupTask` 声明。
- `paths.RESOURCE_DIR`：把 PyInstaller 的 `_internal` 只读资源根与安装根 `APP_DIR`
  分开，修复打包后找不到 `home.ico` / `rubrics` / `rules_hard` 资源的问题。

### Tests
- 新增 `tests/test_notify_and_backend.py`、`tests/test_msix_manifest.py`；共 45 条全绿。

> 已知限制：本机 MSIX 安装验证被 `0x80073CFF`（需开发者模式/旁加载）和自签名根证书
> 信任策略挡住；包结构、签名动作和冻结后台已分别验证通过。公开分发仍需 CA 代码签名证书。

## [0.2.0] - 2026-10-07

### Changed
- **结构（A1 完成）**：`supervisor_gui.py` 从 **2611 行降到 902 行**。13 个页面的渲染方法
  （`pg_home`/`pg_overview`/`pg_trust`/`pg_corr`/`pg_changes`/`pg_logs`/`pg_codex`/`pg_vault`/
  `pg_classes`/`pg_rules`/`pg_learn`/`pg_pc`/`pg_help`）全部移入新模块 `gui_pages.py` 的
  `PagesMixin`；`Main` 改为 `class Main(PagesMixin, QMainWindow)`。
  **方法体一字未改，行为不变**；分 5 批小提交完成。
  依据（T1）：Python 3 教程 §9.5.1「Multiple Inheritance / MRO」
  https://docs.python.org/3/tutorial/classes.html

### Added
- `gui_data.py` —— 界面共享常量（路径 + 大白话词表），消除循环依赖。

## [0.1.1] - 2026-10-07

### Fixed
- **Stage 2b 拆分引入的回归（本次自查发现并修复）**：`supervisor_gui.py` 调用了被搬到
  `gui_widgets.py` 的 `_load_pc_state`；`gui_widgets.py` 里又用到未导入的
  `GLOSSARY` / `GLOSSARY_LONG` / `APP_DIR` / `Path` —— 结果是 **home、pc 等页面在渲染时抛 `NameError`**。
  修法：抽出 `gui_data.py` 统一存放共享常量；`fresh` / `_load_pc_state` 归位到 `gui_util.py`。
- 上述缺陷**编译期查不出来**，而原冒烟测试只验"能切到那一页"、没验"页面真的渲染成功"，所以漏检。
  已把冒烟测试改为：拦截 `_log`，断言没有任何"页面渲染失败"。
- 新增静态守卫测试 `tests/test_no_undefined_names.py`，把"用了却没定义/没导入的名字"挡在提交前。

## [0.1.0] - 2026-10-07

首个纳入版本控制、并带自动化测试的版本。

### Added
- 版本控制：`git` 仓库、`.gitignore`、`.gitattributes`（统一 LF）。
- 自动化测试：`tests/`，共 34 条，全部用标准库 `unittest`（零第三方依赖）。
  - `test_widget_lifetime.py`：控件生命周期（含缺陷复现）。
  - `test_codex_trust.py`：官方哈希引擎契约（13 条）。
  - `test_gui_util.py`：纯工具函数（13 条）。
  - `test_gui_smoke.py`：离屏建界面并逐页渲染 13 页。
- 模块拆分：`gui_util.py`（纯工具）、`gui_widgets.py`（Qt 小部件工厂）。
- `CHANGELOG.md`、`version.py`、`欠账登记.md`。
- README 增补「开发与测试」一节。

### Changed
- `supervisor_gui.py` 从 2611 行拆到 2136 行（纯工具与小部件各归其位，按原名再导出）。
- 窗口标题显示版本号。

### Fixed
- 界面刷新销毁控件后，后台线程回调再访问导致的
  `RuntimeError: libshiboken: Internal C++ object ... already deleted`
  （改用 `shiboken6.isValid` 判活，替换掉吞异常的 `try/except`）。
- 后台线程（`pc_bg` / 联网查证）引用被回收的隐患：改为列表持有引用。

### Removed
- `corrections.json` 移出版本库（每轮追加的运行期载荷，可由 `rules_hard.json` 复现）。
