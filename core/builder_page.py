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
    min-width: 13rem; max-height: 24rem; overflow: auto;
    box-shadow: 0 8px 24px rgba(0,0,0,.45); }}
  .cmd-menu div {{ padding: .25rem .5rem; cursor: pointer; border-radius: .3rem;
    font-size: .78rem; white-space: nowrap; }}
  .cmd-menu div:hover {{ background: var(--accent-soft); }}
  .cmd-menu .cmd-item b {{ display: block; font-size: .78rem; font-family: monospace;
    font-weight: 700; color: var(--text); }}
  .cmd-menu .cmd-item span {{ display: block; color: var(--muted); font-size: .67rem;
    white-space: normal; max-width: 17rem; line-height: 1.3; margin-top: .1rem; }}
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
  .cmd-name {{ font-family: monospace; font-weight: 700; color: var(--accent);
    margin: 0 0 .3rem; font-size: .92rem; }}
  .ex {{ background: var(--bg); border: 1px solid var(--line); border-radius: .5rem;
    padding: .45rem .55rem; margin: .5rem 0; }}
  .ex b {{ font-size: .72rem; color: var(--muted); text-transform: uppercase;
    letter-spacing: .05em; }}
  .ex pre {{ margin: .3rem 0 0; white-space: pre-wrap; word-break: break-word;
    font: .78rem/1.45 ui-monospace, monospace; }}
  .ba {{ display: grid; grid-template-columns: 1fr 1fr; gap: .55rem; margin-top: .5rem; }}
  .ba > div > b {{ font-size: .74rem; color: var(--muted); }}
  .ba-scroll {{ overflow-x: auto; }}
  .ba-t {{ width: 100%; border-collapse: collapse; font-size: .7rem;
    font-family: monospace; margin-top: .25rem; }}
  .ba-t th, .ba-t td {{ border: 1px solid var(--line); padding: .16rem .3rem;
    text-align: left; max-width: 7rem; overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap; }}
  .ba-t th {{ color: var(--muted); background: var(--bg); }}
  @media (max-width: 42rem) {{ .ba {{ grid-template-columns: 1fr; }} }}
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
    <section class="card" id="cmd-card" style="display:none">
      <h2>Команда <span class="role" id="cmd-hint">выбрана в меню ⌘ у ноды</span></h2>
      <div id="cmd-detail"></div>
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

