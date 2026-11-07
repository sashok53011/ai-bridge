"""Страница приставки: чистые функции «данные → HTML».

Здесь нет ни сети, ни файлов, ни часов — только разметка из данных.
Поэтому она целиком покрывается тестами: экранирование чужих слов,
наличие форм там, где должны быть кнопки.

Форматы без JavaScript: формы шлют обычные POST и получают redirect.
Страница работает даже если JS отключён, а чужие слова (имена,
адреса, ошибки) экранируются — витрина не должна быть дырой.
"""

from __future__ import annotations

import json
from html import escape

from core.catalogue import can_remove
from core.markdown import render as render_markdown
from core.routes import is_local_url


def _style() -> str:
    """Общий вид страниц в стиле AI Studio: чистая сетка и выразительные карточки.

    Всё на CSS-переменных: одна правка красит все страницы. Кнопки —
    компактные скруглённые элементы, карточки — с лёгкой тенью; навигация
    одна на все страницы, «выйти» приглушён и подальше от рабочих ссылок.
    """
    return """  :root { color-scheme: light dark;
    --bg: #f1f5f9; --card: #ffffff; --text: #0f172a; --muted: #64748b;
    --line: #e2e8f0; --accent: #059669; --accent-deep: #047857;
    --accent-soft: rgba(5, 150, 105, .10);
    /* Четыре своих цвета: полоса (шапка) и кнопки, у каждого — верх и
       низ. По умолчанию всё следует за акцентом; пипетки разводят. */
    --nav-a: var(--accent); --nav-b: var(--accent-deep);
    --soft: var(--accent-soft); --head: #ffffff;
    --bad: #c0392b; --bad-soft: rgba(192, 57, 43, .10);
    --warn-soft: rgba(176, 140, 20, .14); --blue-soft: rgba(42, 90, 128, .12); }
  @media (prefers-color-scheme: dark) { :root {
    --bg: #060911; --card: #0d121f; --text: #f8fafc; --muted: #94a3b8;
    --line: #1e293b; --accent: #22d3ee; --accent-deep: #0891b2;
    --accent-soft: rgba(34, 211, 238, .12);
    --bad: #e5736a; --bad-soft: rgba(229, 115, 106, .14);
    --warn-soft: rgba(202, 168, 60, .16); --blue-soft: rgba(96, 152, 196, .16); } }
  /* Цветовые темы: класс на <html> переопределяет акцент. «Системная» —
     акцент по умолчанию и светлый/тёмный режим по настройкам системы. */
  .theme-blue { --accent: #2563eb; --accent-deep: #1d4ed8; --accent-soft: rgba(37, 99, 235, .12); }
  .theme-yellow { --accent: #d97706; --accent-deep: #b45309; --accent-soft: rgba(217, 119, 6, .12); }
  .theme-purple { --accent: #7c3aed; --accent-deep: #6d28d9; --accent-soft: rgba(124, 58, 237, .12); }
  .theme-cyberpunk { --accent: #06b6d4; --accent-deep: #0891b2; --accent-soft: rgba(6, 182, 212, .12); }
  .theme-aurora { --accent: #10b981; --accent-deep: #0f766e; --accent-soft: rgba(16, 185, 129, .12); }
  .theme-synthwave { --accent: #d946ef; --accent-deep: #c026d3; --accent-soft: rgba(217, 70, 239, .12); }
  @media (prefers-color-scheme: dark) {
    .theme-blue { --accent: #60a5fa; --accent-deep: #2563eb; --accent-soft: rgba(96, 165, 250, .14); }
    .theme-yellow { --accent: #fbbf24; --accent-deep: #d97706; --accent-soft: rgba(251, 191, 36, .14); }
    .theme-purple { --accent: #a78bfa; --accent-deep: #7c3aed; --accent-soft: rgba(167, 139, 250, .14); }
    .theme-cyberpunk { --accent: #22d3ee; --accent-deep: #0891b2; --accent-soft: rgba(34, 211, 238, .14); }
    .theme-aurora { --accent: #34d399; --accent-deep: #0f766e; --accent-soft: rgba(52, 211, 153, .14); }
    .theme-synthwave { --accent: #e879f9; --accent-deep: #c026d3; --accent-soft: rgba(232, 121, 249, .14); }
  }
  /* Свои цвета (кладёт браузер): производные оттенки считает CSS от
     выбранного base-цвета, JS только запоминает и применяет его. */
  html.custom-accent { --accent-deep: color-mix(in srgb, var(--accent) 78%, #000);
    --accent-soft: color-mix(in srgb, var(--accent) 13%, transparent); }
  html.custom-nav { --nav-b: color-mix(in srgb, var(--nav-a) 74%, #000); }
  * { box-sizing: border-box; }
  html { background: var(--bg); }
  body { font: 15px/1.55 "Plus Jakarta Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
    background: var(--bg); color: var(--text); margin: 0 auto; max-width: 90rem;
    padding: 1rem clamp(.8rem, 3vw, 2rem) 3rem; min-height: 100vh;
    text-rendering: optimizeLegibility; -webkit-font-smoothing: antialiased; }
  h1 { font-size: clamp(1.45rem, 2.5vw, 2rem); line-height: 1.2;
    letter-spacing: -.035em; margin: 1.25rem 0 .45rem; font-weight: 800; }
  h2 { font-size: 1.12rem; margin-top: 1.6rem; letter-spacing: -.015em; }
  a { color: var(--accent); }
  input, select, textarea { padding: .4rem .55rem; border: 1px solid var(--line);
    border-radius: .7rem; background: var(--card); color: var(--text); font: inherit;
    transition: border-color .16s, box-shadow .16s, background .16s; }
  input:focus, select:focus, textarea:focus { outline: none; border-color: var(--accent);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 18%, transparent); }
  button, .btn-ghost { display: inline-block; padding: .38rem .82rem; border: 1px solid var(--line);
    border-radius: .72rem; background: var(--card); color: var(--text); cursor: pointer;
    font: inherit; font-size: .86rem; font-weight: 600; text-decoration: none;
    transition: transform .16s, border-color .16s, box-shadow .16s, background .16s; }
  button:hover, .btn-ghost:hover { border-color: var(--accent); color: var(--accent); }
  button:active, .btn-ghost:active { transform: translateY(1px); }
  button:focus-visible, a:focus-visible, summary:focus-visible {
    outline: 3px solid color-mix(in srgb, var(--accent) 48%, transparent); outline-offset: 2px; }
  /* Основное действие — заливная изумрудная кнопка. */
  button.primary, form.primary button[type=submit] { background: var(--accent);
    border-color: var(--accent); color: #fff; font-weight: 700;
    box-shadow: 0 4px 12px color-mix(in srgb, var(--accent) 20%, transparent); }
  button.primary:hover, form.primary button[type=submit]:hover { background: var(--accent-deep);
    border-color: var(--accent-deep); color: #fff; }
  button.ghost { background: transparent; border-color: transparent; color: var(--muted); }
  button.ghost:hover { color: var(--accent); }
  /* Рамка-пилюля для ghost-кнопок, которым надо быть «как у соседей»
     (в каталоге — «тест» и «убрать»). Остальные ghost-кнопки оставляем
     без рамки — их приведём к пилюлям при следующей правке тех страниц
     (заметка в AGENTS.md). */
  button.ghost.pill { border-color: var(--line); }
  button.danger { color: var(--bad); }
  form { display: inline; }
  .role { color: var(--muted); font-size: .85rem; }
  .notice { background: var(--soft); color: var(--text); padding: .5rem .8rem;
    border-radius: .6rem; }
  .notice.bad { background: var(--bad-soft); }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 1.15rem;
    padding: 1rem 1.1rem; box-shadow: 0 8px 24px rgba(15, 23, 42, .045);
    transition: border-color .2s, box-shadow .2s; }
  @media (prefers-color-scheme: dark) {
    .card { box-shadow: 0 12px 32px rgba(0, 0, 0, .18); }
  }
  /* Таблица шире карточки — прокручивается внутри неё, а не лезет наружу. */
  .card { overflow-x: auto; }
  /* Шапка — плашка своего цвета: логотип-ссылка слева, разделы справа.
     Разделы — пилюли с рамкой (как ссылки-действия в строках), «выйти»
     в той же форме, но приглушено. */
  .topnav { position: sticky; top: .5rem; z-index: 20; display: flex;
    justify-content: space-between; align-items: center;
    gap: .5rem; flex-wrap: wrap; padding: .55rem .9rem; border-radius: 1rem;
    background: linear-gradient(135deg, var(--nav-a), var(--nav-b));
    box-shadow: 0 2px 6px color-mix(in srgb, var(--nav-a) 40%, transparent),
      0 8px 24px color-mix(in srgb, var(--nav-a) 24%, transparent);
    backdrop-filter: blur(16px); }
  .topnav .brand { display: inline-flex; align-items: center; gap: .55rem;
    font-weight: 800; font-size: 1.05rem; letter-spacing: -.025em;
    color: #fff; text-decoration: none; margin-right: .4rem; }
  .topnav .brand::before { content: "✦"; display: inline-grid; place-items: center;
    width: 1.85rem; height: 1.85rem; border-radius: .65rem;
    color: #082f33; background: linear-gradient(135deg, #6ee7b7, #67e8f9);
    box-shadow: 0 2px 12px rgba(103, 232, 249, .3); }
  .topnav .brand:hover { text-decoration: underline; }
  .topnav nav { display: flex; gap: .35rem; align-items: center; font-size: .9rem; flex-wrap: wrap; }
  .topnav nav a { color: color-mix(in srgb, var(--head) 85%, transparent); text-decoration: none;
    font-weight: 600; padding: .28rem .65rem;
    border: 1px solid color-mix(in srgb, var(--head) 40%, transparent); border-radius: 999px; }
  .topnav nav a:hover { color: var(--head); background: color-mix(in srgb, var(--head) 16%, transparent);
    border-color: color-mix(in srgb, var(--head) 65%, transparent); text-decoration: none; }
  .topnav button.out { background: none; color: color-mix(in srgb, var(--head) 65%, transparent);
    font-size: .9rem; padding: .2rem .65rem;
    border: 1px solid color-mix(in srgb, var(--head) 40%, transparent); border-radius: 999px; }
  .topnav button.out:hover { color: var(--head); background: color-mix(in srgb, var(--head) 16%, transparent);
    border-color: color-mix(in srgb, var(--head) 65%, transparent); }
  /* Переключатель цветовой темы и «свои цвета» в шапке. */
  .theme-switch { display: inline-flex; align-items: center; }
  #theme-sel { padding: .2rem .6rem; font-size: .85rem;
    border: 1px solid color-mix(in srgb, var(--head) 40%, transparent);
    background: color-mix(in srgb, var(--head) 15%, transparent); color: var(--head);
    border-radius: 999px; cursor: pointer; font: inherit; }
  #theme-sel:hover { border-color: color-mix(in srgb, var(--head) 65%, transparent); }
  #theme-sel option { color: var(--text); background: var(--card); }
  .topnav.plain #theme-sel { border-color: var(--line); background: var(--card);
    color: var(--text); }
  /* Свои цвета: пилюля-заголовок, при клике — панелька с четырьмя
     независимыми пипетками: полоса верх/низ, кнопки верх/низ. */
  .colors { position: relative; }
  .colors summary { list-style: none; display: inline-block; padding: .2rem .6rem;
    font-size: .85rem; border: 1px solid color-mix(in srgb, var(--head) 40%, transparent);
    background: color-mix(in srgb, var(--head) 15%, transparent); color: var(--head);
    border-radius: 999px; cursor: pointer; }
  .colors summary::-webkit-details-marker { display: none; }
  .colors summary:hover { border-color: color-mix(in srgb, var(--head) 65%, transparent); }
  .topnav.plain .colors summary { border-color: var(--line); background: var(--card);
    color: var(--text); }
  .colors .panel { position: absolute; right: 0; top: calc(100% + .35rem); z-index: 5;
    display: flex; gap: .45rem .6rem; align-items: center; flex-wrap: wrap;
    max-width: min(92vw, 27rem);
    background: var(--card); color: var(--text); border: 1px solid var(--line);
    border-radius: .8rem; padding: .5rem .7rem; box-shadow: 0 6px 18px rgba(0, 0, 0, .18); }
  .colors .panel label { display: inline-flex; align-items: center; gap: .3rem; margin: 0;
    font-size: .8rem; color: var(--muted); white-space: nowrap; }
  .colors .panel input[type=color] { width: 2.2rem; height: 1.5rem; padding: 0;
    border: 1px solid var(--line); border-radius: 999px; background: var(--card);
    cursor: pointer; }
  /* Счётчик посещений: мелкий, приглушённый текст в самом низу. */
  .page-counter { text-align: right; font-size: .75rem; color: var(--muted);
  margin-top: 2rem; opacity: .6; }
  /* Вход/регистрация — шапка без плашки, они ещё не в системе. */
  .topnav.plain { background: none; box-shadow: none; padding: .2rem 0;
    border-bottom: 1px solid var(--line); border-radius: 0; position: static;
    backdrop-filter: none; }
  .topnav.plain .brand { color: var(--text); }
  @media (max-width: 52rem) {
    .topnav { top: .25rem; padding: .5rem .65rem; }
    .topnav nav { flex: 1 1 100%; flex-wrap: nowrap; overflow-x: auto;
      padding-bottom: .15rem; scrollbar-width: none; }
    .topnav nav::-webkit-scrollbar { display: none; }
    .topnav nav > a, .topnav nav > label, .topnav nav > details,
    .topnav nav > form { flex: 0 0 auto; }
  }
  .badge { font-size: .72rem; padding: .12rem .55rem; border-radius: 999px; font-weight: 600; }
  .badge-on { background: var(--accent); color: #fff; }
  .badge-dead { background: var(--bad); color: #fff; }
  .badge-off { background: var(--warn-soft); }
  .badge-mine { background: var(--blue-soft); }
  /* Тумблер вкл/выкл: зелёный с кнопкой справа — включён, красный слева —
     выключен. Чистый CSS, клик — обычный POST. */
  .toggle-form { display: inline; }
  button.switch { width: 2.6rem; height: 1.45rem; padding: 0; border: none;
    border-radius: 999px; background: var(--bad); position: relative;
    vertical-align: middle; transition: background .2s; }
  button.switch:hover { border: none; color: inherit; filter: brightness(1.08); }
  button.switch .knob { position: absolute; top: .18rem; left: .2rem;
    width: 1.09rem; height: 1.09rem; border-radius: 50%; background: #fff;
    box-shadow: 0 1px 2px rgba(0, 0, 0, .3); transition: left .2s; }
  button.switch.on { background: var(--accent); }
  button.switch.on .knob { left: 1.31rem; }
  label { display: block; margin: .5rem 0; }"""


#: Крошечный скрипт копирования: кнопка кладёт адрес в буфер, при отказе
#: (старый браузер, http) выделяет текст — останется Ctrl+C. Весь остальной
#: странице JavaScript не нужен.
COPY_SCRIPT = """<script>
function cp(el, id) {
  var node = document.getElementById(id);
  var done = function() { el.textContent = 'скопировано ✓'; setTimeout(function() { el.textContent = 'копировать'; }, 2000); };
  if (navigator.clipboard) { navigator.clipboard.writeText(node.textContent).then(done, function() { select(node); }); }
  else { select(node); }
  function select(n) { var r = document.createRange(); r.selectNodeContents(n); window.getSelection().removeAllRanges(); window.getSelection().addRange(r); }
}
</script>"""


ANSWER_SCRIPT = """<script>
(function(){
  document.querySelectorAll(".answer").forEach(function(box){
    var copy = box.querySelector(".copy-all");
    var pre = box.querySelector("pre, .answer-md");
    if (copy && pre && navigator.clipboard) {
      copy.addEventListener("click", function(){
        navigator.clipboard.writeText(pre.textContent).then(function(){
          copy.textContent = "скопировано";
          setTimeout(function(){ copy.textContent = "копировать"; }, 1200);
        });
      });
    }
    box.querySelectorAll(".copy-code").forEach(function(btn){
      var code = btn.parentElement.querySelector("code");
      if (!code || !navigator.clipboard) return;
      btn.addEventListener("click", function(){
        navigator.clipboard.writeText(code.textContent).then(function(){
          btn.textContent = "скопировано";
          setTimeout(function(){ btn.textContent = "копировать"; }, 1200);
        });
      });
    });
  });
})();
</script>"""

#: Метки времени: сервер хранит UTC, страница показывает время зрителя.
#: Функция переписывает все [data-ts] в локальное время браузера.
LOCAL_TIME_SCRIPT = """<script>
window.localTimes = function(){
  function pad(n){ return (n<10?"0":"")+n; }
  function fmt(d){
    return d.getFullYear()+"-"+pad(d.getMonth()+1)+"-"+pad(d.getDate())+" "+
           pad(d.getHours())+":"+pad(d.getMinutes())+":"+pad(d.getSeconds());
  }
  var nodes = document.querySelectorAll("[data-ts]");
  for (var i=0;i<nodes.length;i++){
    var d = new Date(nodes[i].getAttribute("data-ts"));
    if (!isNaN(d)) nodes[i].textContent = fmt(d);
  }
};
window.localTimes();
</script>"""


def _copy_line(value: str, node_id: str) -> str:
    """Адрес + кнопка «копировать»: текст не редактируется, кнопка — в буфер."""
    return (
        f'<span class="addr"><code id="{escape(node_id, quote=True)}">{escape(value)}</code> '
        f'<button type="button" class="ghost" onclick="cp(this, '
        f"'{escape(node_id, quote=True)}')\">копировать</button></span>"
    )


