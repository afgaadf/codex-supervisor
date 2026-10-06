"use strict";

/*
 * 管家监督（Codex Supervisor）— Obsidian 社区插件
 * 作用：在 Obsidian 进程内做知识库体检，把结果写成 JSON 报告，供管家界面展示。
 * 形态：纯 JavaScript（CommonJS），无构建步骤，无第三方依赖。
 * 契约：见 README.md（报告目录 ~/.codex/supervisor-plugin-reports/obsidian.json）。
 */

const obsidian = require("obsidian");
const fs = require("fs");
const path = require("path");
const os = require("os");

const Plugin = obsidian.Plugin;
const PluginSettingTab = obsidian.PluginSettingTab;
const Setting = obsidian.Setting;
const Notice = obsidian.Notice;

const PLUGIN_ID = "codex-supervisor";
const PLUGIN_VERSION = "0.1.0";
const REPORT_FILE = "obsidian.json";

const DEFAULT_SETTINGS = {
  reportDir: os.homedir() + "/.codex/supervisor-plugin-reports/",
  throttleSeconds: 5,
  inboxStaleDays: 14,
};

// 工具文件豁免：这些文件本来就不需要 frontmatter
const TOOL_FILE_NAMES = ["AGENTS.md", "CLAUDE.md", "maintenance_prompt.md", ".gitignore"];

// 这些目录不参与“孤立笔记”判定
const NON_KNOWLEDGE_PREFIXES = ["20_附件/", "30_模板/", "90_归档/", ".obsidian/", ".git/"];

const INBOX_PREFIX = "00_Inbox/";
const ATTACHMENT_PREFIX = "20_附件/";
const RENAME_SIMILARITY = 0.8;

function toPosix(p) {
  return String(p === null || p === undefined ? "" : p).replace(/\\/g, "/");
}

function baseName(p) {
  const s = toPosix(p);
  const i = s.lastIndexOf("/");
  return i >= 0 ? s.slice(i + 1) : s;
}

function stripExt(name) {
  const s = String(name === null || name === undefined ? "" : name);
  const i = s.lastIndexOf(".");
  return i > 0 ? s.slice(0, i) : s;
}

function isNonKnowledge(p) {
  const s = toPosix(p);
  for (let i = 0; i < NON_KNOWLEDGE_PREFIXES.length; i++) {
    const pre = NON_KNOWLEDGE_PREFIXES[i];
    if (s.indexOf(pre) === 0) return true;
    if (s.indexOf("/" + pre) !== -1) return true;
  }
  return false;
}

function levenshtein(a, b) {
  a = String(a === null || a === undefined ? "" : a);
  b = String(b === null || b === undefined ? "" : b);
  const m = a.length;
  const n = b.length;
  if (m === 0) return n;
  if (n === 0) return m;
  let prev = new Array(n + 1);
  let cur = new Array(n + 1);
  for (let j = 0; j <= n; j++) prev[j] = j;
  for (let i = 1; i <= m; i++) {
    cur[0] = i;
    for (let j = 1; j <= n; j++) {
      const cost = a.charCodeAt(i - 1) === b.charCodeAt(j - 1) ? 0 : 1;
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
    }
    const tmp = prev;
    prev = cur;
    cur = tmp;
  }
  return prev[n];
}

function similarity(a, b) {
  a = String(a === null || a === undefined ? "" : a);
  b = String(b === null || b === undefined ? "" : b);
  if (a.length === 0 || b.length === 0) return 0;
  if (a === b) return 1;
  const max = Math.max(a.length, b.length);
  if (max === 0) return 1;
  return 1 - levenshtein(a, b) / max;
}

/*
 * 取链接的“核心目标”：
 * - 先去别名（| 之后）
 * - 纯锚点（以 # 开头）返回空字符串 => 不算断链，显式跳过
 * - 再去掉 #小标题 与 ^块引用
 */
function linkCore(raw) {
  let t = String(raw === null || raw === undefined ? "" : raw).trim();
  if (t.length === 0) return "";
  const pipe = t.indexOf("|");
  if (pipe >= 0) t = t.slice(0, pipe).trim();
  if (t.length === 0) return "";
  if (t.charAt(0) === "#") return "";
  const hash = t.indexOf("#");
  if (hash >= 0) t = t.slice(0, hash);
  const caret = t.indexOf("^");
  if (caret >= 0) t = t.slice(0, caret);
  return t.trim();
}

