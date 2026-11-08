"""Страница SQL-конструктора: ноды таблиц, связи, живой скрипт, AI."""

from __future__ import annotations

import json
from html import escape

from core.page import _nav, _style


def builder_page(
    *,
    schema: dict | None = None,
    selected_db: str = "",
    dbs: list[dict] | None = None,
    is_admin: bool = False,
    initial_sql: str = "",
    error: str = "",
) -> str:
    """Конструктор запросов: каждая таблица — нода, скрипт живёт рядом."""
    schema = schema or {}
    tables = schema.get("tables") or []
    dbs = dbs or []

    schema_json = json.dumps(tables, ensure_ascii=False)
    dbs_json = json.dumps(
        [{"path": d["path"], "label": d["label"]} for d in dbs],
        ensure_ascii=False,
    )

    db_options = "".join(
        f'<option value="{escape(d["path"], quote=True)}"'
        f'{" selected" if d["path"] == selected_db else ""}>'
        f'{escape(d["label"])}</option>'
        for d in dbs
    )
    db_select = (
        f'<select id="db-sel" onchange="switchDb(this.value)">{db_options}</select>'
        if dbs
        else "<span class='role'>нет баз</span>"
    )

    notice_html = f'<p class="notice bad">{escape(error)}</p>' if error else ""

    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Конструктор SQL — AI Bridge</title>
