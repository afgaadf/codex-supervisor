# 管家 · Codex 监督插件（安装说明）

让 Codex 更好地被管家监管。本插件由两部分组成：

- **skill**（`SKILL.md`）：约束 Codex 在声称「完成 / 已修复 / 已验证」前必须先自查。
- **MCP 服务器**（`mcp_server.py`）：提供 3 个查询工具，纯标准库、不联网、不装依赖。

| 工具 | 作用 |
|---|---|
| `supervisor_status` | 管家当前分数 / 等级 / 是否维护模式 |
| `failure_modes` | 失败模式库（codex 30 项、obsidian 30 项），可按 `area` 筛选 |
| `check_text` | 对一段文本做确定性失败模式自检 |

插件启动时会写一份自检报告到
`%USERPROFILE%\.codex\supervisor-plugin-reports\codex.json`。

---

## 一、安装 skill

把本目录的 `SKILL.md` 复制（或链接）到 Codex 的 skills 目录：

```
%USERPROFILE%\.codex\skills\codex-supervisor\SKILL.md
```

推荐直接用安装器（幂等、失败不抛）：

```powershell
python "C:\Users\taich\.codex\supervisor\plugins\codex\install.py"
```

先看会做什么、不实际改动：

```powershell
python "C:\Users\taich\.codex\supervisor\plugins\codex\install.py" --dry-run
```

安装器**只复制 skill 文件**，**不会自动改 `config.toml`**——注册 MCP 的片段它会打印出来，由你手动粘贴。

---

## 二、注册 MCP 服务器

编辑 `%USERPROFILE%\.codex\config.toml`，追加：

```toml
[mcp_servers.codex-supervisor]
command = "python"
args = ["C:\\Users\\taich\\.codex\\supervisor\\plugins\\codex\\mcp_server.py"]
```

说明：

- `args` 里的路径是本插件的**绝对路径**；如果你把仓库挪到别处，请同步替换。
- 若 `python` 不在 PATH，可改成绝对路径，例如
  `command = "C:\\Users\\taich\\AppData\\Local\\Programs\\Python\\Python312\\python.exe"`。
- Windows 的 `config.toml` 路径是 `C:\Users\taich\.codex\config.toml`。
- 改完重启 Codex，使其重新加载 MCP 服务器。

---

## 三、验证是否生效

```powershell
python "C:\Users\taich\.codex\supervisor\plugins\codex\selftest_mcp.py"
```

该脚本会启动服务器并依次喂入 `initialize` / `tools/list` / `tools/call`，
打印回包；全部通过时退出码为 0。

也可以看报告文件确认服务器成功启动过：

```powershell
Get-Content "$env:USERPROFILE\.codex\supervisor-plugin-reports\codex.json"
```

---

## 四、目录与依赖

- 依赖：**仅 Python 标准库**（无第三方包、无需联网）。
- 管家数据目录（只读，用于查询）：
  - `C:\Users\taich\.codex\supervisor\rubrics\_failure_modes.json`
  - `C:\Users\taich\.codex\supervisor\checkers.py`
  - `C:\Users\taich\.codex\anti-degradation\state\session.json`
  - `C:\Users\taich\.codex\anti-degradation\rules\rules.json`
  - `C:\Users\taich\.codex\anti-degradation\state\maintenance.json`
- 上述文件缺失时，工具会**优雅降级**并给出中文说明，不会报错崩溃。

---

## 五、来源合规

- Obsidian 侧失败模式来自 Obsidian 官方文档（T1 级）。
- **Codex 侧失败模式目前是「项目经验」，非官方标准**——`_failure_modes.json` 的
  `source` 字段已如实标注，请勿当成官方规则引用。