function localIso(d) {
  const pad = function (n) { return String(n).padStart(2, "0"); };
  const off = -d.getTimezoneOffset();
  const sign = off >= 0 ? "+" : "-";
  const abs = Math.abs(off);
  return (
    d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) +
    "T" + pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds()) +
    sign + pad(Math.floor(abs / 60)) + ":" + pad(abs % 60)
  );
}

function previewList(items, n, fmt) {
  const out = [];
  const upto = Math.min(items.length, n);
  for (let i = 0; i < upto; i++) out.push(fmt(items[i]));
  if (items.length > upto) out.push("…等共 " + items.length + " 项");
  return out.join("；");
}

function safeCache(mc, f) {
  try {
    return mc && mc.getFileCache ? mc.getFileCache(f) : null;
  } catch (e) {
    return null;
  }
}

/*
 * 纯检测逻辑（不依赖 Obsidian 运行时之外的东西），便于单测。
 * 返回 { checks, metrics, healthy, summary }。
 */
function runChecks(app, settings, deps) {
  const cfg = Object.assign({}, DEFAULT_SETTINGS, settings || {});
  const now = (deps && deps.now) || new Date();
  const nowMs = now && now.getTime ? now.getTime() : Date.now();

  const vault = app && app.vault ? app.vault : null;
  const mc = app && app.metadataCache ? app.metadataCache : null;
  const mdFiles = vault && vault.getMarkdownFiles ? (vault.getMarkdownFiles() || []) : [];
  const allFiles = vault && vault.getFiles ? (vault.getFiles() || mdFiles) : mdFiles;
  const resolved = (mc && mc.resolvedLinks) || {};
  const unresolved = (mc && mc.unresolvedLinks) || {};

  // ---- 链接图：出链 / 入链 / 被引用路径 ----
  const outMap = {};
  const inCount = {};
  const referencedPaths = new Set();
  const resolvedKeys = Object.keys(resolved);
  for (let i = 0; i < resolvedKeys.length; i++) {
    const src = toPosix(resolvedKeys[i]);
    const targets = resolved[resolvedKeys[i]] || {};
    const names = Object.keys(targets);
    const set = new Set();
    for (let j = 0; j < names.length; j++) {
      const dst = toPosix(names[j]);
      set.add(dst);
      referencedPaths.add(dst);
      inCount[dst] = (inCount[dst] || 0) + 1;
    }
    outMap[src] = set;
  }

  // ---- obsidian.broken_link（纯锚点显式跳过）----
  let brokenCount = 0;
  const brokenItems = [];
  const brokenCores = new Set();
  const unresolvedKeys = Object.keys(unresolved);
  for (let i = 0; i < unresolvedKeys.length; i++) {
    const src = toPosix(unresolvedKeys[i]);
    const links = unresolved[unresolvedKeys[i]] || {};
    const names = Object.keys(links);
    for (let j = 0; j < names.length; j++) {
      const raw = names[j];
      const core = linkCore(raw);
      if (core.length === 0) continue; // 纯锚点 / 空链接 => 不算断链
      const times = Number(links[raw]) || 1;
      brokenCount += times;
      brokenCores.add(core);
      brokenItems.push({ source: src, target: raw, times: times });
    }
  }

  // ---- obsidian.missing_frontmatter ----
  const missingFm = [];
  for (let i = 0; i < mdFiles.length; i++) {
    const f = mdFiles[i];
    if (TOOL_FILE_NAMES.indexOf(baseName(f.path)) !== -1) continue;
    const cache = safeCache(mc, f);
    const fm = cache ? cache.frontmatter : null;
    if (!fm || Object.keys(fm).length === 0) missingFm.push(toPosix(f.path));
  }

  // ---- obsidian.orphan_note ----
  const orphans = [];
  for (let i = 0; i < mdFiles.length; i++) {
    const p = toPosix(mdFiles[i].path);
    if (isNonKnowledge(p)) continue;
    const outSize = outMap[p] ? outMap[p].size : 0;
    const inc = inCount[p] || 0;
    if (outSize === 0 && inc === 0) orphans.push(p);
  }

  // ---- obsidian.rename_without_link_repair ----
  const noteNames = [];
  for (let i = 0; i < mdFiles.length; i++) noteNames.push(stripExt(baseName(mdFiles[i].path)));
  const renameSuspects = [];
  brokenCores.forEach(function (core) {
    const target = stripExt(baseName(core));
    if (target.length === 0) return;
    let best = 0;
    let guess = "";
    for (let i = 0; i < noteNames.length; i++) {
      const s = similarity(target, noteNames[i]);
      if (s > best) { best = s; guess = noteNames[i]; }
    }
    if (best >= RENAME_SIMILARITY) {
      renameSuspects.push({ link: core, guess: guess, score: Math.round(best * 100) / 100 });
    }
  });

  // ---- obsidian.inbox_no_triage / obsidian.inbox_stale ----
  const staleDays = Math.max(1, Number(cfg.inboxStaleDays) || DEFAULT_SETTINGS.inboxStaleDays);
  const thresholdMs = staleDays * 24 * 60 * 60 * 1000;
  const inboxUntriaged = [];
  const inboxStale = [];
  for (let i = 0; i < mdFiles.length; i++) {
    const f = mdFiles[i];
    const p = toPosix(f.path);
    if (p.indexOf(INBOX_PREFIX) !== 0) continue;
    const cache = safeCache(mc, f);
    const fm = cache ? cache.frontmatter : null;
    const fmTags = fm ? fm.tags : null;
    const inlineTags = cache ? cache.tags : null;
    const hasFmTags = Array.isArray(fmTags)
      ? fmTags.length > 0
      : (fmTags !== null && fmTags !== undefined && String(fmTags).trim().length > 0);
    const hasInlineTags = Array.isArray(inlineTags) && inlineTags.length > 0;
    if (!hasFmTags && !hasInlineTags) inboxUntriaged.push(p);
    const mtime = f.stat && f.stat.mtime ? f.stat.mtime : 0;
    if (mtime > 0 && nowMs - mtime > thresholdMs) inboxStale.push(p);
  }

  // ---- obsidian.orphan_attachment ----
  const orphanAttach = [];
  for (let i = 0; i < allFiles.length; i++) {
    const p = toPosix(allFiles[i].path);
    if (p.indexOf(ATTACHMENT_PREFIX) !== 0) continue;
    if (referencedPaths.has(p)) continue;
    orphanAttach.push(p);
  }

  // ---- 汇总 ----
  const checks = [];
  checks.push({
    id: "obsidian.broken_link",
    status: brokenCount > 0 ? "error" : "ok",
    count: brokenCount,
    detail: brokenCount > 0
      ? "发现 " + brokenCount + " 处链接无法解析到库内文件（纯锚点链接已按规则跳过）：" +
        previewList(brokenItems, 3, function (it) { return "「" + it.target + "」@ " + it.source; })
      : "未发现断链（纯锚点链接已按规则跳过）。",
  });
  checks.push({
    id: "obsidian.missing_frontmatter",
    status: missingFm.length > 0 ? "warn" : "ok",
    count: missingFm.length,
    detail: missingFm.length > 0
      ? "有 " + missingFm.length + " 篇笔记缺少 frontmatter：" +
        previewList(missingFm, 3, function (p) { return p; })
      : "所有笔记均已包含 frontmatter（工具文件已豁免）。",
  });
  checks.push({
    id: "obsidian.orphan_note",
    status: orphans.length > 0 ? "warn" : "ok",
    count: orphans.length,
    detail: orphans.length > 0
      ? "有 " + orphans.length + " 篇孤立笔记（既无出链也无入链）：" +
        previewList(orphans, 3, function (p) { return p; })
      : "未发现孤立笔记。",
  });
  checks.push({
    id: "obsidian.rename_without_link_repair",
    status: renameSuspects.length > 0 ? "warn" : "ok",
    count: renameSuspects.length,
    detail: renameSuspects.length > 0
      ? "有 " + renameSuspects.length + " 处断链疑似改名未修：" +
        previewList(renameSuspects, 3, function (r) { return "「" + r.link + "」≈「" + r.guess + "」"; })
      : "未发现疑似改名未修的链接。",
  });
  checks.push({
    id: "obsidian.inbox_no_triage",
    status: inboxUntriaged.length > 0 ? "warn" : "ok",
    count: inboxUntriaged.length,
    detail: inboxUntriaged.length > 0
      ? "00_Inbox 有 " + inboxUntriaged.length + " 篇笔记未打标签：" +
        previewList(inboxUntriaged, 3, function (p) { return p; })
      : "00_Inbox 中的笔记均已打标签。",
  });
  checks.push({
    id: "obsidian.inbox_stale",
    status: inboxStale.length > 0 ? "warn" : "ok",
    count: inboxStale.length,
    detail: inboxStale.length > 0
      ? "00_Inbox 有 " + inboxStale.length + " 篇超过 " + staleDays + " 天未修改：" +
        previewList(inboxStale, 3, function (p) { return p; })
      : "00_Inbox 没有超过 " + staleDays + " 天未修改的笔记。",
  });
  checks.push({
    id: "obsidian.orphan_attachment",
    status: orphanAttach.length > 0 ? "warn" : "ok",
    count: orphanAttach.length,
    detail: orphanAttach.length > 0
      ? "20_附件 有 " + orphanAttach.length + " 个附件未被任何笔记引用：" +
        previewList(orphanAttach, 3, function (p) { return p; })
      : "20_附件 中的附件均被引用。",
  });

  let healthy = true;
  for (let i = 0; i < checks.length; i++) {
    if (checks[i].status === "error") { healthy = false; break; }
  }

  const parts = [];
  if (brokenCount > 0) parts.push("断链 " + brokenCount);
  if (missingFm.length > 0) parts.push("缺 frontmatter " + missingFm.length);
  if (orphans.length > 0) parts.push("孤立笔记 " + orphans.length);
  if (renameSuspects.length > 0) parts.push("疑似改名未修 " + renameSuspects.length);
  if (inboxUntriaged.length > 0) parts.push("收件箱未分类 " + inboxUntriaged.length);
  if (inboxStale.length > 0) parts.push("收件箱过期 " + inboxStale.length);
  if (orphanAttach.length > 0) parts.push("孤立附件 " + orphanAttach.length);
  const summary = parts.length === 0
    ? "知识库体检通过：共 " + mdFiles.length + " 篇笔记，未发现需要处理的问题。"
    : "知识库体检发现问题：" + parts.join("，") + "（共 " + mdFiles.length + " 篇笔记）。";

  const metrics = {
    notes: mdFiles.length,
    broken_links: brokenCount,
    missing_frontmatter: missingFm.length,
    orphan_notes: orphans.length,
    inbox_untriaged: inboxUntriaged.length,
    orphan_attachments: orphanAttach.length,
  };

  return { checks: checks, metrics: metrics, healthy: healthy, summary: summary };
}