def _nav(*, can_edit: bool) -> str:
    """Шапка страницы: логотип-ссылка слева, разделы справа, «выйти» far right.

    Логотип всегда ведёт в каталог — добраться домой можно с любой
    страницы; пункт «каталог» дублирует его в разделах, чтобы путь
    назад читался явно. «Выйти» визуально приглушено и стоит последним,
    подальше от рабочих ссылок: случайный клик не выкинет из приставки.
    """
    links = [
        '<a href="/">каталог</a>',
        '<a href="/stats">статистика</a>',
        '<a href="/tokens">приложения и токены</a>',
    ]
    if can_edit:
        links.append('<a href="/sql">SQL Playground</a>')
        links.append('<a href="/logs">журнал</a>')
        links.append('<a href="/users">пользователи</a>')
    # Вид шапки живёт в браузере: тема и свои цвета (кнопки, полоса)
    # хранятся в localStorage, применяются сразу, без перезагрузки.
    theme_select = (
        '<label class="theme-switch" title="цветовая тема">'
        '<select id="theme-sel" aria-label="цветовая тема">'
        '<option value="system">системная</option>'
        '<option value="emerald">зелёная</option>'
        '<option value="blue">синяя</option>'
        '<option value="yellow">жёлтая</option>'
        '<option value="purple">фиолетовая</option>'
        '<option value="cyberpunk">киберпанк</option>'
        '<option value="aurora">северное сияние</option>'
        '<option value="synthwave">синтвейв</option>'
        '</select></label>'
    )
    # Свои цвета — четыре независимых: полоса шапки (верх) и нежный
    # фон (низ), кнопки на шапке (верх) и кнопки на странице (низ).
    # В свёрнутом виде — одна пилюля (на телефоне место дорого),
    # раскрывается в панельку с четырьмя образцами и «сбросить».
    colors = (
        '<details class="colors" title="свои цвета">'
        '<summary aria-label="свои цвета">🎨</summary>'
        '<div class="panel">'
        '<label>полоса верх <input type="color" id="color-nav" value="#0e9f6e" '
        'aria-label="цвет полосы шапки"></label>'
        '<label>полоса низ <input type="color" id="color-soft" value="#0e9f6e" '
        'aria-label="цвет нежного фона"></label>'
        '<label>кнопки верх <input type="color" id="color-head" value="#ffffff" '
        'aria-label="цвет кнопок на шапке"></label>'
        '<label>кнопки низ <input type="color" id="color-accent" value="#0e9f6e" '
        'aria-label="цвет кнопок на странице"></label>'
        '<button type="button" class="ghost pill" id="color-reset">сбросить</button>'
        '</div></details>'
    )
    # Ранний скрипт: применяется до отрисовки шапки — цвета не мигают.
    theme_script = """<script>
(function(){
  var root=document.documentElement;
  var key="ai-bridge-theme";
  var saved=localStorage.getItem(key)||"system";
  root.classList.remove("theme-blue","theme-yellow","theme-purple","theme-cyberpunk","theme-aurora","theme-synthwave");
  if(saved==="blue") root.classList.add("theme-blue");
  else if(saved==="yellow") root.classList.add("theme-yellow");
  else if(saved==="purple") root.classList.add("theme-purple");
  else if(saved==="cyberpunk") root.classList.add("theme-cyberpunk");
  else if(saved==="aurora") root.classList.add("theme-aurora");
  else if(saved==="synthwave") root.classList.add("theme-synthwave");
  var accent=localStorage.getItem("ai-bridge-accent");
  var nav=localStorage.getItem("ai-bridge-nav");
  var soft=localStorage.getItem("ai-bridge-soft");
  var head=localStorage.getItem("ai-bridge-head");
  if(accent){ root.style.setProperty("--accent",accent); root.classList.add("custom-accent"); }
  if(nav){ root.style.setProperty("--nav-a",nav); root.classList.add("custom-nav"); }
  if(soft){ root.style.setProperty("--soft","color-mix(in srgb, "+soft+" 12%, transparent)"); }
  if(head){ root.style.setProperty("--head",head); }
})();
</script>"""
    # Живые правила: выбор пресета сбрасывает свои цвета (иначе
    # непонятно, что действует); пипетки красят сразу по движению.
    ui_script = """<script>
(function(){
  var root=document.documentElement;
  var AK="ai-bridge-accent", NK="ai-bridge-nav", SK="ai-bridge-soft", HK="ai-bridge-head";
  var sel=document.getElementById("theme-sel");
  var pAcc=document.getElementById("color-accent");
  var pNav=document.getElementById("color-nav");
  var pSoft=document.getElementById("color-soft");
  var pHead=document.getElementById("color-head");
  var reset=document.getElementById("color-reset");
  function hex(v){
    v=(v||"").trim();
    if(/^#[0-9a-f]{6}$/i.test(v)) return v;
    var m=v.match(/^rgba?\\(\\s*(\\d+)\\s*,\\s*(\\d+)\\s*,\\s*(\\d+)/);
    if(!m) return null;
    return "#"+[m[1],m[2],m[3]].map(function(x){
      var h=(+x).toString(16); return h.length<2 ? "0"+h : h;
    }).join("");
  }
  function put(input, value){ var h=hex(value); if(input && h) input.value=h; }
  function syncInputs(){
    var computed=getComputedStyle(root);
    put(pAcc, computed.getPropertyValue("--accent"));
    put(pNav, computed.getPropertyValue("--nav-a"));
    put(pSoft, computed.getPropertyValue("--soft"));
    put(pHead, computed.getPropertyValue("--head"));
  }
  function load(input, key){ var v=localStorage.getItem(key); if(input && v) input.value=v; }
  function setAccent(v){
    if(v){ root.style.setProperty("--accent",v); root.classList.add("custom-accent"); localStorage.setItem(AK,v); }
    else { root.style.removeProperty("--accent"); root.classList.remove("custom-accent"); localStorage.removeItem(AK); }
  }
  function setNav(v){
    if(v){ root.style.setProperty("--nav-a",v); root.classList.add("custom-nav"); localStorage.setItem(NK,v); }
    else { root.style.removeProperty("--nav-a"); root.classList.remove("custom-nav"); localStorage.removeItem(NK); }
  }
  function setSoft(v){
    if(v){ root.style.setProperty("--soft","color-mix(in srgb, "+v+" 12%, transparent)"); localStorage.setItem(SK,v); }
    else { root.style.removeProperty("--soft"); localStorage.removeItem(SK); }
  }
  function setHead(v){
    if(v){ root.style.setProperty("--head",v); localStorage.setItem(HK,v); }
    else { root.style.removeProperty("--head"); localStorage.removeItem(HK); }
  }
  function clearAll(){ setAccent(""); setNav(""); setSoft(""); setHead(""); }
  load(pAcc,AK); load(pNav,NK); load(pSoft,SK); load(pHead,HK);
  if(sel){
    sel.value=localStorage.getItem("ai-bridge-theme")||"system";
    sel.addEventListener("change",function(){
      localStorage.setItem("ai-bridge-theme",sel.value);
      root.classList.remove("theme-blue","theme-yellow","theme-purple","theme-cyberpunk","theme-aurora","theme-synthwave");
      if(sel.value==="blue") root.classList.add("theme-blue");
      else if(sel.value==="yellow") root.classList.add("theme-yellow");
      else if(sel.value==="purple") root.classList.add("theme-purple");
      else if(sel.value==="cyberpunk") root.classList.add("theme-cyberpunk");
      else if(sel.value==="aurora") root.classList.add("theme-aurora");
      else if(sel.value==="synthwave") root.classList.add("theme-synthwave");
      // Пресет сбрасывает свои цвета — иначе непонятно, что действует.
      clearAll(); syncInputs();
    });
  }
  if(pAcc) pAcc.addEventListener("input",function(){ setAccent(hex(pAcc.value)); });
  if(pNav) pNav.addEventListener("input",function(){ setNav(hex(pNav.value)); });
  if(pSoft) pSoft.addEventListener("input",function(){ setSoft(hex(pSoft.value)); });
  if(pHead) pHead.addEventListener("input",function(){ setHead(hex(pHead.value)); });
  if(reset) reset.addEventListener("click",function(){ clearAll(); syncInputs(); });
  syncInputs();
})();
</script>"""
    links.append(theme_select)
    links.append(colors)
    links.append(ui_script)
    links.append(
        '<form method="post" action="/logout">'
        '<button class="out" type="submit">выйти</button></form>'
    )
    return (
        f'{theme_script}<header class="topnav"><a class="brand" href="/">AI-приставка</a>'
        f'<nav>{" ".join(links)}</nav></header>'
    )

def _footer(visits: int) -> str:
    """Счётчик посещений внизу страницы."""
    return f'<div class="page-counter">посещений: {visits}</div>'



def _badge(text: str, kind: str = "") -> str:
    cls = f"badge badge-{kind}" if kind else "badge"
    return f'<span class="{cls}">{escape(text)}</span>'


def _safe_url(value: object) -> str:
    """Адрес для ссылки: только http/https, иначе пусто (чужая схема — не ссылка)."""
    text = str(value or "").strip()
    if text.startswith(("http://", "https://")):
        return text
    return ""


def _provider_row(
    entry: dict,
    *,
    can_edit: bool,
    key_hint: str,
    status: str = "",
) -> str:
    """Одна строка каталога: имя провайдера, ключ, действие.

    ``status`` — живая строка состояния локального сервера (Ollama/LM
    Studio: что в памяти прямо сейчас); пусто — не рисуем.
    """
    name = escape(entry["name"])
    badges = []
    if entry.get("enabled"):
        badges.append(_badge("вкл", "on"))
    if entry.get("status") == "dead":
        badges.append(_badge("нераб.", "dead"))
    if entry.get("status") == "disabled_for_all":
        badges.append(_badge("выкл. админом", "off"))
    if entry.get("mine"):
        badges.append(_badge("ваш", "mine"))

    # Логотип: первая валидная ссылка из списка, если админ заполнил.
    logo_html = ""
    for line in str(entry.get("logoUrl") or "").splitlines():
        candidate = _safe_url(line)
        if candidate:
            logo_html = (
                f'<img class="logo" src="{escape(candidate, quote=True)}" alt="" '
                f'onerror="this.remove()"> '
            )
            break

    # Справка о модели: контекст, цена, способности — если админ заполнил.
    facts = [
        escape(entry[key])
        for key in ("context", "pricing", "capabilities")
        if entry.get(key)
    ]
    facts_html = f'<span class="facts">{" · ".join(facts)}</span>' if facts else ""

    # Страница провайдера с описанием моделей и ценами — ссылка под моделью.
    if _safe_url(entry.get("infoUrl")):
        facts_html += (
            f'<span class="facts"><a href="{escape(_safe_url(entry["infoUrl"]), quote=True)}" '
            f'target="_blank" rel="noopener">описание и цены</a></span>'
        )

    # Живое состояние локального сервера — что в памяти прямо сейчас.
    status_html = (
        f'<span class="localstatus">⚡ {escape(status)}</span>' if status else ""
    )

    # Действие над строкой. Админ правит определение (править/убрать —
    # корзиной, место на телефоне дорогое), человек ведёт своё пространство:
    # ключ, тест, своя модель. Проверка — кнопкой «тест»: настоящий ответ
    # модели надёжнее списка моделей и он же засчитывается в статистику
    # сообщества.
    entry_id = escape(entry["id"], quote=True)
    state = "on" if entry.get("enabled") else "off"
    actions = [
        f'<form method="post" action="/toggle/{entry_id}" class="toggle-form">'
        f'<button type="submit" class="switch {state}" role="switch" '
        f'aria-checked="{"true" if entry.get("enabled") else "false"}" '
        f'title="{"выключен — нажмите, чтобы включить" if not entry.get("enabled") else "включён — нажмите, чтобы выключить"}">'
        f'<span class="knob"></span></button></form>',
        f'<form method="post" action="/test/{entry_id}">'
        f'<button type="submit" class="ghost pill">тест</button></form>',
        f'<a class="btn-ghost" href="/key/{entry_id}">ключ</a>',
        f'<a class="btn-ghost" href="/pick/{entry_id}">модель</a>',
    ]
    # Ссылка на получение ключа у провайдера — если админ её указал.
    # Открывается в новой вкладке, чтобы не терять страницу каталога.
    get_key_url = _safe_url(entry.get("getApiKeyUrl"))
    if get_key_url:
        actions.append(
            f'<a class="btn-ghost" href="{escape(get_key_url, quote=True)}" '
            f'target="_blank" rel="noopener">get key</a>'
        )
    if can_edit:
        actions.append(f'<a class="btn-ghost" href="/edit/{entry_id}">править</a>')
        if can_remove(entry, is_superuser=can_edit):
            actions.append(
                f'<form method="post" action="/remove/{entry_id}">'
                f'<button type="submit" class="ghost danger pill" '
                f'title="убрать провайдера" aria-label="убрать">🗑</button></form>'
            )

    # Адрес провайдера на страницах не показывается никому — ни человеку,
    # ни админу: витрина делится названием, адрес живёт только в формах
    # админа (добавить/править), иначе управлять каталогом нечем.
    # Модель и проверки убраны: каталог — про провайдеров, а не про
    # цифры (они на статистике). Справка и живой статус — под именем.
    extra = facts_html + status_html
    capabilities = str(entry.get("capabilities") or "").lower()
    search_text = " ".join(
        str(value or "")
        for value in (
            entry.get("name"),
            entry.get("id"),
            entry.get("context"),
            entry.get("pricing"),
            entry.get("capabilities"),
        )
    ).lower()
    return (
        f'<tr class="provider-row{" on" if entry.get("enabled") else ""}" '
        f'data-provider="{entry_id}" data-enabled="{"1" if entry.get("enabled") else "0"}" '
        f'data-local="{"1" if is_local_url(str(entry.get("url") or "")) else "0"}" '
        f'data-key="{"1" if key_hint else "0"}" '
        f'data-capabilities="{escape(capabilities, quote=True)}" '
        f'data-search="{escape(search_text, quote=True)}">'
        f'<td class="name">{logo_html}{name} {" ".join(badges)}{extra}</td>'
        f'<td class="key">{escape(key_hint)}</td>'
        f'<td class="actions">{" ".join(actions)}</td>'
        f"</tr>"
    )


