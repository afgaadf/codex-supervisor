# 管家监督（Codex Supervisor）· Obsidian 插件

本插件是**一个插件**，由原先两个功能重叠的插件合并而来（欠账 A15）：

- **本地体检**（原 \`codex-supervisor\`）：在 Obsidian 进程内直接扫库，写 JSON 报告给管家界面。
- **监督者桥**（原 \`supervisor-bridge\`）：把知识库接到本机监督者（\`http://127.0.0.1:8765\`），
  推送实时事件、同步全库索引、上报体检结果、显示状态面板、执行命令队列。

一次扫描，两个出口：体检只跑一遍，既写本地报告，也上报给监督者。

## 它做什么

**本地体检（只读，不改笔记）**

- 监听知识库变动（新建 / 修改 / 删除 / 改名），节流后自动体检
- 手动体检：命令面板「管家体检：扫描知识库并写报告」
- 体检结果写入报告目录下的 \`obsidian.json\`，供管家界面读取

**监督者桥**

- 实时事件：\`create\` / \`modify\` / \`delete\` / \`rename\` 立即推给监督者（\`POST /api/vault/event\`）
- 全库索引：按设定周期同步（默认 10 分钟）→ \`vault_index.json\`（\`POST /api/vault/index\`）
- 体检上报：把同一份体检结果推给 \`POST /api/vault/check\` → \`vault_checks.json\`
- 状态面板：点左侧盾牌图标 / 命令面板「管家：查看监督者状态面板」，读 \`GET /api/vault/state\`
- 命令队列：执行监督者下发的命令，**白名单只有 \`create_note\` / \`append_note\` / \`move_note\`，绝不删除**

> 鉴权：写接口都带 \`X-Token\` 头，令牌从令牌文件读取（监督者每次启动会重写它）。

## 检测项（id 与管家的失败模式库一致）

| id | 说明 |
|---|---|
| \`obsidian.broken_link\` | 断链；读 \`unresolvedLinks\`，纯锚点 \`[[#标题]]\` **不算**断链 |
| \`obsidian.missing_frontmatter\` | 笔记缺 frontmatter（工具文件 \`AGENTS.md\` / \`CLAUDE.md\` / \`maintenance_prompt.md\` / \`.gitignore\` 豁免） |
| \`obsidian.orphan_note\` | 孤立笔记（既无出链也无入链） |
| \`obsidian.rename_without_link_repair\` | 断链目标与现有笔记名高度相似（≥0.8）→ 疑似改名未修链接 |
| \`obsidian.inbox_no_triage\` | \`00_Inbox/\` 下未打标签的笔记 |
| \`obsidian.inbox_stale\` | \`00_Inbox/\` 下超过阈值天数未修改的笔记 |
| \`obsidian.orphan_attachment\` | \`20_附件/\` 下未被任何笔记引用（按路径或 basename 判定）的附件 |

### 作用域（真库上踩过坑，别改回去）

- \`20_附件/原始资料\` 默认忽略（本机那目录有数万文件），可用「附件忽略目录」设置调整。
- 缺 frontmatter / 断链 / 孤立笔记只统计**知识区**：\`20_附件/\`、\`30_模板/\`、\`90_归档/\`、\`.obsidian/\`、\`.git/\` 不算。
- 孤立附件列表上限 50 条，超出只报数量，避免报告被淹没。
- 孤立附件判定：只要有任何笔记以**同名文件**（basename）引用了它，就不算孤立（欠账 A14 修复）。

## 安装

1. 在知识库里新建目录：\`<vault>/.obsidian/plugins/codex-supervisor/\`
2. 把本目录下的三个文件复制进去：
   - \`manifest.json\`
   - \`main.js\`
   - \`styles.css\`
3. 重启 Obsidian（或重新加载插件目录），打开 **设置 → 第三方插件 / 社区插件**
4. 关闭「受限模式」后，启用「管家监督」

> 本插件为桌面专用（\`isDesktopOnly: true\`），因为需要在本机写报告文件、调用监督者本地接口。

## 设置

设置 → 第三方插件 → 管家监督：

| 设置 | 默认 | 说明 |
|---|---|---|
| 监督者地址 | \`http://127.0.0.1:8765\` | 监督者本地 HTTP 地址 |
| 令牌文件 | \`C:\Users\taich\.codex\supervisor\plugin_token.txt\` | 监督者写令牌的地方 |
| 报告目录 | \`~/.codex/supervisor-plugin-reports/\` | 本地体检报告目录 |
| 推送实时事件 | 开 | \`create\`/\`modify\`/\`delete\`/\`rename\` 立即推给监督者 |
| 自动同步（分钟） | 10 | 全库索引同步周期 |
| 自动体检节流（秒） | 5 | 知识库变动后等这么久再体检 |
| 收件箱阈值（天） | 14 | \`00_Inbox\` 超过这么多天没动就报 |
| 附件忽略目录 | \`20_附件/原始资料\` | 逗号分隔 |

底部还有一排按钮：同步索引 / 库体检 / 执行命令队列 / 看状态。

## 报告格式

UTF-8 无 BOM、缩进 2：

\`\`\`json
{
  "plugin": "obsidian",
  "version": "1.0.0",
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
\`\`\`

- \`checks[].status\`：\`ok\` / \`warn\` / \`error\`
- \`healthy\`：只要没有 \`error\` 级检查项，即为 \`true\`
- 断链为 \`error\` 级；其余为 \`warn\` 级

上报给监督者的体检 payload（\`POST /api/vault/check\`）字段名与原 supervisor-bridge 一致：

\`\`\`json
{
  "checks": {
    "summary": {
      "notes": 0, "broken_links": 0, "missing_frontmatter": 0,
      "inbox_stale": 0, "big_note": 0, "uncommitted": 0, "git_available": 1
    },
    "items": [ { "kind": "broken_link", "path": "…", "detail": "…" } ],
    "ts": "2026-10-07T07:00:00.000Z"
  }
}
\`\`\`

## 自测

不需要 Obsidian，也不需要真的连监督者：

\`\`\`bash
node plugins/obsidian/selftest.js
\`\`\`

用替身 \`obsidian\` 模块 + 一个**本地假 HTTP 服务器**验证：作用域、附件的 basename 判定、
以及桥调用确实发出（\`/api/vault/event\`、\`/api/vault/index\`、\`/api/vault/check\`）且带上了 \`X-Token\`。

## 稳定性

- 所有异常都会被吞掉并写入开发者控制台（\`Ctrl+Shift+I\`），**不会让 Obsidian 崩溃**
- 报告先写临时文件再改名，避免管家读到写了一半的 JSON
- 命令只走白名单，**绝不删除文件**