/* 写报告：UTF-8 无 BOM、缩进 2；先写临时文件再改名，避免读到半截 JSON。 */
function writeReport(report, settings, deps) {
  const cfg = Object.assign({}, DEFAULT_SETTINGS, settings || {});
  const fsm = (deps && deps.fs) || fs;
  const pathm = (deps && deps.path) || path;
  const dir = cfg.reportDir && String(cfg.reportDir).trim()
    ? String(cfg.reportDir).trim()
    : DEFAULT_SETTINGS.reportDir;
  fsm.mkdirSync(dir, { recursive: true });
  const file = pathm.join(dir, REPORT_FILE);
  const text = JSON.stringify(report, null, 2);
  const tmp = file + ".tmp";
  fsm.writeFileSync(tmp, text, { encoding: "utf8" });
  try {
    fsm.renameSync(tmp, file);
  } catch (e) {
    fsm.writeFileSync(file, text, { encoding: "utf8" });
  }
  return file;
}

class CodexSupervisorPlugin extends Plugin {
  constructor() {
    super(...arguments);
    this.settings = Object.assign({}, DEFAULT_SETTINGS);
    this._timer = null;
    this._lastReport = null;
  }

  async onload() {
    try {
      await this.loadSettings();
    } catch (e) {
      console.error("[管家监督] 读取设置失败，改用默认值", e);
      this.settings = Object.assign({}, DEFAULT_SETTINGS);
    }

    try {
      this.addSettingTab(new SupervisorSettingTab(this.app, this));
    } catch (e) {
      console.error("[管家监督] 注册设置页失败", e);
    }

    try {
      this.addCommand({
        id: "scan-vault",
        name: "管家体检：扫描知识库并写报告",
        callback: () => { this.runHealthCheck(true); },
      });
    } catch (e) {
      console.error("[管家监督] 注册命令失败", e);
    }

    try {
      this.addRibbonIcon("shield-check", "管家体检：扫描知识库并写报告", () => {
        this.runHealthCheck(true);
      });
    } catch (e) {
      console.error("[管家监督] 注册侧边栏图标失败", e);
    }

    try {
      const handler = () => { this.scheduleCheck(); };
      this.registerEvent(this.app.vault.on("modify", handler));
      this.registerEvent(this.app.vault.on("create", handler));
      this.registerEvent(this.app.vault.on("delete", handler));
      this.registerEvent(this.app.vault.on("rename", handler));
    } catch (e) {
      console.error("[管家监督] 注册知识库事件失败", e);
    }

    // 启动后延迟跑一次，避免和 Obsidian 启动争抢
    try {
      this.scheduleCheck();
    } catch (e) {
      console.error("[管家监督] 首次体检排程失败", e);
    }
  }

