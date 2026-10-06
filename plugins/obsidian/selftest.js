/*
 * plugins/obsidian/selftest.js —— 管家监督插件的自测（不需要 Obsidian，也不用真连监督者）。
 * 只拦截 require("obsidian")，其余走真实 Node。
 * 覆盖：
 *   ① 作用域：归档/剪藏不计；大归档不淹没报告；纯锚点不算断链。
 *   ② 附件 A14：按 basename 判定同名引用。
 *   ③ 桥调用：起一个本地假 HTTP 服务器，验证事件/索引/体检确实发出且带上 X-Token。
 * 运行：node plugins/obsidian/selftest.js   （全过退出码 0，输出「全部通过」）
 */
const Module = require("module");
const path = require("path");
const http = require("http");

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

let fails = 0;
function check(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) fails++;
  console.log((ok ? "PASS " : "FAIL ") + name + "  got=" + JSON.stringify(got) + " want=" + JSON.stringify(want));
}
function checkTrue(name, cond, note) {
  if (!cond) fails++;
  console.log((cond ? "PASS " : "FAIL ") + name + (note ? ("  (" + note + ")") : ""));
}

// ---- 构造一个假库：正常笔记 + 一个「大归档」（模拟 20_附件/原始资料）----
function md(p, opts) {
  opts = opts || {};
  return { path: p, name: p.split("/").pop(), stat: { mtime: opts.mtime || Date.now(), ctime: 0, size: opts.size || 10 },
           _fm: opts.fm, _tags: opts.tags };
}
function att(p, size) {
  return { path: p, name: p.split("/").pop(), stat: { mtime: Date.now(), ctime: 0, size: size || 1 } };
}

const notes = [
  md("10_笔记/甲.md", { fm: { title: "甲" }, tags: ["知识"] }),
  md("10_笔记/乙.md", { fm: { title: "乙" } }),
  md("10_笔记/孤.md", { fm: { title: "孤" } }),
  md("00_Inbox/收.md", {}),
  // 归档里的一堆「笔记」：缺 frontmatter、且含大量断链 —— 都不该计入
  ...Array.from({ length: 500 }, (_, i) => md("20_附件/原始资料/网页" + i + ".md", {})),
];
const attachments = [
  att("20_附件/被引用.png"),
  att("20_附件/没人用.png"),
  att("20_附件/同名.png"),
  // A14：同一个 basename 出现在别的目录，别处被引用过 => 不算孤立
  att("20_附件/子目录/同名.png"),
  // 大归档：几千个文件，必须被排除
  ...Array.from({ length: 3000 }, (_, i) => att("20_附件/原始资料/素材" + i + ".png")),
];

const resolvedLinks = {
  "10_笔记/甲.md": { "10_笔记/乙.md": 1, "20_附件/被引用.png": 1, "20_附件/同名.png": 1 },
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
    getName: () => "测试库",
    getAllLoadedFiles: () => notes.concat(attachments),
    adapter: { getBasePath: () => "" },
  },
  metadataCache: {
    resolvedLinks,
    unresolvedLinks,
    getFileCache: (f) => (f._fm || f._tags ? { frontmatter: f._fm, tags: (f._tags || []).map((t) => ({ tag: "#" + t })) } : null),
  },
};

