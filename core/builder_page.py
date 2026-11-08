"""Страница SQL-конструктора: ноды в стиле ComfyUI, живой скрипт, AI."""

from __future__ import annotations

import json
from html import escape

from core.page import _nav, _style, db_label


def builder_page(
    *,
    schema: dict | None = None,
    selected_db: str = "",
    dbs: list[dict] | None = None,
    is_admin: bool = False,
    initial_sql: str = "",
    error: str = "",
) -> str:
    """Конструктор запросов: холст ComfyUI, ноды SQL, скрипт живёт рядом."""
    from core.i18n import get_lang, strings_for, t

    schema = schema or {}
    tables = schema.get("tables") or []
    dbs = dbs or []

    schema_json = json.dumps(tables, ensure_ascii=False)
    dbs_json = json.dumps(
        [{"path": d["path"], "label": db_label(d)} for d in dbs],
        ensure_ascii=False,
    )
    # Переводы для JavaScript: меню, подсказки, описания команд.
    lang_json = json.dumps(strings_for(("bld.", "common.")), ensure_ascii=False)

    db_options = "".join(
        f'<option value="{escape(d["path"], quote=True)}"'
        f'{" selected" if d["path"] == selected_db else ""}>'
        f'{escape(db_label(d))}</option>'
        for d in dbs
    )
    db_select = (
        f'<select id="db-sel" onchange="switchDb(this.value)">{db_options}</select>'
        if dbs
        else f'<span class="role">{escape(t("bld.no_bases"))}</span>'
    )

    notice_html = f'<p class="notice bad">{escape(error)}</p>' if error else ""

    return f"""<!doctype html>
<html lang="{get_lang()}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(t("bld.doc_title"))}</title>
<style>
{_style()}
  body {{ max-width: none; padding-top: .5rem; }}
  .builder-top {{ display: flex; gap: .6rem; align-items: center; flex-wrap: wrap;
    padding: .5rem .8rem; background: var(--card); border: 1px solid var(--line);
    border-radius: .8rem; margin-bottom: .7rem; }}
  .builder-top select {{ padding: .3rem .5rem; border-radius: .5rem;
    border: 1px solid var(--line); background: var(--bg); color: var(--text); font: inherit; }}
  .top-spacer {{ flex: 1 1 auto; }}
  .builder-wrap {{ display: grid; grid-template-columns: minmax(0,1fr) minmax(22rem,.75fr);
    gap: .7rem; align-items: start; }}

  /* ---------- холст ComfyUI ---------- */
  #canvas {{ position: relative; min-height: 36rem; height: 64vh; overflow: hidden;
    background-color: #141414;
    background-image: radial-gradient(circle, #2e2e2e 1.2px, transparent 1.2px);
    background-size: 24px 24px; background-position: 0 0;
    border: 1px solid var(--line); border-radius: .8rem; cursor: default;
    touch-action: none; }}
  #world {{ position: absolute; left: 0; top: 0; width: 6000px; height: 4000px;
    transform-origin: 0 0; }}
  #edges {{ position: absolute; left: 0; top: 0; pointer-events: none; overflow: visible; }}
  .link {{ fill: none; stroke-width: 2.5; stroke-linecap: round; opacity: .92; }}
  .link.sel {{ stroke-width: 4.5; filter: drop-shadow(0 0 5px #4ade80); }}
  .link-hit {{ fill: none; stroke: transparent; stroke-width: 16;
    pointer-events: stroke; cursor: pointer; }}
  .link.temp {{ stroke-dasharray: 7 6; opacity: .85; }}
  .fk-link {{ fill: none; stroke: #6b7280; stroke-width: 1.4;
    stroke-dasharray: 5 6; opacity: .65; }}
  .fk-label {{ fill: #9ca3af; font-size: 11px; font-family: ui-monospace, monospace; }}
  #marquee {{ position: absolute; display: none; border: 1px dashed #3b82f6;
    background: rgba(59,130,246,.12); z-index: 4; pointer-events: none; }}
  #zoombar {{ position: absolute; right: 10px; bottom: 10px; display: flex; gap: 2px;
    align-items: center; background: #2b2b2b; border: 1px solid #4e4e4e;
    border-radius: 8px; padding: 3px; z-index: 6; }}
  #zoombar button {{ background: transparent; border: 0; color: #e5e7eb; font-size: 15px;
    width: 28px; height: 24px; border-radius: 5px; cursor: pointer; line-height: 1; }}
  #zoombar button:hover {{ background: #4b5563; }}
  #zoom-label {{ min-width: 46px; text-align: center; font-size: 11px;
    color: #9ca3af; font-family: ui-monospace, monospace; }}
  #canvas-hint {{ position: absolute; left: 10px; bottom: 10px; z-index: 6;
    font-size: 11px; color: #9ca3af; background: rgba(20,20,20,.8);
    padding: 4px 9px; border-radius: 6px; border: 1px solid #333; }}

  /* ---------- нода ComfyUI ---------- */
  .node {{ position: absolute; width: 272px; background: #353535; color: #e5e7eb;
    border: 1px solid #4e4e4e; border-radius: 8px; font-size: 12px;
    box-shadow: 0 4px 16px rgba(0,0,0,.5); user-select: none; }}
  .node.sel {{ border-color: #3b82f6; box-shadow: 0 0 0 1px #3b82f6, 0 4px 16px rgba(0,0,0,.5); }}
  .node-title {{ display: flex; align-items: center; gap: 6px; padding: 5px 8px;
    background: #2b2b2b; border-bottom: 1px solid #4e4e4e;
    border-radius: 7px 7px 0 0; cursor: grab; font-weight: 600; color: #f3f4f6; }}
  .node.custom .node-title {{ background: var(--ncolor, #2b2b2b); color: #fff; }}
  .node-title .collapse {{ cursor: pointer; opacity: .75; width: 12px; flex: none; }}
  .node-title .nlabel {{ flex: 1; overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap; }}
  .node-title .btns {{ display: flex; gap: .3rem; align-items: center; flex: none; }}
  .node-title .cmd, .node-title .ai {{ background: #111827; border: 1px solid #4b5563;
    color: #d1d5db; border-radius: 4px; padding: 0 4px; font-size: 11px; cursor: pointer; }}
  .node-title .cmd:hover {{ border-color: #4ade80; color: #4ade80; }}
  .node-title .ai:hover {{ border-color: #60a5fa; color: #60a5fa; }}
  .node-title .ntype {{ font-size: 10px; color: #9ca3af; font-weight: 500; }}
  .node-body {{ padding: 6px 0 8px; }}
  .node.collapsed .node-body {{ display: none; }}
  .socket-row {{ display: flex; align-items: center; gap: 7px; height: 21px; padding: 0 10px; }}
  .socket-row.out {{ justify-content: flex-end; }}
  .socket-label {{ font-size: 11px; color: #9ca3af; }}
  .socket {{ width: 10px; height: 10px; border-radius: 50%; background: #4ade80;
    border: 1px solid #0b0b0b; box-shadow: 0 0 0 2px rgba(0,0,0,.4);
    cursor: crosshair; flex: none; }}
  .socket.in {{ margin-left: -15px; }}
  .socket.out {{ margin-right: -15px; }}
  .socket.hot {{ transform: scale(1.4); box-shadow: 0 0 9px #4ade80; }}
  .widget {{ display: flex; align-items: center; gap: 6px; padding: 3px 10px; }}
  .widget .wlabel {{ font-size: 11px; color: #9ca3af; width: 68px; flex: none; }}
  .widget input, .widget select {{ flex: 1; min-width: 0; background: #2a2a2a;
    color: #e5e7eb; border: 1px solid #4e4e4e; border-radius: 6px; font-size: 12px;
    padding: 3px 6px; font-family: inherit; }}
  .widget input:focus, .widget select:focus {{ outline: none; border-color: #3b82f6; }}
  .node ul.cols {{ margin: 7px 0 0; padding: 0 6px; list-style: none;
    font-family: ui-monospace, monospace; }}
  .node ul.cols li {{ display: flex; justify-content: space-between; gap: 6px;
    padding: 2px 6px; border-radius: 4px; cursor: pointer; color: #d1d5db; font-size: 11.5px; }}
  .node ul.cols li:hover {{ background: #4b5563; color: #fff; }}
  .node .tag {{ color: #4ade80; font-weight: 700; font-size: 10px; }}
  .node .tag.pk {{ color: #fbbf24; }}
  .node .data {{ padding: 5px 10px 0; color: #6b7280; font-family: ui-monospace, monospace;
    font-size: 10.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .node .kind {{ font-size: 10.5px; color: #9ca3af; padding: 2px 10px 4px; }}

  /* ---------- меню ---------- */
  .gmenu {{ position: fixed; z-index: 90; min-width: 16rem; max-width: 22rem;
    max-height: 62vh; overflow: auto; background: #1f1f1f; border: 1px solid #4e4e4e;
    border-radius: 8px; padding: 6px; box-shadow: 0 16px 44px rgba(0,0,0,.65);
    font-size: 12.5px; color: #e5e7eb; }}
  .gmenu input[type=text] {{ width: 100%; background: #2a2a2a; border: 1px solid #4e4e4e;
    border-radius: 6px; color: #e5e7eb; padding: 5px 8px; font-size: 12.5px;
    margin-bottom: 5px; font-family: inherit; }}
  .gmenu input[type=text]:focus {{ outline: none; border-color: #3b82f6; }}
  .gmenu .cat {{ font-size: 10.5px; text-transform: uppercase; letter-spacing: .06em;
    color: #6b7280; padding: 7px 6px 3px; }}
  .gmenu .item {{ padding: 5px 8px; border-radius: 5px; cursor: pointer;
    display: flex; justify-content: space-between; gap: 10px; align-items: baseline; }}
  .gmenu .item:hover {{ background: #3b82f6; color: #fff; }}
  .gmenu .item .hint {{ color: #9ca3af; font-size: 10.5px; text-align: right; }}
  .gmenu .item:hover .hint {{ color: #dbeafe; }}
  .gmenu .sep {{ height: 1px; background: #333; margin: 5px 0; }}
  .gmenu .swatches {{ display: flex; gap: 5px; padding: 5px 6px; flex-wrap: wrap; }}
  .gmenu .swatch {{ width: 18px; height: 18px; border-radius: 4px; cursor: pointer;
    border: 1px solid rgba(255,255,255,.35); }}
  .cmd-menu .cmd-item b {{ display: block; font-size: 12px; font-family: ui-monospace, monospace;
    font-weight: 700; color: #f3f4f6; }}
  .cmd-menu .cmd-item span {{ display: block; color: #9ca3af; font-size: 10.5px;
    white-space: normal; max-width: 17rem; line-height: 1.3; margin-top: 1px; }}
  .node-ai {{ position: fixed; z-index: 95; width: 19rem; background: #1f1f1f;
    border: 1px solid #4e4e4e; border-radius: 8px; padding: .55rem; display: grid;
    gap: .45rem; font-size: .8rem; color: #e5e7eb;
    box-shadow: 0 16px 44px rgba(0,0,0,.65); }}
  .node-ai .t {{ color: #60a5fa; font-family: ui-monospace, monospace; font-weight: 700; }}
  .node-ai input {{ width: 100%; padding: .35rem .5rem; border-radius: 6px;
    border: 1px solid #4e4e4e; background: #2a2a2a; color: #e5e7eb; font: inherit; }}
  .node-ai .row {{ display: flex; gap: .3rem; flex-wrap: wrap; }}
  .node-ai .row button {{ font-size: .72rem; padding: .28rem .5rem; border-radius: 5px;
    border: 1px solid #4ade80; background: #111827; color: #4ade80; cursor: pointer;
    font-family: inherit; }}
  .node-ai .row button:hover {{ background: #123528; }}
  .node-ai .out {{ white-space: pre-wrap; max-height: 11rem; overflow: auto;
    color: #d1d5db; font-family: ui-monospace, monospace; font-size: .72rem; display: none; }}

  /* ---------- карточки справа ---------- */
  .side {{ display: grid; gap: .7rem; }}
  .side textarea {{ width: 100%; font: .88rem/1.5 ui-monospace, monospace;
    min-height: 12rem; resize: vertical; background: var(--bg); color: var(--text);
    border: 1px solid var(--line); border-radius: .6rem; padding: .6rem; }}
  .ai-bar {{ display: flex; gap: .4rem; flex-wrap: wrap; align-items: center; }}
  .ai-bar input {{ flex: 1 1 12rem; padding: .4rem .6rem; border-radius: .5rem;
    border: 1px solid var(--line); background: var(--bg); color: var(--text); font: inherit; }}
  .ai-out {{ background: var(--card); border: 1px solid var(--line); border-radius: .6rem;
    padding: .6rem; font-size: .83rem; white-space: pre-wrap; max-height: 16rem; overflow: auto; }}
  .cmd-name {{ font-family: ui-monospace, monospace; font-weight: 700; color: var(--accent);
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
    font-family: ui-monospace, monospace; margin-top: .25rem; }}
  .ba-t th, .ba-t td {{ border: 1px solid var(--line); padding: .16rem .3rem;
    text-align: left; max-width: 7rem; overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap; }}
  .ba-t th {{ color: var(--muted); background: var(--bg); }}
  #hints {{ position: fixed; background: var(--card); border: 1px solid var(--accent);
    border-radius: .5rem; padding: .3rem; display: none; z-index: 70; min-width: 13rem; }}
  #hints div {{ padding: .2rem .5rem; cursor: pointer; border-radius: .3rem; font-size: .8rem; }}
  #hints div:hover, #hints div.sel {{ background: var(--accent-soft); }}
  @media (max-width: 52rem) {{
    .builder-wrap {{ grid-template-columns: 1fr; }}
    #canvas {{ height: 52vh; }}
    .ba {{ grid-template-columns: 1fr; }}
  }}
</style></head><body>
{_nav(can_edit=True)}
<div class="builder-top">
  <b>{escape(t("bld.title"))}</b>
  <label>{escape(t("bld.db"))} {db_select}</label>
  <a href="/sql" class="ghost pill">← SQL Playground</a>
  <span class="role">{escape(t("bld.hints"))}</span>
  <span class="top-spacer"></span>
  <button id="export-btn" class="ghost pill" type="button">{escape(t("bld.export"))}</button>
  <button id="import-btn" class="ghost pill" type="button">{escape(t("bld.import"))}</button>
  <input type="file" id="import-file" accept=".json,application/json" hidden>
</div>
{notice_html}
<div class="builder-wrap">
  <div id="canvas">
    <div id="world"><svg id="edges" width="6000" height="4000"></svg></div>
    <div id="marquee"></div>
    <div id="zoombar">
      <button id="zoom-out" type="button" title="{escape(t("bld.zoom_out"))}">−</button>
      <span id="zoom-label">100%</span>
      <button id="zoom-in" type="button" title="{escape(t("bld.zoom_in"))}">+</button>
      <button id="zoom-fit" type="button" title="{escape(t("bld.zoom_fit"))}">⛶</button>
    </div>
    <div id="canvas-hint">{escape(t("bld.canvas_hint"))}</div>
  </div>
  <div class="side">
    <section class="card">
      <h2>{escape(t("bld.script"))}</h2>
      <textarea id="script" spellcheck="false">{escape(initial_sql)}</textarea>
      <div class="ai-bar" style="margin-top:.5rem">
        <button class="primary" id="run-btn">{escape(t("bld.run"))}</button>
        <button class="ghost pill" id="clear-btn">{escape(t("bld.clear"))}</button>
        <button class="ghost pill" id="copy-btn">{escape(t("common.copy"))}</button>
      </div>
      <div class="role" style="margin-top:.4rem">
        {escape(t("bld.script_hint"))}
      </div>
    </section>
    <section class="card" id="cmd-card" style="display:none">
      <h2>{escape(t("bld.cmd_card"))} <span class="role" id="cmd-hint">{escape(t("bld.cmd_hint"))}</span></h2>
      <div id="cmd-detail"></div>
    </section>
    <section class="card">
      <h2>{escape(t("bld.ai"))}</h2>
      <div class="ai-bar">
        <input id="ai-prompt" placeholder="{escape(t("bld.ai_prompt"), quote=True)}">
      </div>
      <div class="ai-bar" style="margin-top:.4rem">
        <button data-ai="explain">{escape(t("bld.ai_explain"))}</button>
        <button data-ai="fix">{escape(t("bld.ai_fix"))}</button>
        <button data-ai="gen">{escape(t("bld.ai_gen"))}</button>
      </div>
      <div id="ai-out" class="ai-out" style="margin-top:.5rem;display:none"></div>
    </section>
    <section class="card" id="result-card" style="display:none">
      <h2>{escape(t("bld.result"))}</h2>
      <div id="result"></div>
    </section>
  </div>
</div>
<div id="hints"></div>
<script>
const TABLES = {schema_json};
const DBS = {dbs_json};
const L = {lang_json};
</script>
{BUILD_JS}
</body></html>"""