  onunload() {
    try {
      if (this._timer) {
        clearTimeout(this._timer);
        this._timer = null;
      }
    } catch (e) {
      console.error("[管家监督] 卸载清理失败", e);
    }
  }

  async loadSettings() {
    const data = (await this.loadData()) || {};
    this.settings = Object.assign({}, DEFAULT_SETTINGS, data);
  }

  async saveSettings() {
    await this.saveData(this.settings);
  }

  /* 节流：知识库连续变动时只跑最后一次 */
  scheduleCheck() {
    try {
      const secs = Math.max(1, Number(this.settings.throttleSeconds) || DEFAULT_SETTINGS.throttleSeconds);
      if (this._timer) clearTimeout(this._timer);
      this._timer = setTimeout(() => {
        this._timer = null;
        this.runHealthCheck(false);
      }, secs * 1000);
    } catch (e) {
      console.error("[管家监督] 体检排程失败", e);
    }
  }

  runHealthCheck(notify) {
    try {
      const settings = Object.assign({}, DEFAULT_SETTINGS, this.settings || {});
      const result = runChecks(this.app, settings, { now: new Date() });
      const report = {
        plugin: "obsidian",
        version: PLUGIN_VERSION,
        ts: localIso(new Date()),
        healthy: result.healthy,
        summary: result.summary,
        checks: result.checks,
        metrics: result.metrics,
      };
      const file = writeReport(report, settings, { fs: fs, path: path });
      this._lastReport = report;
      console.log("[管家监督] 体检报告已写入 " + file);
      if (notify) {
        try { new Notice("管家体检完成：" + result.summary, 6000); } catch (e) { /* 忽略通知失败 */ }
      }
      return report;
    } catch (e) {
      console.error("[管家监督] 体检失败", e);
      if (notify) {
        try { new Notice("管家体检失败，请查看开发者控制台。", 6000); } catch (e2) { /* 忽略 */ }
      }
      return null;
    }
  }
}