// ---- команды: вставка в скрипт + разбор «что делает» с примером «до/после» ----
function tableObj(name) { return TABLES.find(x => x.name === name) || { name, columns: [], sample_rows: [] }; }
function colsOf(tb) { return (tb.columns || []).map(c => c.name); }
function pick(r, names) { const o = {}; names.forEach(n => { o[n] = r[n]; }); return o; }
function nval(v) {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function grid(rows, cols, cap) {
  cap = cap || 6;
  if (!rows || !rows.length) return '<p class="role">строк нет</p>';
  cols = cols || Object.keys(rows[0]);
  const cell = v => esc(v === null || v === undefined || v === "" ? (v === "" ? "''" : "NULL") : String(v));
  return '<div class="ba-scroll"><table class="ba-t"><thead><tr>' +
    cols.map(c => `<th>${esc(c)}</th>`).join("") + '</tr></thead><tbody>' +
    rows.slice(0, cap).map(r => '<tr>' + cols.map(c => `<td>${cell(r[c])}</td>`).join("") + '</tr>').join("") +
    '</tbody></table></div>';
}

function groupCount(rows, col) {
  const g = {};
  rows.forEach(r => { const k = String(r[col]); g[k] = (g[k] || 0) + 1; });
  return Object.entries(g).map(([k, n]) => { const o = {}; o[col] = k; o["count"] = n; return o; });
}

// Колонка для группировки: не PK, заполненная во всех строках и с повторами
// (иначе GROUP BY бессмыслен: все значения пустые или уникальные).
function groupCol(tb, rows) {
  const cols = colsOf(tb);
  const pks = new Set((tb.columns || []).filter(c => c.pk).map(c => c.name));
  const plain = cols.filter(c => !pks.has(c));
  const filled = plain.filter(c => rows.every(r => r[c] !== null && r[c] !== undefined && r[c] !== ""));
  const pool = filled.length ? filled : plain;
  const withRepeats = pool.find(c => {
    const uniq = new Set(rows.map(r => String(r[c]))).size;
    return uniq > 1 && uniq < rows.length;
  });
  return withRepeats || pool[0] || plain[0] || cols[0] || "col";
}

// Колонка, которую безопасно менять в UPDATE: не PK и не FK.
function editCol(tb) {
  const cols = colsOf(tb);
  const pks = new Set((tb.columns || []).filter(c => c.pk).map(c => c.name));
  const fks = new Set((tb.fks || []).map(f => f.from));
  return cols.find(c => !pks.has(c) && !fks.has(c)) || cols.find(c => !pks.has(c)) || cols[0] || "col";
}

// Колонка, в которой есть числа — для MAX/MIN, AVG/SUM.
function numCol(tb, rows) {
  return colsOf(tb).find(col => rows.some(r => nval(r[col]) !== null)) || colsOf(tb)[0] || "id";
}

const COMMANDS = [
  { name: "SELECT * FROM t",
    tpl: t => `SELECT * FROM ${t}`,
    desc: "Возвращает все столбцы и все строки таблицы.",
    ex: t => `SELECT * FROM ${t}\nLIMIT 10;`,
    after: rows => ({ rows: rows.slice(0, 6) }) },

  { name: "SELECT столбцы…",
    tpl: t => `SELECT ${t}.col FROM ${t}`,
    desc: "Оставляет только нужные столбцы (проекция) — остальное отбрасывается.",
    ex: (t, tb) => `SELECT ${t}.${colsOf(tb)[0] || "id"}, ${t}.${colsOf(tb)[1] || "name"}\nFROM ${t}\nLIMIT 10;`,
    after: (rows, tb) => { const c = colsOf(tb).slice(0, 3);
      return { rows: rows.map(r => pick(r, c)), cols: c }; } },

  { name: "DISTINCT",
    tpl: t => `SELECT DISTINCT ${t}.col FROM ${t}`,
    desc: "Убирает повторы: остаются только разные значения.",
    ex: (t, tb) => `SELECT DISTINCT ${t}.${colsOf(tb)[0] || "id"}\nFROM ${t};`,
    after: (rows, tb) => { const c = colsOf(tb).slice(0, 3);
      const seen = new Set(); const out = [];
      rows.forEach(r => { const k = JSON.stringify(pick(r, c)); if (!seen.has(k)) { seen.add(k); out.push(pick(r, c)); } });
      return { rows: out, cols: c, note: `было ${rows.length} строк → стало ${out.length}` }; } },

  { name: "JOIN … ON",
    tpl: t => `SELECT *\nFROM ${t}\nJOIN other_table ON ${t}.id = other_table.id`,
    desc: "Соединяет таблицу с другой по общей колонке: связь 1:N (один родитель — много детей).",
    ex: (t, tb) => { const fk = (tb.fks || [])[0];
      return fk ? `SELECT ${t}.*, ${fk.table}.*\nFROM ${t}\nJOIN ${fk.table}\n  ON ${t}.${fk.from} = ${fk.table}.${fk.to};`
                : `SELECT *\nFROM ${t}\nJOIN other_table ON ${t}.id = other_table.id;`; },
    after: (rows, tb) => { const fk = (tb.fks || [])[0];
      if (!fk) return null;
      const parent = tableObj(fk.table);
      const pr = parent.sample_rows || [];
      if (!pr.length) return null;
      const childCols = colsOf(tb), parentCols = colsOf(parent);
      const cols = [...childCols, ...parentCols.filter(c => !childCols.includes(c))].slice(0, 6);
      const merged = rows.map(r => {
        const m = pr.find(p => String(p[fk.to]) === String(r[fk.from]));
        return m ? { ...r, ...m } : null;
      }).filter(Boolean);
      return { rows: merged, cols,
               note: merged.length ? `${fk.from} → ${fk.table}.${fk.to}`
                                   : "в примере нет строк с совпадающим ключом" }; } },

  { name: "WHERE col = ''",
    tpl: t => `WHERE ${t}.col = ''`,
    desc: "Фильтрует строки: остаются только те, где колонка равна значению.",
    ex: (t, tb) => { const c = colsOf(tb)[0] || "id";
      const v = (tb.sample_rows || [])[0] ? String((tb.sample_rows || [])[0][c]) : "";
      return `SELECT *\nFROM ${t}\nWHERE ${t}.${c} = '${v}';`; },
    after: (rows, tb) => { const c = colsOf(tb)[0] || "id";
      if (!rows.length) return { rows: [] };
      const v = rows[0][c];
      return { rows: rows.filter(r => String(r[c]) === String(v)), cols: colsOf(tb).slice(0, 4),
               note: `условие: ${c} = '${v}'` }; } },

  { name: "WHERE a > b AND …",
    tpl: () => `WHERE col > 0 AND col2 = ''`,
    desc: "Несколько условий через AND/OR: строка проходит, если выполнены все.",
    ex: (t, tb) => { const c = colsOf(tb)[0] || "id";
      return `SELECT *\nFROM ${t}\nWHERE ${t}.${c} > ''\n  AND ${t}.${colsOf(tb)[1] || c} IS NOT NULL;`; },
    after: (rows, tb) => { const c = colsOf(tb)[0] || "id";
      return { rows: rows.filter(r => r[c] !== null && r[c] !== undefined && String(r[c]) !== ""),
               cols: colsOf(tb).slice(0, 4), note: `убраны пустые значения ${c}` }; } },

  { name: "ORDER BY col",
    tpl: t => `ORDER BY ${t}.col`,
    desc: "Сортирует строки по колонке (ASC по умолчанию, DESC — по убыванию).",
    ex: (t, tb) => `SELECT *\nFROM ${t}\nORDER BY ${t}.${colsOf(tb)[0] || "id"} ASC;`,
    after: (rows, tb) => { const c = colsOf(tb)[0] || "id";
      const sorted = [...rows].sort((a, b) => String(a[c]).localeCompare(String(b[c])));
      return { rows: sorted, cols: colsOf(tb).slice(0, 4), note: `сортировка по ${c} ↑` }; } },

  { name: "GROUP BY col",
    tpl: t => `GROUP BY ${t}.col`,
    desc: "Складывает строки в группы с одинаковым значением колонки.",
    ex: (t, tb) => { const c = groupCol(tb, tb.sample_rows || []);
      return `SELECT ${t}.${c}, COUNT(*)\nFROM ${t}\nGROUP BY ${t}.${c};`; },
    after: (rows, tb) => { const c = groupCol(tb, rows);
      return { rows: groupCount(rows, c), cols: [c, "count"] }; } },

  { name: "HAVING COUNT(*) > n",
    tpl: () => `HAVING COUNT(*) > 1`,
    desc: "Фильтр уже по группам (WHERE — по строкам, HAVING — по агрегатам).",
    ex: (t, tb) => { const c = groupCol(tb, tb.sample_rows || []);
      return `SELECT ${t}.${c}, COUNT(*) AS n\nFROM ${t}\nGROUP BY ${t}.${c}\nHAVING COUNT(*) > 1;`; },
    after: (rows, tb) => { const c = groupCol(tb, rows);
      const groups = groupCount(rows, c).filter(g => g["count"] > 1);
      return { rows: groups, cols: [c, "count"],
               note: groups.length ? "" : "ни одна группа не проходит COUNT(*) > 1 — в примере все значения разные" }; } },

  { name: "LIMIT n",
    tpl: () => `LIMIT 10`,
    desc: "Обрезает результат: возвращается только первых n строк.",
    ex: t => `SELECT *\nFROM ${t}\nLIMIT 3;`,
    after: rows => ({ rows: rows.slice(0, 3), note: `было ${rows.length} строк → оставлены первые 3` }) },

  { name: "COUNT(*)",
    tpl: t => `COUNT(${t}.id)`,
    desc: "Считает количество строк (всегда одна итоговая строка).",
    ex: t => `SELECT COUNT(*) AS total\nFROM ${t};`,
    after: rows => ({ rows: [{ count: rows.length }], cols: ["count"] }) },

  { name: "COUNT(DISTINCT col)",
    tpl: t => `COUNT(DISTINCT ${t}.col)`,
    desc: "Считает, сколько разных значений встречается в колонке.",
    ex: (t, tb) => `SELECT COUNT(DISTINCT ${t}.${colsOf(tb)[0] || "id"}) AS uniq\nFROM ${t};`,
    after: (rows, tb) => { const c = colsOf(tb)[0] || "id";
      return { rows: [{ distinct: new Set(rows.map(r => String(r[c]))).size }], cols: ["distinct"] }; } },

  { name: "MAX / MIN",
    tpl: t => `MAX(${t}.col), MIN(${t}.col)`,
    desc: "Максимальное и минимальное значение колонки.",
    ex: (t, tb) => { const c = numCol(tb, tb.sample_rows || []);
      return `SELECT MAX(${t}.${c}), MIN(${t}.${c})\nFROM ${t};`; },
    after: (rows, tb) => { const c = numCol(tb, rows);
      const vals = rows.map(r => r[c]).filter(v => v !== null && v !== undefined && v !== "");
      if (!vals.length) return null;
      const nums = vals.map(nval).filter(v => v !== null);
      const hi = nums.length ? Math.max(...nums) : vals.slice().sort().slice(-1)[0];
      const lo = nums.length ? Math.min(...nums) : vals.slice().sort()[0];
      return { rows: [{ max: hi, min: lo }], cols: ["max", "min"], note: `по колонке ${c}` }; } },

  { name: "AVG / SUM",
    tpl: t => `AVG(${t}.col), SUM(${t}.col)`,
    desc: "Среднее и сумма по числовой колонке.",
    ex: (t, tb) => { const c = numCol(tb, tb.sample_rows || []);
      return `SELECT AVG(${t}.${c}), SUM(${t}.${c})\nFROM ${t};`; },
    after: (rows, tb) => { const c = colsOf(tb).find(col => rows.filter(r => nval(r[col]) !== null).length >= 2);
      if (!c) return null;
      const nums = rows.map(r => nval(r[c])).filter(v => v !== null);
      const sum = nums.reduce((a, b) => a + b, 0);
      return { rows: [{ sum, avg: +(sum / nums.length).toFixed(2) }], cols: ["sum", "avg"],
               note: `по числовой колонке ${c}` }; } },

  { name: "INSERT INTO",
    tpl: t => `INSERT INTO ${t} (col) VALUES ('')`,
    desc: "Добавляет новую строку: перечислите колонки и их значения.",
    ex: (t, tb) => `INSERT INTO ${t} (${colsOf(tb).slice(0, 2).join(", ")})\nVALUES ('', '');`,
    after: (rows, tb) => { const c = colsOf(tb);
      const fresh = {}; c.forEach((name, i) => { fresh[name] = i === 0 ? "'new_id'" : "''"; });
      return { rows: [...rows.slice(0, 4), fresh], cols: c.slice(0, 4),
               note: "строка добавлена в конец (VALUES нужно заполнить своими значениями)" }; } },

  { name: "UPDATE … SET",
    tpl: t => `UPDATE ${t} SET col = ''`,
    desc: "Меняет значения в существующих строках (обычно с условием WHERE).",
    ex: (t, tb) => `UPDATE ${t}\nSET ${editCol(tb)} = 'новое'\nWHERE ${colsOf(tb)[0] || "id"} = '...';`,
    after: (rows, tb) => { const c = colsOf(tb); const col = editCol(tb);
      const out = rows.map((r, i) => { const o = { ...r }; if (i === 0) o[col] = "новое"; return o; });
      return { rows: out, cols: c.slice(0, 4), note: `первая строка: ${col} → 'новое'` }; } },

  { name: "DELETE FROM",
    tpl: t => `DELETE FROM ${t}`,
    desc: "Удаляет строки; без WHERE удаляет всю таблицу (осторожно!).",
    ex: (t, tb) => `DELETE FROM ${t}\nWHERE ${colsOf(tb)[0] || "id"} = '...';`,
    after: (rows, tb) => ({ rows: rows.slice(1), cols: colsOf(tb).slice(0, 4),
                           note: "первая строка удалена" }) },

  { name: "GROUP BY + COUNT",
    tpl: t => `SELECT ${t}.col, COUNT(*)\nFROM ${t}\nGROUP BY ${t}.col`,
    desc: "Классика аналитики: сколько строк в каждой группе.",
    ex: (t, tb) => { const c = groupCol(tb, tb.sample_rows || []);
      return `SELECT ${t}.${c}, COUNT(*) AS n\nFROM ${t}\nGROUP BY ${t}.${c}\nORDER BY n DESC;`; },
    after: (rows, tb) => { const c = groupCol(tb, rows);
      const g = groupCount(rows, c).sort((a, b) => b["count"] - a["count"]);
      return { rows: g, cols: [c, "count"] }; } },
];

// Карточка «Команда»: объяснение, полный пример и данные до/после.
function showCmdDetail(cmd, name) {
  const tb = tableObj(name);
  const card = document.getElementById("cmd-card");
  const detail = document.getElementById("cmd-detail");
  const before = (tb.sample_rows || []).slice(0, 8);
  let res = null, err = "";
  if (cmd.after && before.length) {
    try { res = cmd.after(before, tb); } catch (e) { err = String(e); }
  }
  detail.innerHTML =
    `<p class="cmd-name">${esc(cmd.name)}</p>` +
    `<p class="role">${esc(cmd.desc)}</p>` +
    `<div class="ex"><b>Пример использования · ${esc(name)}</b>` +
    `<pre>${esc(cmd.ex(name, tb))}</pre></div>` +
    `<div class="ba">` +
      `<div><b>до — таблица ${esc(name)}</b>${grid(before, colsOf(tb).slice(0, 4))}</div>` +
      `<div><b>после — результат команды</b>` +
      (res ? grid(res.rows, res.cols)
           : `<p class="role">${esc(err || "для этой команды сравнение не показывается")}</p>`) +
      `</div></div>` +
    (res && res.note ? `<p class="role">${esc(res.note)}</p>` : "");
  card.style.display = "";
  card.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

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
  COMMANDS.forEach(cmd => {
    const item = document.createElement("div");
    item.className = "cmd-item";
    item.innerHTML = `<b>${esc(cmd.name)}</b><span>${esc(cmd.desc)}</span>`;
    item.onclick = () => {
      insertAtCursor(cmd.tpl(table));
      menu.remove();
      showCmdDetail(cmd, table);
    };
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
