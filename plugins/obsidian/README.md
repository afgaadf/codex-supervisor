# 管家监督（Codex Supervisor）· Obsidian 插件

把「管家」的知识库体检从**外部 Python 反复扫盘**，改成**在 Obsidian 进程内直接跑**：更快、更准、有事件驱动。

## 它做什么

- 监听知识库变动（新建 / 修改 / 删除 / 改名），节流后自动体检
- 手动体检：命令面板执行「管家体检：扫描知识库并写报告」，或点左侧盾牌图标
- 体检结果写入报告目录下的 `obsidian.json`，供管家界面读取
- 只读检测，**不会修改你的笔记**

## 检测项（id 与管家的失败模式库一致）

| id | 说明 |
|---|---|
| `obsidian.broken_link` | 断链；纯锚点 `[[#标题]]` **不算**断链 |
| `obsidian.missing_frontmatter` | 笔记缺 frontmatter（`AGENTS.md` / `CLAUDE.md` / `maintenance_prompt.md` / `.gitignore` 豁免） |
| `obsidian.orphan_note` | 孤立笔记（既无出链也无入链），排除 `20_附件`/`30_模板`/`90_归档`/`.obsidian`/`.git` |
| `obsidian.rename_without_link_repair` | 断链目标与现有笔记名高度相似（≥0.8）→ 疑似改名未修链接 |
| `obsidian.inbox_no_triage` | `00_Inbox/` 下未打标签的笔记 |
| `obsidian.inbox_stale` | `00_Inbox/` 下超过阈值天数未修改的笔记 |
| `obsidian.orphan_attachment` | `20_附件/` 下未被任何笔记链接 / 嵌入引用的附件 |

## 安装

1. 在知识库里新建目录：`<vault>/.obsidian/plugins/codex-supervisor/`
2. 把本目录下的三个文件复制进去：
   - `manifest.json`
   - `main.js`
   - `styles.css`
3. 重启 Obsidian（或重新加载插件目录），打开 **设置 → 第三方插件 / 社区插件**
4. 关闭「受限模式」后，启用「管家监督」

> 本插件为桌面专用（`isDesktopOnly: true`），因为需要在本机写报告文件。

## 设置

设置 → 第三方插件 → 管家监督：

- **报告目录**：默认 `~/.codex/supervisor-plugin-reports/`
- **自动体检节流（秒）**：默认 5 秒
- **收件箱过期阈值（天）**：默认 14 天

## 报告格式

UTF-8 无 BOM、缩进 2：

```json
{
  "plugin": "obsidian",
  "version": "0.1.0",
  "ts": "2026-10-07T07:00:00+08:00",
  "healthy": true,
  "summary": "一句话中文状态",
  "checks": [
    { "id": "obsidian.broken_link", "status": "ok", "count": 0, "detail": "中文说明" }
  ],
  "metrics": {
    "notes": 0,
    "broken_links": 0,
    "missing_frontmatter": 0,
    "orphan_notes": 0,
    "inbox_untriaged": 0,
    "orphan_attachments": 0
  }
}
```

- `checks[].status`：`ok` / `warn` / `error`
- `healthy`：只要没有 `error` 级检查项，即为 `true`
- 断链为 `error` 级；其余为 `warn` 级

## 稳定性

- 所有异常都会被吞掉并写入开发者控制台（`Ctrl+Shift+I`），**不会让 Obsidian 崩溃**
- 报告先写临时文件再改名，避免管家读到写了一半的 JSON