<style>
{_style()}
  body {{ max-width: none; padding-top: .5rem; }}
  .builder-top {{ display: flex; gap: .6rem; align-items: center; flex-wrap: wrap;
    padding: .5rem .8rem; background: var(--card); border: 1px solid var(--line);
    border-radius: .8rem; margin-bottom: .7rem; }}
  .builder-top select {{ padding: .3rem .5rem; border-radius: .5rem;
    border: 1px solid var(--line); background: var(--bg); color: var(--text); font: inherit; }}
  .builder-wrap {{ display: grid; grid-template-columns: minmax(0,1fr) minmax(22rem,.75fr);
    gap: .7rem; align-items: start; }}
  #canvas {{ position: relative; min-height: 34rem; background:
    radial-gradient(circle, #1e293b 1px, transparent 1px) 0 0/22px 22px var(--card);
    border: 1px solid var(--line); border-radius: .8rem; overflow: auto; }}
  #edges {{ position: absolute; inset: 0; pointer-events: none; }}
  .node {{ position: absolute; width: 17rem; background: #101a24;
    border: 1.5px solid #2d4a6f; border-radius: .7rem; font-size: .8rem;
    box-shadow: 0 6px 18px rgba(0,0,0,.35); user-select: none; }}
  .node header {{ background: #1e3a5f; color: #7dd3fc; font-family: monospace;
    font-weight: 700; padding: .4rem .6rem; cursor: grab; border-radius: .6rem .6rem 0 0;
    display: flex; justify-content: space-between; gap: .4rem; align-items: center; }}
  .node header .cmd {{ background: #0f172a; border: 1px solid #34d399; color: #34d399;
    border-radius: .4rem; padding: 0 .35rem; font-size: .7rem; cursor: pointer; }}
  .node header .btns {{ display: flex; gap: .3rem; align-items: center; }}
  .node header .ai {{ background: #0f172a; border: 1px solid #38bdf8; color: #38bdf8;
    border-radius: .4rem; padding: 0 .35rem; font-size: .7rem; cursor: pointer; }}
  .cmd-menu {{ position: absolute; z-index: 40; background: var(--card);
    border: 1px solid var(--accent); border-radius: .5rem; padding: .3rem;
    min-width: 13rem; max-height: 18rem; overflow: auto;
    box-shadow: 0 8px 24px rgba(0,0,0,.45); }}
  .cmd-menu div {{ padding: .25rem .5rem; cursor: pointer; border-radius: .3rem;
    font-size: .78rem; white-space: nowrap; }}
  .cmd-menu div:hover {{ background: var(--accent-soft); }}
  .node-ai {{ position: fixed; z-index: 60; width: 19rem; background: #101a24;
    border: 1.5px solid #38bdf8; border-radius: .7rem; padding: .55rem;
    display: grid; gap: .45rem; font-size: .8rem;
    box-shadow: 0 12px 32px rgba(0,0,0,.55); }}
  .node-ai .t {{ color: #7dd3fc; font-family: monospace; font-weight: 700; }}
  .node-ai input {{ width: 100%; padding: .35rem .5rem; border-radius: .4rem;
    border: 1px solid var(--line); background: var(--bg); color: var(--text); font: inherit; }}
  .node-ai .row {{ display: flex; gap: .3rem; flex-wrap: wrap; }}
  .node-ai .row button {{ font-size: .72rem; padding: .25rem .45rem; border-radius: .4rem;
    border: 1px solid #34d399; background: #0f172a; color: #34d399; cursor: pointer;
    font-family: inherit; }}
  .node-ai .row button:hover {{ background: #123528; }}
  .node-ai .out {{ white-space: pre-wrap; max-height: 11rem; overflow: auto;
    color: #d7e3ee; font-family: monospace; font-size: .72rem; display: none; }}
  .node ul {{ margin: 0; padding: .3rem .5rem .45rem; list-style: none;
    font-family: monospace; color: #d7e3ee; }}
  .node li {{ display: flex; gap: .4rem; justify-content: space-between; cursor: pointer;
    padding: .1rem .25rem; border-radius: .3rem; }}
  .node li:hover {{ background: #1e3a5f; color: #7dd3fc; }}
  .node li .tag {{ color: #34d399; font-weight: 700; }}
  .node li .tag.pk {{ color: #fbbf24; }}
  .node .data {{ padding: 0 .5rem .45rem; color: #64748b; font-family: monospace;
    font-size: .68rem; }}
  .side {{ display: grid; gap: .7rem; }}
  .side textarea {{ width: 100%; font: .88rem/1.5 ui-monospace, monospace;
    min-height: 12rem; resize: vertical; }}
  .side textarea {{ background: var(--bg); color: var(--text);
    border: 1px solid var(--line); border-radius: .6rem; padding: .6rem; }}
  .ai-bar {{ display: flex; gap: .4rem; flex-wrap: wrap; align-items: center; }}
  .ai-bar input {{ flex: 1 1 12rem; padding: .4rem .6rem; border-radius: .5rem;
    border: 1px solid var(--line); background: var(--bg); color: var(--text); font: inherit; }}
  .ai-out {{ background: var(--card); border: 1px solid var(--line); border-radius: .6rem;
    padding: .6rem; font-size: .83rem; white-space: pre-wrap; max-height: 16rem; overflow: auto; }}
  #hints {{ position: fixed; background: var(--card); border: 1px solid var(--accent);
    border-radius: .5rem; padding: .3rem; display: none; z-index: 30; min-width: 13rem; }}
  #hints div {{ padding: .2rem .5rem; cursor: pointer; border-radius: .3rem; font-size: .8rem; }}
  #hints div:hover, #hints div.sel {{ background: var(--accent-soft); }}
  @media (max-width: 52rem) {{ .builder-wrap {{ grid-template-columns: 1fr; }} }}
</style></head><body>
{_nav(can_edit=True)}
<div class="builder-top">
  <b>Конструктор SQL</b>
  <label>База: {db_select}</label>
  <a href="/sql" class="ghost pill">← SQL Playground</a>
  <span class="role">клик по ноде тащит; клик по столбцу вставляет его в скрипт</span>
</div>
{notice_html}
<div class="builder-wrap">
  <div id="canvas"><svg id="edges"></svg></div>
  <div class="side">
    <section class="card">
      <h2>Скрипт</h2>
      <textarea id="script" spellcheck="false">{escape(initial_sql)}</textarea>
      <div class="ai-bar" style="margin-top:.5rem">
        <button class="primary" id="run-btn">▶ выполнить</button>
        <button class="ghost pill" id="clear-btn">очистить</button>
        <button class="ghost pill" id="copy-btn">копировать</button>
      </div>
      <div id="ai-per-node" class="role" style="margin-top:.4rem">
        AI живёт в ноде: « объяснить ошибку » / « исправить ошибку » / « по промпту »
      </div>
    </section>
    <section class="card">
      <h2>AI</h2>
      <div class="ai-bar">
        <input id="ai-prompt" placeholder="промпт — не может быть пустым">
      </div>
      <div class="ai-bar" style="margin-top:.4rem">
        <button data-ai="explain">объяснить ошибку</button>
        <button data-ai="fix">исправить ошибку</button>
        <button data-ai="gen">сгенерировать код по промпту</button>
      </div>
      <div id="ai-out" class="ai-out" style="margin-top:.5rem;display:none"></div>
    </section>
    <section class="card" id="result-card" style="display:none">
      <h2>Результат</h2>
      <div id="result"></div>
    </section>
  </div>
</div>
<div id="hints"></div>
<script>
const TABLES = {schema_json};
const DBS = {dbs_json};
</script>
{BUILD_JS}
</body></html>"""


# ------------------------------------------------- фрагмент JS вынесен ниже

BUILD_JS = r"""
<script>
"use strict";
const canvas = document.getElementById("canvas");
const edgesSvg = document.getElementById("edges");
const scriptEl = document.getElementById("script");
const hints = document.getElementById("hints");
const DBSEL = document.getElementById("db-sel") || { value: "" };
const includeData = true;

const COLS = 3, NW = 272;
const pos = {};
TABLES.forEach((t, i) => {
  pos[t.name] = { x: 24 + (i % COLS) * (NW + 40), y: 24 + Math.floor(i / COLS) * 210 };
});
function esc(s) { return String(s ?? "").replace(/[&<>"]/g, m => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[m])); }

function renderNodes() {
  canvas.querySelectorAll(".node").forEach(n => n.remove());
  TABLES.forEach(t => {
    const p = pos[t.name] || { x: 24, y: 24 };
    const el = document.createElement("div");
    el.className = "node";
    el.style.left = p.x + "px"; el.style.top = p.y + "px";
    el.dataset.table = t.name;
    const fkCols = new Set((t.fks || []).map(f => f.from));
    const rows = (t.columns || []).map(col => {
      const tags = [];
      if (col.pk) tags.push("PK");
      if (fkCols.has(col.name)) tags.push("FK");
      const tag = tags.length ? `<span class="tag ${col.pk ? "pk" : ""}">${tags.join("/")}</span>` : "<span></span>";
      return `<li data-col="${esc(col.name)}"><span>${esc(col.name)}</span>${tag}</li>`;
    }).join("");
    let dataHtml = "";
    if (includeData && t.sample_rows && t.sample_rows.length) {
      const vals = Object.values(t.sample_rows[0] || {}).map(v => String(v).slice(0, 10));
      dataHtml = `<div class="data">${esc(vals.join(", "))}</div>`;
    }
    el.innerHTML = `<header><span>${esc(t.name)}</span><span class="btns">` +
      `<span class="ai" title="AI по этой таблице">🤖</span>` +
      `<span class="cmd" title="команды">⌘</span></span></header><ul>${rows}</ul>${dataHtml}`;
    canvas.appendChild(el);
  });
  drawEdges();
}

function drawEdges() {
  let W = canvas.scrollWidth, H = canvas.scrollHeight;
  edgesSvg.setAttribute("width", W); edgesSvg.setAttribute("height", H);
  let html = '<defs><marker id="ar" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><path d="M0,0 L8,3 L0,6 Z" fill="#34d399"/></marker></defs>';
  TABLES.forEach(t => {
    (t.fks || []).forEach(f => {
      const a = pos[t.name], b = pos[f.table];
      if (!a || !b) return;
      const x1 = a.x + 136, y1 = a.y + 60, x2 = b.x, y2 = b.y + 60, mx = (x1 + x2) / 2;
      html += `<path d="M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}" fill="none" stroke="#34d399" stroke-width="1.6" marker-end="url(#ar)" opacity=".85"/>`;
    });
  });
  edgesSvg.innerHTML = html;
}

let drag = null;
canvas.addEventListener("pointerdown", (e) => {
  const header = e.target.closest("header");
  const node = e.target.closest(".node");
  if (!node || !header || e.target.closest(".cmd, .ai")) return;
  drag = { node, dx: e.clientX - node.offsetLeft, dy: e.clientY - node.offsetTop };
  node.setPointerCapture(e.pointerId);
});
canvas.addEventListener("pointermove", (e) => {
  if (!drag) return;
  drag.node.style.left = Math.max(0, e.clientX - drag.dx) + "px";
  drag.node.style.top = Math.max(0, e.clientY - drag.dy) + "px";
  pos[drag.node.dataset.table] = { x: drag.node.offsetLeft, y: drag.node.offsetTop };
  drawEdges();
});
canvas.addEventListener("pointerup", () => { drag = null; });

function insertAtCursor(text) {
  const s = scriptEl.selectionStart, e = scriptEl.selectionEnd;
  const before = scriptEl.value.slice(0, s), after = scriptEl.value.slice(e);
  const needSpace = before && !/[\s(,.\[]$/.test(before);
  const ins = (needSpace ? " " : "") + text;
  scriptEl.value = before + ins + after;
  scriptEl.setSelectionRange(s + ins.length, s + ins.length);
  scriptEl.focus();
  onScriptChange();
}

canvas.addEventListener("click", (e) => {
  const li = e.target.closest("li[data-col]");
  if (!li) return;
  const table = li.closest(".node").dataset.table;
  insertAtCursor(table + "." + li.dataset.col);
});

const COMMANDS = [
  ["SELECT * FROM t", t => `SELECT * FROM ${t}`],
  ["SELECT cols…", t => `SELECT ${t}.col FROM ${t}`],
  ["JOIN … ON", t => `SELECT *\nFROM ${t}\nJOIN other_table ON ${t}.id = other_table.id`],
  ["WHERE col = ''", t => `WHERE ${t}.col = ''`],
  ["WHERE a > b AND …", () => `WHERE col > 0 AND col2 = ''`],
  ["ORDER BY col", t => `ORDER BY ${t}.col`],
  ["GROUP BY col", t => `GROUP BY ${t}.col`],
  ["HAVING COUNT(*) > n", () => `HAVING COUNT(*) > 1`],
  ["LIMIT n", () => `LIMIT 10`],
  ["COUNT(*)", t => `COUNT(${t}.id)`],
  ["COUNT(DISTINCT col)", t => `COUNT(DISTINCT ${t}.col)`],
  ["MAX/MIN", t => `MAX(${t}.col), MIN(${t}.col)`],
  ["AVG/SUM", t => `AVG(${t}.col), SUM(${t}.col)`],
  ["INSERT INTO", t => `INSERT INTO ${t} (col) VALUES ('')`],
  ["UPDATE … SET", t => `UPDATE ${t} SET col = ''`],
  ["DELETE FROM", t => `DELETE FROM ${t}`],
  ["GROUP BY + COUNT", t => `SELECT ${t}.col, COUNT(*)\nFROM ${t}\nGROUP BY ${t}.col`],
];

canvas.addEventListener("click", (e) => {
  const btn = e.target.closest(".cmd");
  if (!btn) return;
  e.stopPropagation();
  const table = btn.closest(".node").dataset.table;
  const old = canvas.querySelector(".cmd-menu");
  if (old) old.remove();
  const menu = document.createElement("div");
  menu.className = "cmd-menu";
  const r = btn.getBoundingClientRect(), cr = canvas.getBoundingClientRect();
  menu.style.left = (r.left - cr.left + canvas.scrollLeft) + "px";
  menu.style.top = (r.bottom - cr.top + canvas.scrollTop) + "px";
  COMMANDS.forEach(([name, tpl]) => {
    const item = document.createElement("div");
    item.textContent = name;
    item.onclick = () => { insertAtCursor(tpl(table)); menu.remove(); };
    menu.appendChild(item);
  });
  canvas.appendChild(menu);
  setTimeout(() => {
    const close = (ev) => { if (!menu.contains(ev.target)) { menu.remove(); document.removeEventListener("click", close, true); } };
    document.addEventListener("click", close, true);
  }, 0);
});

canvas.addEventListener("click", (e) => {
  const btn = e.target.closest(".ai");
  if (!btn) return;
  e.stopPropagation();
  openNodeAi(btn, btn.closest(".node").dataset.table);
});

const KEYWORDS = ["SELECT","FROM","WHERE","JOIN","LEFT","RIGHT","INNER","OUTER","ON","AND","OR","NOT","NULL","IS",
  "GROUP","BY","ORDER","HAVING","LIMIT","OFFSET","INSERT","INTO","VALUES","UPDATE","SET","DELETE","CREATE","TABLE",
  "DROP","ALTER","ADD","PRIMARY","KEY","FOREIGN","REFERENCES","UNIQUE","CHECK","DEFAULT","DISTINCT","AS","CASE",
  "WHEN","THEN","ELSE","END","BETWEEN","LIKE","IN","ASC","DESC","COUNT","SUM","AVG","MAX","MIN","WITH","UNION","ALL","EXISTS"];

function showHints() {
  const caret = scriptEl.selectionStart;
  const before = scriptEl.value.slice(0, caret);
  const tok = before.match(/[A-Za-z_][A-Za-z0-9_.]*$/);
  if (!tok) { hints.style.display = "none"; return; }
  const q = tok[0].toLowerCase();
  const cols = new Set();
  TABLES.forEach(t => (t.columns || []).forEach(c => cols.add(t.name + "." + c.name)));
  const items = [...[...cols].filter(x => x.toLowerCase().includes(q)),
                 ...TABLES.map(t => t.name).filter(x => x.toLowerCase().includes(q)),
                 ...KEYWORDS.filter(x => x.toLowerCase().startsWith(q))].slice(0, 12);
  if (!items.length) { hints.style.display = "none"; return; }
  const start = caret - tok[0].length;
  hints.innerHTML = items.map(x =>
    `<div data-v="${esc(x)}" data-s="${start}" data-e="${caret}">${esc(x)}</div>`).join("");
  const cr = caretRect();
  hints.style.display = "block";
  hints.style.left = Math.min(cr.left, window.innerWidth - 220) + "px";
  hints.style.top = Math.min(cr.bottom + 4, window.innerHeight - 40) + "px";
}

// Прямоугольник каретки: рисуем копию текста до курсора и меряем маркер.
function caretRect() {
  const cs = getComputedStyle(scriptEl);
  const box = document.createElement("div");
  ["fontFamily", "fontSize", "fontWeight", "lineHeight", "letterSpacing",
   "paddingTop", "paddingRight", "paddingBottom", "paddingLeft",
   "borderTopWidth", "borderRightWidth", "borderBottomWidth", "borderLeftWidth",
   "boxSizing", "textAlign"].forEach(p => { box.style[p] = cs[p]; });
  box.style.position = "fixed";
  box.style.visibility = "hidden";
  box.style.whiteSpace = "pre-wrap";
  box.style.wordWrap = "break-word";
  box.style.width = scriptEl.clientWidth + "px";
  box.textContent = scriptEl.value.slice(0, scriptEl.selectionStart);
  const mark = document.createElement("span");
  mark.textContent = "\u200b";
  box.appendChild(mark);
  document.body.appendChild(box);
  const r = mark.getBoundingClientRect();
  const er = scriptEl.getBoundingClientRect();
  const out = { left: Math.max(er.left + 4, r.left), bottom: r.bottom || er.top + 24 };
  box.remove();
  return out;
}
scriptEl.addEventListener("input", () => { onScriptChange(); showHints(); });
scriptEl.addEventListener("click", showHints);
scriptEl.addEventListener("blur", () => { setTimeout(() => { hints.style.display = "none"; }, 150); });
hints.addEventListener("mousedown", (e) => {
  const d = e.target.closest("div[data-v]");
  if (!d) return;
  e.preventDefault();
  const v = d.dataset.v, s = +d.dataset.s, en = +d.dataset.e;
  const val = scriptEl.value;
  scriptEl.value = val.slice(0, s) + v + val.slice(en);
  scriptEl.setSelectionRange(s + v.length, s + v.length);
  scriptEl.focus();
  hints.style.display = "none";
  onScriptChange();
});

// Стрелки/Tab/Enter выбирают подсказку, Esc закрывает.
scriptEl.addEventListener("keydown", (e) => {
  if (hints.style.display !== "block" || !hints.children.length) return;
  const items = [...hints.children];
  let idx = items.findIndex(d => d.classList.contains("sel"));
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    idx = e.key === "ArrowDown"
      ? Math.min(idx + 1, items.length - 1)
      : Math.max(idx - 1, 0);
    items.forEach(d => d.classList.remove("sel"));
    items[idx].classList.add("sel");
    items[idx].scrollIntoView({ block: "nearest" });
  } else if (e.key === "Tab" || (e.key === "Enter" && idx >= 0)) {
    e.preventDefault();
    items[idx >= 0 ? idx : 0].dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
  } else if (e.key === "Escape") {
    hints.style.display = "none";
  }
});

let timer = null, lastSql = "";
// В live-режиме выполняем только читающие запросы — запись только по кнопке.
function isReadOnly(sql) { return /^\s*(select|with|pragma|explain|describe|show)\b/i.test(sql); }
function onScriptChange() {
  if (timer) clearTimeout(timer);
  const sql = scriptEl.value.trim();
  if (!sql || sql === lastSql || !isReadOnly(sql)) return;
  timer = setTimeout(() => runSql(sql, true), 700);
}
async function runSql(sql, live) {
  if (!sql) return;
  try {
    const body = new URLSearchParams({ query: sql, db: DBSEL.value, include_data: live ? "" : "1" });
    const res = await fetch("/sql/run", { method: "POST", body });
    const doc = new DOMParser().parseFromString(await res.text(), "text/html");
    const target = document.getElementById("result-card");
    const table = doc.querySelector(".sql-result table");
    const err = doc.querySelector(".notice.bad");
    if (target) {
      target.style.display = "";
      if (table) {
        document.getElementById("result").innerHTML = table.outerHTML;
      } else {
        document.getElementById("result").innerHTML = err
          ? `<p class="notice bad">${esc(err.textContent)}</p>`
          : `<p class="role">${esc(doc.querySelector(".sql-result p") ? doc.querySelector(".sql-result p").textContent : "нет результатов")}</p>`;
      }
    }
    lastSql = sql;
  } catch (e) { if (!live) document.getElementById("result").textContent = "Ошибка сети: " + e.message; }
}
document.getElementById("run-btn").onclick = () => runSql(scriptEl.value.trim(), false);
document.getElementById("clear-btn").onclick = () => { scriptEl.value = ""; lastSql = ""; document.getElementById("result-card").style.display = "none"; scriptEl.focus(); };
document.getElementById("copy-btn").onclick = function () {
  navigator.clipboard.writeText(scriptEl.value).then(() => {
    this.textContent = "скопировано"; setTimeout(() => this.textContent = "копировать", 1200);
  });
};

const aiOut = document.getElementById("ai-out");

// Убираем markdown-обёртку ```sql … ``` из ответа AI.
function stripFences(s) {
  const m = String(s).match(/```[a-zA-Z]*\s*([\s\S]*?)```/);
  return (m ? m[1] : s).trim();
}

// Общий вызов AI: action, prompt, таблица ноды, куда писать ответ.
async function callAi(action, prompt, table, out) {
  if (!prompt) {
    out.style.display = "block"; out.style.color = "var(--bad)";
    out.textContent = "Промпт не может быть пустым — напишите вопрос для AI.";
    return false;
  }
  out.style.display = "block"; out.style.color = ""; out.textContent = "AI думает…";
  try {
    const res = await fetch("/builder/ai", { method: "POST",
      body: new URLSearchParams({ action, prompt, sql: scriptEl.value.trim(),
                                 db: DBSEL.value, table: table || "" }) });
    const data = await res.json();
    const ans = stripFences(data.answer || data.error || "пустой ответ");
    out.textContent = ans;
    if (action === "fix" && /^\s*(SELECT|WITH|INSERT|UPDATE|DELETE|CREATE|DROP)\b/i.test(ans)) {
      scriptEl.value = ans; onScriptChange();
    }
    return true;
  } catch (e) {
    out.textContent = "Ошибка связи: " + e.message;
    return false;
  }
}

// Кнопка AI в ноде: панель с полем prompt и тремя действиями.
let aiPanel = null;
function closeNodeAi() {
  if (aiPanel) { aiPanel.remove(); aiPanel = null; }
}
function openNodeAi(btn, table) {
  closeNodeAi();
  aiPanel = document.createElement("div");
  aiPanel.className = "node-ai";
  aiPanel.innerHTML =
    `<div class="t">🤖 ${esc(table)}</div>` +
    `<input id="node-ai-prompt" placeholder="промпт — не может быть пустым">` +
    `<div class="row">` +
    `<button data-a="explain">объяснить ошибку</button>` +
    `<button data-a="fix">исправить ошибку</button>` +
    `<button data-a="gen">сгенерировать код</button>` +
    `</div><div class="out"></div>`;
  document.body.appendChild(aiPanel);
  const r = btn.getBoundingClientRect();
  aiPanel.style.left = Math.max(8, Math.min(r.left - 240, window.innerWidth - 320)) + "px";
  aiPanel.style.top = (r.bottom + 8) + "px";
  const out = aiPanel.querySelector(".out");
  const input = aiPanel.querySelector("input");
  aiPanel.querySelectorAll("button").forEach(b => {
    b.onclick = async () => {
      const prompt = input.value.trim();
      if (!prompt) {
        out.style.display = "block"; out.style.color = "var(--bad)";
        out.textContent = "Промпт не может быть пустым — напишите вопрос для AI.";
        input.focus();
        return;
      }
      await callAi(b.dataset.a, prompt, table, out);
    };
  });
  input.focus();
  setTimeout(() => {
    const close = (ev) => {
      if (aiPanel && !aiPanel.contains(ev.target) && ev.target !== btn) {
        closeNodeAi();
        document.removeEventListener("click", close, true);
      }
    };
    document.addEventListener("click", close, true);
  }, 0);
}

// Кнопки в боковой панели AI.
document.querySelectorAll("[data-ai]").forEach(b => {
  b.onclick = async () => {
    const prompt = document.getElementById("ai-prompt").value.trim();
    if (!prompt) {
      aiOut.style.display = "block"; aiOut.style.color = "var(--bad)";
      aiOut.textContent = "Промпт не может быть пустым — напишите вопрос для AI.";
      document.getElementById("ai-prompt").focus();
      return;
    }
    await callAi(b.dataset.ai, prompt, "", aiOut);
  };
});

function switchDb(p) { location.href = "/builder?db=" + encodeURIComponent(p); }
renderNodes();
</script>
"""