async function main() {
  console.log("=== 插件自测（plugins/obsidian/selftest.js）===");

  // ---------------- ① 作用域 ----------------
  const result = Supervisor.runChecks(app, Supervisor.DEFAULT_SETTINGS, { now: new Date() });
  const byId = {};
  result.checks.forEach((c) => { byId[c.id] = c; });
  console.log("metrics =", JSON.stringify(result.metrics));

  check("断链只算真断链（纯锚点跳过、归档里的不算）", byId["obsidian.broken_link"].count, 1);
  check("缺 frontmatter 只算知识区（归档 500 篇不计）", byId["obsidian.missing_frontmatter"].count, 1);
  check("孤立笔记只算知识区（10_笔记/孤 + 00_Inbox/收，归档 500 篇不计）", byId["obsidian.orphan_note"].count, 2);
  check("收件箱未分类", byId["obsidian.inbox_no_triage"].count, 1);
  check("孤立附件排除 原始资料，且同名引用不算孤立", byId["obsidian.orphan_attachment"].count, 1);
  check("metrics 同步正确", result.metrics.orphan_attachments, 1);
  check("断链为 error 级 => healthy=false", result.healthy, false);
  check("报告上限常量存在", typeof Supervisor.ORPHAN_ATTACHMENT_MAX_LIST, "number");
  check("linkCore 纯锚点返回空", Supervisor.linkCore("#某标题"), "");
  check("linkCore 去掉小标题", Supervisor.linkCore("笔记#标题"), "笔记");

  // ---------------- ② A14 专项：basename 判定 ----------------
  const miniNotes = [md("10_笔记/a.md", { fm: { title: "a" } })];
  const miniAtt = [att("20_附件/图.png"), att("20_附件/子目录/图.png")];
  const miniApp = {
    vault: { getMarkdownFiles: () => miniNotes, getFiles: () => miniNotes.concat(miniAtt) },
    metadataCache: {
      resolvedLinks: { "10_笔记/a.md": { "20_附件/图.png": 1 } },
      unresolvedLinks: {},
      getFileCache: (f) => (f.path === "10_笔记/a.md" ? { frontmatter: f._fm } : null),
    },
  };
  const mini = Supervisor.runChecks(miniApp, Supervisor.DEFAULT_SETTINGS, { now: new Date() });
  check("A14：同名附件在别处被引用 => 不算孤立", mini.metrics.orphan_attachments, 0);

  // ---------------- ③ 桥调用：本地假 HTTP 服务器 ----------------
  const captured = [];
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => { body += c; });
    req.on("end", () => {
      captured.push({ method: req.method, url: req.url, headers: req.headers, body: body });
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end('{"ok":true}');
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  const base = "http://127.0.0.1:" + port;

  // 把 Obsidian 的 requestUrl 换成真发 HTTP（打到假服务器，绝不碰 127.0.0.1:8765）
  const requestUrlShim = (opts) => new Promise((resolve, reject) => {
    const u = new URL(opts.url);
    const req = http.request({
      hostname: u.hostname, port: u.port, path: u.pathname + u.search,
      method: opts.method || "GET", headers: opts.headers || {},
    }, (res) => {
      let data = "";
      res.setEncoding("utf8");
      res.on("data", (c) => { data += c; });
      res.on("end", () => {
        let json = null;
        try { json = JSON.parse(data); } catch (e) { json = null; }
        resolve({ status: res.statusCode, text: data, json: json });
      });
    });
    req.on("error", reject);
    if (opts.body) req.write(opts.body);
    req.end();
  });

  const bridge = { base: base, token: "TEST-TOKEN-123", requestUrl: requestUrlShim };

  await Supervisor.bridgePost(
    bridge, "/api/vault/event",
    Supervisor.buildEventPayload("create", { path: "10_笔记/甲.md", stat: { size: 12, mtime: 111 } }));
  await Supervisor.bridgePost(bridge, "/api/vault/index", { index: Supervisor.buildIndex(app) });
  const withGit = Supervisor.runChecks(app, Supervisor.DEFAULT_SETTINGS,
    { now: new Date(), git: { available: true, changed: 3, files: [] } });
  await Supervisor.bridgePost(bridge, "/api/vault/check",
    { checks: Supervisor.bridgeCheckPayload(withGit, new Date()) });

  await new Promise((resolve) => server.close(resolve));

  const ev = captured.filter((c) => c.url === "/api/vault/event")[0];
  const ix = captured.filter((c) => c.url === "/api/vault/index")[0];
  const ck = captured.filter((c) => c.url === "/api/vault/check")[0];
  console.log("bridge 捕获到 " + captured.length + " 个请求：" + captured.map((c) => c.url).join(", "));

  checkTrue("事件打到 /api/vault/event 且是 POST", !!ev && ev.method === "POST");
  checkTrue("事件 body 里有 event.type / event.path",
    !!ev && (function () {
      const b = JSON.parse(ev.body);
      return b.event && b.event.type === "create" && b.event.path === "10_笔记/甲.md";
    })());
  checkTrue("索引打到 /api/vault/index 且 body 有 files/count",
    !!ix && (function () {
      const b = JSON.parse(ix.body);
      return !!(b.index && Array.isArray(b.index.files) && typeof b.index.count === "number" && b.index.count > 0);
    })());
  checkTrue("体检打到 /api/vault/check 且 summary 字段齐全",
    !!ck && (function () {
      const b = JSON.parse(ck.body);
      const s = b.checks && b.checks.summary;
      const keys = ["notes", "broken_links", "missing_frontmatter", "inbox_stale", "big_note", "uncommitted", "git_available"];
      return !!s && keys.every((k) => typeof s[k] === "number");
    })());
  checkTrue("体检 summary 数值正确（断链1 / 未提交3 / git_available1）",
    !!ck && (function () {
      const s = JSON.parse(ck.body).checks.summary;
      return s.broken_links === 1 && s.uncommitted === 3 && s.git_available === 1;
    })());
  checkTrue("三次桥调用都带上了 X-Token 头",
    !!ev && ev.headers["x-token"] === "TEST-TOKEN-123"
    && !!ix && ix.headers["x-token"] === "TEST-TOKEN-123"
    && !!ck && ck.headers["x-token"] === "TEST-TOKEN-123");


  // ---------------- ④ 命令白名单：绝不删除 ----------------
  const store = {};
  const cmdApp = {
    vault: {
      getAbstractFileByPath: (f) => store[f] || (f === "已有.md" ? { path: f } : null),
      create: async (f, c) => { store[f] = { path: f, content: c }; return store[f]; },
      process: async () => {},
      rename: async () => {},
    },
    fileManager: null,
  };
  const rCreate = await Supervisor.execCommand(cmdApp, { action: "create_note", path: "新建.md", content: "hi" });
  check("命令：create_note 可新建", rCreate.ok, true);
  const rExists = await Supervisor.execCommand(cmdApp, { action: "create_note", path: "已有.md", content: "x" });
  check("命令：create_note 已存在则不覆盖", rExists.ok, false);
  const rDel = await Supervisor.execCommand(cmdApp, { action: "delete_note", path: "新建.md" });
  checkTrue("命令：删除被拒绝（白名单外，本插件绝不删除）", rDel.ok === false && /白名单/.test(rDel.detail), rDel.detail);
  check("命令：删除后文件仍在", !!store["新建.md"], true);

  console.log(fails === 0 ? "=== 全部通过 ===" : "=== 有 " + fails + " 项失败 ===");
  process.exitCode = fails === 0 ? 0 : 1;
}

main().catch((e) => {
  console.error("自测崩溃：", e);
  process.exitCode = 1;
});
