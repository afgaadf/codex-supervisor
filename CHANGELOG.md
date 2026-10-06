# 变更日志

本文件记录「管家」的重要变更。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本 2.0.0](https://semver.org/lang/zh-CN/)。

## [Unreleased]

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