class SupervisorSettingTab extends PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display() {
    const containerEl = this.containerEl;
    containerEl.empty();
    containerEl.createEl("h2", { text: "管家监督 设置" });
    containerEl.createEl("p", {
      text: "体检报告写入下面的目录，文件名固定为 " + REPORT_FILE + "，供管家界面读取。",
    });

    new Setting(containerEl)
      .setName("报告目录")
      .setDesc("默认：" + DEFAULT_SETTINGS.reportDir)
      .addText((t) => t
        .setPlaceholder(DEFAULT_SETTINGS.reportDir)
        .setValue(String(this.plugin.settings.reportDir || ""))
        .onChange(async (v) => {
          this.plugin.settings.reportDir = (v || "").trim() || DEFAULT_SETTINGS.reportDir;
          await this.plugin.saveSettings();
        }));

    new Setting(containerEl)
      .setName("自动体检节流（秒）")
      .setDesc("知识库变动后等待这么多秒再体检，避免频繁扫描。默认 5 秒。")
      .addText((t) => t
        .setValue(String(this.plugin.settings.throttleSeconds))
        .onChange(async (v) => {
          const n = Number(v);
          this.plugin.settings.throttleSeconds =
            Number.isFinite(n) && n >= 1 ? Math.floor(n) : DEFAULT_SETTINGS.throttleSeconds;
          await this.plugin.saveSettings();
        }));

    new Setting(containerEl)
      .setName("收件箱过期阈值（天）")
      .setDesc("00_Inbox 中超过这么多天未修改的笔记会被标记。默认 14 天。")
      .addText((t) => t
        .setValue(String(this.plugin.settings.inboxStaleDays))
        .onChange(async (v) => {
          const n = Number(v);
          this.plugin.settings.inboxStaleDays =
            Number.isFinite(n) && n >= 1 ? Math.floor(n) : DEFAULT_SETTINGS.inboxStaleDays;
          await this.plugin.saveSettings();
        }));
  }
}

module.exports = CodexSupervisorPlugin;
module.exports.PLUGIN_ID = PLUGIN_ID;
module.exports.PLUGIN_VERSION = PLUGIN_VERSION;
module.exports.DEFAULT_SETTINGS = DEFAULT_SETTINGS;
module.exports.runChecks = runChecks;
module.exports.writeReport = writeReport;
module.exports.linkCore = linkCore;
module.exports.similarity = similarity;
module.exports.localIso = localIso;
