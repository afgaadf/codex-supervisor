# 功能确认报告（2026-10-06）

对 `anti-degradation supervisor` + `codex-supervisor` 程序做的端到端检查。
可复跑：`python work\verify_all.py`（脚本在同层 work\ 目录）。

## 结果：**17 项全部通过 ✅**

| # | 检查项 | 结果 |
|---|---|---|
| 1 | `hooks.json` 合法、共 11 条 handler | PASS |
| 2 | 信任哈希 **11/11** 与官方算法一致（全部 trusted） | PASS |
| 3 | (a) `SessionEnd` timeout=3 且**仍 trusted**（不需重新信任） | PASS |
| 4 | (b) `Stop` handler 存在且 trusted | PASS |
| 5–7 | `hook_supervisor/session/post.ps1` 存在且带 UTF-8 BOM | PASS |
| 8 | `supervisor.py doctor` 全部通过 | PASS |
| 9 | `run_tests.ps1` 回归全绿（0 FAIL） | PASS |
| 10 | 程序 `/api/state` 返回 hooks + monitor + supervisor | PASS |
| 11 | 监控：有独立判定 + 找到 transcript | PASS |
| 12 | 监控：事件**按窗口**统计（非全历史） | PASS |
| 13 | 程序 `/api/logs` 可用 | PASS |
| 14 | 界面含中文说明/图例 | PASS |
| 15 | 写接口**无令牌被拒（403）** | PASS |
| 16 | 界面写入的 `trusted_hash` == 官方算法结果 | PASS |
| 17 | 活体 `run_hook.cmd stop` 返回 `{}` | PASS |

## 各功能「有效性」的判定依据

- **信任引擎**：不是"看起来对"，是拿你机器上已存在的 11 条官方 `trusted_hash`
  逐条重算比对 —— 含 `session_end`（被 clamp 到 3s）那条。
- **监控**：独立读 Codex 的 transcript 文件（primary 证据）自己出等级，
  并与 Codex 内部的结论并列；还检测"内部停摆"。
- **写入**：在**沙盒副本**上做撤销/重授，验证写入的哈希与官方算法一致、
  TOML 仍合法；真实文件未被测试触碰。
- **安全**：无令牌的写请求 403；令牌只在进程内存、重启即失效。


---

# 评估体系 v2（2026-10-06 23:25）—— 联网核实改成硬要求

## 改了什么

| 之前 | 现在 |
|---|---|
| 只有关键词命中（规格/版本/官方…）才联网 | **命中任一监管者的轮次一律联网核实**（不再"看着不需要就不查"） |
| AI 自己检索过就算"查了" | AI 自己说查过**不算**；监督者自己联网复核，以它带回来的材料为准 |
| 来源不分级 | T1 一手权威 / T2 二手专业 / T3 百科·聚合 / T4 未分级；关键数值**必须 ≥1 条 T1** |
| 只说"没查到" | 找不到 T1 会自动再打一次 `site:<官方域名>`；仍没有就判「未充分查证」+ 列缺口 |
| 材料只给模型看一次 | 来源、等级、缺口、判定一并落进 `judgments.jsonl`，界面「告警与审计 → 判断记录」可点开看 |

**规则落点**：`rubrics/2d-video.md` D 轴（D1–D5）+「判定规则」第 5 条；
来源名单 `rubrics/_sources.json`（人可改，改完下一轮生效）；代码在 `judge.py`（`tier_of` / `site_queries` / `check_online` / `_online_block`）。

## 实测（真联网，2026-10-06 23:25）

问题：`抖音短视频上传：官方对帧率和码率的规定是什么？`，并给一句待核断言「1080x1920、30fps、码率≤6000kbps」。

| 检查 | 结果 |
|---|---|
| 来源分级器 | douyin.com→T1；github.com/FFmpeg/FFmpeg→T1；stackoverflow→T2；baike.baidu.com→T3；未知域名→T4 |
| 触发规则 | 命中视频监管者的轮次 `needed=True`；通用轮且无关键词 `needed=False` |
| 第 1 轮检索（通用问句） | 只有 T3（新浪转载 + 工具站）→ 判定**「未达证据线」** |
| 自动补 `site:douyin.com` | 拿到 **T1×2**：`douyin.com/shipin/...`、`streamingtool.douyin.com/docs/qna_how_to_set` |
| 结论 | 「有一手权威来源（T1×2）」，并诚实写明缺口：**官方没发布短视频帧率/码率的数值规定**（材料不足，不许编） |
| 耗时 | 49.5 秒（2 轮检索 + 抓正文 + 归纳；同问 6 小时内走缓存 `logs/online_cache.jsonl`） |

可复跑：`python work\test_online.py`（过程写进 `work\test_online.log`）。