def catalogue_page(
    providers: list[dict],
    *,
    can_edit: bool = False,
    keyed_ids: frozenset[str] = frozenset(),
    notice: str = "",
    notice_bad: bool = False,
    role: str = "",
    app_address: str = "",
    statuses: dict | None = None,
) -> str:
    """Весь каталог одной страницей: тумблеры, тест, ключи, добавление.

    ``statuses`` — {id провайдера: живая строка состояния локального}.
    """
    statuses = statuses or {}
    rows = "\n".join(
        _provider_row(
            entry,
            can_edit=can_edit,
            key_hint="задан" if entry["id"] in keyed_ids else "",
            status=statuses.get(entry["id"], ""),
        )
        for entry in providers
    )
    if not rows:
        rows = '<tr><td colspan="3" class="empty">каталог пуст</td></tr>'

    notice_html = (
        f'<p class="notice{" bad" if notice_bad else ""}">{escape(notice)}</p>'
        if notice
        else ""
    )
    role_html = f'<p class="role">{escape(role)}</p>' if role else ""

    active_count = sum(bool(entry.get("enabled")) for entry in providers)
    keyed_count = sum(entry.get("id") in keyed_ids for entry in providers)
    # Добавить провайдера может каждый: админ — в каталог для всех,
    # человек — в своё личное пространство (форма рассказывает разницу).
    endpoint_html = (
        f'<div class="hero-endpoint"><span class="hero-label">Для приложений · endpoint OpenAI</span>'
        f'<div class="endpoint-line">{_copy_line(app_address, "v1addr")}</div></div>'
        if app_address else ""
    )
    hero_html = f"""<section class="catalogue-hero">
  <div class="hero-copy">
    <span class="hero-status"><span class="status-dot"></span>ваше пространство AI-провайдеров</span>
    <h1>Каталог провайдеров</h1>
    <p>Подключайте модели, храните ключи локально и выбирайте, кому доверить запросы.</p>
  </div>
  <div class="hero-side">
    {endpoint_html}
    <div class="hero-metrics">
      <div><b>{len(providers)}</b><span>провайдеров</span></div>
      <div><b>{active_count}</b><span>включено</span></div>
      <div><b>{keyed_count}</b><span>с ключом</span></div>
    </div>
  </div>
</section>"""
    copy_script = COPY_SCRIPT if app_address else ""
    add_html = '<p class="add"><a class="btn-ghost" href="/add">+ добавить провайдера</a></p>'

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI-приставка</title>
<style>
{_style()}
  table {{ border-collapse: separate; border-spacing: 0; width: 100%; }}
  th, td {{ text-align: left; padding: .55rem .7rem; border-bottom: 1px solid var(--line);
    overflow-wrap: anywhere; }}
  th {{ font-size: .72rem; text-transform: uppercase; letter-spacing: .08em; color: var(--muted);
    background: color-mix(in srgb, var(--bg) 75%, var(--card)); }}
  tbody tr:last-child td {{ border-bottom: none; }}
  table.catalogue {{ border-spacing: 0 .45rem; margin: -.45rem 0; }}
  table.catalogue tr.provider-row td {{ background: var(--card); border-top: 1px solid var(--line);
    border-bottom: 1px solid var(--line); vertical-align: middle; }}
  table.catalogue tr.provider-row td:first-child {{ border-left: 1px solid var(--line);
    border-radius: .85rem 0 0 .85rem; }}
  table.catalogue tr.provider-row td:last-child {{ border-right: 1px solid var(--line);
    border-radius: 0 .85rem .85rem 0; }}
  table.catalogue tr.on td {{ background: color-mix(in srgb, var(--soft) 60%, var(--card)); }}
  .actions {{ white-space: nowrap; display: flex; justify-content: flex-end; align-items: center;
    gap: .3rem; flex-wrap: wrap; }}
  .actions form {{ margin: 0; }}
  .facts {{ display: block; font-size: .8em; color: var(--muted); }}
  /* Живое состояние локального сервера — что в памяти прямо сейчас. */
  .localstatus {{ display: inline-block; margin-top: .15rem; font-size: .78rem;
    color: var(--accent); background: var(--soft);
    padding: .1rem .5rem; border-radius: 999px; }}
  img.logo {{ height: 1.3em; vertical-align: middle; margin-right: .3rem; }}
  .empty {{ opacity: .6; text-align: center; padding: 2rem; }}
  .addr code {{ font-family: monospace; user-select: all; background: var(--soft);
    padding: .15rem .4rem; border-radius: .35rem; }}
  .catalogue-hero {{ position: relative; overflow: hidden; display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(18rem, .78fr); gap: 1.5rem;
    align-items: center; margin: 1.1rem 0 1.2rem; padding: clamp(1.25rem, 3vw, 2rem);
    color: #f8fafc; border: 1px solid rgba(103, 232, 249, .18); border-radius: 1.5rem;
    background: linear-gradient(120deg, #0b1220 0%, #111827 56%, #0b2430 100%);
    box-shadow: 0 18px 48px rgba(2, 8, 23, .18); }}
  .catalogue-hero::after {{ content: ""; position: absolute; width: 23rem; height: 23rem;
    right: 12%; top: -18rem; border-radius: 50%; pointer-events: none;
    background: #06b6d4; opacity: .16; filter: blur(60px); }}
  .hero-copy, .hero-side {{ position: relative; z-index: 1; }}
  .hero-status {{ display: inline-flex; align-items: center; gap: .5rem; padding: .32rem .7rem;
    border: 1px solid rgba(103, 232, 249, .28); border-radius: 999px;
    background: rgba(8, 47, 73, .55); color: #a5f3fc; font-size: .76rem; font-weight: 700; }}
  .status-dot {{ width: .48rem; height: .48rem; border-radius: 50%; background: #34d399;
    box-shadow: 0 0 12px rgba(52, 211, 153, .8); }}
  .catalogue-hero h1 {{ margin: .85rem 0 .45rem; color: #fff; }}
  .catalogue-hero p {{ max-width: 39rem; margin: 0; color: #cbd5e1; }}
  .hero-side {{ display: grid; gap: .7rem; }}
  .hero-endpoint {{ min-width: 0; padding: .75rem .85rem; border: 1px solid rgba(148, 163, 184, .24);
    border-radius: 1rem; background: rgba(2, 6, 23, .46); }}
  .hero-label {{ display: block; margin-bottom: .35rem; color: #cbd5e1;
    font-size: .7rem; font-weight: 700; letter-spacing: .03em; }}
  .hero-endpoint .addr {{ display: flex; align-items: center; gap: .5rem; min-width: 0; }}
  .hero-endpoint .addr code {{ min-width: 0; overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap; color: #a5f3fc; background: rgba(15, 23, 42, .85); }}
  .hero-endpoint .addr button {{ flex: 0 0 auto; border-color: rgba(148, 163, 184, .4);
    background: rgba(30, 41, 59, .8); color: #e2e8f0; }}
  .hero-metrics {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: .55rem; }}
  .hero-metrics > div {{ display: flex; flex-direction: column; gap: .1rem;
    padding: .55rem .7rem; border: 1px solid rgba(148, 163, 184, .18);
    border-radius: .85rem; background: rgba(15, 23, 42, .52); }}
  .hero-metrics b {{ color: #fff; font-size: 1.25rem; line-height: 1.2; }}
  .hero-metrics span {{ color: #94a3b8; font-size: .7rem; }}
  .catalogue-toolbar {{ display: flex; align-items: center; gap: .8rem; flex-wrap: wrap;
    margin: 0 0 .8rem; padding: .8rem; border: 1px solid var(--line);
    border-radius: 1rem; background: var(--card); box-shadow: 0 4px 16px rgba(15, 23, 42, .035); }}
  .catalogue-search {{ flex: 1 1 15rem; min-width: 12rem; }}
  .catalogue-search input {{ width: 100%; padding: .62rem .8rem .62rem 2.35rem;
    background: var(--bg); }}
  .search-wrap {{ position: relative; flex: 1 1 16rem; }}
  .search-wrap::before {{ content: "⌕"; position: absolute; left: .8rem; top: 50%;
    transform: translateY(-53%); color: var(--muted); font-size: 1.25rem; }}
  .catalogue-toolbar select {{ min-width: 10rem; padding: .58rem .75rem; background: var(--bg); }}
  .catalogue-count {{ margin-left: auto; color: var(--muted); font-size: .78rem;
    white-space: nowrap; }}
  #filtered-empty[hidden] {{ display: none; }}
  .add {{ margin-top: 1rem; }}
  input[type=password] {{ font-family: monospace; }}
  @media (max-width: 52rem) {{
    .catalogue-hero {{ grid-template-columns: 1fr; }}
    .catalogue-toolbar {{ align-items: stretch; }}
    .catalogue-count {{ margin: 0; }}
    /* На телефоне строка каталога складывается в карточку. */
    table.catalogue thead {{ display: none; }}
    table.catalogue tr.provider-row {{ display: block; border: 1px solid var(--line);
      border-radius: .8rem; margin: .6rem 0; padding: .3rem .5rem; background: var(--card); }}
    table.catalogue tr.provider-row[hidden] {{ display: none; }}
    table.catalogue tr.provider-row.on {{ background: var(--soft); }}
    table.catalogue tr.provider-row td, table.catalogue tr.provider-row td:first-child,
    table.catalogue tr.provider-row td:last-child {{ display: block; border: none;
      border-radius: 0; padding: .2rem .2rem; background: transparent; }}
    .actions {{ white-space: normal; }}
    .actions {{ justify-content: flex-start; }}
    .hero-metrics {{ gap: .4rem; }}
    .hero-metrics > div {{ padding: .5rem; }}
  }}
</style>
</head>
<body>
{_nav(can_edit=can_edit)}
{role_html}
{notice_html}
{hero_html}
<div class="catalogue-toolbar" role="search">
  <div class="search-wrap catalogue-search">
    <input id="catalogue-search" type="search" placeholder="Найти провайдера или способность…"
           aria-label="Поиск по каталогу" autocomplete="off">
  </div>
  <select id="catalogue-filter" aria-label="Фильтр каталога">
    <option value="all">Все провайдеры</option>
    <option value="enabled">Включённые</option>
    <option value="local">Локальные</option>
    <option value="cloud">Облачные</option>
    <option value="key">С ключом</option>
    <option value="vision">Vision</option>
    <option value="tools">Tools</option>
  </select>
  <span class="catalogue-count" id="catalogue-count" aria-live="polite">
    показано {len(providers)} из {len(providers)}
  </span>
</div>
<div class="card">
<table class="catalogue">
<tr><th>провайдер</th><th>ключ</th><th></th></tr>
{rows}
<tr id="filtered-empty" hidden><td colspan="3" class="empty">по этим условиям ничего не найдено</td></tr>
</table>
</div>

{add_html}

<script>
(function () {{
  var search = document.getElementById("catalogue-search");
  var filter = document.getElementById("catalogue-filter");
  var count = document.getElementById("catalogue-count");
  var empty = document.getElementById("filtered-empty");
  var rows = Array.prototype.slice.call(document.querySelectorAll("tr[data-provider]"));
  function applyFilter() {{
    var query = (search.value || "").trim().toLowerCase();
    var kind = filter.value;
    var visible = 0;
    rows.forEach(function (row) {{
      var matches = !query || (row.getAttribute("data-search") || "").indexOf(query) >= 0;
      if (kind === "enabled") matches = matches && row.dataset.enabled === "1";
      else if (kind === "local") matches = matches && row.dataset.local === "1";
      else if (kind === "cloud") matches = matches && row.dataset.local !== "1";
      else if (kind === "key") matches = matches && row.dataset.key === "1";
      else if (kind === "vision") matches = matches && row.dataset.capabilities.indexOf("vision") >= 0;
      else if (kind === "tools") matches = matches && row.dataset.capabilities.indexOf("tools") >= 0;
      row.hidden = !matches;
      if (matches) visible++;
    }});
    empty.hidden = rows.length === 0 || visible !== 0;
    count.textContent = "показано " + visible + " из " + rows.length;
  }}
  search.addEventListener("input", applyFilter);
  filter.addEventListener("change", applyFilter);
}})();
</script>
{copy_script}
</body>
</html>"""


def edit_page(
    entry: dict,
    *,
    notice: str = "",
    values: dict | None = None,
    models: list[str] | None = None,
    models_error: str = "",
    has_key: bool = False,
    oauth: bool = False,
) -> str:
    """Форма правки одного провайдера: определение, ключ и список моделей.

    Справка о модели (контекст, цена, способности), логотипы и страницы
    провайдера отсюда убраны: они заполняются при добавлении и в каталоге
    остаются как есть — править их здесь незачем. Пустое поле ничего не
    стирает (можно менять одну строку, не трогая остальные) — **кроме
    модели**: очищенное поле модели её убирает, иначе смена/удаление
    модели не состоялась бы никогда.

    ``values`` — уже введённое (после «показать модели» ничего не теряется);
    ``models`` — список с сервера (тогда модель выбирается из списка);
    ``oauth`` — провайдер принимает вход через GitHub (кнопка ниже).
    """
    v = values or {}

    def field(label: str, name: str, value: str, *, placeholder: str = "") -> str:
        return (
            f'<label>{escape(label)}'
            f'<input name="{name}" value="{escape(value, quote=True)}" '
            f'placeholder="{escape(placeholder, quote=True)}"></label>'
        )

    def filled(name: str, fallback: str = "") -> str:
        return str(v.get(name) if name in v else fallback)

    if models is not None:
        options = list(models)
        current = filled("model", str(entry.get("model") or ""))
        if current and current not in options:
            options.insert(0, current)
        opts = "\n    ".join(
            f'<option value="{escape(option, quote=True)}"'
            f'{" selected" if option == current else ""}>{escape(option)}</option>'
            for option in options
        )
        model_control = f'<label>Модель<select name="model">\n    {opts}\n  </select></label>'
    else:
        model_control = field(
            "Модель", "model", filled("model", str(entry.get("model") or "")),
            placeholder="qwen3:0.6b",
        )

    # Ключ возвращается в ту же форму после «показать модели»: иначе он
    # терялся между запросом списка и сохранением. Пустое поле ключа
    # ничего не стирает — ключ меняется только явным вводом.
    key_hint = "сейчас задан — введите новый, чтобы заменить" if has_key else ""
    key_field = (
        "<label>API-ключ (останется на устройстве)"
        f'<input type="password" name="api_key" autocomplete="off" '
        f'value="{escape(filled("api_key"), quote=True)}" '
        'placeholder="sk-…"></label>'
    )
    if key_hint:
        key_field += f'<p class="role">{escape(key_hint)}</p>'


    fields = "\n  ".join(
        [
            field("Название", "name", filled("name", str(entry.get("name") or ""))),
            field(
                "Адрес endpoint (базовый, без /chat/completions)",
                "url", filled("url", str(entry.get("url") or "")),
            ),
            key_field,
            model_control,
            field(
                "Приоритет", "fallbackPriority",
                filled("fallbackPriority", str(entry.get("fallbackPriority", 0))),
            ),
            field(
                "Где взять ключ", "getApiKeyUrl",
                filled("getApiKeyUrl", str(entry.get("getApiKeyUrl") or "")),
            ),
        ]
    )

    oauth_html = ""
    if oauth:
        provider_id = escape(str(entry.get("id") or ""), quote=True)
        oauth_html = f"""
<form method="post" action="/oauth/{provider_id}">
  <button type="submit" name="action" value="start">Войти через GitHub</button>
  <span class="role">вместо вставки ключа: GitHub выдаст код, вы введёте его на github.com/login/device</span>
</form>"""

    notice_html = f'<p class="notice">{escape(notice)}</p>' if notice else ""

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Правка — {escape(str(entry.get("name") or ""))}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
{_style()}
  body {{ max-width: 100%; }}
  input, textarea, select {{ width: 100%; }}
  textarea {{ font-family: monospace; }}
  button {{ margin-top: .8rem; padding: .35rem 1.1rem; }}
</style>
</head>
<body>
{_nav(can_edit=True)}
<h1>Правка: {escape(str(entry.get("name") or ""))}</h1>
{notice_html}
<div class="card">
<form method="post" action="/edit/{escape(str(entry.get("id") or ""), quote=True)}">
  {fields}
  <button type="submit" name="action" value="fetch">показать модели</button>
  <button type="submit" class="primary">сохранить</button>
  <a href="/">назад</a>
</form>
{oauth_html}
</div>
</body>
</html>"""


def oauth_page(
    provider_name: str,
    *,
    provider_id: str,
    user_code: str,
    verify_url: str,
    interval: int = 5,
    notice: str = "",
    can_edit: bool = False,
) -> str:
    """Ожидание device-flow GitHub: код, ссылка и кнопка «проверить».

    Форма шлёт POST на ``/oauth/<id>`` (action=check) — сервер один раз
    спросит GitHub, готов ли токен. Скрипт подставляет кнопку сам, пока
    страница открыта; без JS работает ручная проверка. ``interval`` —
    как часто GitHub просит опрашивать (подставляется в скрипт).
    ``verify_url`` — только http/https (адрес приходит от GitHub, но
    доверяем ему не больше, чем любому другому слову на странице).
    """
    notice_html = f'<p class="notice">{escape(notice)}</p>' if notice else ""
    link = _safe_url(verify_url) or "https://github.com/login/device"
    poll_ms = max(int(interval), 1) * 1000
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Вход через GitHub — {escape(provider_name)}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
{_style()}
  .code {{ font-size: 2rem; letter-spacing: .3rem; font-family: monospace;
    text-align: center; padding: 1rem; }}
  button {{ margin-top: .8rem; padding: .35rem 1.1rem; }}
</style>
</head>
<body>
{_nav(can_edit=can_edit)}
<h1>Вход через GitHub</h1>
{notice_html}
<div class="card">
<p>Откройте <a href="{escape(link, quote=True)}" target="_blank" rel="noopener">{escape(link)}</a>
и введите этот код:</p>
<p class="code">{escape(user_code)}</p>
<form id="oauth-check" method="post" action="/oauth/{escape(provider_id, quote=True)}">
  <input type="hidden" name="action" value="check">
  <button type="submit" class="primary">Проверить</button>
  <a href="/key/{escape(provider_id, quote=True)}">отмена</a>
</form>
<p class="role">Пока страница открыта, приставка спрашивает GitHub сама —
можно просто дождаться.</p>
<script>
setTimeout(function () {{
  var form = document.getElementById("oauth-check");
  if (form) form.submit();
}}, {poll_ms});
</script>
</div>
</body>
</html>"""


def add_page(
    *,
    values: dict | None = None,
    models: list[str] | None = None,
    models_error: str = "",
    is_admin: bool = False,
    notice: str = "",
) -> str:
    """Форма добавления провайдера: полная, как у правки, плюс ключ и модели.

    ``values`` — уже введённое (после «показать модели» ничего не теряется);
    ``models`` — список с сервера (тогда модель выбирается из списка);
    ``models_error`` — почему список не получился (подсказка человеку).
    """
    v = values or {}

    def field(label: str, name: str, *, placeholder: str = "", value: str = "") -> str:
        return (
            f'<label>{escape(label)}'
            f'<input name="{name}" value="{escape(str(value), quote=True)}" '
            f'placeholder="{escape(placeholder, quote=True)}"></label>'
        )

    if models is not None:
        options = list(models)
        current = str(v.get("model") or "")
        if current and current not in options:
            options.insert(0, current)
        opts = "\n    ".join(
            f'<option value="{escape(option, quote=True)}"'
            f'{" selected" if option == current else ""}>{escape(option)}</option>'
            for option in options
        )
        model_control = f'<label>Модель<select name="model">\n    {opts}\n  </select></label>'
    else:
        model_control = field(
            "Модель", "model", value=str(v.get("model") or ""), placeholder="qwen3:0.6b"
        )

    # Ключ возвращается в ту же форму после «показать модели»: иначе он
    # терялся между запросом списка и сохранением — провайдер добавлялся
    # без ключа, а повторный запрос списка жаловался «без ключа». В поле
    # type=password — на экране точки, в форме — ровно то, что ввёл человек.
    key_field = (
        "<label>API-ключ (останется на устройстве)"
        f'<input type="password" name="api_key" autocomplete="off" '
        f'value="{escape(str(v.get("api_key") or ""), quote=True)}" '
        'placeholder="sk-…"></label>'
    )
    notice_html = f'<p class="notice bad">{escape(notice)}</p>' if notice else ""

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Новый провайдер — AI-приставка</title>
<style>
{_style()}
  body {{ max-width: 100%; }}
  input, select, textarea {{ width: 100%; font-family: monospace; }}
  textarea {{ font-family: monospace; }}
  button {{ margin: .3rem .3rem 0 0; }}
</style>
</head>
{_nav(can_edit=is_admin)}
<h1>Новый провайдер</h1>
{notice_html}
<div class="card">
<form method="post" action="/add">
  {field("Название", "name", value=str(v.get("name") or ""), placeholder="Мой LMS")}
  {field("Адрес (базовый, без /chat/completions)", "url", value=str(v.get("url") or ""), placeholder="https://…/v1")}
  {key_field}
  {model_control}
  <button type="submit" name="action" value="fetch">показать модели</button>
  {field("Где получить ключ", "getApiKeyUrl", value=str(v.get("getApiKeyUrl") or ""))}
  <button type="submit" name="action" value="save" class="primary">сохранить</button>
  <a href="/">назад</a>
</form>
</div>
</body>
</html>"""


def login_page(notice: str = "", *, signup_open: bool = True) -> str:
    """Вход в приставку: логин/пароль аккаунта сервера каталога.

    Токен остаётся аварийным входом: если сервер каталога ляжет,
    админ всё равно попадёт на страницу. Регистрация — открытая,
    но ссылка показывается только когда сервер её вообще принимает.
    """
    notice_html = (
        f'<p class="notice bad">{escape(notice)}</p>' if notice else ""
    )
    signup_html = (
        '<p class="role">нет аккаунта? <a href="/signup">зарегистрироваться</a></p>'
        if signup_open
        else ""
    )
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Вход — AI-приставка</title>
<style>
{_style()}
  input {{ width: 100%; margin: .3rem 0; font-family: monospace; }}
  button {{ margin-top: .5rem; padding: .4rem 1.2rem; }}
  details {{ margin-top: 1.2rem; }}
</style>
</head>
<body>
<header class="topnav plain"><span class="brand">AI-приставка</span></header>
<h1>Вход</h1>
{notice_html}
<div class="card">
<form method="post" action="/login">
  <input name="email" type="email" placeholder="почта" autocomplete="username" autofocus>
  <input name="password" type="password" placeholder="пароль" autocomplete="current-password">
  <button type="submit" class="primary">войти</button>
</form>
{signup_html}
<details>
<summary class="role">вход по токену (аварийный)</summary>
<form method="post" action="/login">
  <input name="token" type="password" placeholder="токен приставки" autocomplete="off">
  <button type="submit">войти по токену</button>
</form>
<p class="role">Токен: <code>uv run python app.py --token</code>.</p>
</details>
</div>
</body>
</html>"""


def signup_page(notice: str = "") -> str:
    """Регистрация: открыта, без подтверждения почты — защита это модерация."""
    notice_html = (
        f'<p class="notice bad">{escape(notice)}</p>' if notice else ""
    )
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Регистрация — AI-приставка</title>
<style>
{_style()}
  input {{ width: 100%; margin: .3rem 0; font-family: monospace; }}
  button {{ margin-top: .5rem; padding: .4rem 1.2rem; }}
</style>
</head>
<body>
<header class="topnav plain"><a class="brand" href="/login">AI-приставка</a></header>
<h1>Регистрация</h1>
{notice_html}
<div class="card">
<form method="post" action="/signup">
  <input name="email" type="email" placeholder="почта" autocomplete="username" autofocus required>
  <input name="password" type="password" placeholder="пароль" autocomplete="new-password" required>
  <input name="password2" type="password" placeholder="ещё раз пароль" autocomplete="new-password" required>
  <button type="submit" class="primary">создать аккаунт</button>
  <a href="/login">назад ко входу</a>
</form>
</div>
<p class="role">Аккаунт открывает ваше пространство приставки: свои ключи,
свои переключатели, свои токены для приложений.</p>
</body>
</html>"""


def bugs_page(
    posts: list[dict],
    *,
    can_edit: bool = False,
    notice: str = "",
    notice_bad: bool = False,
) -> str:
    """Нашёл bug: общая доска сообщений — отдельный раздел для всех вошедших.

    Стоит отдельно от страницы токенов намеренно: та показывает
    конфиденциальные хвосты токенов, а баг-доска — разговор, который
    должен быть виден каждому участнику. Сообщения, комментарии и
    статусы хранятся на сервере (``storage/board.py``); статус ставится
    только с пояснением «почему». Время лежит в UTC, страница через
    ``data-ts`` показывает время зрителя.
    """
    from storage.board import STATUSES, current_status

    notice_html = (
        f'<p class="notice{" bad" if notice_bad else ""}">{escape(notice)}</p>'
        if notice
        else ""
    )
    badges = {
        "wait": "badge-off",
        "work": "badge-mine",
        "done": "badge-on",
        "wontfix": "badge-dead",
    }

    def stamp(value: object) -> str:
        """Момент времени: локальное время зрителя поверх UTC-значения."""
        text = str(value or "")
        if not text:
            return ""
        short = escape(text.replace("T", " ").replace("Z", ""))
        return f'<span class="role" data-ts="{escape(text, quote=True)}">{short}</span>'

    def status_options(current: str = "") -> str:
        rows = ['<option value="">— выбрать статус —</option>']
        for key, label in STATUSES.items():
            mark = " selected" if key == current else ""
            rows.append(f'<option value="{escape(key, quote=True)}"{mark}>{escape(label)}</option>')
        return "".join(rows)

    def post_html(post: dict) -> str:
        pid = escape(str(post.get("id") or ""), quote=True)
        cur = current_status(post)
        badge = ""
        why_html = ""
        if cur and str(cur.get("key") or "") in STATUSES:
            key = str(cur["key"])
            badge = f'<span class="badge {badges[key]}">{escape(STATUSES[key])}</span>'
            why_html = (
                f'<div class="bug-why">почему: {escape(str(cur.get("why") or ""))} '
                f'<span class="role">— {escape(str(cur.get("by") or "—"))}, '
                f'{stamp(cur.get("ts"))}</span></div>'
            )

        history = [h for h in (post.get("history") or []) if isinstance(h, dict)]
        history_html = ""
        if len(history) > 1:
            rows = []
            for item in history[:-1]:
                key = str(item.get("key") or "")
                label = STATUSES.get(key, key)
                rows.append(
                    f'<li><span class="badge {badges.get(key, "badge-off")}">{escape(label)}</span> '
                    f'{escape(str(item.get("why") or ""))} '
                    f'<span class="role">— {escape(str(item.get("by") or "—"))}, '
                    f'{stamp(item.get("ts"))}</span></li>'
                )
            history_html = (
                f'<details class="bug-details"><summary>история статусов ({len(history) - 1})</summary>'
                f'<ul class="bug-comments">{"".join(rows)}</ul></details>'
            )

        comments = [c for c in (post.get("comments") or []) if isinstance(c, dict)]
        comments_html = ""
        if comments:
            rows = [
                f'<li><b>{escape(str(c.get("by") or "—"))}</b> {stamp(c.get("ts"))}'
                f'<div class="c-text">{escape(str(c.get("text") or ""))}</div></li>'
                for c in comments
            ]
            comments_html = f'<ul class="bug-comments">{"".join(rows)}</ul>'

        drop_html = ""
        if can_edit:
            drop_html = (
                f'<form method="post" action="/bugs/drop"'
                f' onsubmit="return confirm(\'Удалить сообщение вместе с обсуждением?\')">'
                f'<input type="hidden" name="id" value="{pid}">'
                f'<button type="submit" class="ghost danger">удалить</button></form>'
            )

        return f"""<article class="bug">
<div class="bug-head"><b>{escape(str(post.get("author") or "—"))}</b>{stamp(post.get("ts"))}{badge}</div>
<div class="bug-text">{escape(str(post.get("text") or ""))}</div>
{why_html}{comments_html}{history_html}
<div class="bug-actions">
<details class="bug-details"><summary>комментировать</summary>
<form method="post" action="/bugs/comment">
<input type="hidden" name="id" value="{pid}">
<textarea name="text" rows="2" required placeholder="ваш комментарий…"></textarea>
<div class="bug-row"><button type="submit" class="primary">добавить</button></div>
</form></details>
<details class="bug-details"><summary>поставить статус</summary>
<form method="post" action="/bugs/status">
<input type="hidden" name="id" value="{pid}">
<select name="key" required aria-label="статус">{status_options(str((cur or {}).get("key") or ""))}</select>
<textarea name="why" rows="2" required placeholder="почему именно так: кто ставит — тот и объясняет"></textarea>
<div class="bug-row"><button type="submit" class="primary">поставить статус</button></div>
</form></details>
{drop_html}
</div>
</article>"""

    if posts:
        board_html = "\n".join(post_html(p) for p in posts)
    else:
        board_html = '<p class="role">пока ни одного сообщения — напишите, если что-то сломалось</p>'

    legend = "".join(
        f'<li><span class="badge {badges[key]}">{escape(label)}</span> {escape(_LEGEND[key])}</li>'
        for key, label in STATUSES.items()
    )

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Нашёл bug — AI-приставка</title>
<style>
{_style()}
  h2 {{ font-size: 1.05rem; margin-top: 1.6rem; }}
  .bug-new textarea {{ width: 100%; min-height: 4.5rem; font-family: inherit; }}
  .bug-row {{ display: flex; gap: .6rem; align-items: center; margin-top: .5rem;
    flex-wrap: wrap; }}
  #bug-chat {{ display: flex; flex-direction: column; gap: .7rem; margin-top: .7rem; }}
  .bug {{ background: var(--card); border: 1px solid var(--line); border-radius: .8rem;
    padding: .7rem .9rem; box-shadow: 0 1px 3px rgba(0, 0, 0, .05); }}
  .bug-head {{ display: flex; gap: .6rem; align-items: baseline; flex-wrap: wrap;
    font-size: .9rem; }}
  .bug-text {{ white-space: pre-wrap; overflow-wrap: anywhere; margin: .45rem 0 .2rem; }}
  .bug-why {{ background: var(--soft); border-radius: .5rem; padding: .35rem .6rem;
    font-size: .88rem; margin-top: .4rem; overflow-wrap: anywhere; }}
  .bug-comments {{ list-style: none; margin: .5rem 0 0; padding: 0;
    border-top: 1px dashed var(--line); }}
  .bug-comments li {{ padding: .4rem 0 .1rem .7rem; border-left: 2px solid var(--accent);
    margin-top: .4rem; font-size: .92rem; overflow-wrap: anywhere; }}
  .bug-comments li b {{ font-weight: 600; }}
  .c-text {{ white-space: pre-wrap; overflow-wrap: anywhere; }}
  .bug-actions {{ display: flex; gap: .5rem; margin-top: .55rem; flex-wrap: wrap;
    align-items: flex-start; }}
  /* <details> вместо скрипта: раскрывается нативно, форма видна всем. */
  .bug-details summary {{ display: inline-block; padding: .32rem .9rem;
    border: 1px solid var(--line); border-radius: 999px; cursor: pointer;
    font-size: .88rem; color: var(--muted); user-select: none; }}
  .bug-details summary:hover {{ border-color: var(--accent); color: var(--accent); }}
  .bug-details[open] summary {{ border-color: var(--accent); color: var(--accent); }}
  .bug-details form {{ display: flex; flex-direction: column; gap: .5rem;
    margin-top: .55rem; max-width: 34rem; }}
  .bug-details textarea {{ width: 100%; font-family: inherit; }}
  .bug-details select {{ width: 100%; }}
  .stat-expl li {{ margin: .2rem 0; }}
</style>
</head>
<body>
{_nav(can_edit=can_edit)}
<h1>Нашёл bug</h1>
{notice_html}
<p class="role">Нашли ошибку, зависание или странное поведение — напишите здесь.
Каждый участник видит доску и может добавить комментарий и поставить статус;
статус ставится только с пояснением «почему» — иначе приставка его не примет.</p>
<div class="bug-new">
<form method="post" action="/bugs/post">
<label class="role" for="bug-input">что случилось: где, что нажали, что ожидали увидеть</label>
<textarea id="bug-input" name="text" rows="4" required placeholder="например: нажал «остановить тест» — прогресс остался висеть на 0%"></textarea>
<div class="bug-row"><button type="submit" class="primary">Нашёл bug</button></div>
</form>
</div>
<h2>Обсуждение</h2>
<div id="bug-chat">{board_html}</div>
<h2>Статусы и их смысл</h2>
<ul class="role stat-expl">{legend}</ul>
{LOCAL_TIME_SCRIPT}
</body>
</html>"""


#: Пояснения к статусам — одной строкой на каждый, как они значатся на странице.
_LEGEND = {
    "wait": "баг принят, но его ещё не чинят.",
    "work": "уже правится; в пояснении — что именно делается.",
    "done": "исправлено; в пояснении — что сделано.",
    "wontfix": "так задумано или невозможно; в пояснении — причина.",
}


def messages_page(
    messages: list[dict],
    *,
    people: list[dict],
    viewer_id: str = "",
    can_edit: bool = False,
    mail_on: bool = True,
    box_owner: dict | None = None,
    notify_token: str = "",
    tg_chat: str = "",
    notice: str = "",
    notice_bad: bool = False,
) -> str:
    """Личная почта участников: ящик, написать любому, рассылка админа.

    Проще некуда: один ящик на человека, письмо — кому угодно, админу —
    рассылка всем разом. Человек может отказаться от получения (``mail_on``
    в профиле) — тогда его письма получает администратор (``orig_to``),
    ящик при этом не закрывается: полная власть остаётся у админа.
    ``box_owner`` — админ смотрит чужой ящик; показан заголовок и ссылка
    назад. ``notify_token``/``tg_chat`` — секция Telegram-уведомлений:
    токен задаёт только админ (на странице не показывается), chat_id —
    каждый сам. Время — UTC в ``data-ts``, страница покажет время зрителя.
    """
    notice_html = (
        f'<p class="notice{" bad" if notice_bad else ""}">{escape(notice)}</p>'
        if notice
        else ""
    )
    viewing_other = bool(box_owner and str(box_owner.get("id") or "") != str(viewer_id))

    # Адресаты: все, кроме меня. Админу — ещё и «все» (рассылка).
    options = []
    if can_edit:
        options.append('<option value="*">все участники (рассылка)</option>')
    options.append('<option value="">— выбрать адресата —</option>')
    for person in people:
        pid = str(person.get("id") or "")
        if pid == str(viewer_id):
            continue
        email = str(person.get("email") or pid)
        mark = " selected" if box_owner and pid == str(box_owner.get("id") or "") else ""
        options.append(f'<option value="{escape(pid, quote=True)}"{mark}>{escape(email)}</option>')

    # Уведомления в Telegram: токен бота (админ, не показываем) и свой chat_id.
    # Человек пишет боту сообщение и жмёт «узнать» — приставка читает
    # getUpdates: ни вебхука, ни стека на сервере не нужно.
    if viewing_other:
        notify_html = ""
    else:
        parts = []
        if can_edit:
            token_hint = (
                "текущий токен сохранён — вставьте новый, чтобы заменить"
                if notify_token
                else "токен от @BotFather, например 123456:ABC-DEF…"
            )
            parts.append(
                '<form method="post" action="/notify/token" class="tg-row">'
                '<input type="password" name="token" autocomplete="off" '
                f'placeholder="{token_hint}">'
                '<button type="submit" class="ghost">сохранить токен</button>'
                '<button type="submit" formaction="/notify/hello" class="ghost">'
                "проверить бота</button>"
                "</form>"
            )
            if notify_token:
                parts.append(
                    '<p class="role">Токен сохранён и нигде не показывается. '
                    "Отключить уведомления — "
                    '<button type="submit" form="tg-off" class="ghost danger">стереть токен</button>'
                    '<form id="tg-off" method="post" action="/notify/token">'
                    '<input type="hidden" name="token" value=""></form></p>'
                )
        else:
            parts.append(
                '<p class="role">Уведомления в Telegram: '
                + ("токен бота задан — осталось указать свой chat_id."
                   if notify_token
                   else "настроит администратор (токен бота).")
                + "</p>"
            )
        # Свой chat_id — каждый задаёт сам, даже если токена ещё нет.
        if tg_chat:
            status = (
                f'<p class="role">Ваш chat_id: <code>{escape(tg_chat)}</code> — '
                "уведомления приходят."
                + (" Можно проверить кнопкой ниже." if notify_token else "")
                + "</p>"
            )
        else:
            status = (
                '<p class="role">Chat_id не задан: напишите боту любое сообщение '
                'и нажмите «узнать chat_id» — или впишите вручную.</p>'
            )
        parts.append(status)
        if notify_token or can_edit:
            buttons = [
                '<button type="submit" formaction="/notify/detect" class="ghost">'
                "узнать chat_id</button>",
                '<button type="submit" class="primary">сохранить chat_id</button>',
            ]
            if tg_chat:
                buttons.append(
                    '<button type="submit" formaction="/notify/test" class="ghost">'
                    "отправить тест</button>"
                )
            parts.append(
                '<form method="post" action="/notify/chat" class="tg-row">'
                f'<input name="chat" value="{escape(tg_chat, quote=True)}" '
                'placeholder="chat_id, например 123456789">'
                + "".join(buttons)
                + "</form>"
            )
        notify_html = "".join(parts)

    # Своя почта: тумблер приёма. Чужой ящик админа переключать нечего.
    if viewing_other:
        switch_html = (
            '<p class="role">Чужой ящик: только чтение и удаление — '
            'полная власть администратора.</p>'
        )
    elif mail_on:
        switch_html = (
            '<form method="post" action="/messages/mail">'
            '<p class="role">Вы принимаете сообщения. Не хотите, чтобы вам писали — '
            'личные письма за вас будет получать администратор, '
            'рассылки придут как обычно.</p>'
            '<button type="submit" class="ghost">не получать сообщения</button>'
            "</form>"
        )
    else:
        switch_html = (
            '<form method="post" action="/messages/mail">'
            '<p class="role">Вы не принимаете личные сообщения — их получает '
            'администратор. Ящик открыт и никуда не делся.</p>'
            '<button type="submit" class="primary">снова получать сообщения</button>'
            "</form>"
        )

    def message_html(item: dict) -> str:
        sender = str(item.get("from_email") or "—")
        ts = str(item.get("ts") or "")
        short = escape(ts.replace("T", " ").replace("Z", ""))
        marker = ""
        if item.get("orig_to"):
            marker = (
                f'<span class="badge badge-mine">за адресата: '
                f'{escape(str(item["orig_to"]))}</span>'
            )
        drop = ""
        if can_edit:
            drop = (
                f'<form method="post" action="/messages/drop">'
                f'<input type="hidden" name="id" value="{escape(str(item.get("id") or ""), quote=True)}">'
                f'<button type="submit" class="ghost danger">удалить</button></form>'
            )
        return (
            f'<article class="mail">'
            f'<div class="mail-head"><b>{escape(sender)}</b> '
            f'<span class="role" data-ts="{escape(ts, quote=True)}">{short}</span>'
            f"{marker}{drop}</div>"
            f'<div class="mail-text">{escape(str(item.get("text") or ""))}</div>'
            f"</article>"
        )

    if messages:
        inbox_html = "\n".join(message_html(m) for m in messages)
    else:
        inbox_html = '<p class="role">ящик пуст — напишите кому-нибудь первым</p>'

    notify_section = (
        f"<h2>Уведомления в Telegram</h2>{notify_html}" if notify_html else ""
    )

    heading = "Сообщения"
    if viewing_other:
        heading += f": {escape(str(box_owner.get('email') or 'чужой ящик'))}"
    back = ""
    if viewing_other:
        back = '<p><a href="/messages">← мой ящик</a></p>'

    box_switch = ""
    if can_edit and people:
        others = [
            f'<option value="{escape(str(p.get("id") or ""), quote=True)}"'
            f'>{escape(str(p.get("email") or ""))}</option>'
            for p in people
            if str(p.get("id") or "") != str(viewer_id)
        ]
        if others:
            box_switch = (
                '<form method="get" action="/messages" class="box-switch">'
                '<span class="role">открыть ящик:</span> '
                f'<select name="box">{"".join(others)}</select>'
                '<button type="submit" class="ghost">открыть</button>'
                "</form>"
            )

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Сообщения — AI-приставка</title>
<style>
{_style()}
  h2 {{ font-size: 1.05rem; margin-top: 1.6rem; }}
  .compose textarea {{ width: 100%; min-height: 5rem; font-family: inherit; }}
  .compose select {{ width: 100%; margin-top: .4rem; }}
  .mail-row {{ display: flex; gap: .6rem; align-items: center; margin-top: .5rem;
    flex-wrap: wrap; }}
  .box-switch {{ display: flex; gap: .5rem; align-items: center; margin-top: .6rem;
    flex-wrap: wrap; }}
  .box-switch select {{ max-width: 20rem; }}
  .tg-row {{ display: flex; gap: .5rem; align-items: center; margin-top: .5rem;
    flex-wrap: wrap; }}
  .tg-row input {{ flex: 1 1 16rem; min-width: 12rem; }}
  #inbox {{ display: flex; flex-direction: column; gap: .7rem; margin-top: .7rem; }}
  .mail {{ background: var(--card); border: 1px solid var(--line); border-radius: .8rem;
    padding: .7rem .9rem; box-shadow: 0 1px 3px rgba(0, 0, 0, .05); }}
  .mail-head {{ display: flex; gap: .6rem; align-items: baseline; flex-wrap: wrap;
    font-size: .9rem; }}
  .mail-head form {{ margin-left: auto; }}
  .mail-text {{ white-space: pre-wrap; overflow-wrap: anywhere; margin-top: .4rem; }}
  .rules li {{ margin: .15rem 0; }}
</style>
</head>
<body>
{_nav(can_edit=can_edit)}
<h1>{heading}</h1>
{notice_html}
{back}
<h2>Написать</h2>
<form method="post" action="/messages/send" class="compose">
<select name="to" required>{"".join(options)}</select>
<textarea name="text" rows="4" required placeholder="текст сообщения…"></textarea>
<div class="mail-row">
<button type="submit" class="primary">отправить</button>
<span class="role">адресат получит письмо в свой ящик</span>
</div>
</form>
<h2>Приём сообщений</h2>
{switch_html}
{notify_section}
{box_switch}
<h2>Ящик</h2>
<div id="inbox">{inbox_html}</div>
<h2>Как это устроено</h2>
<ul class="role rules">
<li>У каждого свой ящик: вам могут написать все, и вы можете написать любому.</li>
<li>Администратор отправляет рассылку всем разом.</li>
<li>Отказ от приёма перенаправляет личные письма администратору — ящик
не закрывается, администратор читает и удаляет всё.</li>
</ul>
{LOCAL_TIME_SCRIPT}
</body>
</html>"""


def tokens_page(
    tokens: list[dict],
    *,
    app_address: str = "",
    api_on: bool = True,
    notice: str = "",
    can_edit: bool = False,
) -> str:
    """Приложения и токены: адрес для настройки, тумблер доступа, список."""
    notice_html = f'<p class="notice">{escape(notice)}</p>' if notice else ""
    if tokens:
        rows = "\n".join(
            f'<tr><td>{escape(str(item.get("name") or ""))}</td>'
            f'<td><code>…{escape(str(item.get("tail") or ""))}</code></td>'
            f'<td>{escape(str(item.get("created") or "")[:10])}</td>'
            f'<td><form method="post" action="/tokens/revoke">'
            f'<input type="hidden" name="tail" value="{escape(str(item.get("tail") or ""), quote=True)}">'
            f'<button type="submit" class="ghost danger">отозвать</button></form></td></tr>'
            for item in tokens
        )
    else:
        rows = '<tr><td colspan="4" class="empty">токенов пока нет</td></tr>'

    api_button = "включить доступ" if not api_on else "выключить доступ"
    api_status = (
        "доступ приложений включён — токены работают"
        if api_on
        else "доступ приложений ВЫКЛЮЧЕН — ни один токен не пройдёт"
    )
    address_html = ""
    copy_script = ""
    if app_address:
        copy_script = COPY_SCRIPT
        address_html = f"""<h2>Настройка в приложении</h2>
<p class="role">Тип endpoint — <b>OpenAI</b>. Адрес вставьте в поле
«custom base URL», а ключом будет ваш токен ниже.</p>
<p>{_copy_line(app_address, "v1big")}</p>"""

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Приложения и токены — AI-приставка</title>
<style>
{_style()}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ text-align: left; padding: .45rem .6rem; border-bottom: 1px solid var(--line); }}
  th {{ font-size: .8rem; color: var(--muted); }}
  h2 {{ font-size: 1.1rem; margin-top: 1.6rem; }}
  .api {{ background: var(--soft); padding: .6rem .8rem; border-radius: .6rem;
    display: flex; justify-content: space-between; align-items: center; gap: 1rem;
    flex-wrap: wrap; }}
  .api.off {{ background: var(--bad-soft); }}
  .addr code {{ font-family: monospace; user-select: all; background: var(--soft);
    padding: .15rem .4rem; border-radius: .35rem; }}
</style>
</head>
<body>
{_nav(can_edit=can_edit)}
<h1>Приложения и токены</h1>
{notice_html}
{address_html}
<div class="api{" off" if not api_on else ""}">
  <span>{escape(api_status)}</span>
  <form method="post" action="/tokens/api">
    <button type="submit" class="ghost">{api_button}</button>
  </form>
</div>
<h2>Мои токены</h2>
<table>
<tr><th>название</th><th>хвост</th><th>создан</th><th></th></tr>
{rows}
</table>
<form method="post" action="/tokens/create">
  <input name="name" placeholder="название: телефон, ноутбук…" required>
  <button type="submit">создать токен</button>
</form>
<p class="role">Токен показывается один раз при создании — скопируйте.
Отозванный токен перестаёт работать мгновенно.</p>
{copy_script}
</body>
</html>"""


def stats_page(
    rows: list[dict], *, can_edit: bool = False, notice: str = "",
    penalized: list[dict] | None = None,
    whitelist: dict | None = None, greylist: str = "", blacklist: str = "",
    grey_rows: list[dict] | None = None,
    novision: str = "", notools: str = "", paid: str = "",
    code_lists: dict[str, str] | None = None,
) -> str:
    """Статистика по моделям: что есть у модели и как она служит.

    Колонки ровно те, что нужны для выбора: модель (с провайдером,
    контекстом и способностями под ней), сколько запросов, когда
    последний и какой процент удачных. Строки приходят собранными
    (webui), здесь только разметка.

    Админу внизу — списки приставки: белый **разложен по группам**
    способностей (text / vision / tools / vision-tools), серый
    (персональный) с **таймером пробуждения** и чёрный.
    """
    notice_html = f'<p class="notice">{escape(notice)}</p>' if notice else ""
    whitelist = whitelist or {}
    code_lists = code_lists or {}

    def code_lists_html() -> str:
        """Кодовые слова: имя = модель для клиента, значение — список моделей.

        Админ создаёт/правит/удаляет списки. Форма сохранения — POST
        на /stats/codes/save, удаление — /stats/codes/drop. Список моделей
        — по одной на строку, порядок = приоритет.
        """
        if not can_edit:
            return ""

        def card(name: str, text: str) -> str:
            return (
                f'<div class="listcard"><h3>{escape(name)}</h3>'
                f'<p class="role">модели по одной на строку, сверху — главные</p>'
                f'<form method="post" action="/stats/codes/save/{escape(name, quote=True)}">'
                f'<textarea name="text" rows="6" spellcheck="false" '
                f'placeholder="провайдер/модель">{escape(text)}</textarea>'
                f'<button type="submit" class="primary">сохранить</button>'
                f"</form>"
                f'<form method="post" action="/stats/codes/drop/{escape(name, quote=True)}" '
                f'style="margin-top:.4rem">'
                f'<button type="submit">удалить список</button></form>'
                f"</div>"
            )

        cards = "".join(card(name, text) for name, text in sorted(code_lists.items()))
        create = (
            '<div class="listcard"><h3>Новый список</h3>'
            '<p class="role">имя — кодовое слово (латиница, цифры, дефис); '
            'клиент увидит его как модель</p>'
            '<form method="post" action="/stats/codes/create">'
            '<input name="name" placeholder="vision_tools_1m" '
            'pattern="[a-z0-9][a-z0-9_-]{0,63}" required>'
            '<textarea name="text" rows="4" spellcheck="false" '
            'placeholder="провайдер/модель"></textarea>'
            '<button type="submit" class="primary">создать</button>'
            "</form></div>"
        )
        return (
            '<h2>Кодовые слова (списки моделей)</h2>'
            '<p class="role">Это то, что клиент видит в списке моделей как «модель». '
            'Запрос на кодовое слово идёт по моделям списка сверху вниз. '
            'Порядок строк = приоритет.</p>'
            f'<div class="lists">{cards}{create}</div>'
        )

    def grey_timer_html() -> str:
        """Таблица серого списка: провайдер спит, видно сколько осталось.

        Провайдер отстранён целиком (со всеми моделями); остаток
        пересчитывается на каждый рендер, страница обновляется сама.
        """
        if not grey_rows:
            return ""
        lines = [
            '<h3 class="listgroup">Серый список — таймер пробуждения</h3>',
            "<table class=\"greytimer\">",
            "<tr><th>провайдер</th><th>проснётся через</th>"
            "<th>причина</th><th>цикл</th></tr>",
        ]
        for row in grey_rows:
            minutes = int(row.get("minutes", 0))
            if minutes > 0:
                hours, mins = divmod(minutes, 60)
                left = f"{hours} ч {mins} мин" if hours else f"{mins} мин"
            else:
                left = "уже проснулся"
            lines.append(
                f'<tr><td class="model">{escape(str(row.get("id") or "—"))}</td>'
                f"<td>{left}</td>"
                f'<td class="role">{escape(str(row.get("reason") or ""))}</td>'
                f"<td>{int(row.get('cycle', 1))}</td></tr>"
            )
        lines.append("</table>")
        return "\n".join(lines)

    def paused_html() -> str:
        """Секция «на паузе» — только админу, кнопка «вернуть» рядом."""
        if not can_edit or not penalized:
            return ""
        lines = [
            "<h2>На паузе</h2>",
            "<p class=\"role\">Модели с штрафами за поломки. Пауза снимается "
            "сама после нормального ответа — или кнопкой ниже.</p>",
            "<table>",
            "<tr><th>провайдер/модель</th><th>штрафов</th><th>блокировок</th>"
            "<th>вернётся через</th><th></th></tr>",
        ]
        for row in penalized:
            left = int(row.get("until", 0))
            when = f"{left // 60} мин" if left else "—"
            lines.append(
                f'<tr><td class="model">{escape(str(row.get("key") or "—"))}</td>'
                f'<td>{int(row.get("fails", 0))}</td>'
                f'<td>{int(row.get("blocks", 0))}</td>'
                f'<td>{when}</td>'
                f'<td><form method="post" action="/stats/release" style="display:inline">'
                f'<input type="hidden" name="key" value="{escape(str(row.get("key") or ""))}">'
                f'<button type="submit">вернуть</button></form></td></tr>'
            )
        lines.append("</table>")
        return "\n".join(lines)

    def lists_html() -> str:
        """Списки приставки: белый — четыре секции по группам, серый и чёрный.

        Каждый список — форма с textarea: правка руками, сохранение —
        POST, файл перечитывается мгновенно.
        """
        if not can_edit:
            return ""

        def one(filename: str, title: str, hint: str, text: str) -> str:
            return (
                f'<div class="listcard"><h3>{escape(title)}</h3>'
                f'<p class="role">{escape(hint)}</p>'
                f'<form method="post" action="/stats/save/{escape(filename, quote=True)}">'
                f'<textarea name="text" rows="6" spellcheck="false" '
                f'placeholder="имя на строке, # — комментарий">{escape(text)}</textarea>'
                f'<button type="submit" class="primary">сохранить</button>'
                f"</form></div>"
            )

        group_titles = {
            "text": "Только текст",
            "vision": "Vision (с картинками)",
            "tools": "Tools (с инструментами)",
            "vision-tools": "Vision + Tools",
        }
        group_hints = {
            "text": "для role-based задач и дешёвого текста",
            "vision": "отвечают на картинки",
            "tools": "вызывают инструменты, coding",
            "vision-tools": "и картинки, и инструменты",
        }
        white_cards = "".join(
            one(
                f"whitelist-{group}.txt",
                group_titles[group],
                group_hints[group],
                whitelist.get(group, ""),
            )
            for group in ("text", "vision", "tools", "vision-tools")
            if group in whitelist
        )
        if not white_cards:
            white_cards = one("whitelist-text.txt", "Белый список", "подтверждённые успехом", "")

        return (
            '<h2>Списки приставки</h2>'
            '<p class="role">Имя на строке, <code>#</code> — комментарий; правка '
            'файла действует сразу, без перезапуска. Заполняются автоматикой, '
            'руки нужны не всегда. Цепочка: белый (своей группы первым) → '
            'неизвестный → проснувшийся серый → чёрный.</p>'
            '<h3 class="listgroup">Белый список — по группам</h3>'
            '<div class="lists">'
            + white_cards
            + "</div>"
            + grey_timer_html()
            + '<div class="lists">'
            + one(
                "greylist.txt",
                "Серый список (ваш)",
                "спит сутки после мягкого отказа (лимит/кредиты) — провайдер "
                "целиком, со всеми моделями; просыпается попробовать себя; "
                "3 цикла подряд — в чёрный",
                greylist,
            )
            + one(
                "blacklist.txt",
                "Чёрный список",
                "после жёсткого отказа; только последний шанс, когда больше некому",
                blacklist,
            )
            + one(
                "novision.txt",
                "Доказано без vision",
                "модели, доказавшие, что не умеют картинки — при запросе "
                "с картинкой приставка их пропускает",
                novision,
            )
            + one(
                "notools.txt",
                "Доказано без tools",
                "модели, доказавшие, что не умеют инструменты — при запросе "
                "с tools приставка их пропускает",
                notools,
            )
            + one(
                "paid.txt",
                "Доказано платные",
                "модели, вернувшие 402 при тесте — при маршрутизации идут "
                "последними (бесплатные предпочтительнее), но не исключаются",
                paid,
            )
            + "</div>"
        )

    def cell(row: dict) -> str:
        requests = int(row.get("requests", 0))
        fails = int(row.get("fails", 0))
        # Процент удачных: зелёный, если всё хорошо; красный — заметно хуже.
        if requests:
            ok_pct = round((requests - fails) * 100 / requests)
            cls = "ok-good" if ok_pct >= 90 else ("ok-bad" if ok_pct < 70 else "ok-warn")
            pct = f'<span class="{cls}">{ok_pct}%</span>'
            if fails:
                pct += f' <span class="role">({fails} ✗)</span>'
        else:
            pct = '<span class="role">—</span>'
        facts = " · ".join(
            escape(str(row[field]))
            for field in ("ctx", "caps")
            if row.get(field)
        )
        facts_html = f'<div class="facts">{facts}</div>' if facts else ""
        raw_last = str(row.get("last") or "")
        if raw_last:
            short = escape(raw_last.replace("T", " ").replace("Z", "")[:16])
            last = (
                f'<span data-ts="{escape(raw_last, quote=True)}">{short}</span>'
            )
        else:
            last = "—"
        model = str(row.get("model") or "—")
        return (
            f'<tr><td class="model">'
            f'<span class="mname" title="{escape(model, quote=True)}">{escape(model)}</span>'
            f'<div class="role">{escape(str(row.get("provider") or ""))}</div>{facts_html}</td>'
            f'<td class="num">{requests}</td>'
            f'<td class="num">{last}</td>'
            f"<td>{pct}</td></tr>"
        )

    body = "\n".join(cell(row) for row in rows) or (
        '<tr><td colspan="4" class="empty">пока нет ни моделей, ни запросов</td></tr>'
    )
    total_requests = sum(int(row.get("requests", 0)) for row in rows)
    total_fails = sum(int(row.get("fails", 0)) for row in rows)
    success_rate = (
        f"{round((total_requests - total_fails) * 100 / total_requests)}%"
        if total_requests else "—"
    )
    paused_count = len(penalized or [])
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>Статистика — AI-приставка</title>
<style>
{_style()}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ text-align: left; padding: .45rem .6rem; border-bottom: 1px solid var(--line); }}
  th {{ font-size: .8rem; color: var(--muted); }}
  .stats-kpis {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: .8rem; margin: 1.1rem 0; }}
  .stats-kpi {{ min-width: 0; padding: 1rem 1.1rem; background: var(--card);
    border: 1px solid var(--line); border-radius: 1rem;
    box-shadow: 0 6px 20px rgba(15, 23, 42, .035); }}
  .stats-kpi-label {{ color: var(--muted); font-size: .7rem; font-weight: 700;
    letter-spacing: .07em; text-transform: uppercase; }}
  .stats-kpi-value {{ display: block; margin-top: .25rem; font-size: 1.55rem;
    line-height: 1.2; font-weight: 800; letter-spacing: -.03em;
    font-variant-numeric: tabular-nums; }}
  .stats-kpi-detail {{ display: block; margin-top: .25rem; color: var(--muted); font-size: .75rem; }}
  @media (max-width: 42rem) {{ .stats-kpis {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
  td.num {{ font-family: monospace; white-space: nowrap; }}
  /* Длинное имя модели — в одну строку с многоточием; полное видно
     по наведению (title). Иначе on-phone строка растёт в высоту. */
  td.model {{ max-width: 18rem; }}
  td.model .mname {{ display: block; max-width: 100%; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap; }}
  td.model .facts {{ white-space: normal; }}
  .ok-good {{ color: var(--accent); font-weight: 600; }}
  .ok-warn {{ color: #b08c14; font-weight: 600; }}
  .ok-bad {{ color: var(--bad); font-weight: 600; }}
  h2 {{ margin-bottom: .2rem; }}
  .listgroup {{ margin: 1.2rem 0 .4rem; font-size: .95rem; color: var(--muted);
    text-transform: uppercase; letter-spacing: .04em; }}
  .lists {{ display: flex; gap: 1rem; flex-wrap: wrap; }}
  .listcard {{ flex: 1 1 18rem; background: var(--card); border: 1px solid var(--line);
    border-radius: 1rem; padding: .7rem .9rem; box-shadow: 0 1px 3px rgba(0, 0, 0, .05); }}
  .listcard h3 {{ margin: 0 0 .1rem; font-size: .95rem; }}
  .listcard textarea {{ width: 100%; font-family: ui-monospace, monospace;
    font-size: .82rem; border: 1px solid var(--line); border-radius: .5rem;
    background: var(--bg); color: var(--text); padding: .5rem; resize: vertical; }}
  .listcard button {{ margin-top: .5rem; }}
  .listcard pre.listfile {{ background: var(--bg); border: 1px solid var(--line);
    border-radius: .6rem; padding: .5rem .7rem; font-size: .85rem;
    white-space: pre-wrap; word-break: break-word; margin: .4rem 0 0; }}
  /* Таблица таймера серого списка: кто спит и сколько осталось. */
  table.greytimer {{ margin: .6rem 0 1rem; border-collapse: collapse; width: 100%; }}
  table.greytimer th, table.greytimer td {{ text-align: left; padding: .35rem .55rem;
    border-bottom: 1px solid var(--line); font-size: .88rem; }}
  table.greytimer th {{ font-size: .78rem; color: var(--muted); }}
</style>
</head>
<body>
{_nav(can_edit=can_edit)}
<h1>Статистика</h1>
{notice_html}
<section class="stats-kpis" aria-label="Сводка статистики">
  <div class="stats-kpi"><span class="stats-kpi-label">Модели</span>
    <b class="stats-kpi-value">{len(rows)}</b>
    <span class="stats-kpi-detail">в каталоге</span></div>
  <div class="stats-kpi"><span class="stats-kpi-label">Запросы</span>
    <b class="stats-kpi-value">{total_requests}</b>
    <span class="stats-kpi-detail">по всем моделям</span></div>
  <div class="stats-kpi"><span class="stats-kpi-label">Успешность</span>
    <b class="stats-kpi-value">{success_rate}</b>
    <span class="stats-kpi-detail">{total_fails} сбоев</span></div>
  <div class="stats-kpi"><span class="stats-kpi-label">На паузе</span>
    <b class="stats-kpi-value">{paused_count}</b>
    <span class="stats-kpi-detail">моделей с ограничением</span></div>
</section>
<div class="card">
<table>
<tr><th>модель</th><th>запросов</th><th>последний</th><th>удачных</th></tr>
{body}
</table>
</div>
{lists_html()}
{code_lists_html()}
{paused_html()}
{LOCAL_TIME_SCRIPT}
</body>
</html>"""


def token_created_page(token: str, name: str, *, can_edit: bool = False) -> str:
    """Один раз показать новый токен: скопируй и уходи."""
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Новый токен — AI-приставка</title>
<style>
{_style()}
  input {{ width: 100%; font-family: monospace; }}
</style>
</head>
{_nav(can_edit=can_edit)}
<h1>Новый токен: {escape(name)}</h1>
<div class="card">
<input readonly value="{escape(token, quote=True)}" onclick="this.select()">
<p class="role">Скопируйте сейчас: показывается один раз. В приложении укажите
адрес приставки <code>/v1</code> и этот токен в Authorization.</p>
</div>
<p><a href="/tokens">← к токенам</a></p>
</body>
</html>"""


def sql_playground_page(
    *,
    schema: dict | None = None,
    query: str = "",
    result: dict | None = None,
    error: str = "",
    include_data: bool = False,
    configured: bool = True,
) -> str:
    """Админская схема SQLite, предпросмотр записей и read-only SQL."""
    schema = schema or {"tables": [], "relations": []}
    tables = schema.get("tables") or []
    relations = schema.get("relations") or []
    notice_html = (
        f'<p class="notice bad">{escape(error)}</p>' if error else ""
    )
    relation_html = "".join(
        f'<div class="relation"><b>{escape(str(item.get("source") or ""))}'
        f'.{escape(str(item.get("field") or ""))}</b>'
        f'<span class="relation-kind">{escape(str(item.get("cardinality") or ""))}</span>'
        f'<span>→ {escape(str(item.get("target") or ""))}</span></div>'
        for item in relations
    ) or '<p class="role">Связи не обнаружены.</p>'

    def render_table(table: dict) -> str:
        name = str(table.get("name") or "")
        columns = table.get("columns") or []
        fields = "".join(
            "<tr>"
            f'<td><code>{escape(str(column.get("name") or ""))}</code></td>'
            f'<td>{escape(str(column.get("type") or "—"))}</td>'
            f'<td>{"PK" if column.get("primary_key") else ""}</td>'
            f'<td>{"да" if column.get("not_null") else "нет"}</td>'
            "</tr>"
            for column in columns
        )
        sample_html = ""
        if include_data:
            sample_columns = table.get("sample_columns") or []
            sample_rows = table.get("sample_rows") or []
            sample_html = (
                '<div class="sql-scroll"><table class="sql-data"><thead><tr>'
                + "".join(f"<th>{escape(str(column))}</th>" for column in sample_columns)
                + "</tr></thead><tbody>"
                + "".join(
                    "<tr>"
                    + "".join(
                        f"<td>{escape(str(value))}</td>" for value in row
                    )
                    + "</tr>"
                    for row in sample_rows
                )
                + "</tbody></table></div>"
                + ("" if sample_rows else '<p class="role">Таблица пуста.</p>')
                + (
                    '<p class="role">Предпросмотр ограничен.</p>'
                    if table.get("sample_truncated")
                    else ""
                )
            )
        return (
            '<details class="sql-table">'
            f"<summary><strong>{escape(name)}</strong>"
            f'<span class="role">{len(columns)} полей · {escape(str(table.get("type") or "table"))}</span>'
            "</summary>"
            '<div class="sql-scroll"><table class="sql-fields"><thead><tr>'
            "<th>поле</th><th>тип</th><th>ключ</th><th>обязательно</th>"
            f"</tr></thead><tbody>{fields}</tbody></table></div>{sample_html}</details>"
        )

    schema_html = "".join(render_table(table) for table in tables)
    if not schema_html:
        schema_html = '<p class="role">Схема пока недоступна.</p>'

    result_html = ""
    if result is not None:
        columns = result.get("columns") or []
        rows = result.get("rows") or []
        result_html = (
            '<section class="card sql-result"><h2>Результат запроса</h2>'
            f'<p class="role">{len(rows)} строк'
            f'{" · показана первая часть" if result.get("truncated") else ""}</p>'
            '<div class="sql-scroll"><table><thead><tr>'
            + "".join(f"<th>{escape(str(column))}</th>" for column in columns)
            + "</tr></thead><tbody>"
            + "".join(
                "<tr>"
                + "".join(f"<td>{escape(str(value))}</td>" for value in row)
                + "</tr>"
                for row in rows
            )
            + "</tbody></table></div>"
            + (
                '<p class="role">'
                + (
                    "Превышен лимит объёма ответа."
                    if result.get("truncated")
                    else "Запрос не вернул строк."
                )
                + "</p>"
                if not rows
                else ""
            )
            + "</section>"
        )

    query_form = (
        '<form method="post" action="/sql/run" class="sql-query">'
        '<label for="sql-query">SQL-запрос</label>'
        f'<textarea id="sql-query" name="query" rows="9" spellcheck="false" '
        f'placeholder="SELECT name, type FROM _collections ORDER BY name;">'
        f"{escape(query)}</textarea>"
        '<label class="sql-check"><input type="checkbox" name="include_data" '
        f'value="1"{" checked" if include_data else ""}>'
        "Показывать до 5 примеров записей в таблицах</label>"
        f'<button type="submit" class="primary"{" disabled" if not configured else ""}>'
        "выполнить SELECT</button>"
        "</form>"
        if configured
        else '<p class="role">Задайте AI_BRIDGE_SQLITE_PATH в окружении службы, '
        "указав путь к data.db PocketBase. У пользователя службы должны быть "
        "права чтения файла базы и файлов WAL.</p>"
    )
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>SQL Playground — AI Bridge</title>
{_style()}
<style>
  .sql-hero {{ padding: 1.25rem 1.4rem; margin: 1.2rem 0; border-radius: 1.2rem;
    color: #fff; background: linear-gradient(120deg, #0f766e, #0f172a 75%); }}
  .sql-hero h1 {{ margin: 0 0 .35rem; color: #fff; }}
  .sql-hero p {{ margin: 0; color: #dbeafe; }}
  .sql-layout {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(19rem, .8fr);
    gap: 1rem; align-items: start; }}
  .sql-query {{ display: grid; gap: .65rem; }}
  .sql-query textarea {{ width: 100%; font: .9rem/1.5 ui-monospace, SFMono-Regular, Consolas, monospace; }}
  .sql-check {{ display: flex; gap: .5rem; align-items: center; color: var(--muted); font-size: .88rem; }}
  .sql-check input {{ accent-color: var(--accent); }}
  .sql-relations {{ display: grid; gap: .45rem; }}
  .relation {{ display: flex; align-items: center; flex-wrap: wrap; gap: .55rem;
    padding: .65rem .75rem; border: 1px solid var(--line); border-radius: .75rem;
    background: var(--card); }}
  .relation-kind {{ border-radius: 999px; padding: .1rem .5rem; color: var(--accent-deep);
    background: var(--accent-soft); font-size: .78rem; font-weight: 700; }}
  .sql-tables {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 22rem), 1fr));
    gap: .65rem; margin-top: .75rem; }}
  .sql-table {{ min-width: 0; border: 1px solid var(--line); border-radius: .85rem;
    background: var(--card); overflow: hidden; }}
  .sql-table summary {{ display: flex; justify-content: space-between; gap: .75rem;
    align-items: center; cursor: pointer; padding: .75rem .85rem; }}
  .sql-table summary .role {{ white-space: nowrap; }}
  .sql-scroll {{ overflow-x: auto; }}
  .sql-fields, .sql-data {{ width: 100%; border-collapse: collapse; font-size: .83rem; }}
  .sql-fields th, .sql-fields td, .sql-data th, .sql-data td {{
    padding: .38rem .55rem; text-align: left; border-top: 1px solid var(--line); }}
  .sql-fields th, .sql-data th {{ color: var(--muted); font-weight: 650; }}
  .sql-data {{ margin-bottom: .35rem; }}
  .sql-result {{ margin-top: 1rem; }}
  .sql-result table {{ min-width: 100%; border-collapse: collapse; font-size: .84rem; }}
  .sql-result th, .sql-result td {{ padding: .42rem .6rem; text-align: left;
    border-bottom: 1px solid var(--line); overflow-wrap: anywhere; }}
  @media (max-width: 48rem) {{ .sql-layout {{ grid-template-columns: 1fr; }}
    .sql-table summary {{ align-items: flex-start; }} }}
</style></head><body>
{_nav(can_edit=True)}
<main>
  <section class="sql-hero"><h1>SQL Playground</h1>
    <p>Только администратору · схема PocketBase и SQL-запросы только для чтения.</p></section>
  {notice_html}
  <div class="sql-layout">
    <section class="card"><h2>SQL-запрос</h2>
      <p class="role">Разрешены SELECT и WITH. Запрос ограничен по времени и объёму ответа.</p>
      {query_form}
    </section>
    <section class="card"><h2>Связи коллекций</h2>
      <p class="role">N:1 — запись ссылается на одну запись; N:M — поле допускает несколько связей.</p>
      <div class="sql-relations">{relation_html}</div>
    </section>
  </div>
  <section><h2>Схема базы · {len(tables)} объектов</h2>
    <p class="role">Раскройте таблицу, чтобы увидеть поля{" и примеры записей" if include_data else ""}.</p>
    <div class="sql-tables">{schema_html}</div>
  </section>
  {result_html}
</main></body></html>"""


def _log_row(item: dict) -> str:
    """Строка журнала: тип, статус, событие плюс сворачиваемые тела.

    Тела показываются целиком (без обрезки) в тёмной моноширинной
    карточке, но **свёрнуты по умолчанию** — журнал читается как
    журнал, а не как стена ответов; раскрываются по клику. `<details>` —
    чистый HTML, работает без JavaScript. Base64-вставки вырезаются
    ещё при записи события (service/eventlog.py).
    Статус (HTTP 200/402/500) — цветной бейдж рядом с типом, чтобы
    у решения было видно основание.
    """
    kind = str(item.get("kind") or "")
    kind_classes = {
        "чат": "badge-on", "тест": "badge-on", "модель": "badge-mine",
        "ключ": "badge-mine", "токен": "badge-mine", "пауза": "badge-dead",
        "способности": "badge-mine", "списки": "badge-off",
        "решение": "badge-mine",
    }
    kind_html = (
        f'<span class="badge {kind_classes.get(kind, "badge-off")}">{escape(kind)}</span>'
    )
    status = item.get("status")
    status_html = ""
    if isinstance(status, int) and status:
        status_cls = "badge-on" if status < 400 else "badge-dead"
        status_html = f' <span class="badge {status_cls}">{status}</span>'
    bodies = ""
    for key, label in (("req", "Тело запроса"), ("resp", "Тело ответа")):
        value = str(item.get(key) or "")
        if value:
            bodies += (
                f'<div class="payload-label">→ {escape(label)}:</div>'
                f'<pre class="payload-body">{escape(value)}</pre>'
            )
    if bodies:
        # Стабильный ключ тела — по нему автообновление восстанавливает
        # раскрытые блоки после перезагрузки страницы, даже если сверху
        # появились новые события (индексы при этом сдвигаются).
        body_key = str(item.get("id") or "")
        if not body_key:
            body_key = (
                f"{item.get('ts') or ''}|{item.get('kind') or ''}|"
                f"{item.get('user') or ''}"
            )
        # По умолчанию свёрнуто: тело есть, но журнал не раздувает вид.
        bodies = (
            '<details class="payload"><summary>показать тело</summary>'
            f'<div class="payload-card" data-key="{escape(body_key, quote=True)}">'
            f'{bodies}</div></details>'
        )
    provider = str(item.get("provider") or "")
    provider_html = (
        f'<span class="role"> · {escape(provider)}</span>' if provider else ""
    )
    # Доп. поля события (путь страницы, адрес провайдера, модель…) —
    # мелкими подписями под текстом: из строки должно быть понятно всё.
    known = {"ts", "kind", "user", "text", "req", "resp", "status", "provider"}
    extras = [
        f'<span class="meta">{escape(str(key))}: {escape(_short(value))}</span>'
        for key, value in item.items()
        if key not in known and value not in (None, "")
    ]
    extras_html = f'<div class="meta-line">{" ".join(extras)}</div>' if extras else ""
    return (
        f'<tr><td class="ts" data-ts="{escape(str(item.get("ts") or ""), quote=True)}">'
        f'{escape(str(item.get("ts") or "").replace("T", " ").replace("Z", ""))}</td>'
        f'<td class="kind">{kind_html}{status_html}</td>'
        f'<td class="user" title="{escape(str(item.get("user") or ""), quote=True)}">'
        f'{escape(str(item.get("user") or ""))}</td>'
        f'<td class="text">{escape(str(item.get("text") or ""))}{provider_html}'
        f'{extras_html}{bodies}</td></tr>'
    )


def _short(value: object, limit: int = 120) -> str:
    """Значение доп. поля — компактно, длинное с многоточием."""
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _log_summary(events: list[dict]) -> str:
    """Сводка сверху журнала: что за события лежат и с кем.

    Считается по тому, что показано (последние события журнала) —
    честнее выдумывать период. Пустой журнал — пустая строка.
    """
    if not events:
        return ""
    kinds: dict[str, int] = {}
    people: dict[str, int] = {}
    for item in events:
        kind = str(item.get("kind") or "?")
        kinds[kind] = kinds.get(kind, 0) + 1
        user = str(item.get("user") or "—").strip() or "—"
        people[user] = people.get(user, 0) + 1
    kind_chips = " ".join(
        f'<span class="badge badge-off">{escape(name)} {count}</span>'
        for name, count in sorted(kinds.items(), key=lambda kv: -kv[1])
    )
    top_people = sorted(people.items(), key=lambda kv: -kv[1])[:8]
    people_chips = " ".join(
        f'<span class="badge badge-mine">{escape(name)} {count}</span>'
        for name, count in top_people
    )
    span = (
        f'{escape(str(events[-1].get("ts") or "").replace("T", " ").replace("Z", ""))}'
        f' — {escape(str(events[0].get("ts") or "").replace("T", " ").replace("Z", ""))}'
    )
    return (
        f'<div class="summary">'
        f'<p class="role">Показано событий: <b>{len(events)}</b> · период (UTC): {span}</p>'
        f'<p>{kind_chips}</p>'
        f'<p class="role">С кем:</p><p>{people_chips}</p>'
        f"</div>"
    )


def logs_page(
    events: list[dict],
    *,
    notice: str = "",
) -> str:
    """Журнал событий: таблица, новые сверху, тела — сворачиванные.

    Тела запросов/ответов лежат в тёмной карточке **свёрнутыми по
    умолчанию** (`<details>`, работает и без JavaScript) — читать
    журнал не мешают, открываются по клику. Тела пишутся **всегда**
    (переключателя нет, журнал видит только администратор), но
    base64-вставки вырезаются при записи. Автообновление раз в
    10 секунд пропускает моменты, когда открыто тело или человек
    занят страницей — иначе чтение рвёт прыжком.
    """
    rows = "\n".join(_log_row(item) for item in events) or (
        '<tr><td colspan="4" class="empty">событий пока нет</td></tr>'
    )
    notice_html = f'<p class="notice">{escape(notice)}</p>' if notice else ""
    summary_html = _log_summary(events)
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Журнал — AI-приставка</title>
<style>
{_style()}
  table {{ border-collapse: collapse; width: 100%; font-size: .9rem; }}
  th, td {{ text-align: left; padding: .4rem .5rem; border-bottom: 1px solid var(--line); }}
  th {{ font-size: .8rem; color: var(--muted); }}
  td.ts {{ white-space: nowrap; font-family: monospace; }}
  td.kind {{ white-space: nowrap; }}
  /* Email — в одну строку с многоточием, полное значение по наведению:
     иначе адрес рвётся посреди слова и строка вырастает в три ряда. */
  td.user {{ white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    max-width: 13rem; }}
  td.text {{ overflow-wrap: break-word; }}
  /* Сводка сверху: период, виды событий, участники. */
  .summary {{ background: var(--soft); border-radius: .7rem;
    padding: .55rem .8rem; margin-bottom: .8rem; }}
  .summary p {{ margin: .25rem 0; }}
  .summary .badge {{ margin-right: .3rem; }}
  /* Доп. поля события — мелкими подписями под текстом. */
  .meta-line {{ margin-top: .2rem; }}
  .meta {{ display: inline-block; font-size: .76rem; color: var(--muted);
    background: var(--card); border: 1px solid var(--line); border-radius: .4rem;
    padding: .05rem .4rem; margin: .1rem .25rem .1rem 0; }}
  /* На телефоне строка журнала складывается в карточку — не лезет за
     экран и не требует горизонтального скролла. */
  @media (max-width: 52rem) {{
    table thead {{ display: none; }}
    table tr {{ display: block; border: 1px solid var(--line);
      border-radius: .7rem; margin: .5rem 0; padding: .3rem .6rem;
      background: var(--card); }}
    table td {{ display: block; border: none; padding: .15rem 0; }}
    td.user {{ max-width: none; }}
    td.ts, td.kind {{ display: inline-block; margin-right: .6rem; }}
  }}
  /* Сворачиваемое тело: тёмная карточка с моноширинным текстом. */
  details.payload {{ margin-top: .4rem; }}
  details.payload summary {{ cursor: pointer; color: var(--muted); font-size: .8rem;
    user-select: none; list-style: none; text-align: right; }}
  details.payload summary::before {{ content: "⌄ "; }}
  details.payload[open] summary::before {{ content: "⌃ "; }}
  details.payload summary:hover {{ color: var(--accent); }}
  .payload-card {{ background: #101a24; border-radius: .7rem; padding: .7rem .9rem;
    margin-top: .35rem; }}
  .payload-label {{ color: #34d399; font: 600 .78rem/1.6 ui-monospace, monospace; }}
  .payload-body {{ color: #d7e3ee; font: .78rem/1.55 ui-monospace, monospace;
    white-space: pre-wrap; word-break: break-word; margin: 0 0 .6rem; }}
</style>
</head>
<body>
{_nav(can_edit=True)}
<h1>Журнал</h1>
{notice_html}
{summary_html}
<p><form method="post" action="/logs/clear" style="display:inline">
  <button type="submit" class="ghost danger">очистить</button>
</form>
</p>
<div class="card">
<table>
<tr><th>время</th><th>тип</th><th>кто</th><th>событие</th></tr>
{rows}
</table>
</div>
<script>
// Автообновление: раз в 10 секунд, но не пока открыто тело или человек
// занят страницей. Перед перезагрузкой запоминаем место чтения и
// раскрытые тела — после загрузки возвращаем, чтение не рвётся прыжком.
var busy = false;
["click", "scroll", "keydown", "mousemove"].forEach(function (ev) {{
  window.addEventListener(ev, function () {{ busy = true; }}, {{ passive: true }});
}});
function _payloadKey(d) {{
  var card = d.querySelector(".payload-card");
  return card ? (card.getAttribute("data-key") || "") : "";
}}
function saveOpenKeys() {{
  var open = [];
  document.querySelectorAll("details.payload").forEach(function (d) {{
    if (d.open) {{
      var key = _payloadKey(d);
      if (key) open.push(key);
    }}
  }});
  try {{
    sessionStorage.setItem("log:open", JSON.stringify(open));
  }} catch (e) {{}}
}}
function saveState() {{
  // Сохраняем только место чтения: открытые тела запоминаются отдельно,
  // при каждом переключении (saveOpenKeys), иначе эта функция, вызываемая
  // перед автообновлением, затирала бы сохранённые ключи пустым списком.
  try {{
    sessionStorage.setItem("log:scroll", String(window.scrollY));
  }} catch (e) {{}}
}}
function restoreState() {{
  var y = 0, open = [];
  try {{
    y = parseInt(sessionStorage.getItem("log:scroll") || "0", 10) || 0;
    open = JSON.parse(sessionStorage.getItem("log:open") || "[]");
  }} catch (e) {{}}
  document.querySelectorAll("details.payload").forEach(function (d) {{
    var key = _payloadKey(d);
    if (key && open.indexOf(key) >= 0) d.open = true;
  }});
  if (y > 0) window.scrollTo(0, y);
}}
window.addEventListener("load", restoreState);
// Сохраняем раскрытые тела при каждом переключении — иначе при ручной
// перезагрузке (F5) или после закрытия тела состояние терялось, и
// раскрытый запрос «сворачивался» сам по себе.
document.querySelectorAll("details.payload").forEach(function (d) {{
  d.addEventListener("toggle", saveOpenKeys);
}});
setTimeout(function tick() {{
  if (!busy && !document.querySelector("details.payload[open]")) {{
    saveState();
    location.reload();
  }} else {{ busy = false; setTimeout(tick, 5000); }}
}}, 10000);
</script>
{LOCAL_TIME_SCRIPT}
</body>
</html>"""


def users_page(users: list[dict], *, current_id: str = "", notice: str = "") -> str:
    """Пользователи приставки: блокировка и удаление (модерация)."""
    notice_html = f'<p class="notice">{escape(notice)}</p>' if notice else ""
    if users:
        rows = []
        for item in users:
            mine = str(item.get("id")) == current_id
            blocked = bool(item.get("blocked"))
            badge = ' <span class="badge badge-dead">заблокирован</span>' if blocked else ""
            controls = "<td></td>" if mine else (
                f'<td><form method="post" action="/users/block">'
                f'<input type="hidden" name="id" value="{escape(str(item.get("id")), quote=True)}">'
                f'<button type="submit" class="ghost">'
                f'{"разблокировать" if blocked else "заблокировать"}</button></form> '
                f'<form method="post" action="/users/delete">'
                f'<input type="hidden" name="id" value="{escape(str(item.get("id")), quote=True)}">'
                f'<button type="submit" class="ghost danger">удалить</button></form></td>'
            )
            rows.append(
                f'<tr><td>{escape(str(item.get("email") or ""))}{badge}</td>'
                f'<td>{escape(str(item.get("role") or ""))}</td>'
                f'<td>{escape(str(item.get("lastLogin") or "")[:10])}</td>'
                f"{controls}</tr>"
            )
        rows_html = "\n".join(rows)
    else:
        rows_html = '<tr><td colspan="4" class="empty">никто ещё не регистрировался</td></tr>'
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Пользователи — AI-приставка</title>
<style>
{_style()}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ text-align: left; padding: .45rem .6rem; border-bottom: 1px solid var(--line); }}
  th {{ font-size: .8rem; color: var(--muted); }}
</style>
</head>
<body>
{_nav(can_edit=True)}
<h1>Пользователи</h1>
{notice_html}
<div class="card">
<table>
<tr><th>почта</th><th>роль</th><th>последний вход</th><th></th></tr>
{rows_html}
</table>
</div>
</body>
</html>"""


def rules_page(
    rules: dict,
    providers: list[dict],
    *,
    notice: str = "",
) -> str:
    """Страница «Правила» (только админ): условие → действие.

    Два блока: условия в **ответах** провайдеров (у каждого провайдера
    свои, условие составное: строка в запросе + строка в ответе + статус)
    и строки в **запросах** клиентов (глобальные — по ним роутер
    выбирает модель с нужной способностью). Каждое правило можно
    посмотреть, изменить прямо в строке и убрать.

    ``rules`` — {"response": [...], "request": [...]}.
    """
    from storage.rules import REQUEST_ACTIONS, RESPONSE_ACTIONS

    notice_html = f'<p class="notice">{escape(notice)}</p>' if notice else ""

    def options(actions: dict, selected: str = "") -> str:
        return "".join(
            f'<option value="{code}"{" selected" if code == selected else ""}>'
            f"{escape(label)}</option>"
            for code, label in actions.items()
        )

    def provider_options(selected: str) -> str:
        return "".join(
            f'<option value="{escape(str(entry["id"]), quote=True)}"'
            f'{" selected" if str(entry["id"]).lower() == selected.lower() else ""}>'
            f'{escape(str(entry.get("name") or entry["id"]))}</option>'
            for entry in providers
        )

    # --- блок «условия в ответе»: правило — строка таблицы с полями.
    # Форма лежит в первой ячейке, поля привязываются атрибутом form=
    # (HTML5) — так форма может охватывать всю строку без вложенности.
    response_rows = []
    for index, rule in enumerate(rules.get("response") or []):
        provider = str(rule.get("provider") or "")
        action = str(rule.get("action") or "")
        status = rule.get("status")
        fid = f"resp{index}"
        response_rows.append(
            f"<tr>"
            f'<td><form id="{fid}" method="post" action="/rules/edit"></form>'
            f'<input type="hidden" name="where" form="{fid}" value="response">'
            f'<input type="hidden" name="index" form="{fid}" value="{index}">'
            f'<select name="provider" form="{fid}">{provider_options(provider)}</select></td>'
            f'<td><input name="req" form="{fid}" '
            f'value="{escape(str(rule.get("req") or ""), quote=True)}" '
            f'placeholder="в запросе (можно пусто)"></td>'
            f'<td><input name="text" form="{fid}" '
            f'value="{escape(str(rule.get("text") or ""), quote=True)}" '
            f'placeholder="в ответе"{" required" if not status else ""}></td>'
            f'<td class="rstatus"><input name="status" form="{fid}" '
            f'inputmode="numeric" '
            f'value="{escape(str(status if status is not None else ""))}" '
            f'placeholder="статус" title="код ответа, например 200"></td>'
            f'<td><select name="action" form="{fid}">'
            f"{options(RESPONSE_ACTIONS, action)}</select></td>"
            f'<td class="rbtns">'
            f'<button type="submit" form="{fid}">сохранить</button> '
            f'<button type="submit" form="{fid}" formaction="/rules/drop" '
            f'class="ghost danger">убрать</button></td>'
            f"</tr>"
        )
    response_html = "\n".join(response_rows) or (
        '<tr><td colspan="6" class="empty">правил пока нет — работает '
        "встроенное распознавание по кодам ошибок</td></tr>"
    )

    # --- блок «строки в запросе»: строка + действие (глобально)
    request_rows = []
    for index, rule in enumerate(rules.get("request") or []):
        action = str(rule.get("action") or "")
        fid = f"req{index}"
        request_rows.append(
            f"<tr>"
            f'<td><form id="{fid}" method="post" action="/rules/edit"></form>'
            f'<input type="hidden" name="where" form="{fid}" value="request">'
            f'<input type="hidden" name="index" form="{fid}" value="{index}">'
            f'<input name="text" form="{fid}" '
            f'value="{escape(str(rule.get("text") or ""), quote=True)}" '
            f'placeholder="строка в запросе" required></td>'
            f'<td><select name="action" form="{fid}">'
            f"{options(REQUEST_ACTIONS, action)}</select></td>"
            f'<td class="rbtns">'
            f'<button type="submit" form="{fid}">сохранить</button> '
            f'<button type="submit" form="{fid}" formaction="/rules/drop" '
            f'class="ghost danger">убрать</button></td>'
            f"</tr>"
        )
    request_html = "\n".join(request_rows) or (
        '<tr><td colspan="3" class="empty">правил пока нет — запросы '
        "распознаются встроенными строками</td></tr>"
    )

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Правила — AI-приставка</title>
<style>
{_style()}
  table {{ border-collapse: collapse; width: 100%; margin: .5rem 0 1.2rem; }}
  th, td {{ text-align: left; padding: .4rem .5rem; border-bottom: 1px solid var(--line); }}
  th {{ font-size: .8rem; color: var(--muted); }}
  table input, table select {{ padding: .3rem .45rem; border: 1px solid var(--line);
    border-radius: .45rem; background: var(--bg); color: var(--text);
    font-size: .85rem; max-width: 100%; }}
  table input[name="text"], table input[name="req"] {{ min-width: 11rem;
    font-family: ui-monospace, monospace; }}
  td.rstatus {{ width: 5.5rem; }}
  td.rstatus input {{ width: 4.5rem; font-family: ui-monospace, monospace; }}
  td.rbtns {{ white-space: nowrap; }}
  td.rbtns button {{ font-size: .82rem; padding: .3rem .55rem; }}
  h2 {{ margin: 1.3rem 0 .3rem; font-size: 1.05rem; }}
  .addform {{ display: flex; gap: .5rem; flex-wrap: wrap; align-items: end;
    background: var(--card); border: 1px solid var(--line); border-radius: .8rem;
    padding: .6rem .8rem; }}
  .addform label {{ display: flex; flex-direction: column; gap: .2rem;
    font-size: .78rem; color: var(--muted); }}
  .addform input, .addform select {{ padding: .35rem .5rem;
    border: 1px solid var(--line); border-radius: .5rem; background: var(--bg);
    color: var(--text); }}
  .addform input {{ min-width: 10rem; }}
  .legend {{ background: var(--card); border: 1px solid var(--line);
    border-radius: .8rem; padding: .6rem .9rem; margin: .6rem 0; }}
  .legend ul {{ margin: .3rem 0 0; padding-left: 1.2rem; }}
  .legend li {{ margin: .15rem 0; font-size: .88rem; }}
</style>
</head>
<body>
{_nav(can_edit=True)}
<h1>Правила</h1>
{notice_html}
<p class="role">Правило срабатывает, когда <b>все указанные части условия
совпали разом</b>. Правило админа сильнее встроенного решения: например,
сервер вернул 429 (встроенное — «серый»), а ваше правило говорит «чёрный»
→ чёрный. Срабатывают <b>все</b> подошедшие правила по порядку — одной
строкой можно и выучить способность, и в белый список положить.
Каждое срабатывание видно в журнале.</p>

<div class="legend">
<b>Что делают действия:</b>
<ul>
<li><b>в серый список (24 ч)</b> — провайдер спит сутки со всеми моделями,
потом пробует себя; три цикла подряд — уходит в чёрный.</li>
<li><b>в чёрный список</b> — эта модель (провайдер/модель) становится
последним шансом, когда больше некому.</li>
<li><b>в белый список</b> — подтвердить, что модель рабочая: она будет
служить первой.</li>
<li><b>переключить сейчас</b> — ответ не отдавать, взять другого провайдера.</li>
<li><b>списки не трогать (только журнал)</b> — заметить строку и ничего
не менять: понаблюдайте, сколько раз она попадается, потом назначьте
настоящее действие.</li>
<li><b>выучить: умеет / не умеет (картинки, инструменты)</b> — записать
способность модели: «умеет» — приставка начнёт давать ей картинки
(инструменты), «не умеет» — больше не будет давать.</li>
</ul>
</div>

<h2>Условия в ответах провайдеров</h2>
<p class="role">Строка в запросе — необязательное условие (например
<code>image_url</code>): правило сработает, только если запрос её содержит.
Строка в ответе и статус — хотя бы одно из двух обязательно.
Пустое поле — условие не проверяется. Правка прямо в строке:
изменил — «сохранить»; «убрать» — удалить.</p>
<div class="card">
<table>
<tr><th>провайдер</th><th>строка в запросе</th><th>строка в ответе</th>
<th>статус</th><th>действие</th><th></th></tr>
{response_html}
</table>
<form method="post" action="/rules/add" class="addform">
  <input type="hidden" name="where" value="response">
  <label>провайдер
    <select name="provider" required>{provider_options("")}</select>
  </label>
  <label>строка в запросе (необязательно)
    <input name="req" placeholder="image_url">
  </label>
  <label>строка в ответе (или статус)
    <input name="text" placeholder="insufficient credits">
  </label>
  <label>статус (или строка)
    <input name="status" inputmode="numeric" placeholder="200">
  </label>
  <label>действие
    <select name="action">{options(RESPONSE_ACTIONS)}</select>
  </label>
  <button type="submit" class="primary">добавить</button>
</form>
</div>

<h2>Строки в запросах клиентов</h2>
<p class="role">Глобальные: по строке в запросе приставка понимает, что
запросу нужна модель с способностью, и выбирает её (белый список →
неизвестные → проснувшийся серый → чёрный последним шансом).
Строки <code>image_url</code>, <code>input_image</code>, <code>tools</code>
и <code>tool_choice</code> распознаются <b>автоматически</b> — это правила
добавлять не нужно. Здесь добавляйте <b>дополнительные</b> маркеры, если
ваш клиент или провайдер присылает их в другом виде
(например <code>base64-image</code> или <code>нарисуй</code>).</p>
<div class="card">
<table>
<tr><th>строка</th><th>действие</th><th></th></tr>
{request_html}
</table>
<form method="post" action="/rules/add" class="addform">
  <input type="hidden" name="where" value="request">
  <label>строка (регистр не важен)
    <input name="text" placeholder="image_url" required>
  </label>
  <label>действие
    <select name="action">{options(REQUEST_ACTIONS)}</select>
  </label>
  <button type="submit" class="primary">добавить</button>
</form>
</div>
</body>
</html>"""


def _answer_formatted(row: dict) -> str:
    """Всё тело ответа, отформатированное: JSON с отступами,
    а если тело не JSON — как markdown (теги отрендерены безопасно)."""
    body = str(row.get("resp") or "")
    try:
        inner = f'<pre class="answer-json">{escape(json.dumps(json.loads(body), ensure_ascii=False, indent=2))}</pre>'
    except (ValueError, TypeError):
        inner = f'<div class="answer-md">{render_markdown(body)}</div>'
    return (
        '<div class="answer">'
        '<button type="button" class="copy-all">копировать</button>'
        f"{inner}</div>"
    )


def _answer_raw(row: dict) -> str:
    """Точное тело ответа, как пришло."""
    return (
        '<div class="answer">'
        '<button type="button" class="copy-all">копировать</button>'
        f'<pre class="answer-raw">{escape(str(row.get("resp") or ""))}</pre>'
        "</div>"
    )


def _media_html(row: dict) -> str:
    """Медиа-результат: картинка или плеер по ссылке из ответа провайдера.

    Ссылка чужая — только http/https (_safe_url), чтобы из ответа не
    родился «javascript:». Base64-картинка сюда не попадает: в результате
    остаётся лишь пометка о ней, мегабайты в журнал не пишутся.
    """
    test = str(row.get("test") or "")
    if test not in ("image", "tts", "video", "3d"):
        return ""
    url = _safe_url(str(row.get("response") or ""))
    if not url:
        return ""
    if test == "image":
        return (
            f'<div class="gen"><img src="{escape(url, quote=True)}" '
            f'alt="сгенерировано" loading="lazy"></div>'
        )
    if test == "tts":
        return f'<audio controls preload="none" src="{escape(url, quote=True)}"></audio>'
    if test == "video":
        return f'<video controls preload="none" src="{escape(url, quote=True)}"></video>'
    return (
        f'<p><a class="btn-ghost" href="{escape(url, quote=True)}" '
        f'target="_blank" rel="noopener">открыть 3D-файл</a></p>'
    )


def autotest_results_html(
    results: list,
    *,
    provider: str = "",
    timestamp: str = "",
    prompt: str = "",
    running: bool = False,
) -> str:
    """Секция результатов автотеста: заголовок и таблица.

    ``running=True`` — показывает частичные результаты текущего прогона.
    Время в ячейках несёт ``data-ts`` (UTC) — скрипт страницы
    переписывает его в локальное время зрителя.
    """
    if not results:
        if running:
            return '<p class="role">Результаты появятся здесь по ходу прогона…</p>'
        return ""
    ok = sum(1 for r in results if r.get("status") == "ok")
    total = len(results)
    heading = "Результаты прогона (идёт…)" if running else "Результаты последнего прогона"
    ts_html = (
        f'<span data-ts="{escape(timestamp, quote=True)}">'
        f'{escape(str(timestamp).replace("T", " ").replace("Z", ""))}</span>'
        if timestamp
        else "—"
    )
    rows = []
    for r in results:
        status = r.get("status", "?")
        badge = "✅" if status == "ok" else "❌"
        model = escape(str(r.get("model") or "—"))
        test = escape(str(r.get("test") or "—"))
        http = r.get("http_status", 0)
        dur = r.get("duration", 0)
        ts = str(r.get("ts") or "").replace("T", " ").replace("Z", "")
        reaction = escape(str(r.get("reaction") or "—"))
        used_tool = r.get("used_tool")
        tool_hint = ""
        if used_tool is True:
            tool_hint = ' <span class="badge badge-on">вызвала tool</span>'
        elif used_tool is False:
            tool_hint = ' <span class="role">(tool не вызван)</span>'
        rows.append(
            f'<tr><td class="ts" data-ts="{escape(str(r.get("ts") or ""), quote=True)}">{escape(ts)}</td>'
            f'<td class="model">{model}</td><td>{test}</td>'
            f'<td class="status">{badge} HTTP {http} · {dur}s{tool_hint}</td>'
            f'<td class="cell">{_media_html(r)}{_answer_formatted(r)}</td>'
            f'<td class="cell">{_answer_raw(r)}</td>'
            f'<td class="reaction">{reaction}</td></tr>'
        )
    return (
        f'<h2>{heading}</h2>'
        f'<p class="role">Провайдер: {escape(provider or "—")} · '
        f'запуск: {ts_html} · '
        f'прошли: {ok}/{total} · время местное</p>'
        f'<p class="role">prompt: <code>{escape(prompt or "—")}</code></p>'
        f'<div class="card"><table class="results">'
        f'<tr><th>время</th><th>модель</th><th>тест</th><th>статус</th>'
        f'<th>форматированный</th><th>raw</th><th>реакция роутера</th></tr>'
        + "\n".join(rows) +
        f"</table></div>"
    )


def autotest_page(
    providers: list[dict],
    *,
    last_run: dict | None = None,
    progress: dict | None = None,
    notice: str = "",
    can_edit: bool = False,
) -> str:
    """Автотесты моделей провайдера (только админ).

    Форма: выбор провайдера, тип теста (текст/vision/tools/free плюс
    генерация картинок, звук, видео, векторы и распознавание звука),
    своя модель, голос, аудиофайл, prompt, галочка «записать».
    Результаты последнего прогона — таблицей ниже: медиа-ответы
    показываются картинкой/плеером, тела запросов — по клику.
    """
    notice_html = f'<p class="notice">{escape(notice)}</p>' if notice else ""
    progress = progress or {}
    running = bool(progress.get("running"))
    last_run = last_run or {"provider": "", "timestamp": "", "results": []}
    results = last_run.get("results") or []
    provider_id = str(last_run.get("provider") or "")

    def results_html() -> str:
        """Секция результатов в обёртке, которую можно заменить живьём."""
        return (
            '<div id="results-wrap">' + autotest_results_html(
                results,
                provider=provider_id,
                timestamp=str(last_run.get("timestamp") or ""),
                prompt=str(last_run.get("prompt") or ""),
                running=running,
            ) + "</div>"
        )

    def _provider_option(entry: dict) -> str:
        mark = "" if entry.get("enabled") else " · выключен"
        return (
            f'<option value="{escape(str(entry["id"]), quote=True)}">'
            f'{escape(str(entry.get("name") or entry["id"]))}{mark}</option>'
        )

    provider_options = "".join(
        _provider_option(entry)
        for entry in sorted(
            providers,
            key=lambda e: (not e.get("enabled"), str(e.get("name") or e.get("id"))),
        )
    )

    def progress_html() -> str:
        """Блок хода теста: что идёт сейчас, сколько сделано из всего."""
        if not running:
            return ""
        return (
            '<div class="progress-block" id="progress">'
            '<h2>Ход теста</h2>'
            '<p id="prog-text">Идёт: '
            + escape(str(progress.get("provider") or ""))
            + ' · <span id="prog-current">—</span></p>'
            '<progress id="prog-bar" max="100" value="0"></progress>'
            '<p class="role" id="prog-count">0 из 0 (0%)</p>'
            '<form method="post" action="/autotest/stop" style="margin-top:.3rem">'
            '<button type="submit" class="ghost danger" id="stop-btn">остановить тест</button>'
            '</form>'
            '</div>'
        )

    def progress_script() -> str:
        """Опрос статуса раз в секунду: фаза, проценты, живые результаты."""
        if not running:
            return ""
        return """<script>
(function(){
  var current=document.getElementById("prog-current");
  var bar=document.getElementById("prog-bar");
  var count=document.getElementById("prog-count");
  var text=document.getElementById("prog-text");
  var settled=false;
  var prevDone=-1;
  function fetchResults(){
    var wrap=document.getElementById("results-wrap");
    if(!wrap) return;
    fetch("/autotest/results",{cache:"no-store"})
      .then(function(r){return r.text();})
      .then(function(html){
        wrap.innerHTML=html;
        if(window.localTimes) window.localTimes();
        bindCopies(wrap);
      });
  }
  function bindCopies(scope){
    scope.querySelectorAll(".copy-all").forEach(function(copy){
      var box=copy.closest(".answer");
      var pre=box&&box.querySelector("pre, .answer-md");
      if(!pre||!navigator.clipboard) return;
      copy.addEventListener("click", function(){
        navigator.clipboard.writeText(pre.textContent).then(function(){
          copy.textContent="скопировано";
          setTimeout(function(){copy.textContent="копировать";},1200);
        });
      });
    });
  }
  function poll(){
    fetch("/autotest/status",{cache:"no-store"}).then(function(r){return r.json();})
      .then(function(s){
        if(s.running){
          if(!s.total){
            if(text) text.textContent="Получаю список моделей провайдера…";
            if(current) current.textContent="—";
            if(count) count.textContent="список ещё не получен";
          } else {
            if(current) current.textContent=(s.model||"—")+" / "+(s.test||"—");
            if(text) text.textContent="Идёт: "+s.provider;
            var pct=Math.round(s.done*100/s.total);
            if(bar) bar.value=pct;
            if(count) count.textContent=s.done+" из "+s.total+" ("+pct+"%)";
          }
          if(s.stop){
            if(text) text.textContent="Останавливается после текущего теста…";
            var btn=document.getElementById("stop-btn");
            if(btn) btn.disabled=true;
          }
          if(s.done!==prevDone && s.done>0){
            fetchResults();
            prevDone=s.done;
          }
          setTimeout(poll,1000);
        } else if(!settled){
          settled=true;
          if(s.done!==prevDone && s.done>0){ fetchResults(); prevDone=s.done; }
          setTimeout(function(){ location.reload(); }, 500);
        }
      }).catch(function(){ setTimeout(poll,2000); });
  }
  poll();
})();
</script>"""
    def answer_script() -> str:
        """Кнопки копирования: всё тело ответа и блоки кода."""
        return """<script>
(function(){
  document.querySelectorAll(".answer").forEach(function(box){
    var copy = box.querySelector(".copy-all");
    var pre = box.querySelector("pre, .answer-md");
    if (copy && pre && navigator.clipboard) {
      copy.addEventListener("click", function(){
        navigator.clipboard.writeText(pre.textContent).then(function(){
          copy.textContent = "скопировано";
          setTimeout(function(){ copy.textContent = "копировать"; }, 1200);
        });
      });
    }
    box.querySelectorAll(".copy-code").forEach(function(btn){
      btn.addEventListener("click", function(){
        var code = btn.parentElement.querySelector("code");
        if (!code || !navigator.clipboard) return;
        navigator.clipboard.writeText(code.textContent).then(function(){
          btn.textContent = "скопировано";
          setTimeout(function(){ btn.textContent = "копировать"; }, 1200);
        });
      });
    });
  });
})();
</script>"""

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Автотесты — AI-приставка</title>
<style>
{_style()}
  body {{ max-width: 100%; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ text-align: left; padding: .4rem .55rem; border-bottom: 1px solid var(--line); }}
  th {{ font-size: .8rem; color: var(--muted); }}
  td.model {{ white-space: nowrap; font-family: monospace; font-size: .85rem; }}
  .addform {{ display: flex; gap: .5rem; flex-wrap: wrap; align-items: end;
    background: var(--card); border: 1px solid var(--line); border-radius: .8rem;
    padding: .6rem .8rem; }}
  .addform label {{ display: flex; flex-direction: column; gap: .2rem;
    font-size: .78rem; color: var(--muted); }}
  .addform input, .addform textarea, .addform select {{ padding: .35rem .5rem;
    border: 1px solid var(--line); border-radius: .5rem; background: var(--bg);
    color: var(--text); }}
  .addform textarea {{ min-width: 20rem; min-height: 3rem; font-family: monospace; }}
  .addform input[type=file] {{ min-width: 16rem; }}
  table.results td.cell, table.results td.reaction {{ vertical-align: top;
    max-width: 30rem; }}
  table.results td.reaction {{ min-width: 12rem; font-size: .85rem; }}
  table.results td.status {{ white-space: nowrap; }}
  .answer {{ position: relative; margin: 0; }}
  .answer .copy-all {{ position: absolute; top: .3rem; right: .3rem; z-index: 1;
    font-size: .72rem; padding: .15rem .5rem; border-radius: .5rem; }}
  .answer-json, .answer-raw {{ white-space: pre-wrap; overflow-wrap: anywhere;
    background: var(--bg); border: 1px solid var(--line); border-radius: .6rem;
    padding: .5rem .7rem; margin: 0; font: .78rem/1.5 ui-monospace, monospace;
    max-height: 24rem; overflow: auto; }}
  .answer-md {{ background: var(--bg); border: 1px solid var(--line);
    border-radius: .6rem; padding: .5rem .7rem; overflow-wrap: anywhere; }}
  .answer-md p, .answer-md ul, .answer-md ol {{ margin: .3rem 0; }}
  .answer-md h1, .answer-md h2, .answer-md h3 {{ margin: .4rem 0 .2rem;
    font-size: 1rem; }}
  .answer-md code {{ background: var(--soft); padding: .05rem .3rem;
    border-radius: .3rem; font-family: monospace; }}
  .answer-text {{ margin-top: .5rem; overflow-wrap: anywhere; }}
  .answer-text p, .answer-text ul, .answer-text ol {{ margin: .3rem 0; }}
  .answer-text h1, .answer-text h2, .answer-text h3 {{ margin: .4rem 0 .2rem;
    font-size: 1rem; }}
  .answer-text code {{ background: var(--soft); padding: .05rem .3rem;
    border-radius: .3rem; font-family: monospace; }}
  .codeblock {{ position: relative; margin: .35rem 0; }}
  .codeblock pre {{ background: #101a24; color: #d7e3ee; padding: .8rem .7rem .6rem;
    border-radius: .6rem; white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; }}
  .codeblock pre code {{ background: none; color: inherit; padding: 0; }}
  .copy-code {{ position: absolute; top: .35rem; right: .35rem; font-size: .72rem;
    padding: .15rem .5rem; border-radius: .5rem; }}
  .progress-block {{ background: var(--card); border: 1px solid var(--line);
    border-radius: .8rem; padding: .7rem .9rem; margin: .8rem 0; }}
  .progress-block h2 {{ margin: 0 0 .4rem; font-size: 1rem; }}
  .progress-block progress {{ width: 100%; height: 1.1rem; }}
  td.ts {{ white-space: nowrap; font-family: monospace; font-size: .78rem;
    color: var(--muted); }}
  /* Медиа-результаты: картинка в столбце, звук и видео — плеером. */
  .gen img {{ max-width: 18rem; border-radius: .6rem; display: block; margin: .2rem 0; }}
  table.results audio {{ width: 17rem; }}
  table.results video {{ max-width: 18rem; }}
</style>
</head>
<body>
{_nav(can_edit=True)}
<h1>Автотесты моделей</h1>
{notice_html}
<p class="role">Прогоняет тесты по всем моделям выбранного провайдера
последовательно. Тест не трогает списки и способности, пока не включена
галочка «записать результаты». Прогон может занять несколько минут —
не закрывайте страницу.</p>
<div class="card">
<form method="post" action="/autotest/run" enctype="multipart/form-data" class="addform">
  <label>провайдер
    <select name="provider" required>{provider_options}</select>
  </label>
  <label>картинка для vision-теста (файл)
    <input name="image" type="file" accept="image/*">
  </label>
  <label>или URL / data: URL картинки
    <textarea name="image_url" placeholder="https://… или data:image/png;base64,…"></textarea>
  </label>
  <label>или тестовая картинка 1×1 (PNG)
    <input name="use_test_image" type="checkbox" value="1">
  </label>
  <label>тест
    <select name="test">
      <option value="text">text — текстовый запрос</option>
      <option value="vision">vision — с картинкой</option>
      <option value="tools">tools — с инструментом</option>
      <option value="free">free — проверка бесплатности</option>
      <option value="image">image — сгенерировать картинку</option>
      <option value="tts">tts — озвучить текст</option>
      <option value="stt">stt — распознать звук</option>
      <option value="video">video — сгенерировать видео</option>
      <option value="3d">3d — модель</option>
      <option value="embedding">embedding — вектор</option>
    </select>
  </label>
  <label>модель (необязательно — гоняет только её)
    <input name="only_model" placeholder="пусто — все модели нужного типа">
  </label>
  <label>голос для tts (необязательно)
    <input name="voice" placeholder="nova, alloy …">
  </label>
  <label>аудио для stt (файл; пусто — тестовый тон)
    <input name="audio" type="file" accept="audio/*">
  </label>
  <label>prompt (обязательно — только ваш текст, подсказок нет)
    <textarea name="prompt" required placeholder="например: Ответь одним словом — привет"></textarea>
  </label>
  <label>записать результаты в способности/списки
    <input name="record" type="checkbox" value="1">
  </label>
  <button type="submit" class="primary">запустить тесты</button>
</form>
</div>
{progress_html()}
{results_html()}
{progress_script()}
{answer_script()}
{LOCAL_TIME_SCRIPT}
</body>
</html>"""


def verify_page(
    providers: list[dict],
    *,
    system_prompt: str = "",
    user_prompt: str = "",
    notice: str = "",
    can_edit: bool = False,
    last_run: dict | None = None,
    progress: dict | None = None,
) -> str:
    """Проверка: несколько провайдеров × несколько картинок, один прогон.

    Каждый провайдер — одной моделью (выбирается в таблице) и одной
    или несколькими картинками. Отчёт чистый: способности и списки не
    меняются. ``providers`` — каталог глазами вошедшего (с has_key,
    models, enabled).
    """
    progress = progress or {}
    running = bool(progress.get("running"))
    last_run = last_run or {"results": [], "timestamp": "", "images": []}
    results = last_run.get("results") or []
    images = last_run.get("images") or []

    notice_html = f'<p class="notice{" bad" if False else ""}">{escape(notice)}</p>' if notice else ""

    # Таблица провайдеров: чекбокс «включить» + выбор одной модели.
    rows = []
    for entry in providers:
        pid = escape(str(entry.get("id") or ""), quote=True)
        name = escape(str(entry.get("name") or entry.get("id") or ""))
        models = entry.get("models") or []
        current = str(entry.get("model") or "")
        options = [f'<option value="">как настроено ({escape(current or "—")})</option>']
        for m in models:
            selected = " selected" if m == current else ""
            options.append(f'<option value="{escape(str(m), quote=True)}"{selected}>{escape(str(m))}</option>')
        key_hint = "ключ задан" if entry.get("has_key") else "без ключа"
        disabled = "" if (entry.get("has_key") and (models or current)) else "disabled"
        rows.append(
            f'<tr><td><input type="checkbox" name="providers" value="{pid}"></td>'
            f'<td>{name}<span class="role"> · {escape(key_hint)}</span></td>'
            f'<td><select name="model_{pid}">{"".join(options)}</select></td></tr>'
        )
    providers_table = (
        f'<div class="card"><table>'
        f'<tr><th></th><th>провайдер</th><th>модель (одна на прогон)</th></tr>'
        + "\n".join(rows) +
        f'</table></div>'
    ) if rows else '<p class="role">провайдеров нет</p>'

    progress_html = ""
    if running:
        done = int(progress.get("done") or 0)
        total = int(progress.get("total") or 0)
        pct = int(done * 100 / total) if total else 0
        progress_html = (
            f'<div class="progress-block" id="progress">'
            f'<h2>Идёт проверка</h2>'
            f'<p class="role">{escape(str(progress.get("model") or "—"))} · '
            f'{done} из {total} ({pct}%)</p>'
            f'<progress id="prog-bar" max="100" value="{pct}"></progress>'
            f'<form method="post" action="/verify/stop" style="margin-top:.5rem">'
            f'<button type="submit" class="ghost danger">остановить</button></form>'
            f'</div>'
        )

    results_html = verify_results_html(
        results,
        provider=str(last_run.get("provider") or ""),
        timestamp=str(last_run.get("timestamp") or ""),
        system_prompt=str(last_run.get("system_prompt") or ""),
        user_prompt=str(last_run.get("prompt") or ""),
        images=images,
        running=running,
    )

    notice_html = f'<p class="notice{" bad" if False else ""}">{escape(notice)}</p>' if notice else ""

    # Polling — по образцу автотеста: статус раз в секунду, результаты
    # подгружаются, пока идёт прогон.
    poll_script = ""
    if running:
        poll_script = """<script>
(function(){
  var settled = false;
  function poll(){
    fetch("/verify/status", {cache: "no-store"}).then(function(r){return r.json();})
      .then(function(s){
        if (s.running) {
          var done = s.done || 0, total = s.total || 0;
          var pct = total ? Math.round(done * 100 / total) : 0;
          var bar = document.getElementById("prog-bar");
          if (bar) bar.value = pct;
          var t = document.querySelector("#progress .role");
          if (t) t.textContent = (s.model || "—") + " · " + done + " из " + total + " (" + pct + "%)";
          setTimeout(poll, 1000);
        } else if (!settled) {
          settled = true;
          fetch("/verify/results", {cache: "no-store"})
            .then(function(r){return r.text();})
            .then(function(html){
              var wrap = document.getElementById("verify-results");
              if (wrap) wrap.innerHTML = html;
            });
          setTimeout(function(){ location.reload(); }, 500);
        }
      }).catch(function(){ setTimeout(poll, 2000); });
  }
  poll();
})();
</script>"""

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Проверка провайдеров — AI-приставка</title>
<style>
{_style()}
  .verify-th {{ white-space: nowrap; }}
  .verify-img {{ max-width: 6rem; max-height: 6rem; border-radius: .4rem; display: block; }}
  table.verify results td {{ vertical-align: top; }}
</style>
</head>
<body>
{_nav(can_edit=can_edit)}
<h1>Проверка провайдеров на картинках</h1>
{notice_html}
<p class="role">Один прогон: выбранные провайдеры × выбранные картинки.
Каждая картинка — отдельный запрос с system/user prompt. Чистый отчёт:
способности и списки не меняются. На платных моделях — доли цента.</p>
<form method="post" action="/verify/run" enctype="multipart/form-data" class="addform">
  <label>system prompt (необязательно)
    <textarea name="system_prompt" rows="3"
              placeholder="например: ты ассистент, отвечаешь коротко">{escape(system_prompt)}</textarea>
  </label>
  <label>user prompt (обязательно)
    <textarea name="user_prompt" rows="3" required
              placeholder="например: что на картинке? Ответь одним предложением">{escape(user_prompt)}</textarea>
  </label>
  <label>картинки / PDF (несколько)
    <input name="images" type="file" accept="image/*,.pdf" multiple>
  </label>
  {providers_table}
  <button type="submit" class="primary">запустить проверку</button>
</form>
{progress_html}
<div id="verify-results">{results_html}</div>
<p class="role"><a class="btn-ghost" href="/verify/export" download>сохранить как markdown</a>
  <span class="role">— копия всего прогона (картинки встроены)</span></p>
{poll_script}
{ANSWER_SCRIPT}
{LOCAL_TIME_SCRIPT}
</body>
</html>"""


def verify_export_markdown(
    results: list,
    *,
    system_prompt: str = "",
    user_prompt: str = "",
    images: list[str] | None = None,
    timestamp: str = "",
    provider: str = "",
) -> str:
    """Результаты «Проверки» в markdown — для сохранения и пересылки.

    Картинки встраиваются data-URI (как просил пользователь), чтобы файл
    был самодостаточным. Чужие схемы (javascript:) не попадают.
    """
    images = images or []
    lines: list[str] = []
    lines.append("# Проверка провайдеров")
    lines.append("")
    if timestamp:
        lines.append(f"Запуск: {timestamp}")
    if provider:
        lines.append(f"Провайдеры: {provider}")
    if system_prompt:
        lines.append(f"Системный промпт: {system_prompt}")
    if user_prompt:
        lines.append(f"Пользовательский промпт: {user_prompt}")
    lines.append(f"Всего запросов: {len(results)}, прошли: {sum(1 for r in results if r.get('status') == 'ok')}")
    lines.append("")

    # Группируем по провайдеру, чтобы читалось.
    by_provider: dict[str, list] = {}
    for r in results:
        by_provider.setdefault(str(r.get("provider") or "—"), []).append(r)

    for prov, rows in by_provider.items():
        lines.append(f"## {prov}")
        lines.append("")
        for r in rows:
            idx = int(r.get("image_index") or 0)
            status = "✅" if r.get("status") == "ok" else "❌"
            http = r.get("http_status", 0)
            dur = r.get("duration", 0)
            model = str(r.get("model") or "—")
            answer = str(r.get("response") or "")
            lines.append(f"### Картинка {idx + 1} — {model}")
            lines.append("")
            if 0 <= idx < len(images):
                src = images[idx].strip()
                if src.startswith(("http://", "https://", "data:image/")):
                    lines.append(f"![картинка {idx + 1}]({src})")
                    lines.append("")
            lines.append(f"**Статус**: {status} HTTP {http} · {dur}s")
            lines.append("")
            lines.append("**Ответ**:")
            lines.append("")
            lines.append(answer)
            lines.append("")
            lines.append("---")
            lines.append("")
    if not results:
        lines.append("_результатов пока нет_")
    return "\n".join(lines)

def verify_results_html(
    results: list,
    *,
    provider: str = "",
    timestamp: str = "",
    system_prompt: str = "",
    user_prompt: str = "",
    images: list[str] | None = None,
    running: bool = False,
) -> str:
    """Результаты проверки: плоская таблица провайдер·модель·картинка·ответ."""
    images = images or []
    if not results and not running:
        return '<p class="role">результатов пока нет</p>'
    ok = sum(1 for r in results if r.get("status") == "ok")
    total = len(results)
    heading = "Идёт проверка…" if running else "Результаты последнего прогона"
    ts_html = (
        f'<span data-ts="{escape(timestamp, quote=True)}">'
        f'{escape(str(timestamp).replace("T", " ").replace("Z", ""))}</span>'
        if timestamp else "—"
    )
    rows = []
    for r in results:
        idx = int(r.get("image_index") or 0)
        thumb = "—"
        if 0 <= idx < len(images):
            src = images[idx].strip()
            if src.startswith(("http://", "https://", "data:image/")):
                thumb = (
                    f'<img class="verify-img" src="{escape(src, quote=True)}" '
                    f'alt="картинка {idx + 1}" loading="lazy">'
                )
        status = r.get("status", "?")
        badge = "✅" if status == "ok" else "❌"
        prov = escape(str(r.get("provider") or "—"))
        mdl = escape(str(r.get("model") or "—"))
        http = r.get("http_status", 0)
        dur = r.get("duration", 0)
        ts = str(r.get("ts") or "").replace("T", " ").replace("Z", "")
        answer = str(r.get("response") or "")
        rows.append(
            f'<tr><td class="ts" data-ts="{escape(str(r.get("ts") or ""), quote=True)}">{escape(ts)}</td>'
            f'<td>{prov}</td><td class="model">{mdl}</td>'
            f'<td>{thumb}</td>'
            f'<td class="status">{badge} HTTP {http} · {dur}s</td>'
            f'<td class="cell"><div class="answer"><button type="button" class="copy-all">копировать</button>'
            f'<div class="answer-md">{render_markdown(answer)}</div></div></td></tr>'
        )
    return (
        f'<h2>{heading}</h2>'
        f'<p class="role">запуск: {ts_html} · прошли: {ok}/{total} · время местное</p>'
        + (f'<p class="role">system: <code>{escape(system_prompt)}</code></p>' if system_prompt else "")
        + (f'<p class="role">user: <code>{escape(user_prompt)}</code></p>' if user_prompt else "")
        + f'<div class="card"><table class="verify">'
        f'<tr><th>время</th><th>провайдер</th><th>модель</th><th>картинка</th>'
        f'<th>статус</th><th>ответ</th></tr>'
        + "\n".join(rows) +
        f"</table></div>"
    )
def pick_model_page(
    provider_name: str,
    provider_id: str,
    models: list[str],
    *,
    current: str = "",
    notice: str = "",
    can_edit: bool = False,
    facts: str = "",
) -> str:
    """Выбор своей модели провайдера: список живых моделей радиокнопками."""
    notice_html = (
        f'<p class="notice bad">{escape(notice)}</p>' if notice else ""
    )
    facts_html = (
        f'<p class="role">Справка о текущей модели: {escape(facts)}</p>' if facts else ""
    )
    if models:
        rows = "\n".join(
            f'<label><input type="radio" name="model" value="{escape(model, quote=True)}"'
            f'{" checked" if model == current else ""}> {escape(model)}</label>'
            for model in models
        )
        body = f"""<form method="post" action="/pick/{escape(provider_id, quote=True)}">
  {rows}
  <button type="submit" class="primary">выбрать</button>
</form>"""
    else:
        body = (
            '<p class="role">модели не получились — проверьте ключ '
            f'у «{escape(provider_name)}»</p>'
        )
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Модель — {escape(provider_name)}</title>
<style>
{_style()}
  label {{ display: block; margin: .2rem 0; }}
</style>
</head>
<body>
{_nav(can_edit=can_edit)}
<h1>Модель: {escape(provider_name)}</h1>
<p class="role">Какую модель обслуживать по умолчанию — выбор ваш, личный.</p>
{facts_html}
{notice_html}
<div class="card">
{body}
</div>
</body>
</html>"""


def _image_box(result: dict | None) -> str:
    """Поле результата генерации: картинка, data-URI, ошибка или пустое место.

    Ссылка чужая — только http/https (_safe_url): из ответа провайдера
    не должно родиться «javascript:».
    """
    if not result:
        return '<div class="genbox"><p class="role">картинка появится здесь</p></div>'
    if str(result.get("error") or ""):
        return (
            f'<div class="genbox"><p class="notice bad">'
            f'{escape(str(result["error"]))}</p></div>'
        )
    url = _safe_url(str(result.get("url") or ""))
    b64 = str(result.get("b64") or "").strip()
    if url:
        src = url
    elif b64:
        src = b64 if b64.startswith("data:") else "data:image/png;base64," + b64
    else:
        return '<div class="genbox"><p class="notice bad">в ответе нет картинки</p></div>'
    caption = escape(str(result.get("prompt") or ""))
    model_name = escape(str(result.get("model") or ""))
    return (
        f'<div class="genbox"><img src="{escape(src, quote=True)}" alt="сгенерировано">'
        f'<p class="role">{model_name} · «{caption}»</p></div>'
    )


def _result_note(result: dict | None, box: str, empty: str, caption: str = "") -> str:
    """Общий бокс результата: ошибка — notice, ссылка — по-своему, пусто — заглушка.

    ``caption`` — готовая подпись (экранированная вызывающим).
    """
    if not result:
        return f'<div class="{box}"><p class="role">{empty}</p></div>'
    if str(result.get("error") or ""):
        return (
            f'<div class="{box}"><p class="notice bad">'
            f'{escape(str(result["error"]))}</p></div>'
        )
    return ""  # успешный случай дорисовывает вызывающий — у каждого свой


def _audio_box(result: dict | None) -> str:
    """Озвучка: плеер по публичной ссылке либо честная ошибка."""
    if result and not str(result.get("error") or ""):
        url = _safe_url(str(result.get("url") or ""))
        if not url:
            return (
                f'<div class="audiobox"><p class="notice bad">'
                f'ссылки на звук нет — показать нечего</p></div>'
            )
        caption = escape(str(result.get("model") or ""))
        spoken = escape(str(result.get("text") or ""))
        caption_line = f"{caption} · «{spoken}»" if spoken else caption
        return (
            f'<div class="audiobox"><audio controls preload="none" '
            f'src="{escape(url, quote=True)}"></audio>'
            f'<p class="role">{caption_line}</p></div>'
        )
    note = _result_note(result, "audiobox", "озвучка появится здесь")
    return note


def _three_box(result: dict | None) -> str:
    """3D: ссылка на glb («скачать») либо честная ошибка."""
    if result and not str(result.get("error") or ""):
        url = _safe_url(str(result.get("url") or ""))
        if not url:
            return (
                f'<div class="threebox"><p class="notice bad">'
                f'ссылки на файл нет — скачать нечего</p></div>'
            )
        caption = escape(str(result.get("model") or ""))
        return (
            f'<div class="threebox"><a class="btn-ghost" href="{escape(url, quote=True)}" '
            f'target="_blank" rel="noopener">скачать .glb</a>'
            f'<p class="role">{caption}</p></div>'
        )
    return _result_note(result, "threebox", "3D-модель появится здесь")


def _embed_box(result: dict | None) -> str:
    """Вектор: длина и первые числа либо честная ошибка."""
    if result and not str(result.get("error") or ""):
        count = int(result.get("count") or 0)
        sample = escape(str(result.get("sample") or ""))
        caption = escape(str(result.get("model") or ""))
        return (
            f'<div class="embbox"><p><b>вектор из {count} чисел</b> · '
            f'{caption}</p><code>{sample}'
            f'{" …" if count > 12 else ""}</code></div>'
        )
    return _result_note(result, "embbox", "вектор появится здесь")


def _stt_box(result: dict | None) -> str:
    """Распознавание: текст и модель либо честная ошибка."""
    if result and not str(result.get("error") or ""):
        caption = escape(str(result.get("model") or ""))
        text = escape(str(result.get("text") or ""))
        return (
            f'<div class="sttbox"><p class="role">{caption}</p>'
            f"<p>{text}</p></div>"
        )
    return _result_note(result, "sttbox", "распознанный текст появится здесь")


def _model_options(items: list[dict]) -> str:
    """Опции для select: имя модели и пометка «платная»."""
    return "".join(
        f'<option value="{escape(str(item.get("name") or ""), quote=True)}">'
        f'{escape(str(item.get("name") or ""))}'
        f'{" · платная" if item.get("paid") else ""}</option>'
        for item in items
    )


#: Действия обработки звука — подписи для формы (значения: changer/isolator/stem).
_VOICE_LABELS = {
    "changer": "смена голоса",
    "isolator": "очистка от шума",
    "stem": "разделение дорожек",
}


def test_page(
    provider_name: str,
    model: str,
    question: str,
    *,
    answer: str = "",
    error: str = "",
    can_edit: bool = False,
    provider_id: str = "",
    media: dict[str, list[dict]] | None = None,
    models: list[str] | None = None,
    current_model: str = "",
    image_result: dict | None = None,
    audio_result: dict | None = None,
    music_result: dict | None = None,
    three_result: dict | None = None,
    embed_result: dict | None = None,
    transcript_result: dict | None = None,
    voice_result: dict | None = None,
    voices: list[str] | None = None,
) -> str:
    """Страница теста: чат, свой промпт и секции генерации по типам моделей.

    ``question`` пуст — чат-блок скрыт (страница открыта после
    генерации); ``media`` — списки моделей по секциям (пустая секция не
    рисуется вовсе); каждый ``*_result`` — что показать в её поле
    результата: ссылку, data-URI, текст, число или честную ошибку;
    ``voices`` — голоса каталога для подсказок в поле голоса.
    """
    chat_html = ""
    if question:
        notice_html = (
            f'<p class="notice bad">{escape(error)}</p>' if error else ""
        )
        # Ответ — markdown (ссылки и ![картинки] оживают; чужой HTML
        # рендерер экранирует, как и в автотестах). Кнопка «копировать»
        # кладёт всё тело (включая codeblocks) в буфер.
        answer_html = (
            f'<div class="answer"><button type="button" class="copy-all">копировать</button>'
            f'<div class="answer-md">{render_markdown(answer)}</div></div>'
        ) if answer else ""
        chat_html = (
            f'<p class="role">Тест — настоящий, но крошечный запрос: на платной модели он\n'
            f'стоит доли цента.</p>\n'
            f'<p class="q">«{escape(question)}»</p>\n'
            f"{notice_html}\n{answer_html}"
        )

    prompt_form = ""
    model_field = ""
    if models:
        options = "".join(
            f'<option value="{escape(str(name), quote=True)}">'
            f"{escape(str(name))}</option>"
            for name in models
        )
        model_field = f"""    <label>модель (любая из каталога — тест идёт на ней)
      <select name="test_model">
        <option value="" selected>как настроено ({escape(current_model or "—")})</option>
        {options}
      </select>
    </label>
"""
    prompt_form = f"""
<div class="card">
  <h2>Свой промпт</h2>
  <form method="post" action="/test/{escape(provider_id, quote=True)}">
{model_field}    <label>вопрос модели (свой текст)
      <textarea name="question" rows="3"
                placeholder="например: Ответь одним словом — привет"></textarea>
    </label>
    <label>картинка к вопросу (URL, необязательно) — для vision-моделей
      и агентов-генераторов, например 3D-для-Blender
      <input name="image_url" placeholder="https://…/picture.png">
    </label>
    <button type="submit" class="primary">спросить</button>
    <span class="role">крошечная просьба: на платной модели — доли цента</span>
  </form>
</div>"""

    media = media or {}

    image_html = ""
    if media.get("image"):
        image_html = f"""
<div class="card">
  <h2>Картинка</h2>
  <p class="role">Настоящий запрос генерации: бесплатной моделью — бесплатно,
  платной — доли цента.</p>
  <form method="post" action="/imgtest/{escape(provider_id, quote=True)}">
    <label>модель
      <select name="model">{_model_options(media["image"])}</select>
    </label>
    <label>промпт
      <textarea name="prompt" rows="3" required
                placeholder="например: кот в космосе, детская книжка"></textarea>
    </label>
    <button type="submit" class="primary">сгенерировать</button>
  </form>
  {_image_box(image_result)}
</div>"""

    audio_html = ""
    if media.get("audio"):
        audio_html = f"""
<div class="card">
  <h2>Озвучка</h2>
  <p class="role">Голосом из списка провайдера; готовый mp3 показывается
  плеером по ссылке.</p>
  <form method="post" action="/audiotest/{escape(provider_id, quote=True)}">
    <label>модель
      <select name="model">{_model_options(media["audio"])}</select>
    </label>
    <label>голос (необязательно)
      <input name="voice" list="voices" placeholder="nova, alloy …">
    </label>
    <label>текст
      <textarea name="text" rows="2" required
                placeholder="например: Привет от AI-приставки"></textarea>
    </label>
    <button type="submit" class="primary">озвучить</button>
  </form>
  {_audio_box(audio_result)}
</div>"""

    music_html = ""
    if media.get("music"):
        music_html = f"""
<div class="card">
  <h2>Музыка</h2>
  <p class="role">Описание мелодии текстом; платные модели помечены
  (цена — за генерацию).</p>
  <form method="post" action="/audiotest/{escape(provider_id, quote=True)}">
    <input type="hidden" name="kind" value="music">
    <label>модель
      <select name="model">{_model_options(media["music"])}</select>
    </label>
    <label>описание музыки
      <textarea name="text" rows="3" required
                placeholder="например: спокойная фортепианная мелодия, 30 секунд"></textarea>
    </label>
    <button type="submit" class="primary">создать музыку</button>
  </form>
  {_audio_box(music_result)}
</div>"""

    three_html = ""
    if media.get("three"):
        resolutions = "".join(
            f'<option value="{value}"{" selected" if value == "low" else ""}>{label}'
            f"</option>"
            for value, label in (("low", "низкое"), ("medium", "среднее"),
                                 ("high", "высокое"))
        )
        three_html = f"""
<div class="card">
  <h2>3D-модель</h2>
  <p class="role">Из картинки-ссылки (так в доках): строится не мгновенно,
  результат — файл .glb на скачивание.</p>
  <form method="post" action="/3dtest/{escape(provider_id, quote=True)}">
    <label>картинка-источник (http-ссылка)
      <input name="image" required placeholder="https://…/picture.png">
    </label>
    <label>модель
      <select name="model">{_model_options(media["three"])}</select>
    </label>
    <label>разрешение
      <select name="resolution">{resolutions}</select>
    </label>
    <label>промпт (необязательно — trellis его игнорирует)
      <textarea name="prompt" rows="2" placeholder="например: a lighthouse"></textarea>
    </label>
    <button type="submit" class="primary">построить</button>
  </form>
  {_three_box(three_result)}
</div>"""

    embed_html = ""
    if media.get("embedding"):
        embed_html = f"""
<div class="card">
  <h2>Вектор</h2>
  <p class="role">OpenAI-совместимые embeddings: текст становится числом —
  покажем длину и первые значения.</p>
  <form method="post" action="/embedtest/{escape(provider_id, quote=True)}">
    <label>модель
      <select name="model">{_model_options(media["embedding"])}</select>
    </label>
    <label>текст
      <textarea name="text" rows="2" required
                placeholder="например: Привет от AI-приставки"></textarea>
    </label>
    <button type="submit" class="primary">получить вектор</button>
  </form>
  {_embed_box(embed_result)}
</div>"""

    stt_html = ""
    if media.get("stt"):
        stt_html = f"""
<div class="card">
  <h2>Распознавание звука</h2>
  <p class="role">Свой аудиофайл превращается в текст; пустое поле —
  приставка сама подставит свой тестовый тон (полсекунды).</p>
  <form method="post" action="/stttest/{escape(provider_id, quote=True)}"
        enctype="multipart/form-data">
    <label>модель
      <select name="model">{_model_options(media["stt"])}</select>
    </label>
    <label>аудиофайл (пусто — тестовый тон)
      <input name="audio" type="file" accept="audio/*">
    </label>
    <button type="submit" class="primary">распознать</button>
  </form>
  {_stt_box(transcript_result)}
</div>"""

    voice_html = ""
    if media.get("voice"):
        options = "".join(
            f'<option value="{escape(str(item.get("action") or ""), quote=True)}'
            f'|{escape(str(item.get("name") or ""), quote=True)}">'
            f'{escape(str(item.get("name") or ""))} · '
            f'{escape(_VOICE_LABELS.get(str(item.get("action") or ""),
                                       str(item.get("action") or "")))}'
            f'{" · платная" if item.get("paid") else ""}</option>'
            for item in media["voice"]
        )
        voice_html = f"""
<div class="card">
  <h2>Обработка звука</h2>
  <p class="role">Смена голоса, очистка от шума или разделение дорожек:
  вход — свой аудиофайл (пусто — тестовый тон), результат — плеер.</p>
  <form method="post" action="/voicetest/{escape(provider_id, quote=True)}"
        enctype="multipart/form-data">
    <label>действие и модель
      <select name="model">{options}</select>
    </label>
    <label>голос для смены (необязательно)
      <input name="voice" list="voices" placeholder="nova, alloy …">
    </label>
    <label>аудиофайл (пусто — тестовый тон)
      <input name="audio" type="file" accept="audio/*">
    </label>
    <button type="submit" class="primary">обработать</button>
  </form>
  {_audio_box(voice_result)}
</div>"""

    voices_datalist = ""
    if voices:
        options = "".join(
            f'<option value="{escape(str(voice), quote=True)}">' for voice in voices
        )
        voices_datalist = f'<datalist id="voices">{options}</datalist>'

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Тест — {escape(provider_name)}</title>
<style>
{_style()}
  .q {{ opacity: .6; }}
  .genbox, .audiobox, .threebox, .embbox, .sttbox {{ margin-top: .7rem; }}
  .genbox img {{ max-width: 24rem; border-radius: .6rem; display: block; }}
  .genbox .role, .audiobox .role, .threebox .role, .sttbox .role {{
    margin-top: .3rem;
  }}
  .audiobox audio {{ width: 20rem; max-width: 100%; }}
  .embbox code {{ display: block; margin-top: .3rem; padding: .4rem .6rem;
    background: var(--bg); border: 1px solid var(--line); border-radius: .6rem;
    overflow-wrap: anywhere; }}
</style>
</head>
<body>
{_nav(can_edit=can_edit)}
<h1>Тест: {escape(provider_name)} · {escape(model or "—")}</h1>
{chat_html}
{prompt_form}
{image_html}
{voices_datalist}
{audio_html}
{music_html}
{three_html}
{embed_html}
{stt_html}
{voice_html}
{ANSWER_SCRIPT}
{LOCAL_TIME_SCRIPT}
</body>
</html>"""


def key_page(
    provider_name: str,
    *,
    provider_id: str,
    has_key: bool,
    notice: str = "",
    can_edit: bool = False,
    login_url: str = "",
    key_url: str = "",
    oauth: bool = False,
) -> str:
    """Ввод ключа одного провайдера: форма плюс честное предупреждение.

    В скрытом поле — id провайдера (не имя: сервер ищет запись по id).
    Ссылки «где взять ключ» и «страница логина» — из полей каталога.
    ``oauth`` — провайдер принимает вход через GitHub: кнопка «Войти»
    ведёт на device-flow вместо вставки ключа.
    """
    notice_html = f'<p class="notice">{escape(notice)}</p>' if notice else ""
    value_hint = "сейчас задан — можно оставить пустым, чтобы удалить" if has_key else ""
    hints = []
    if _safe_url(key_url):
        hints.append(
            f'Ещё нет ключа? <a href="{escape(_safe_url(key_url), quote=True)}" '
            f'target="_blank" rel="noopener">получить ключ у провайдера</a>'
        )
    if _safe_url(login_url):
        hints.append(
            f'Страница входа провайдера: <a href="{escape(_safe_url(login_url), quote=True)}" '
            f'target="_blank" rel="noopener">открыть</a>'
        )
    hints_html = f'<p class="role">{" · ".join(hints)}</p>' if hints else ""
    oauth_html = ""
    if oauth:
        oauth_html = f"""
<form method="post" action="/oauth/{escape(provider_id, quote=True)}">
  <input type="hidden" name="action" value="start">
  <button type="submit">Войти через GitHub</button>
  <span class="role">вместо вставки ключа: GitHub выдаст код, вы введёте его на github.com/login/device</span>
</form>"""
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ключ — {escape(provider_name)}</title>
<style>
{_style()}
  input {{ width: 100%; font-family: monospace; }}
  button {{ margin-top: .6rem; padding: .35rem 1.1rem; }}
</style>
</head>
<body>
{_nav(can_edit=can_edit)}
<h1>Ключ: {escape(provider_name)}</h1>
{notice_html}
<div class="card">
<form method="post" action="/key">
  <input type="hidden" name="id" value="{escape(provider_id, quote=True)}">
  <input name="api_key" type="password" placeholder="вставьте ключ" autocomplete="off" autofocus>
  <p class="role">{escape(value_hint)}</p>
  <button type="submit" class="primary">сохранить</button>
  <a href="/">назад</a>
</form>
{oauth_html}
</div>
{hints_html}
<p class="role">Ключ остаётся на устройстве: он уходит только к провайдеру в его запросе.</p>
</body>
</html>"""
