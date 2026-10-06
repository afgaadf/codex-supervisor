# 边界：让「监管者」不被「被监管者」改写

## 为什么要这一层

Codex 自带的 hook 信任**只哈希 handler 的定义**（事件名 + matcher + command/timeout），
**不哈希脚本内容**（官方 `fingerprint.rs` / `discovery.rs`，本机已用 11 条哈希交叉验证）。
后果：同权限的一方可以直接改脚本、信任状态不变 —— 我们已经实测过。

在 hook 链里再加哈希也没用：同一个可写域，先改链子就行。
**唯一真正的边界 = 把监管者移出被监管者的可写域。**

## 必须「不可写」的路径（只读无所谓）

```
C:\Users\taich\.codex\anti-degradation\     ← supervisor（脚本 / 状态 / 规则 / 欠账）
C:\Users\taich\.codex\codex-supervisor\     ← 本程序（已装在这里）
C:\Users\taich\.codex\hooks.json
C:\Users\taich\.codex\config.toml
```

一句话：**整个 `C:\Users\taich\.codex\` 都要在 agent 的可写域之外。**

## 怎么做（键名已在 openai/codex 源码核对）

在 `~/.codex/config.toml` 里：

```toml
sandbox_mode = "workspace-write"

[sandbox_workspace_write]
# 千万不要把 C:\Users\taich\.codex 写进来
writable_roots = []
network_access = false          # 视需要
```

- `sandbox_mode` 取值：`read-only` / `workspace-write` / `danger-full-access`
  （官方 `SandboxMode` 枚举）
- `workspace-write` = 只允许写「当前 workspace + 临时目录」
- `~/.codex` 不在 workspace 里 → **自动不可写** ✅

### 前提：两者不能落在同一个 workspace

| 角色 | 位置 |
|---|---|
| agent 干活的工作区 | `C:\Users\taich\Documents\Codex\...` |
| 监管资产 | `C:\Users\taich\.codex\...` |

**绝不能**把 `C:\Users\taich\.codex` 放进 workspace 或 `writable_roots`。
（这也是本程序**没有**放在工作区 `outputs\` 下运行、而是装到
`~/.codex\codex-supervisor\` 的原因。）

### Windows 注意

- Windows 上如果 Windows sandbox 处于 `Disabled`，`workspace-write` 会被
  官方**降级成 `read-only`**（连工作区都不能写）—— 见 `config_toml.rs::effective_sandbox_mode`。
- 你本机 `config.toml` 已有 `[windows] sandbox = "elevated"`，属于开启状态。

## 怎么验证边界真的生效

沙箱生效后，从 Codex 里试写监管目录，应当**被拒**：

```powershell
Set-Content -LiteralPath C:\Users\taich\.codex\anti-degradation\_probe.txt -Value x
# 期望：失败（Access denied / sandbox violation）
```

若能写成功 → 边界没生效，回到上面的配置检查。

## 现状（2026-10-06）

- 本会话的 sandbox 是 `danger-full-access` → **边界尚未生效**，agent 可写监管目录。
- 本程序已装到 `~/.codex\codex-supervisor\`（工作区之外），只差你把
  `sandbox_mode` 收紧。
