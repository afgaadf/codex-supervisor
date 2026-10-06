/*
 * plugins/obsidian/selftest.js —— 管家监督插件的自测（不需要 Obsidian）。
 * 只拦截 require("obsidian")，其余走真实 Node。
 * 覆盖：作用域（归档/剪藏不计）、大归档不淹没报告、纯锚点不算断链、基础检测正确。
 * 运行：node plugins/obsidian/selftest.js   （全过退出码 0）
 */
const Module = require("module");
const path = require("path");

const origLoad = Module._load;
Module._load = function (req) {
  if (req === "obsidian") {
    return {
      Plugin: class { constructor() {} },
      PluginSettingTab: class {},
      Setting: class {},
      Notice: class {},
      Modal: class {},
      requestUrl: async () => ({}),
      normalizePath: (p) => p,
    };
  }
  return origLoad.apply(this, arguments);
};

const Supervisor = require(path.resolve(__dirname, "main.js"));

// ---- 构造一个假库：正常笔记 + 一个"大归档"（模拟 20_附件/原始资料）----
function md(p, opts) {
  opts = opts || {};
  return { path: p, name: p.split("/").pop(), stat: { mtime: opts.mtime || Date.now(), ctime: 0, size: 10 },
           _fm: opts.fm, _tags: opts.tags };
}
function att(p) { return { path: p, name: p.split("/").pop(), stat: { mtime: Date.now(), ctime: 0, size: 1 } }; }

const notes = [
  md("10_笔记/甲.md", { fm: { title: "甲" }, tags: ["知识"] }),
  md("10_笔记/乙.md", { fm: { title: "乙" } }),
  md("10_笔记/孤.md", { fm: { title: "孤" } }),
  md("00_Inbox/收.md", {}),
  // 归档里的一堆"笔记"：缺 frontmatter、且含大量断链 —— 都不该计入
  ...Array.from({ length: 500 }, (_, i) =>
    md("20_附件/原始资料/网页" + i + ".md", {})),
];
const attachments = [
  att("20_附件/被引用.png"),
  att("20_附件/没人用.png"),
  // 大归档：几千个文件，必须被排除
  ...Array.from({ length: 3000 }, (_, i) => att("20_附件/原始资料/素材" + i + ".png")),
];

const resolvedLinks = {
  "10_笔记/甲.md": { "10_笔记/乙.md": 1, "20_附件/被引用.png": 1 },
  "20_附件/原始资料/网页0.md": { "10_笔记/甲.md": 1 },
};
const unresolvedLinks = {
  "10_笔记/甲.md": { "不存在的笔记": 1, "#某标题": 1 },     // 1 真断链 + 1 纯锚点
  "20_附件/原始资料/网页1.md": { "归档里的断链": 1 },       // 不该算
};

const app = {
  vault: {
    getMarkdownFiles: () => notes,
    getFiles: () => notes.concat(attachments),
  },
  metadataCache: {
    resolvedLinks,
    unresolvedLinks,
    getFileCache: (f) => (f._fm || f._tags ? { frontmatter: f._fm, tags: (f._tags || []).map((t) => ({ tag: "#" + t })) } : null),
  },
};

const result = Supervisor.runChecks(app, Supervisor.DEFAULT_SETTINGS, { now: new Date() });
const byId = {};
result.checks.forEach((c) => { byId[c.id] = c; });

let fails = 0;
function check(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) fails++;
  console.log((ok ? "PASS " : "FAIL ") + name + "  got=" + JSON.stringify(got) + " want=" + JSON.stringify(want));
}

console.log("=== 插件自测（plugins/obsidian/selftest.js）===");
console.log("metrics =", JSON.stringify(result.metrics));
check("断链只算真断链（纯锚点跳过、归档里的不算）", byId["obsidian.broken_link"].count, 1);
check("缺 frontmatter 只算知识区（归档 500 篇不计）", byId["obsidian.missing_frontmatter"].count, 1);
check("孤立笔记只算知识区（10_笔记/孤 + 00_Inbox/收，归档 500 篇不计）", byId["obsidian.orphan_note"].count, 2);
check("收件箱未分类", byId["obsidian.inbox_no_triage"].count, 1);
check("孤立附件排除 原始资料（3000 文件不计）", byId["obsidian.orphan_attachment"].count, 1);
check("metrics 同步正确", result.metrics.orphan_attachments, 1);
check("断链为 error 级 => healthy=false", result.healthy, false);
check("报告上限常量存在", typeof Supervisor.ORPHAN_ATTACHMENT_MAX_LIST, "number");

console.log(fails === 0 ? "=== 全部通过 ===" : "=== 有 " + fails + " 项失败 ===");
process.exit(fails === 0 ? 0 : 1);