# ------------------------------------------------- фрагмент JS вынесен ниже

BUILD_JS = r"""
<script>
"use strict";
const canvas = document.getElementById("canvas");
const world = document.getElementById("world");
const edgesSvg = document.getElementById("edges");
const scriptEl = document.getElementById("script");
const hints = document.getElementById("hints");
const marquee = document.getElementById("marquee");
const zoomLabel = document.getElementById("zoom-label");
const DBSEL = document.getElementById("db-sel") || { value: "" };

const COLW = 272;
const TYPE_COLOR = "#4ade80";

function esc(s) { return String(s ?? "").replace(/[&<>"]/g, m => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[m])); }

// Переводы: L заполняется на сервере (core/i18n), msg подставляет {поля}.
function msg(key, args) {
  let s = Object.prototype.hasOwnProperty.call(L, key) ? L[key] : key;
  if (args) Object.keys(args).forEach(k => {
    s = s.split("{" + k + "}").join(String(args[k]));
  });
  return s;
}

// ============================ типы нод ============================
const NODE_TYPES = {
  SQLTable: { label: msg("bld.type.table"), cat: msg("bld.cat.sources"), inputs: [], outputs: ["rows"],
    widgets: [{ key: "table", label: msg("bld.w.table"), kind: "select" }],
    hint: msg("bld.hint.table") },
  SQLSelect: { label: "SELECT", cat: msg("bld.cat.selection"), inputs: ["table"], outputs: ["rows"],
    widgets: [{ key: "cols", label: msg("bld.w.cols"), kind: "text", def: "*" }],
    hint: msg("bld.hint.select") },
  SQLFilter: { label: "WHERE", cat: msg("bld.cat.selection"), inputs: ["table"], outputs: ["rows"],
    widgets: [{ key: "cond", label: msg("bld.w.cond"), kind: "text", def: "1 = 1" }],
    hint: msg("bld.hint.filter") },
  SQLGroup: { label: "GROUP BY", cat: msg("bld.cat.selection"), inputs: ["table"], outputs: ["rows"],
    widgets: [{ key: "by", label: msg("bld.w.by"), kind: "text", def: "" },
              { key: "agg", label: msg("bld.w.agg"), kind: "text", def: "COUNT(*)" }],
    hint: msg("bld.hint.group") },
  SQLSort: { label: "ORDER BY", cat: msg("bld.cat.selection"), inputs: ["table"], outputs: ["rows"],
    widgets: [{ key: "col", label: msg("bld.w.col"), kind: "text", def: "" },
              { key: "dir", label: msg("bld.w.dir"), kind: "select", options: ["ASC", "DESC"] }],
    hint: msg("bld.hint.sort") },
  SQLLimit: { label: "LIMIT", cat: msg("bld.cat.selection"), inputs: ["table"], outputs: ["rows"],
    widgets: [{ key: "n", label: msg("bld.w.n"), kind: "number", def: 10 }],
    hint: msg("bld.hint.limit") },
  SQLJoin: { label: "JOIN", cat: msg("bld.cat.joins"), inputs: ["main", "join"], outputs: ["rows"],
    widgets: [{ key: "how", label: msg("bld.w.how"), kind: "select",
                options: ["INNER", "LEFT", "RIGHT", "FULL"] },
              { key: "on", label: msg("bld.w.on"), kind: "text", def: "" }],
    hint: msg("bld.hint.join") },
  SQLOutput: { label: msg("bld.type.output"), cat: msg("bld.cat.output"), inputs: ["table"], outputs: [],
    widgets: [], hint: msg("bld.hint.output") },
};

// ============================ состояние ============================
const graph = { nodes: [], links: [], lastNodeId: 0, lastLinkId: 0 };
const view = { x: 40, y: 30, k: 1 };
const sel = { nodes: new Set(), link: 0 };
let lastDragMoved = false;
let spaceDown = false;
// любой новый клик начинается «не двигали»; перетаскивание выставляет true
window.addEventListener("pointerdown", () => { lastDragMoved = false; }, true);

function nodeById(id) { return graph.nodes.find(n => n.id === id) || null; }
function tableNode(name) {
  return graph.nodes.find(n => n.type === "SQLTable" && n.widgets.table === name) || null;
}

// ============================ вид: зум и панорама ============================
function applyView() {
  world.style.transform = "translate(" + view.x + "px," + view.y + "px) scale(" + view.k + ")";
  zoomLabel.textContent = Math.round(view.k * 100) + "%";
  const g = 24 * view.k;
  canvas.style.backgroundSize = g + "px " + g + "px";
  canvas.style.backgroundPosition = view.x + "px " + view.y + "px";
}
function zoomAt(factor, cx, cy) {
  const k2 = Math.min(2.5, Math.max(0.2, view.k * factor));
  const wx = (cx - view.x) / view.k, wy = (cy - view.y) / view.k;
  view.k = k2;
  view.x = cx - wx * view.k;
  view.y = cy - wy * view.k;
  applyView();
}
function fitView() {
  if (!graph.nodes.length) { view.x = 40; view.y = 30; view.k = 1; applyView(); return; }
  const r = canvas.getBoundingClientRect();
  let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
  graph.nodes.forEach(n => {
    x0 = Math.min(x0, n.x); y0 = Math.min(y0, n.y);
    x1 = Math.max(x1, n.x + n.w); y1 = Math.max(y1, n.y + (n.h || 160));
  });
  const pad = 44;
  const k = Math.min(1, Math.max(0.2, Math.min(
    (r.width - pad * 2) / Math.max(1, x1 - x0),
    (r.height - pad * 2) / Math.max(1, y1 - y0))));
  view.k = k;
  view.x = (r.width - (x1 - x0) * k) / 2 - x0 * k;
  view.y = (r.height - (y1 - y0) * k) / 2 - y0 * k;
  applyView();
}
function worldPoint(e) {
  const r = canvas.getBoundingClientRect();
  return { x: (e.clientX - r.left - view.x) / view.k, y: (e.clientY - r.top - view.y) / view.k };
}
canvas.addEventListener("wheel", e => {
  e.preventDefault();
  const r = canvas.getBoundingClientRect();
  const cx = e.clientX - r.left, cy = e.clientY - r.top;
  if (e.ctrlKey || e.metaKey) zoomAt(Math.exp(-e.deltaY * 0.0016), cx, cy);
  else { view.x -= e.deltaX; view.y -= e.deltaY; applyView(); }
}, { passive: false });
document.getElementById("zoom-in").onclick = () =>
  zoomAt(1.2, canvas.clientWidth / 2, canvas.clientHeight / 2);
document.getElementById("zoom-out").onclick = () =>
  zoomAt(1 / 1.2, canvas.clientWidth / 2, canvas.clientHeight / 2);
document.getElementById("zoom-fit").onclick = fitView;

// щипок двумя пальцами
let pinch = null;
canvas.addEventListener("touchstart", e => {
  if (e.touches.length === 2) {
    const a = e.touches[0], b = e.touches[1];
    pinch = { d: Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY),
              cx: (a.clientX + b.clientX) / 2, cy: (a.clientY + b.clientY) / 2 };
  }
}, { passive: true });
canvas.addEventListener("touchmove", e => {
  if (e.touches.length === 2 && pinch) {
    e.preventDefault();
    const a = e.touches[0], b = e.touches[1];
    const d = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
    const r = canvas.getBoundingClientRect();
    zoomAt(d / Math.max(1, pinch.d), pinch.cx - r.left, pinch.cy - r.top);
    pinch.d = d;
  }
}, { passive: false });
canvas.addEventListener("touchend", () => { pinch = null; });

// ============================ построение нод ============================
function defaultWidget(w) {
  if (w.kind === "select") {
    if (w.key === "table") return TABLES.length ? TABLES[0].name : "";
    return w.options && w.options.length ? w.options[0] : "";
  }
  if (w.kind === "number") return w.def != null ? w.def : 0;
  return w.def != null ? w.def : "";
}

function addNode(type, x, y, opts) {
  opts = opts || {};
  const def = NODE_TYPES[type];
  const id = opts.id || ++graph.lastNodeId;
  if (opts.id) graph.lastNodeId = Math.max(graph.lastNodeId, opts.id);
  const n = { id: id, type, title: opts.title || def.label,
              x: Math.round(x), y: Math.round(y), w: COLW, h: 150,
              collapsed: !!opts.collapsed, color: opts.color || "",
              widgets: {}, el: null };
  def.widgets.forEach((w, i) => {
    if (opts.values && opts.values[i] !== undefined) n.widgets[w.key] = opts.values[i];
    else if (opts.widgets && opts.widgets[w.key] !== undefined) n.widgets[w.key] = opts.widgets[w.key];
    else n.widgets[w.key] = defaultWidget(w);
  });
  graph.nodes.push(n);
  renderNode(n);
  return n;
}

function socketRow(side, name, slot) {
  const row = document.createElement("div");
  row.className = "socket-row " + side;
  const sock = document.createElement("span");
  sock.className = "socket " + side;
  sock.dataset.side = side;
  sock.dataset.slot = slot;
  sock.title = (side === "in" ? msg("bld.in") : msg("bld.out")) + name;
  const label = document.createElement("span");
  label.className = "socket-label";
  label.textContent = name;
  if (side === "in") row.append(sock, label); else row.append(label, sock);
  return row;
}

function widgetRow(n, w) {
  const row = document.createElement("div");
  row.className = "widget " + w.kind;
  const lab = document.createElement("span");
  lab.className = "wlabel";
  lab.textContent = w.label;
  let field;
  if (w.kind === "select") {
    field = document.createElement("select");
    const options = w.key === "table" ? TABLES.map(t => t.name) : (w.options || []);
    options.forEach(o => {
      const op = document.createElement("option");
      op.value = op.textContent = o;
      field.appendChild(op);
    });
    field.value = n.widgets[w.key];
  } else {
    field = document.createElement("input");
    field.type = w.kind === "number" ? "number" : "text";
    field.value = n.widgets[w.key];
    if (w.def) field.placeholder = w.def;
  }
  field.dataset.key = w.key;
  const onChange = () => {
    n.widgets[w.key] = w.kind === "number" ? field.value : field.value;
    if (n.type === "SQLTable" && w.key === "table") {
      n.title = field.value || n.title;
      const lab2 = n.el && n.el.querySelector(".nlabel");
      if (lab2) lab2.textContent = n.title;
      renderNode(n);
      drawLinks();
    }
    scheduleRebuild();
  };
  field.addEventListener(w.kind === "select" ? "change" : "input", onChange);
  row.append(lab, field);
  return row;
}

function columnsList(n) {
  const t = TABLES.find(x => x.name === n.widgets.table) || { columns: [], fks: [], sample_rows: [] };
  const fkCols = new Set((t.fks || []).map(f => f.from));
  const wrap = document.createElement("div");
  const ul = document.createElement("ul");
  ul.className = "cols";
  (t.columns || []).forEach(col => {
    const li = document.createElement("li");
    li.dataset.col = col.name;
    const tags = [];
    if (col.pk) tags.push("PK");
    if (fkCols.has(col.name)) tags.push("FK");
    li.innerHTML = "<span>" + esc(col.name) + "</span>" +
      (tags.length ? '<span class="tag ' + (col.pk ? "pk" : "") + '">' + tags.join("/") + "</span>" : "");
    ul.appendChild(li);
  });
  wrap.appendChild(ul);
  if (t.sample_rows && t.sample_rows.length) {
    const d = document.createElement("div");
    d.className = "data";
    d.textContent = Object.values(t.sample_rows[0]).map(v => String(v).slice(0, 10)).join(", ");
    d.title = msg("bld.sample_row");
    wrap.appendChild(d);
  }
  return wrap;
}

function renderNode(n) {
  const def = NODE_TYPES[n.type];
  if (n.el) n.el.remove();
  const el = document.createElement("div");
  el.className = "node" + (n.color ? " custom" : "") +
    (sel.nodes.has(n.id) ? " sel" : "") + (n.collapsed ? " collapsed" : "");
  el.dataset.id = n.id;
  el.style.left = n.x + "px";
  el.style.top = n.y + "px";
  el.style.width = n.w + "px";
  if (n.color) el.style.setProperty("--ncolor", n.color);

  const title = document.createElement("div");
  title.className = "node-title";
  title.innerHTML = '<span class="collapse" title="' + esc(msg("bld.collapse_title")) + '">' +
    (n.collapsed ? "▸" : "▾") + "</span>" +
    '<span class="nlabel">' + esc(n.title) + "</span>" +
    '<span class="btns">' +
    (n.type === "SQLTable"
      ? '<span class="ai" title="' + esc(msg("bld.node_ai_title")) + '">🤖</span>' +
        '<span class="cmd" title="' + esc(msg("bld.cmd_btn_title")) + '">⌘</span>'
      : '<span class="ntype" title="' + esc(def.hint) + '">' + esc(def.label) + "</span>") +
    "</span>";
  el.appendChild(title);

  const body = document.createElement("div");
  body.className = "node-body";
  def.inputs.forEach((name, i) => body.appendChild(socketRow("in", name, i)));
  def.widgets.forEach(w => body.appendChild(widgetRow(n, w)));
  if (n.type === "SQLTable") body.appendChild(columnsList(n));
  if (n.type === "SQLOutput") {
    const p = document.createElement("div");
    p.className = "kind";
    p.textContent = msg("bld.result_note");
    body.appendChild(p);
  }
  def.outputs.forEach((name, i) => body.appendChild(socketRow("out", name, i)));
  el.appendChild(body);
  world.appendChild(el);
  n.el = el;
  n.h = el.offsetHeight;
}

function renderAll() {
  graph.nodes.forEach(renderNode);
  drawLinks();
}

// ============================ связи ============================
function linkTo(nodeId, slot) {
  return graph.links.find(l => l.to.node === nodeId && l.to.slot === slot) || null;
}
function linksFrom(nodeId, slot) {
  return graph.links.filter(l => l.from.node === nodeId && l.from.slot === slot);
}
function connect(from, to) {
  if (!from || !to || from.node === to.node) return false;
  const old = linkTo(to.node, to.slot);
  if (old) removeLink(old.id, true);
  graph.links.push({ id: ++graph.lastLinkId,
                     from: { node: from.node, slot: from.slot },
                     to: { node: to.node, slot: to.slot } });
  drawLinks();
  scheduleRebuild();
  return true;
}
function removeLink(id, silent) {
  graph.links = graph.links.filter(l => l.id !== id);
  if (sel.link === id) sel.link = 0;
  drawLinks();
  if (!silent) scheduleRebuild();
}
function socketPos(nodeId, side, slot) {
  const n = nodeById(nodeId);
  if (!n || !n.el) return null;
  const s = n.el.querySelector(".socket." + side + '[data-slot="' + slot + '"]');
  if (s && s.offsetParent) {
    return { x: n.x + s.offsetLeft + s.offsetWidth / 2,
             y: n.y + s.offsetTop + s.offsetHeight / 2 };
  }
  // свёрнутая нода: сокеты скрыты — рисуем у края заголовка
  return { x: n.x + (side === "out" ? n.w : 0), y: n.y + 14 };
}
function bez(a, b) {
  const dx = Math.max(46, Math.abs(b.x - a.x) * 0.5);
  return "M" + a.x + "," + a.y + " C" + (a.x + dx) + "," + a.y + " " +
         (b.x - dx) + "," + b.y + " " + b.x + "," + b.y;
}
function bezMid(a, b) {
  const dx = Math.max(46, Math.abs(b.x - a.x) * 0.5);
  const p0 = a, p1 = { x: a.x + dx, y: a.y }, p2 = { x: b.x - dx, y: b.y }, p3 = b;
  const t = 0.5, u = 1 - t;
  return {
    x: u * u * u * p0.x + 3 * u * u * t * p1.x + 3 * u * t * t * p2.x + t * t * t * p3.x,
    y: u * u * u * p0.y + 3 * u * u * t * p1.y + 3 * u * t * t * p2.y + t * t * t * p3.y,
  };
}
let linkDrag = null;

function drawLinks() {
  const parts = [];
  // связи схемы (FK) — пунктиром, для справки
  TABLES.forEach(t => (t.fks || []).forEach(f => {
    const a = tableNode(t.name), b = tableNode(f.table);
    if (!a || !b) return;
    const p1 = { x: a.x + a.w, y: a.y + 26 };
    const p2 = { x: b.x, y: b.y + 26 };
    const d = bez(p1, p2);
    parts.push('<path class="fk-link" d="' + d + '"/>');
    const m = bezMid(p1, p2);
    parts.push('<text class="fk-label" x="' + m.x + '" y="' + (m.y - 5) +
               '" text-anchor="middle">1:N</text>');
  }));
  // рабочие линии между сокетами
  graph.links.forEach(l => {
    if (linkDrag && linkDrag.detached === l.id) return;
    const a = socketPos(l.from.node, "out", l.from.slot);
    const b = socketPos(l.to.node, "in", l.to.slot);
    if (!a || !b) return;
    const d = bez(a, b);
    parts.push('<path class="link' + (sel.link === l.id ? " sel" : "") +
               '" d="' + d + '" stroke="' + TYPE_COLOR + '"/>');
    parts.push('<path class="link-hit" data-link="' + l.id + '" d="' + d + '"/>');
  });
  if (linkDrag && linkDrag.cur) {
    let a = linkDrag.origin ? socketPos(linkDrag.origin.node, "out", linkDrag.origin.slot) : null;
    let b = linkDrag.target ? socketPos(linkDrag.target.node, "in", linkDrag.target.slot) : null;
    if (linkDrag.reverse) { if (!a) a = linkDrag.cur; }
    else if (!b) b = linkDrag.cur;
    if (a && b) parts.push('<path class="link temp" d="' + bez(a, b) +
                           '" stroke="' + TYPE_COLOR + '"/>');
  }
  edgesSvg.innerHTML = parts.join("");
  refreshPlaceholders();
}

// подсказка-условие для JOIN: выводится из FK между таблицами
function nearestTable(node) {
  let cur = node, guard = 0;
  while (cur && guard++ < 30) {
    if (cur.type === "SQLTable") return cur.widgets.table;
    const l = linkTo(cur.id, 0);
    cur = l ? nodeById(l.from.node) : null;
  }
  return "";
}
function autoOn(a, b) {
  if (!a || !b || a[0] === "(" || b[0] === "(") return "1 = 1";
  const ta = TABLES.find(t => t.name === a);
  if (ta) {
    const f = (ta.fks || []).find(x => x.table === b);
    if (f) return a + "." + f.from + " = " + b + "." + f.to;
  }
  const tb = TABLES.find(t => t.name === b);
  if (tb) {
    const f = (tb.fks || []).find(x => x.table === a);
    if (f) return b + "." + f.from + " = " + a + "." + f.to;
  }
  return a + ".id = " + b + ".id";
}
function joinOn(n) {
  const l0 = linkTo(n.id, 0), l1 = linkTo(n.id, 1);
  const main = l0 ? nodeById(l0.from.node) : null;
  const second = l1 ? nodeById(l1.from.node) : null;
  const typed = String(n.widgets.on || "").trim();
  return typed || autoOn(main ? nearestTable(main) : "", second ? nearestTable(second) : "");
}
function refreshPlaceholders() {
  graph.nodes.forEach(n => {
    if (n.type !== "SQLJoin" || !n.el) return;
    const f = n.el.querySelector('input[data-key="on"]');
    if (f) f.placeholder = msg("bld.auto") + joinOn(n);
  });
}

// ============================ сборка SQL из графа ============================
function freshAcc() { return { from: "", cols: "", where: [], group: "", order: "", limit: "" }; }

function sourceSql(node, seen) {
  if (!node || seen.has(node.id)) return "";
  seen.add(node.id);
  if (node.type === "SQLTable") return node.widgets.table;
  if (node.type === "SQLJoin") {
    const l0 = linkTo(node.id, 0), l1 = linkTo(node.id, 1);
    const a = l0 ? sourceSql(nodeById(l0.from.node), seen) : "";
    const b = l1 ? sourceSql(nodeById(l1.from.node), new Set(seen)) : "";
    if (!a) return b || "";
    if (!b) return a;
    return a + " " + node.widgets.how + " JOIN " + b + " ON " + joinOn(node);
  }
  const sub = selectSql(node);
  return sub ? "(" + sub.split("\n").join("\n  ") + ") AS t" + node.id : "";
}

function analyze(node, acc, seen) {
  if (!node || seen.has(node.id)) return acc;
  seen.add(node.id);
  const up = slot => { const l = linkTo(node.id, slot); return l ? nodeById(l.from.node) : null; };
  switch (node.type) {
    case "SQLTable":
      if (!acc.from) acc.from = node.widgets.table;
      break;
    case "SQLJoin": {
      analyze(up(0), acc, seen);
      const second = up(1);
      const b = second ? sourceSql(second, new Set()) : "";
      if (acc.from && b) {
        acc.from = acc.from + " " + node.widgets.how + " JOIN " + b + " ON " + joinOn(node);
      }
      break;
    }
    case "SQLFilter":
      analyze(up(0), acc, seen);
      if (String(node.widgets.cond || "").trim()) acc.where.push("(" + node.widgets.cond + ")");
      break;
    case "SQLSelect":
      analyze(up(0), acc, seen);
      acc.cols = String(node.widgets.cols || "").trim() || "*";
      break;
    case "SQLGroup":
      analyze(up(0), acc, seen);
      acc.group = String(node.widgets.by || "").trim();
      acc.cols = (acc.group || "*") + ", " + (String(node.widgets.agg || "").trim() || "COUNT(*)");
      break;
    case "SQLSort":
      analyze(up(0), acc, seen);
      acc.order = (String(node.widgets.col || "").trim() || "1") + " " + node.widgets.dir;
      break;
    case "SQLLimit":
      analyze(up(0), acc, seen);
      acc.limit = node.widgets.n;
      break;
    case "SQLOutput":
      analyze(up(0), acc, seen);
      break;
    default: break;
  }
  return acc;
}

function compose(acc) {
  let sql = "SELECT " + (acc.cols || "*") + "\nFROM " + (acc.from || "");
  if (acc.where.length) sql += "\nWHERE " + acc.where.join(" AND ");
  if (acc.group) sql += "\nGROUP BY " + acc.group;
  if (acc.order) sql += "\nORDER BY " + acc.order;
  if (acc.limit !== "" && acc.limit !== null && acc.limit !== undefined) sql += "\nLIMIT " + acc.limit;
  return sql + ";";
}
function selectSql(node) {
  return compose(analyze(node, freshAcc(), new Set()));
}
function terminalNode() {
  const out = graph.nodes.find(n => n.type === "SQLOutput");
  if (out) {
    const l = linkTo(out.id, 0);
    return l ? nodeById(l.from.node) : null;
  }
  const cands = graph.nodes.filter(n =>
    NODE_TYPES[n.type].inputs.length && NODE_TYPES[n.type].outputs.length &&
    linksFrom(n.id, 0).length === 0);
  return cands.length ? cands[cands.length - 1] : null;
}
let rebuildTimer = null;
function scheduleRebuild() {
  if (rebuildTimer) clearTimeout(rebuildTimer);
  rebuildTimer = setTimeout(rebuildScript, 200);
}
function rebuildScript() {
  const term = terminalNode();
  if (!term) return;
  const acc = analyze(term, freshAcc(), new Set());
  if (!acc.from) return;
  const sql = compose(acc);
  if (sql === scriptEl.value.trim()) return;
  scriptEl.value = sql;
  onScriptChange();
}

// ============================ выделение ============================
function refreshSel() {
  graph.nodes.forEach(n => { if (n.el) n.el.classList.toggle("sel", sel.nodes.has(n.id)); });
  drawLinks();
}
function selectOnly(id) { sel.nodes.clear(); sel.nodes.add(id); sel.link = 0; refreshSel(); }
function toggleSel(id) {
  if (sel.nodes.has(id)) sel.nodes.delete(id); else sel.nodes.add(id);
  sel.link = 0;
  refreshSel();
}
function clearSel() { sel.nodes.clear(); sel.link = 0; refreshSel(); }
function deleteNodes(ids) {
  ids.forEach(id => {
    graph.links = graph.links.filter(l => l.from.node !== id && l.to.node !== id);
    const n = nodeById(id);
    if (n && n.el) n.el.remove();
    graph.nodes = graph.nodes.filter(x => x.id !== id);
    sel.nodes.delete(id);
  });
  drawLinks();
  refreshSel();
  scheduleRebuild();
}

// ============================ взаимодействие на холсте ============================
// панорама: средняя кнопка или пробел+тяна; тач-панорама
function startPan(e) {
  e.preventDefault();
  const sx = e.clientX, sy = e.clientY, vx = view.x, vy = view.y;
  const mv = ev => { view.x = vx + (ev.clientX - sx); view.y = vy + (ev.clientY - sy); applyView(); };
  const up = () => {
    window.removeEventListener("pointermove", mv);
    window.removeEventListener("pointerup", up);
  };
  window.addEventListener("pointermove", mv);
  window.addEventListener("pointerup", up);
}

// рамка выделения левой кнопкой по фону
function startMarquee(e) {
  if (e.pointerType === "touch") { startPan(e); return; }
  const r = canvas.getBoundingClientRect();
  const x0 = e.clientX - r.left, y0 = e.clientY - r.top;
  let moved = false;
  const mv = ev => {
    const x1 = ev.clientX - r.left, y1 = ev.clientY - r.top;
    if (!moved && Math.hypot(x1 - x0, y1 - y0) < 5) return;
    moved = true;
    marquee.style.display = "block";
    marquee.style.left = Math.min(x0, x1) + "px";
    marquee.style.top = Math.min(y0, y1) + "px";
    marquee.style.width = Math.abs(x1 - x0) + "px";
    marquee.style.height = Math.abs(y1 - y0) + "px";
  };
  const up = ev => {
    window.removeEventListener("pointermove", mv);
    window.removeEventListener("pointerup", up);
    marquee.style.display = "none";
    if (!moved) { clearSel(); return; }
    const bx0 = Math.min(x0, ev.clientX - r.left), bx1 = Math.max(x0, ev.clientX - r.left);
    const by0 = Math.min(y0, ev.clientY - r.top), by1 = Math.max(y0, ev.clientY - r.top);
    if (!ev.shiftKey) sel.nodes.clear();
    graph.nodes.forEach(n => {
      const nx0 = view.x + n.x * view.k, ny0 = view.y + n.y * view.k;
      const nx1 = nx0 + n.w * view.k, ny1 = ny0 + (n.h || 150) * view.k;
      if (nx1 > bx0 && nx0 < bx1 && ny1 > by0 && ny0 < by1) sel.nodes.add(n.id);
    });
    sel.link = 0;
    refreshSel();
  };
  window.addEventListener("pointermove", mv);
  window.addEventListener("pointerup", up);
}

canvas.addEventListener("pointerdown", e => {
  if (e.target.closest(".node") || e.target.closest("#zoombar") || e.target.closest(".gmenu")) return;
  if (e.target.classList && e.target.classList.contains("link-hit")) return;
  if (e.button === 2) return;
  lastDragMoved = false;
  if (e.button === 1 || (e.button === 0 && spaceDown)) { startPan(e); return; }
  if (e.button === 0) startMarquee(e);
});

// перетаскивание нод
world.addEventListener("pointerdown", e => {
  const sock = e.target.closest(".socket");
  if (sock) { e.stopPropagation(); startLinkDrag(sock, e); return; }
  if (e.target.closest("input, select, button")) return;
  const nodeEl = e.target.closest(".node");
  if (!nodeEl) return;
  if (e.button !== 0) return;
  const id = +nodeEl.dataset.id;
  if (e.shiftKey) toggleSel(id);
  else if (!sel.nodes.has(id)) selectOnly(id);
  if (e.target.closest(".collapse") || e.target.closest(".cmd") || e.target.closest(".ai")) return;
  lastDragMoved = false;
  const start = worldPoint(e);
  const moving = graph.nodes.filter(n => sel.nodes.has(n.id)).map(n => ({ n, x: n.x, y: n.y }));
  const mv = ev => {
    const p = worldPoint(ev);
    const dx = p.x - start.x, dy = p.y - start.y;
    if (!lastDragMoved && Math.hypot(dx, dy) * view.k < 3) return;
    lastDragMoved = true;
    moving.forEach(m => {
      m.n.x = Math.round(m.x + dx);
      m.n.y = Math.round(m.y + dy);
      if (m.n.el) { m.n.el.style.left = m.n.x + "px"; m.n.el.style.top = m.n.y + "px"; }
    });
    drawLinks();
  };
  const up = () => {
    window.removeEventListener("pointermove", mv);
    window.removeEventListener("pointerup", up);
  };
  window.addEventListener("pointermove", mv);
  window.addEventListener("pointerup", up);
});

// создание линии: тянем от выхода ко входу (или наоборот)
function startLinkDrag(sock, e) {
  const side = sock.dataset.side, slot = +sock.dataset.slot;
  const nodeId = +sock.closest(".node").dataset.id;
  let origin = null, target = null, reverse = false, detached = 0;
  if (side === "out") origin = { node: nodeId, slot };
  else {
    const l = linkTo(nodeId, slot);
    if (l) { origin = l.from; detached = l.id; }   // линию отцепляем только визуально
    else { reverse = true; target = { node: nodeId, slot }; }
  }
  if (!origin && !reverse) return;
  linkDrag = { origin, target, reverse, detached, cur: worldPoint(e) };
  document.querySelectorAll(".socket").forEach(s => {
    const ok = reverse ? s.dataset.side === "out" : s.dataset.side === "in";
    s.classList.toggle("hot", ok);
  });
  drawLinks();
  const mv = ev => { linkDrag.cur = worldPoint(ev); drawLinks(); };
  const up = ev => {
    window.removeEventListener("pointermove", mv);
    window.removeEventListener("pointerup", up);
    document.querySelectorAll(".socket.hot").forEach(s => s.classList.remove("hot"));
    const under = document.elementFromPoint(ev.clientX, ev.clientY);
    const sock2 = under && under.closest ? under.closest(".socket") : null;
    if (sock2) {
      const s2 = sock2.dataset.side, sl2 = +sock2.dataset.slot;
      const id2 = +sock2.closest(".node").dataset.id;
      if (reverse && s2 === "out") connect({ node: id2, slot: sl2 }, target);
      else if (!reverse && s2 === "in") connect(origin, { node: id2, slot: sl2 });
    }
    linkDrag = null;
    drawLinks();
  };
  window.addEventListener("pointermove", mv);
  window.addEventListener("pointerup", up);
}

// клик по линии — выделить
edgesSvg.addEventListener("click", e => {
  const hit = e.target.closest(".link-hit");
  if (!hit) return;
  sel.nodes.clear();
  sel.link = +hit.dataset.link;
  refreshSel();
});

// ============================ меню ============================
function closeMenus() {
  document.querySelectorAll(".gmenu:not(.cmd-menu)").forEach(m => m.remove());
}
function placeMenu(m, cx, cy) {
  m.style.left = Math.max(6, Math.min(cx, window.innerWidth - 300)) + "px";
  m.style.top = Math.max(6, Math.min(cy, window.innerHeight - 120)) + "px";
  document.body.appendChild(m);
  setTimeout(() => {
    const close = ev => {
      if (!m.contains(ev.target)) {
        m.remove();
        document.removeEventListener("pointerdown", close, true);
      }
    };
    document.addEventListener("pointerdown", close, true);
  }, 0);
}

function openAddMenu(cx, cy) {
  closeMenus();
  const m = document.createElement("div");
  m.className = "gmenu add-menu";
  const input = document.createElement("input");
  input.type = "text";
  input.placeholder = msg("bld.search");
  m.appendChild(input);
  const list = document.createElement("div");
  m.appendChild(list);
  const draw = q => {
    list.innerHTML = "";
    const cats = {};
    Object.entries(NODE_TYPES).forEach(([type, def]) => {
      const hay = (def.label + " " + type + " " + def.hint + " " + def.cat).toLowerCase();
      if (q && !hay.includes(q)) return;
      (cats[def.cat] = cats[def.cat] || []).push([type, def]);
    });
    Object.entries(cats).forEach(([cat, items]) => {
      const h = document.createElement("div");
      h.className = "cat";
      h.textContent = cat;
      list.appendChild(h);
      items.forEach(([type, def]) => {
        const it = document.createElement("div");
        it.className = "item";
        it.innerHTML = "<span>" + esc(def.label) + '</span><span class="hint">' +
                       esc(def.hint) + "</span>";
        it.onclick = () => {
          const p = worldPoint({ clientX: cx, clientY: cy });
          addNode(type, p.x - COLW / 2, p.y - 40);
          closeMenus();
          drawLinks();
          scheduleRebuild();
        };
        list.appendChild(it);
      });
    });
    if (!list.children.length) list.innerHTML = '<div class="cat">' + esc(msg("bld.not_found")) + "</div>";
  };
  draw("");
  input.addEventListener("input", () => draw(input.value.trim().toLowerCase()));
  placeMenu(m, cx, cy);
  input.focus();
}

const NODE_COLORS = ["", "#ef4444", "#f97316", "#eab308", "#22c55e",
                     "#3b82f6", "#a855f7", "#6b7280"];

function openNodeMenu(id, cx, cy) {
  closeMenus();
  const n = nodeById(id);
  if (!n) return;
  const m = document.createElement("div");
  m.className = "gmenu node-menu";
  const add = (label, hint, fn) => {
    const it = document.createElement("div");
    it.className = "item";
    it.innerHTML = "<span>" + esc(label) + "</span>" +
                   (hint ? '<span class="hint">' + esc(hint) + "</span>" : "");
    it.onclick = () => { closeMenus(); fn(); };
    m.appendChild(it);
  };
  add(msg("bld.clone"), "Ctrl+D", () => {
    const c = addNode(n.type, n.x + 28, n.y + 28,
      { title: n.title, widgets: Object.assign({}, n.widgets), color: n.color });
    selectOnly(c.id);
    drawLinks();
    scheduleRebuild();
  });
  add(n.collapsed ? msg("bld.expand") : msg("bld.collapse"), "", () => {
    n.collapsed = !n.collapsed;
    renderNode(n);
    drawLinks();
  });
  if (n.type === "SQLTable") {
    add(msg("bld.insert_select"), msg("bld.insert_hint"), () => {
      insertAtCursor("SELECT * FROM " + n.widgets.table + ";");
    });
    add(msg("bld.ai_node"), "", () => openNodeAi(n.el.querySelector(".ai"), n.widgets.table));
  }
  const sep = document.createElement("div");
  sep.className = "sep";
  m.appendChild(sep);
  const cat = document.createElement("div");
  cat.className = "cat";
  cat.textContent = msg("bld.color");
  m.appendChild(cat);
  const sw = document.createElement("div");
  sw.className = "swatches";
  NODE_COLORS.forEach(c => {
    const b = document.createElement("span");
    b.className = "swatch";
    b.style.background = c || "#353535";
    b.title = c ? c : msg("bld.by_default");
    b.onclick = () => { n.color = c; closeMenus(); renderNode(n); };
    sw.appendChild(b);
  });
  m.appendChild(sw);
  const sep2 = document.createElement("div");
  sep2.className = "sep";
  m.appendChild(sep2);
  add(msg("bld.delete"), "Delete", () => deleteNodes([id]));
  placeMenu(m, cx, cy);
}

canvas.addEventListener("contextmenu", e => {
  e.preventDefault();
  if (e.target.closest(".node")) return;
  openAddMenu(e.clientX, e.clientY);
});
world.addEventListener("contextmenu", e => {
  const nodeEl = e.target.closest(".node");
  if (!nodeEl) return;
  e.preventDefault();
  e.stopPropagation();
  openNodeMenu(+nodeEl.dataset.id, e.clientX, e.clientY);
});
canvas.addEventListener("dblclick", e => {
  if (e.target.closest(".node") || e.target.closest("#zoombar")) return;
  openAddMenu(e.clientX, e.clientY);
});

// клики внутри ноды: свернуть, команды, AI, столбец
world.addEventListener("click", e => {
  if (lastDragMoved) return;
  const col = e.target.closest("li[data-col]");
  if (col) {
    const n = nodeById(+col.closest(".node").dataset.id);
    if (n) insertAtCursor(n.widgets.table + "." + col.dataset.col);
    return;
  }
  const collapse = e.target.closest(".collapse");
  if (collapse) {
    const n = nodeById(+collapse.closest(".node").dataset.id);
    if (n) { n.collapsed = !n.collapsed; renderNode(n); drawLinks(); }
    return;
  }
  const cmd = e.target.closest(".cmd");
  if (cmd) { e.stopPropagation(); openCmdMenu(cmd); return; }
  const ai = e.target.closest(".ai");
  if (ai) { e.stopPropagation(); openNodeAi(ai, tableOf(ai)); }
});

function tableOf(btn) {
  const n = nodeById(+btn.closest(".node").dataset.id);
  return n ? n.widgets.table : "";
}

// клавиатура
window.addEventListener("keydown", e => {
  const tag = (document.activeElement && document.activeElement.tagName) || "";
  const typing = /INPUT|TEXTAREA|SELECT/.test(tag);
  if (e.code === "Space" && !typing) { spaceDown = true; e.preventDefault(); }
  if (typing) return;
  if (e.key === "Escape") { closeMenus(); clearSel(); return; }
  if (e.key === "Delete" || e.key === "Backspace") {
    if (sel.link) removeLink(sel.link);
    else if (sel.nodes.size) deleteNodes([...sel.nodes]);
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "d") {
    e.preventDefault();
    const created = [];
    [...sel.nodes].forEach(id => {
      const n = nodeById(id);
      if (!n) return;
      const c = addNode(n.type, n.x + 28, n.y + 28,
        { title: n.title, widgets: Object.assign({}, n.widgets), color: n.color });
      created.push(c.id);
    });
    if (created.length) { sel.nodes = new Set(created); refreshSel(); drawLinks(); scheduleRebuild(); }
  }
});
window.addEventListener("keyup", e => { if (e.code === "Space") spaceDown = false; });

// ============================ экспорт / импорт workflow ============================
function toWorkflow() {
  const nodes = graph.nodes.map((n, i) => {
    const def = NODE_TYPES[n.type];
    return {
      id: n.id,
      type: n.type,
      pos: [Math.round(n.x), Math.round(n.y)],
      size: [Math.round(n.w), Math.round(n.h || 150)],
      flags: {},
      order: i,
      mode: 0,
      inputs: def.inputs.map((name, s) => {
        const l = linkTo(n.id, s);
        return { name: name, type: "TABLE", link: l ? l.id : null };
      }),
      outputs: def.outputs.map((name, s) => ({
        name: name, type: "TABLE",
        links: linksFrom(n.id, s).map(l => l.id),
        slot_index: s,
      })),
      properties: { "Node name for S&R": n.type, cnr_id: "ai-bridge", ver: "1.0.0" },
      widgets_values: def.widgets.map(w => n.widgets[w.key]),
      title: n.title,
      color: n.color || undefined,
      collapsed: n.collapsed || undefined,
    };
  });
  const links = graph.links.map(l =>
    [l.id, l.from.node, l.from.slot, l.to.node, l.to.slot, "TABLE"]);
  return {
    last_node_id: graph.lastNodeId,
    last_link_id: graph.lastLinkId,
    nodes: nodes,
    links: links,
    groups: [],
    config: {},
    extra: { ds: { scale: view.k, offset: [Math.round(view.x), Math.round(view.y)], bound: false } },
    version: 0.4,
  };
}

function importWorkflow(data) {
  if (!data || !Array.isArray(data.nodes)) throw new Error(msg("bld.no_nodes_field"));
  graph.nodes.forEach(n => { if (n.el) n.el.remove(); });
  graph.nodes = [];
  graph.links = [];
  graph.lastNodeId = 0;
  graph.lastLinkId = 0;
  sel.nodes.clear();
  sel.link = 0;
  data.nodes.forEach(nd => {
    const type = NODE_TYPES[nd.type] ? nd.type : "SQLTable";
    const n = addNode(type, (nd.pos && nd.pos[0]) || 40, (nd.pos && nd.pos[1]) || 40, {
      id: nd.id,
      title: nd.title,
      values: Array.isArray(nd.widgets_values) ? nd.widgets_values : undefined,
      color: nd.color || "",
      collapsed: !!nd.collapsed,
    });
    if (Array.isArray(nd.size) && nd.size[0]) { n.w = nd.size[0]; n.el.style.width = n.w + "px"; }
  });
  (data.links || []).forEach(arr => {
    if (!Array.isArray(arr) || arr.length < 5) return;
    const [id, a, as, b, bs] = arr;
    if (!nodeById(a) || !nodeById(b)) return;
    graph.links.push({ id: id, from: { node: a, slot: as }, to: { node: b, slot: bs } });
    graph.lastLinkId = Math.max(graph.lastLinkId, id);
  });
  renderAll();
  const ds = data.extra && data.extra.ds;
  if (ds && Array.isArray(ds.offset)) {
    view.k = ds.scale || 1;
    view.x = ds.offset[0] || 0;
    view.y = ds.offset[1] || 0;
    applyView();
  } else fitView();
  scheduleRebuild();
}

function flash(msg, bad) {
  const old = document.getElementById("io-notice");
  if (old) old.remove();
  const p = document.createElement("p");
  p.id = "io-notice";
  p.className = "notice" + (bad ? " bad" : "");
  p.textContent = msg;
  const top = document.querySelector(".builder-top");
  top.parentNode.insertBefore(p, top.nextSibling);
  setTimeout(() => { if (p.parentNode) p.remove(); }, 6000);
}

document.getElementById("export-btn").onclick = () => {
  const text = JSON.stringify(toWorkflow(), null, 2);
  const blob = new Blob([text], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "sql-workflow.json";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 3000);
  flash(msg("bld.exported", { n: graph.nodes.length, l: graph.links.length }));
};
document.getElementById("import-btn").onclick = () =>
  document.getElementById("import-file").click();
document.getElementById("import-file").addEventListener("change", ev => {
  const f = ev.target.files && ev.target.files[0];
  if (!f) return;
  const rd = new FileReader();
  rd.onload = () => {
    try {
      importWorkflow(JSON.parse(rd.result));
      flash(msg("bld.imported", { n: graph.nodes.length }));
    } catch (err) {
      flash(msg("bld.import_fail", { e: err.message }), true);
    }
    ev.target.value = "";
  };
  rd.readAsText(f);
});

// ============================ скрипт: вставка, команды, подсказки ============================
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

function openCmdMenu(btn) {
  const n = nodeById(+btn.closest(".node").dataset.id);
  if (!n) return;
  const table = n.widgets.table;
  const old = document.querySelector(".cmd-menu");
  if (old) old.remove();
  const menu = document.createElement("div");
  menu.className = "gmenu cmd-menu";
  const r = btn.getBoundingClientRect();
  menu.style.left = Math.max(6, Math.min(r.left, window.innerWidth - 320)) + "px";
  menu.style.top = Math.max(6, Math.min(r.bottom + 4, window.innerHeight - 140)) + "px";
  COMMANDS.forEach(cmd => {
    const item = document.createElement("div");
    item.className = "item cmd-item";
    item.innerHTML = "<span><b>" + esc(cmd.name) + "</b><span>" + esc(cmd.desc) + "</span></span>";
    item.onclick = () => {
      insertAtCursor(cmd.tpl(table));
      menu.remove();
      showCmdDetail(cmd, table);
    };
    menu.appendChild(item);
  });
  document.body.appendChild(menu);
  setTimeout(() => {
    const close = ev => {
      if (!menu.contains(ev.target)) {
        menu.remove();
        document.removeEventListener("click", close, true);
      }
    };
    document.addEventListener("click", close, true);
  }, 0);
}

// ---- данные для примеров «до/после» ----
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
  if (!rows || !rows.length) return '<p class="role">' + esc(msg("bld.no_rows")) + "</p>";
  cols = cols || Object.keys(rows[0]);
  const cell = v => esc(v === null || v === undefined ? "NULL" : (v === "" ? "''" : String(v)));
  return '<div class="ba-scroll"><table class="ba-t"><thead><tr>' +
    cols.map(c => "<th>" + esc(c) + "</th>").join("") + "</tr></thead><tbody>" +
    rows.slice(0, cap).map(r => "<tr>" + cols.map(c => "<td>" + cell(r[c]) + "</td>").join("") + "</tr>").join("") +
    "</tbody></table></div>";
}
function groupCount(rows, col) {
  const g = {};
  rows.forEach(r => { const k = String(r[col]); g[k] = (g[k] || 0) + 1; });
  return Object.entries(g).map(([k, n]) => { const o = {}; o[col] = k; o["count"] = n; return o; });
}
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
function editCol(tb) {
  const cols = colsOf(tb);
  const pks = new Set((tb.columns || []).filter(c => c.pk).map(c => c.name));
  const fks = new Set((tb.fks || []).map(f => f.from));
  return cols.find(c => !pks.has(c) && !fks.has(c)) || cols.find(c => !pks.has(c)) || cols[0] || "col";
}
function numCol(tb, rows) {
  return colsOf(tb).find(col => rows.some(r => nval(r[col]) !== null)) || colsOf(tb)[0] || "id";
}

const COMMANDS = [
  { name: "SELECT * FROM t",
    tpl: t => "SELECT * FROM " + t,
    desc: msg("bld.d0"),
    ex: t => "SELECT * FROM " + t + "\nLIMIT 10;",
    after: rows => ({ rows: rows.slice(0, 6) }) },

  { name: msg("bld.select_cols"),
    tpl: t => "SELECT " + t + ".col FROM " + t,
    desc: msg("bld.d1"),
    ex: (t, tb) => "SELECT " + t + "." + (colsOf(tb)[0] || "id") + ", " + t + "." + (colsOf(tb)[1] || "name") +
                   "\nFROM " + t + "\nLIMIT 10;",
    after: (rows, tb) => { const c = colsOf(tb).slice(0, 3);
      return { rows: rows.map(r => pick(r, c)), cols: c }; } },

  { name: "DISTINCT",
    tpl: t => "SELECT DISTINCT " + t + ".col FROM " + t,
    desc: msg("bld.d2"),
    ex: (t, tb) => "SELECT DISTINCT " + t + "." + (colsOf(tb)[0] || "id") + "\nFROM " + t + ";",
    after: (rows, tb) => { const c = colsOf(tb).slice(0, 3);
      const seen = new Set(); const out = [];
      rows.forEach(r => { const k = JSON.stringify(pick(r, c)); if (!seen.has(k)) { seen.add(k); out.push(pick(r, c)); } });
      return { rows: out, cols: c, note: msg("bld.note.distinct", { a: rows.length, b: out.length }) }; } },

  { name: "JOIN … ON",
    tpl: t => "SELECT *\nFROM " + t + "\nJOIN other_table ON " + t + ".id = other_table.id",
    desc: msg("bld.d3"),
    ex: (t, tb) => { const fk = (tb.fks || [])[0];
      return fk
        ? "SELECT " + t + ".*, " + fk.table + ".*\nFROM " + t + "\nJOIN " + fk.table +
          "\n  ON " + t + "." + fk.from + " = " + fk.table + "." + fk.to + ";"
        : "SELECT *\nFROM " + t + "\nJOIN other_table ON " + t + ".id = other_table.id;"; },
    after: (rows, tb) => { const fk = (tb.fks || [])[0];
      if (!fk) return null;
      const parent = tableObj(fk.table);
      const pr = parent.sample_rows || [];
      if (!pr.length) return null;
      const childCols = colsOf(tb), parentCols = colsOf(parent);
      const cols = [...childCols, ...parentCols.filter(c => !childCols.includes(c))].slice(0, 6);
      const merged = rows.map(r => {
        const m = pr.find(p => String(p[fk.to]) === String(r[fk.from]));
        return m ? Object.assign({}, r, m) : null;
      }).filter(Boolean);
      return { rows: merged, cols: cols,
               note: merged.length ? fk.from + " → " + fk.table + "." + fk.to
                                   : msg("bld.note.join_none") }; } },

  { name: "WHERE col = ''",
    tpl: t => "WHERE " + t + ".col = ''",
    desc: msg("bld.d4"),
    ex: (t, tb) => { const c = colsOf(tb)[0] || "id";
      const v = (tb.sample_rows || [])[0] ? String((tb.sample_rows || [])[0][c]) : "";
      return "SELECT *\nFROM " + t + "\nWHERE " + t + "." + c + " = '" + v + "';"; },
    after: (rows, tb) => { const c = colsOf(tb)[0] || "id";
      if (!rows.length) return { rows: [] };
      const v = rows[0][c];
      return { rows: rows.filter(r => String(r[c]) === String(v)), cols: colsOf(tb).slice(0, 4),
               note: msg("bld.note.where_eq", { c: c, v: v }) }; } },

  { name: "WHERE a > b AND …",
    tpl: () => "WHERE col > 0 AND col2 = ''",
    desc: msg("bld.d5"),
    ex: (t, tb) => "SELECT *\nFROM " + t + "\nWHERE " + t + "." + (colsOf(tb)[0] || "id") +
                   " > ''\n  AND " + t + "." + (colsOf(tb)[1] || "id") + " IS NOT NULL;",
    after: (rows, tb) => { const c = colsOf(tb)[0] || "id";
      return { rows: rows.filter(r => r[c] !== null && r[c] !== undefined && String(r[c]) !== ""),
               cols: colsOf(tb).slice(0, 4), note: msg("bld.note.where_gt", { c: c }) }; } },

  { name: "ORDER BY col",
    tpl: t => "ORDER BY " + t + ".col",
    desc: msg("bld.d6"),
    ex: (t, tb) => "SELECT *\nFROM " + t + "\nORDER BY " + t + "." + (colsOf(tb)[0] || "id") + " ASC;",
    after: (rows, tb) => { const c = colsOf(tb)[0] || "id";
      const sorted = rows.slice().sort((a, b) => String(a[c]).localeCompare(String(b[c])));
      return { rows: sorted, cols: colsOf(tb).slice(0, 4), note: msg("bld.note.order", { c: c }) }; } },

  { name: "GROUP BY col",
    tpl: t => "GROUP BY " + t + ".col",
    desc: msg("bld.d7"),
    ex: (t, tb) => { const c = groupCol(tb, tb.sample_rows || []);
      return "SELECT " + t + "." + c + ", COUNT(*)\nFROM " + t + "\nGROUP BY " + t + "." + c + ";"; },
    after: (rows, tb) => { const c = groupCol(tb, rows);
      return { rows: groupCount(rows, c), cols: [c, "count"] }; } },

  { name: "HAVING COUNT(*) > n",
    tpl: () => "HAVING COUNT(*) > 1",
    desc: msg("bld.d8"),
    ex: (t, tb) => { const c = groupCol(tb, tb.sample_rows || []);
      return "SELECT " + t + "." + c + ", COUNT(*) AS n\nFROM " + t +
             "\nGROUP BY " + t + "." + c + "\nHAVING COUNT(*) > 1;"; },
    after: (rows, tb) => { const c = groupCol(tb, rows);
      const groups = groupCount(rows, c).filter(g => g["count"] > 1);
      return { rows: groups, cols: [c, "count"],
               note: groups.length ? "" : msg("bld.note.having") }; } },

  { name: "LIMIT n",
    tpl: () => "LIMIT 10",
    desc: msg("bld.d9"),
    ex: t => "SELECT *\nFROM " + t + "\nLIMIT 3;",
    after: rows => ({ rows: rows.slice(0, 3), note: msg("bld.note.limit", { n: rows.length, k: 3 }) }) },

  { name: "COUNT(*)",
    tpl: t => "COUNT(" + t + ".id)",
    desc: msg("bld.d10"),
    ex: t => "SELECT COUNT(*) AS total\nFROM " + t + ";",
    after: rows => ({ rows: [{ count: rows.length }], cols: ["count"] }) },

  { name: "COUNT(DISTINCT col)",
    tpl: t => "COUNT(DISTINCT " + t + ".col)",
    desc: msg("bld.d11"),
    ex: (t, tb) => "SELECT COUNT(DISTINCT " + t + "." + (colsOf(tb)[0] || "id") + ") AS uniq\nFROM " + t + ";",
    after: (rows, tb) => { const c = colsOf(tb)[0] || "id";
      return { rows: [{ distinct: new Set(rows.map(r => String(r[c]))).size }], cols: ["distinct"] }; } },

  { name: "MAX / MIN",
    tpl: t => "MAX(" + t + ".col), MIN(" + t + ".col)",
    desc: msg("bld.d12"),
    ex: (t, tb) => { const c = numCol(tb, tb.sample_rows || []);
      return "SELECT MAX(" + t + "." + c + "), MIN(" + t + "." + c + ")\nFROM " + t + ";"; },
    after: (rows, tb) => { const c = numCol(tb, rows);
      const vals = rows.map(r => r[c]).filter(v => v !== null && v !== undefined && v !== "");
      if (!vals.length) return null;
      const nums = vals.map(nval).filter(v => v !== null);
      const hi = nums.length ? Math.max.apply(null, nums) : vals.slice().sort().slice(-1)[0];
      const lo = nums.length ? Math.min.apply(null, nums) : vals.slice().sort()[0];
      return { rows: [{ max: hi, min: lo }], cols: ["max", "min"], note: msg("bld.note.maxmin", { c: c }) }; } },

  { name: "AVG / SUM",
    tpl: t => "AVG(" + t + ".col), SUM(" + t + ".col)",
    desc: msg("bld.d13"),
    ex: (t, tb) => { const c = numCol(tb, tb.sample_rows || []);
      return "SELECT AVG(" + t + "." + c + "), SUM(" + t + "." + c + ")\nFROM " + t + ";"; },
    after: (rows, tb) => { const c = colsOf(tb).find(col => rows.filter(r => nval(r[col]) !== null).length >= 2);
      if (!c) return null;
      const nums = rows.map(r => nval(r[c])).filter(v => v !== null);
      const sum = nums.reduce((a, b) => a + b, 0);
      return { rows: [{ sum: sum, avg: +(sum / nums.length).toFixed(2) }], cols: ["sum", "avg"],
               note: msg("bld.note.avgsum", { c: c }) }; } },

  { name: "INSERT INTO",
    tpl: t => "INSERT INTO " + t + " (col) VALUES ('')",
    desc: msg("bld.d14"),
    ex: (t, tb) => "INSERT INTO " + t + " (" + colsOf(tb).slice(0, 2).join(", ") + ")\nVALUES ('', '');",
    after: (rows, tb) => { const c = colsOf(tb);
      const fresh = {}; c.forEach((name, i) => { fresh[name] = i === 0 ? "'new_id'" : "''"; });
      return { rows: rows.slice(0, 4).concat([fresh]), cols: c.slice(0, 4),
               note: msg("bld.note.insert") }; } },

  { name: "UPDATE … SET",
    tpl: t => "UPDATE " + t + " SET col = ''",
    desc: msg("bld.d15"),
    ex: (t, tb) => "UPDATE " + t + "\nSET " + editCol(tb) + " = '" + msg("bld.new_value") + "'\nWHERE " +
                   (colsOf(tb)[0] || "id") + " = '...';",
    after: (rows, tb) => { const c = colsOf(tb); const col = editCol(tb);
      const out = rows.map((r, i) => { const o = Object.assign({}, r); if (i === 0) o[col] = msg("bld.new_value"); return o; });
      return { rows: out, cols: c.slice(0, 4), note: msg("bld.note.update", { c: col, v: nv }) }; } },

  { name: "DELETE FROM",
    tpl: t => "DELETE FROM " + t,
    desc: msg("bld.d16"),
    ex: (t, tb) => "DELETE FROM " + t + "\nWHERE " + (colsOf(tb)[0] || "id") + " = '...';",
    after: (rows, tb) => ({ rows: rows.slice(1), cols: colsOf(tb).slice(0, 4),
                           note: msg("bld.note.delete") }) },

  { name: "GROUP BY + COUNT",
    tpl: t => "SELECT " + t + ".col, COUNT(*)\nFROM " + t + "\nGROUP BY " + t + ".col",
    desc: msg("bld.d17"),
    ex: (t, tb) => { const c = groupCol(tb, tb.sample_rows || []);
      return "SELECT " + t + "." + c + ", COUNT(*) AS n\nFROM " + t +
             "\nGROUP BY " + t + "." + c + "\nORDER BY n DESC;"; },
    after: (rows, tb) => { const c = groupCol(tb, rows);
      const g = groupCount(rows, c).sort((a, b) => b["count"] - a["count"]);
      return { rows: g, cols: [c, "count"] }; } },
];

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
    '<p class="cmd-name">' + esc(cmd.name) + "</p>" +
    '<p class="role">' + esc(cmd.desc) + "</p>" +
    '<div class="ex"><b>' + esc(msg("bld.example_of", { t: name })) + "</b>" +
    "<pre>" + esc(cmd.ex(name, tb)) + "</pre></div>" +
    '<div class="ba">' +
      "<div><b>" + esc(msg("bld.before", { t: name })) + "</b>" + grid(before, colsOf(tb).slice(0, 4)) + "</div>" +
      "<div><b>" + esc(msg("bld.after")) + "</b>" +
      (res ? grid(res.rows, res.cols)
           : '<p class="role">' + esc(err || msg("bld.no_compare")) + "</p>") +
      "</div></div>" +
    (res && res.note ? '<p class="role">' + esc(res.note) + "</p>" : "");
  card.style.display = "";
  card.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

// ---- автодополнение ----
const KEYWORDS = ["SELECT","FROM","WHERE","JOIN","LEFT","RIGHT","INNER","OUTER","ON","AND","OR","NOT","NULL","IS",
  "GROUP","BY","ORDER","HAVING","LIMIT","OFFSET","INSERT","INTO","VALUES","UPDATE","SET","DELETE","CREATE","TABLE",
  "DROP","ALTER","ADD","PRIMARY","KEY","FOREIGN","REFERENCES","UNIQUE","CHECK","DEFAULT","DISTINCT","AS","CASE",
  "WHEN","THEN","ELSE","END","BETWEEN","LIKE","IN","ASC","DESC","COUNT","SUM","AVG","MAX","MIN","WITH","UNION",
  "ALL","EXISTS"];

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
    '<div data-v="' + esc(x) + '" data-s="' + start + '" data-e="' + caret + '">' + esc(x) + "</div>").join("");
  const cr = caretRect();
  hints.style.display = "block";
  hints.style.left = Math.min(cr.left, window.innerWidth - 220) + "px";
  hints.style.top = Math.min(cr.bottom + 4, window.innerHeight - 40) + "px";
}
scriptEl.addEventListener("input", () => { onScriptChange(); showHints(); });
scriptEl.addEventListener("click", showHints);
scriptEl.addEventListener("blur", () => { setTimeout(() => { hints.style.display = "none"; }, 150); });
hints.addEventListener("mousedown", e => {
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
scriptEl.addEventListener("keydown", e => {
  if (hints.style.display !== "block" || !hints.children.length) return;
  const items = [...hints.children];
  let idx = items.findIndex(d => d.classList.contains("sel"));
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    idx = e.key === "ArrowDown" ? Math.min(idx + 1, items.length - 1) : Math.max(idx - 1, 0);
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

// ---- живой запрос ----
let timer = null, lastSql = "";
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
          ? '<p class="notice bad">' + esc(err.textContent) + "</p>"
          : '<p class="role">' + esc(doc.querySelector(".sql-result p")
              ? doc.querySelector(".sql-result p").textContent : msg("bld.no_results")) + "</p>";
      }
    }
    lastSql = sql;
  } catch (e) {
    if (!live) document.getElementById("result").textContent = msg("bld.net_error", { e: e.message });
  }
}
document.getElementById("run-btn").onclick = () => runSql(scriptEl.value.trim(), false);
document.getElementById("clear-btn").onclick = () => {
  scriptEl.value = ""; lastSql = "";
  document.getElementById("result-card").style.display = "none";
  scriptEl.focus();
};
document.getElementById("copy-btn").onclick = function () {
  navigator.clipboard.writeText(scriptEl.value).then(() => {
    this.textContent = msg("common.copied");
    setTimeout(() => { this.textContent = msg("common.copy"); }, 1200);
  });
};

// ---- AI ----
const aiOut = document.getElementById("ai-out");
function stripFences(s) {
  const m = String(s).match(/```[a-zA-Z]*\s*([\s\S]*?)```/);
  return (m ? m[1] : s).trim();
}
async function callAi(action, prompt, table, out) {
  if (!prompt) {
    out.style.display = "block"; out.style.color = "var(--bad)";
    out.textContent = msg("bld.ai_empty");
    return false;
  }
  out.style.display = "block"; out.style.color = ""; out.textContent = msg("bld.ai_thinking");
  try {
    const res = await fetch("/builder/ai", { method: "POST",
      body: new URLSearchParams({ action: action, prompt: prompt, sql: scriptEl.value.trim(),
                                 db: DBSEL.value, table: table || "" }) });
    const data = await res.json();
    const ans = stripFences(data.answer || data.error || msg("bld.ai_no_answer"));
    out.textContent = ans;
    if (action === "fix" && /^\s*(SELECT|WITH|INSERT|UPDATE|DELETE|CREATE|DROP)\b/i.test(ans)) {
      scriptEl.value = ans;
      onScriptChange();
    }
    return true;
  } catch (e) {
    out.textContent = msg("bld.ai_conn", { e: e.message });
    return false;
  }
}
let aiPanel = null;
function closeNodeAi() { if (aiPanel) { aiPanel.remove(); aiPanel = null; } }
function openNodeAi(btn, table) {
  if (!btn) return;
  closeNodeAi();
  aiPanel = document.createElement("div");
  aiPanel.className = "node-ai";
  aiPanel.innerHTML =
    '<div class="t">🤖 ' + esc(table) + "</div>" +
    '<input id="node-ai-prompt" placeholder="' + esc(msg("bld.ai_prompt")) + '">' +
    '<div class="row">' +
    '<button data-a="explain">' + esc(msg("bld.ai_explain")) + "</button>" +
    '<button data-a="fix">' + esc(msg("bld.ai_fix")) + "</button>" +
    '<button data-a="gen">' + esc(msg("bld.ai_gen_node")) + "</button>" +
    '</div><div class="out"></div>';
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
        out.style.display = "block"; out.style.color = "#f87171";
        out.textContent = msg("bld.ai_empty");
        input.focus();
        return;
      }
      await callAi(b.dataset.a, prompt, table, out);
    };
  });
  input.focus();
  setTimeout(() => {
    const close = ev => {
      if (aiPanel && !aiPanel.contains(ev.target) && ev.target !== btn) {
        closeNodeAi();
        document.removeEventListener("click", close, true);
      }
    };
    document.addEventListener("click", close, true);
  }, 0);
}
document.querySelectorAll("[data-ai]").forEach(b => {
  b.onclick = async () => {
    const prompt = document.getElementById("ai-prompt").value.trim();
    if (!prompt) {
      aiOut.style.display = "block"; aiOut.style.color = "var(--bad)";
      aiOut.textContent = msg("bld.ai_empty");
      document.getElementById("ai-prompt").focus();
      return;
    }
    await callAi(b.dataset.ai, prompt, "", aiOut);
  };
});

function switchDb(p) { location.href = "/builder?db=" + encodeURIComponent(p); }

// ============================ старт ============================
function initGraph() {
  const colY = [30, 30, 30];
  TABLES.forEach((t, i) => {
    const c = i % 3;
    const n = addNode("SQLTable", 30 + c * (COLW + 60), colY[c],
      { title: t.name, widgets: { table: t.name } });
    colY[c] += (n.h || 160) + 44;
  });
  drawLinks();
  applyView();
}
initGraph();
</script>
"""
