"use strict";

/*
 * 管家监督（Codex Supervisor）— Obsidian 社区插件（合并版 v1.0.0）
 *
 * 本插件由两个插件合并而来：
 *   · supervisor-bridge（监督者桥）：事件流 / 全库索引 / 体检上报 / 状态面板 / 命令队列
 *   · codex-supervisor（本地体检）：知识库自检 + 写本地 JSON 报告给管家界面
 *
 * 设计要点：
 *   · 体检只做一次（runChecks），结果同时供「本地报告」与「上报监督者」两个出口使用。
 *   · 命令队列白名单只有 create_note / append_note / move_note，**绝不删除**。
 *   · 纯 JavaScript（CommonJS），无构建步骤，无第三方依赖。
 *
 * 依据（T1，2026-10-06 核对官方仓库 obsidianmd/obsidian-api 的 obsidian.d.ts）：
 *   · PluginManifest: id/name/version/description/author/minAppVersion 必填，isDesktopOnly? 可选
 *   · Vault 事件: on('create'|'modify'|'delete'|'rename', cb)
 *   · Vault 读写: create / createFolder / read / write / process / remove
 */

const obsidian = require("obsidian");
const fs = require("fs");
const path = require("path");
const os = require("os");
const child_process = require("child_process");

const Plugin = obsidian.Plugin;
const PluginSettingTab = obsidian.PluginSettingTab;
const Setting = obsidian.Setting;
const Notice = obsidian.Notice;
const Modal = obsidian.Modal;
const requestUrl = obsidian.requestUrl || (async function () { return { status: 0 }; });
const normalizePath = obsidian.normalizePath || (function (p) { return p; });

const PLUGIN_ID = "codex-supervisor";
const PLUGIN_VERSION = "1.0.0";
const REPORT_FILE = "obsidian.json";

const DEFAULT_SETTINGS = {
  // —— 本地体检 / 报告（原 codex-supervisor）——
  reportDir: os.homedir() + "/.codex/supervisor-plugin-reports/",
  throttleSeconds: 5,
  inboxStaleDays: 7,        // 与管家 vault_ops._rescan 的口径一致（7 天）
  // 这些目录只当「仓库」不当「知识笔记」，默认不参与体检（原始资料归档可达数万文件）
  attachmentIgnoreFolders: ["20_附件/原始资料"],
  // —— 监督者桥（原 supervisor-bridge）——
  base: "http://127.0.0.1:8765",
  tokenPath: "C:\\Users\\taich\\.codex\\supervisor\\plugin_token.txt",
  autoSyncMinutes: 10,
  pushEvents: true,
  bigNoteKb: 200
};

// 附件报告上限：超过只统计数量，不再逐条列出，避免报告被淹没
const ORPHAN_ATTACHMENT_MAX_LIST = 50;

// 工具文件豁免：这些文件本来就不需要 frontmatter
const TOOL_FILE_NAMES = ["AGENTS.md", "CLAUDE.md", "maintenance_prompt.md", ".gitignore"];

// 这些目录不参与「孤立笔记」判定
const NON_KNOWLEDGE_PREFIXES = ["20_附件/", "30_模板/", "90_归档/", ".obsidian/", ".git/"];

const INBOX_PREFIX = "00_Inbox/";
const ATTACHMENT_PREFIX = "20_附件/";
const RENAME_SIMILARITY = 0.8;
const BRIDGE_ITEMS_MAX = 120;

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
  // 点开头的目录一律不算知识笔记（.obsidian/.git/.agents/.claudian/.copilot…）
  const parts = s.split("/");
  for (let i = 0; i < parts.length - 1; i++) {
    if (parts[i].charAt(0) === ".") return true;
  }
  for (let i = 0; i < NON_KNOWLEDGE_PREFIXES.length; i++) {
    const pre = NON_KNOWLEDGE_PREFIXES[i];
    if (s.indexOf(pre) === 0) return true;
    if (s.indexOf("/" + pre) !== -1) return true;
  }
  return false;
}

function isIgnoredFolder(p, folders) {
  const s = toPosix(p);
  const list = Array.isArray(folders) ? folders : [];
  for (let i = 0; i < list.length; i++) {
    const pre = toPosix(list[i]).replace(/\/+$/, "");
    if (!pre) continue;
    if (s === pre || s.indexOf(pre + "/") === 0) return true;
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
 * 取链接的「核心目标」：
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
 * 体检：一次扫描，两个出口。
 * 返回 { checks, metrics, healthy, summary, bridge }：
 *   · checks / metrics / healthy / summary —— 本地报告 schema（管家界面读它，保持不变）
 *   · bridge.summary / bridge.items        —— 上报监督者 /api/vault/check 用的 payload
 * deps: { now, git }（git 由调用方异步取好后传入，便于单测）
 */
function runChecks(app, settings, deps) {
  const cfg = Object.assign({}, DEFAULT_SETTINGS, settings || {});
  const now = (deps && deps.now) || new Date();
  const nowMs = now && now.getTime ? now.getTime() : Date.now();
  const git = (deps && deps.git) || { available: false, changed: 0, files: [] };

  const vault = app && app.vault ? app.vault : null;
  const mc = app && app.metadataCache ? app.metadataCache : null;
  const mdFiles = vault && vault.getMarkdownFiles ? (vault.getMarkdownFiles() || []) : [];
  const allFiles = vault && vault.getFiles ? (vault.getFiles() || mdFiles) : mdFiles;
  const resolved = (mc && mc.resolvedLinks) || {};
  const unresolved = (mc && mc.unresolvedLinks) || {};

  // ---- 链接图：出链 / 入链 / 被引用路径（含 basename 集合，见 A14 修复）----
  const outMap = {};
  const inCount = {};
  const referencedPaths = new Set();
  const referencedBasenames = new Set();
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
      referencedBasenames.add(baseName(dst));
      inCount[dst] = (inCount[dst] || 0) + 1;
    }
    outMap[src] = set;
  }

  // ---- obsidian.broken_link（纯锚点显式跳过；只算知识区）----
  let brokenCount = 0;
  const brokenItems = [];
  const brokenCores = new Set();
  const unresolvedKeys = Object.keys(unresolved);
  for (let i = 0; i < unresolvedKeys.length; i++) {
    const src = toPosix(unresolvedKeys[i]);
    if (isNonKnowledge(src)) continue; // 归档/剪藏里的链接不算笔记断链
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

  // ---- 库规口径：缺 type/title/updated 中任意一个（桥上报用，与 vault_ops._rescan 一致）----
  const REQUIRED_PROP_KEYS = ["type", "title", "updated"];
  const missingRequired = [];
  for (let i = 0; i < mdFiles.length; i++) {
    const f = mdFiles[i];
    if (TOOL_FILE_NAMES.indexOf(baseName(f.path)) !== -1) continue;
    if (isNonKnowledge(f.path)) continue;
    const _c = safeCache(mc, f);
    const _fm = _c ? _c.frontmatter : null;
    const lack = [];
    for (let j = 0; j < REQUIRED_PROP_KEYS.length; j++) {
      const k = REQUIRED_PROP_KEYS[j];
      if (!_fm || _fm[k] === undefined || _fm[k] === null || String(_fm[k]).trim() === "") lack.push(k);
    }
    if (lack.length) missingRequired.push({ path: toPosix(f.path), lack: lack });
  }

  // ---- obsidian.missing_frontmatter（工具文件豁免；只算知识区）----
  const missingFm = [];
  for (let i = 0; i < mdFiles.length; i++) {
    const f = mdFiles[i];
    if (TOOL_FILE_NAMES.indexOf(baseName(f.path)) !== -1) continue;
    if (isNonKnowledge(f.path)) continue; // 归档/剪藏/模板不要求 frontmatter
    const cache = safeCache(mc, f);
    const fm = cache ? cache.frontmatter : null;
    if (!fm || Object.keys(fm).length === 0) missingFm.push(toPosix(f.path));
  }

  // ---- obsidian.orphan_note（只算知识区）----
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
  // A14 修复：除了「路径完全一致」，再按 basename 判定 —— 只要有任何笔记以同名文件引用了它，
  // 就不算孤立（避免同一附件在别的目录被引用时误报）。
  const orphanAttach = [];
  let orphanAttachTotal = 0;
  const ignoreFolders = cfg.attachmentIgnoreFolders;
  for (let i = 0; i < allFiles.length; i++) {
    const p = toPosix(allFiles[i].path);
    if (p.indexOf(ATTACHMENT_PREFIX) !== 0) continue;
    if (isIgnoredFolder(p, ignoreFolders)) continue;
    if (referencedPaths.has(p)) continue;
    if (referencedBasenames.has(baseName(p))) continue;
    orphanAttachTotal += 1;
    if (orphanAttach.length < ORPHAN_ATTACHMENT_MAX_LIST) orphanAttach.push(p);
  }

  // ---- 大笔记（桥用的 big_note，只算知识区）----
  const bigKb = Math.max(1, Number(cfg.bigNoteKb) || DEFAULT_SETTINGS.bigNoteKb);
  const bigBytes = bigKb * 1024;
  const bigNotes = [];
  for (let i = 0; i < mdFiles.length; i++) {
    const f = mdFiles[i];
    const p = toPosix(f.path);
    if (isNonKnowledge(p)) continue;
    const size = (f.stat && f.stat.size) || 0;
    if (size > bigBytes) bigNotes.push({ path: p, kb: Math.round(size / 1024) });
  }

  // ---- 汇总（本地报告 schema，保持不变）----
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
    status: orphanAttachTotal > 0 ? "warn" : "ok",
    count: orphanAttachTotal,
    detail: orphanAttachTotal > 0
      ? "20_附件 有 " + orphanAttachTotal + " 个附件未被任何笔记引用："
        + (Array.isArray(ignoreFolders) && ignoreFolders.length
            ? "（已排除 " + ignoreFolders.join("、") + "）" : "") +
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
  if (orphanAttachTotal > 0) parts.push("孤立附件 " + orphanAttachTotal);
  const summary = parts.length === 0
    ? "知识库体检通过：共 " + mdFiles.length + " 篇笔记，未发现需要处理的问题。"
    : "知识库体检发现问题：" + parts.join("，") + "（共 " + mdFiles.length + " 篇笔记）。";

  const metrics = {
    notes: mdFiles.length,
    broken_links: brokenCount,
    missing_frontmatter: missingFm.length,
    orphan_notes: orphans.length,
    inbox_untriaged: inboxUntriaged.length,
    orphan_attachments: orphanAttachTotal,
    missing_required_props: missingRequired.length,
  };

  // ---- 桥用 payload（字段名与原 supervisor-bridge 保持一致）----
  const bridgeItems = [];
  const pushItem = function (o) { if (bridgeItems.length < BRIDGE_ITEMS_MAX) bridgeItems.push(o); };
  for (let i = 0; i < brokenItems.length; i++) {
    const it = brokenItems[i];
    pushItem({ kind: "broken_link", path: it.source, detail: "→ [[" + it.target + "]] ×" + it.times });
  }
  for (let i = 0; i < missingRequired.length; i++) {
    pushItem({ kind: "missing_frontmatter", path: missingRequired[i].path,
               detail: "缺 " + missingRequired[i].lack.join("/") });
  }
  for (let i = 0; i < inboxStale.length; i++) {
    pushItem({ kind: "inbox_stale", path: inboxStale[i], detail: "收件箱超过 " + staleDays + " 天未动" });
  }
  for (let i = 0; i < bigNotes.length; i++) {
    pushItem({ kind: "big_note", path: bigNotes[i].path, detail: bigNotes[i].kb + " KB" });
  }
  if (git.available && git.changed) {
    pushItem({ kind: "uncommitted", path: ".", detail: git.changed + " 个改动没提交" });
  }
  const bridgeSummary = {
    notes: mdFiles.length,
    broken_links: brokenCount,
    missing_frontmatter: missingRequired.length,
    inbox_stale: inboxStale.length,
    big_note: bigNotes.length,
    uncommitted: git.available ? git.changed : 0,
    git_available: git.available ? 1 : 0,
  };

  return {
    checks: checks,
    metrics: metrics,
    healthy: healthy,
    summary: summary,
    bridge: { summary: bridgeSummary, items: bridgeItems },
  };
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

// ==================== 监督者桥（原 supervisor-bridge）====================

/* 读取令牌文件；读不到返回空串（不抛）。 */
function readToken(settings, deps) {
  const fsm = (deps && deps.fs) || fs;
  const p = (settings && settings.tokenPath) || DEFAULT_SETTINGS.tokenPath;
  try { return fsm.readFileSync(p, "utf8").trim(); }
  catch (e) { return ""; }
}

/* POST 到监督者；cfg = { base, token, requestUrl }。返回是否 200。 */
async function bridgePost(cfg, apiPath, body) {
  const send = (cfg && cfg.requestUrl) || requestUrl;
  try {
    const r = await send({
      url: String(cfg.base || DEFAULT_SETTINGS.base) + apiPath,
      method: "POST",
      throw: false,
      headers: { "Content-Type": "application/json", "X-Token": String((cfg && cfg.token) || "") },
      body: JSON.stringify(body || {}),
    });
    return !!(r && r.status === 200);
  } catch (e) {
    return false;
  }
}

/* 事件 payload：字段与原 supervisor-bridge 一致。 */
function buildEventPayload(type, file, oldPath) {
  const f = file || {};
  return {
    event: {
      type: type,
      path: f.path || "",
      oldPath: oldPath || "",
      size: (f.stat && f.stat.size) || 0,
      mtime: (f.stat && f.stat.mtime) || Date.now(),
    },
  };
}

/* 全库索引：形状与原 supervisor-bridge 的 buildIndex 一致，不要改。 */
function buildIndex(app) {
  const v = app && app.vault ? app.vault : null;
  const mc = app && app.metadataCache ? app.metadataCache : null;
  const index = { vault: v && v.getName ? v.getName() : "", count: 0, files: [], stats: {} };
  if (!v || !v.getMarkdownFiles) return index;
  const files = [];
  const mdFiles = v.getMarkdownFiles() || [];
  for (let i = 0; i < mdFiles.length; i++) {
    const f = mdFiles[i];
    const c = safeCache(mc, f) || {};
    const fm = c.frontmatter || {};
    const tags = (c.tags || []).map(function (t) { return t.tag; })
      .concat(Array.isArray(fm.tags) ? fm.tags.map(String) : (fm.tags ? [String(fm.tags)] : []));
    files.push({
      path: f.path, name: f.name, folder: f.parent ? f.parent.path : "",
      size: f.stat.size, mtime: f.stat.mtime, ctime: f.stat.ctime,
      frontmatter_keys: Object.keys(fm), tags: Array.from(new Set(tags)),
      links: (c.links || []).map(function (l) { return l.link; }), embeds: (c.embeds || []).length,
      headings: (c.headings || []).length, has_frontmatter: Object.keys(fm).length > 0,
    });
  }
  const unresolved = (mc && mc.unresolvedLinks) || {};
  let brokenCount = 0;
  const uk = Object.keys(unresolved);
  for (let i = 0; i < uk.length; i++) brokenCount += Object.keys(unresolved[uk[i]] || {}).length;
  const loaded = v.getAllLoadedFiles ? (v.getAllLoadedFiles() || []) : [];
  const folders = loaded.filter(function (x) { return x.children !== undefined; }).length;
  index.vault = v.getName ? v.getName() : "";
  index.count = files.length;
  index.files = files;
  index.stats = {
    notes: files.length, folders: folders,
    total_bytes: files.reduce(function (a, b) { return a + b.size; }, 0),
    unresolved_links: brokenCount,
    with_frontmatter: files.filter(function (f) { return f.has_frontmatter; }).length,
  };
  return index;
}

/* 体检 payload：{ summary, items, ts }，供 POST /api/vault/check 的 { checks: ... } 使用。 */
function bridgeCheckPayload(result, now) {
  const d = now || new Date();
  const bridge = (result && result.bridge) || { summary: {}, items: [] };
  return { summary: bridge.summary, items: bridge.items, ts: d.toISOString() };
}

/* 取库的 git 状态（异步，失败不抛）。 */
function gitStatus(vaultDir, deps) {
  const run = (deps && deps.execFile) || child_process.execFile;
  return new Promise(function (resolve) {
    if (!vaultDir) return resolve({ available: false, changed: 0, files: [] });
    try {
      run("git", ["-C", vaultDir, "status", "--porcelain"], { windowsHide: true, timeout: 15000 },
        function (err, stdout) {
          if (err) return resolve({ available: false, changed: 0, files: [], error: String(err.message || err) });
          const files = String(stdout || "").split("\n").map(function (s) { return s.trim(); }).filter(Boolean);
          resolve({ available: true, changed: files.length, files: files.slice(0, 60) });
        });
    } catch (e) {
      resolve({ available: false, changed: 0, files: [], error: String((e && e.message) || e) });
    }
  });
}

/*
 * 执行监督者下发的命令。白名单只有 create_note / append_note / move_note，**绝不删除**。
 * app 为 Obsidian App（便于单测注入）。
 */
async function execCommand(app, c) {
  const v = app && app.vault ? app.vault : null;
  const fmg = app && app.fileManager ? app.fileManager : null;
  if (!v) return { ok: false, detail: "没有 vault" };
  try {
    if (c.action === "create_note") {
      const p = normalizePath(String(c.path || ""));
      if (!p) return { ok: false, detail: "路径为空" };
      if (v.getAbstractFileByPath(p)) return { ok: false, detail: "已存在，不覆盖：" + p };
      await v.create(p, String(c.content || ""));
      return { ok: true, path: p, detail: "已新建 " + p };
    }
    if (c.action === "append_note") {
      const p = normalizePath(String(c.path || ""));
      const f = v.getAbstractFileByPath(p);
      if (!f) return { ok: false, detail: "找不到：" + p };
      await v.process(f, function (data) {
        return data + (data.endsWith("\n") ? "" : "\n") + String(c.content || "") + "\n";
      });
      return { ok: true, path: p, detail: "已追加 " + p };
    }
    if (c.action === "move_note") {
      const from = v.getAbstractFileByPath(normalizePath(String(c.path || "")));
      const to = normalizePath(String(c.to || ""));
      if (!from) return { ok: false, detail: "找不到源文件" };
      if (!to) return { ok: false, detail: "目标为空" };
      if (v.getAbstractFileByPath(to)) return { ok: false, detail: "目标已存在，不覆盖" };
      if (fmg && fmg.renameFile) await fmg.renameFile(from, to);
      else await v.rename(from, to);
      return { ok: true, path: to, detail: "已移动 → " + to };
    }
    return { ok: false, detail: "命令不在白名单（本插件不删除文件）：" + c.action };
  } catch (e) {
    return { ok: false, detail: String((e && e.message) || e) };
  }
}

// ==================== 插件主体 ====================

class CodexSupervisorPlugin extends Plugin {
  constructor() {
    super(...arguments);
    this.settings = Object.assign({}, DEFAULT_SETTINGS);
    this._timer = null;
    this._lastReport = null;
    this.lastChecks = null;
    this.lastSync = null;
    this.lastCmdIndex = 0;
  }

  async onload() {
    try {
      await this.loadSettings();
    } catch (e) {
      console.error("[管家监督] 读取设置失败，改用默认值", e);
      this.settings = Object.assign({}, DEFAULT_SETTINGS);
    }
    this.lastCmdIndex = Number(this.settings.lastCmdIndex) || 0;

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
      this.addCommand({
        id: "sb-sync",
        name: "管家：同步库索引给监督者",
        callback: () => { this.syncIndex(true); },
      });
      this.addCommand({
        id: "sb-status",
        name: "管家：查看监督者状态面板",
        callback: () => { this.openPanel(); },
      });
      this.addCommand({
        id: "sb-queue",
        name: "管家：执行监督者命令队列",
        callback: () => { this.drainCommands(true); },
      });
    } catch (e) {
      console.error("[管家监督] 注册命令失败", e);
    }

    try {
      this.addRibbonIcon("shield-check", "管家：查看监督者状态面板", () => {
        this.openPanel();
      });
    } catch (e) {
      console.error("[管家监督] 注册侧边栏图标失败", e);
    }

    try {
      const handler = (type) => (file, oldPath) => {
        this.onVaultEvent(type, file, oldPath);
        this.scheduleCheck();
      };
      this.registerEvent(this.app.vault.on("create", handler("create")));
      this.registerEvent(this.app.vault.on("modify", handler("modify")));
      this.registerEvent(this.app.vault.on("delete", handler("delete")));
      this.registerEvent(this.app.vault.on("rename", handler("rename")));
    } catch (e) {
      console.error("[管家监督] 注册知识库事件失败", e);
    }

    try {
      const everyMs = Math.max(1, Number(this.settings.autoSyncMinutes) || DEFAULT_SETTINGS.autoSyncMinutes) * 60000;
      this.registerInterval(window.setInterval(() => this.syncIndex(false), everyMs));
      this.registerInterval(window.setInterval(() => this.drainCommands(false), 60000));
    } catch (e) {
      console.error("[管家监督] 注册定时任务失败", e);
    }

    try {
      this.app.workspace.onLayoutReady(() => {
        window.setTimeout(() => { this.syncIndex(false); this.runHealthCheck(false); }, 4000);
      });
    } catch (e) {
      console.error("[管家监督] 首次同步排程失败", e);
    }

    console.log("[管家监督] 已加载；监督者=" + this.settings.base);
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
    const data = Object.assign({}, this.settings, { lastCmdIndex: this.lastCmdIndex || 0 });
    await this.saveData(data);
  }

  // ---------------- 与监督者通讯 ----------------
  token() {
    return readToken(this.settings, { fs: fs });
  }

  async post(apiPath, body) {
    return bridgePost({ base: this.settings.base, token: this.token(), requestUrl: requestUrl }, apiPath, body);
  }

  async get(apiPath) {
    try {
      const r = await requestUrl({ url: String(this.settings.base) + apiPath, method: "GET", throw: false });
      return r.status === 200 ? r.json : null;
    } catch (e) {
      return null;
    }
  }

  // ---------------- 事件 ----------------
  onVaultEvent(type, file, oldPath) {
    if (!this.settings.pushEvents) return;
    this.post("/api/vault/event", buildEventPayload(type, file, oldPath));
  }

  // ---------------- 全库索引 ----------------
  async syncIndex(loud) {
    const idx = buildIndex(this.app);
    const ok = await this.post("/api/vault/index", { index: idx });
    this.lastSync = { ts: Date.now(), ok: ok, count: idx.count };
    if (loud && typeof Notice === "function") {
      try { new Notice(ok ? ("已同步 " + idx.count + " 篇给监督者") : "同步失败：监督者没在跑？"); }
      catch (e) { /* 忽略通知失败 */ }
    }
    return ok;
  }

  // ---------------- 体检：一次扫描，两个出口 ----------------
  basePath() {
    try { return this.app.vault.adapter.getBasePath(); } catch (e) { return ""; }
  }

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

  async runHealthCheck(notify) {
    try {
      const settings = Object.assign({}, DEFAULT_SETTINGS, this.settings || {});
      const git = await gitStatus(this.basePath());
      const result = runChecks(this.app, settings, { now: new Date(), git: git });
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
      this.lastChecks = { summary: result.bridge.summary, items: result.bridge.items };
      console.log("[管家监督] 体检报告已写入 " + file);
      const ok = await this.post("/api/vault/check", { checks: bridgeCheckPayload(result, new Date()) });
      if (notify && typeof Notice === "function") {
        try {
          new Notice("管家体检完成：" + result.summary + (ok ? "" : "（未能上报监督者）"), 6000);
        } catch (e) { /* 忽略通知失败 */ }
      }
      return report;
    } catch (e) {
      console.error("[管家监督] 体检失败", e);
      if (notify && typeof Notice === "function") {
        try { new Notice("管家体检失败，请查看开发者控制台。", 6000); } catch (e2) { /* 忽略 */ }
      }
      return null;
    }
  }

  // ---------------- 执行监督者下发的命令 ----------------
  async execCommand(c) {
    return execCommand(this.app, c);
  }

  async drainCommands(loud) {
    const st = await this.get("/api/vault/state");
    const cmds = (st && st.commands) || [];
    const todo = cmds.filter((c) => Number(c.i) > Number(this.lastCmdIndex || 0));
    for (let i = 0; i < todo.length; i++) {
      const c = todo[i];
      const r = await this.execCommand(c);
      await this.post("/api/vault/command-result", {
        action: c.action, path: r.path || c.path, ok: r.ok, detail: r.detail,
      });
      this.lastCmdIndex = Math.max(this.lastCmdIndex || 0, Number(c.i) || 0);
      await this.saveSettings();
      if (typeof Notice === "function") {
        try { new Notice("[管家] " + (r.ok ? "完成：" : "未完成：") + r.detail); } catch (e) { /* 忽略 */ }
      }
    }
    if (loud && !todo.length && typeof Notice === "function") {
      try { new Notice("管家命令队列：没有待执行命令"); } catch (e) { /* 忽略 */ }
    }
    return todo.length;
  }

  // ---------------- 状态面板 ----------------
  openPanel() {
    let modal;
    try {
      modal = new Modal(this.app);
    } catch (e) {
      console.error("[管家监督] 打开状态面板失败", e);
      return;
    }
    modal.titleEl.setText("管家 · Obsidian 桥");
    const box = modal.contentEl.createDiv({ cls: "sb-wrap" });
    box.createEl("p", { cls: "sb-mut", text: "正在取监督者状态…" });
    modal.open();
    const self = this;
    this.get("/api/vault/state").then(function (s) {
      box.empty();
      if (!s) {
        box.createEl("p", { text: "连不上监督者（" + self.settings.base + "）。确认「管家」窗口在跑。" });
        return;
      }
      const lvl = String(s.level || "?");
      const cls = lvl === "NORMAL" ? "sb-ok" : lvl === "WATCH" ? "sb-watch"
        : lvl === "DEGRADED" ? "sb-bad" : "sb-block";
      const shape = { NORMAL: "●", WATCH: "◆", DEGRADED: "▲", BLOCKED: "■" }[lvl] || "·";
      const head = box.createDiv({ cls: "sb-row" });
      head.createSpan({ cls: "sb-badge " + cls, text: shape + " " + lvl });
      head.createSpan({ text: "分数 " + (s.score === undefined ? "—" : s.score) });
      const kv = function (k, v) {
        const r = box.createDiv({ cls: "sb-kv sb-row" });
        r.createEl("b", { text: k });
        r.createSpan({ text: String(v) });
      };
      kv("原因", (s.reasons || []).join("；") || "—");
      kv("待你处理", (s.pending_corrections || 0) + " 条责令改正");
      kv("上次索引", s.index_ts ? (s.index_ts + "（" + (s.notes || 0) + " 篇）") : "还没同步过");
      const c = s.checks || {};
      kv("库体检", "断链 " + (c.broken_links || 0) + " · 缺 frontmatter " + (c.missing_frontmatter || 0)
        + " · 收件箱堆积 " + (c.inbox_stale || 0) + " · 未提交 " + (c.uncommitted || 0));
      if ((s.commands || []).length) kv("排队命令", (s.commands || []).length + " 条");
      const bar = box.createDiv({ cls: "sb-row" });
      const b1 = bar.createEl("button", { text: "同步索引", cls: "sb-btn" });
      b1.onclick = function () { self.syncIndex(true); };
      const b2 = bar.createEl("button", { text: "库体检", cls: "sb-btn" });
      b2.onclick = function () { self.runHealthCheck(true).then(function () { self.openPanel(); }); };
      const b3 = bar.createEl("button", { text: "执行命令队列", cls: "sb-btn" });
      b3.onclick = function () { self.drainCommands(true); };
      if (self.lastChecks && (self.lastChecks.items || []).length) {
        box.createEl("h4", { text: "体检明细（前 20）" });
        const ul = box.createEl("ul", { cls: "sb-list" });
        const items = self.lastChecks.items.slice(0, 20);
        for (let i = 0; i < items.length; i++) {
          ul.createEl("li", { text: "[" + items[i].kind + "] " + items[i].path + " — " + items[i].detail });
        }
      }
    });
  }
}

class SupervisorSettingTab extends PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display() {
    const containerEl = this.containerEl;
    const plugin = this.plugin;
    containerEl.empty();
    containerEl.createEl("h2", { text: "管家监督 设置" });
    containerEl.createEl("p", {
      text: "本插件既是本地体检（报告写入下面的目录），也是与监督者通讯的桥（事件 / 索引 / 体检 / 命令）。",
    });

    new Setting(containerEl)
      .setName("监督者地址")
      .setDesc("默认：" + DEFAULT_SETTINGS.base)
      .addText((t) => t
        .setPlaceholder(DEFAULT_SETTINGS.base)
        .setValue(String(plugin.settings.base || ""))
        .onChange(async (v) => {
          plugin.settings.base = (v || "").trim() || DEFAULT_SETTINGS.base;
          await plugin.saveSettings();
        }));

    new Setting(containerEl)
      .setName("令牌文件")
      .setDesc("监督者每次启动会重写它（plugin_token.txt）")
      .addText((t) => t
        .setPlaceholder(DEFAULT_SETTINGS.tokenPath)
        .setValue(String(plugin.settings.tokenPath || ""))
        .onChange(async (v) => {
          plugin.settings.tokenPath = (v || "").trim() || DEFAULT_SETTINGS.tokenPath;
          await plugin.saveSettings();
        }));

    new Setting(containerEl)
      .setName("报告目录")
      .setDesc("默认：" + DEFAULT_SETTINGS.reportDir + "（文件名固定 " + REPORT_FILE + "）")
      .addText((t) => t
        .setPlaceholder(DEFAULT_SETTINGS.reportDir)
        .setValue(String(plugin.settings.reportDir || ""))
        .onChange(async (v) => {
          plugin.settings.reportDir = (v || "").trim() || DEFAULT_SETTINGS.reportDir;
          await plugin.saveSettings();
        }));

    new Setting(containerEl)
      .setName("推送实时事件")
      .setDesc("create/modify/delete/rename 立刻推给监督者")
      .addToggle((t) => t
        .setValue(!!plugin.settings.pushEvents)
        .onChange(async (v) => {
          plugin.settings.pushEvents = !!v;
          await plugin.saveSettings();
        }));

    new Setting(containerEl)
      .setName("自动同步（分钟）")
      .setDesc("每隔这么多分钟同步一次全库索引。默认 " + DEFAULT_SETTINGS.autoSyncMinutes + " 分钟。")
      .addText((t) => t
        .setValue(String(plugin.settings.autoSyncMinutes))
        .onChange(async (v) => {
          const n = Number(v);
          plugin.settings.autoSyncMinutes =
            Number.isFinite(n) && n >= 1 ? Math.floor(n) : DEFAULT_SETTINGS.autoSyncMinutes;
          await plugin.saveSettings();
        }));

    new Setting(containerEl)
      .setName("自动体检节流（秒）")
      .setDesc("知识库变动后等待这么多秒再体检，避免频繁扫描。默认 " + DEFAULT_SETTINGS.throttleSeconds + " 秒。")
      .addText((t) => t
        .setValue(String(plugin.settings.throttleSeconds))
        .onChange(async (v) => {
          const n = Number(v);
          plugin.settings.throttleSeconds =
            Number.isFinite(n) && n >= 1 ? Math.floor(n) : DEFAULT_SETTINGS.throttleSeconds;
          await plugin.saveSettings();
        }));

    new Setting(containerEl)
      .setName("收件箱阈值（天）")
      .setDesc("00_Inbox 中超过这么多天未修改的笔记会被标记。默认 " + DEFAULT_SETTINGS.inboxStaleDays + " 天。")
      .addText((t) => t
        .setValue(String(plugin.settings.inboxStaleDays))
        .onChange(async (v) => {
          const n = Number(v);
          plugin.settings.inboxStaleDays =
            Number.isFinite(n) && n >= 1 ? Math.floor(n) : DEFAULT_SETTINGS.inboxStaleDays;
          await plugin.saveSettings();
        }));

    new Setting(containerEl)
      .setName("附件忽略目录")
      .setDesc("这些目录不参与体检，多个用英文逗号分隔。默认：" + DEFAULT_SETTINGS.attachmentIgnoreFolders.join("、"))
      .addText((t) => t
        .setPlaceholder(DEFAULT_SETTINGS.attachmentIgnoreFolders.join(","))
        .setValue(Array.isArray(plugin.settings.attachmentIgnoreFolders)
          ? plugin.settings.attachmentIgnoreFolders.join(",") : "")
        .onChange(async (v) => {
          const list = String(v || "").split(",").map((s) => s.trim()).filter(Boolean);
          plugin.settings.attachmentIgnoreFolders = list.length ? list : DEFAULT_SETTINGS.attachmentIgnoreFolders.slice();
          await plugin.saveSettings();
        }));

    new Setting(containerEl)
      .setName("手动跑一次")
      .addButton((b) => b.setButtonText("同步索引").onClick(() => plugin.syncIndex(true)))
      .addButton((b) => b.setButtonText("库体检").onClick(() => plugin.runHealthCheck(true)))
      .addButton((b) => b.setButtonText("执行命令队列").onClick(() => plugin.drainCommands(true)))
      .addButton((b) => b.setButtonText("看状态").onClick(() => plugin.openPanel()));

    containerEl.createEl("p", {
      cls: "sb-mut",
      text: "令牌：" + (plugin.token() ? "已读到 ✅" : "读不到 ⚠️（检查令牌文件路径 / 监督者是否在跑）"),
    });
  }
}

module.exports = CodexSupervisorPlugin;
module.exports.PLUGIN_ID = PLUGIN_ID;
module.exports.PLUGIN_VERSION = PLUGIN_VERSION;
module.exports.DEFAULT_SETTINGS = DEFAULT_SETTINGS;
module.exports.ORPHAN_ATTACHMENT_MAX_LIST = ORPHAN_ATTACHMENT_MAX_LIST;
module.exports.runChecks = runChecks;
module.exports.writeReport = writeReport;
module.exports.linkCore = linkCore;
module.exports.similarity = similarity;
module.exports.localIso = localIso;
module.exports.readToken = readToken;
module.exports.bridgePost = bridgePost;
module.exports.buildEventPayload = buildEventPayload;
module.exports.buildIndex = buildIndex;
module.exports.bridgeCheckPayload = bridgeCheckPayload;
module.exports.gitStatus = gitStatus;
module.exports.execCommand = execCommand;
