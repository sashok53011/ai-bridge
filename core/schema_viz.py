"""Визуальная схема БД: SVG ER-диаграмма для страницы SQL Playground."""

from __future__ import annotations

import sqlite3
from html import escape
from pathlib import Path
from typing import Any


def load_schema(db_path: str | Path, *, include_data: bool = False) -> dict[str, Any]:
    """Читает схему БД: таблицы, поля, связи (FK)."""
    path = Path(db_path).expanduser()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        tables = []
        names = conn.execute(
            "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for name, kind in names:
            q = '"' + name.replace('"', '""') + '"'
            columns = []
            for row in conn.execute(f"PRAGMA table_info({q})"):
                columns.append({
                    "name": row[1], "type": row[2] or "", "pk": bool(row[5]), "notnull": bool(row[3]),
                })
            fk_list = []
            for row in conn.execute(f"PRAGMA foreign_key_list({q})"):
                fk_list.append({"from": row[3], "table": row[2], "to": row[4]})
            sample_rows = []
            if include_data:
                try:
                    cur = conn.execute(f"SELECT * FROM {q} LIMIT 12")
                    cols = [c[0] for c in cur.description] if cur.description else []
                    while True:
                        r = cur.fetchone()
                        if r is None:
                            break
                        sample_rows.append(dict(zip(cols, r)))
                except sqlite3.Error:
                    pass
            tables.append({
                "name": name, "type": kind, "columns": columns,
                "fks": fk_list, "sample_rows": sample_rows,
            })
        return {"tables": tables}
    finally:
        conn.close()


def _junction_names(tables: list[dict]) -> set[str]:
    """Связующие таблицы — признак N:M.

    Считаем таблицу связующей, если в ней два и больше FK и почти все
    поля участвуют в связях (обычно PK = пара FK). Такая таблица
    разворачивает «многие ко многим» в два ребра 1:N.
    """
    out: set[str] = set()
    for table in tables:
        fks = table.get("fks") or []
        if len(fks) < 2:
            continue
        fk_cols = {f["from"] for f in fks}
        pk_cols = {c["name"] for c in table["columns"] if c.get("pk")}
        other = [c for c in table["columns"] if c["name"] not in fk_cols]
        if (pk_cols and pk_cols <= fk_cols) or len(other) <= 1:
            out.add(table["name"])
    return out


def _levels(names: set[str], edges: list[tuple[str, str]]) -> dict[str, int]:
    """Уровни таблиц: ссылки всегда правее ссылающихся (циклы упираются в потолок)."""
    level = {name: 0 for name in names}
    for _ in range(len(names) + 1):
        changed = False
        for parent, child in edges:
            if level[child] < level[parent] + 1 and level[parent] + 1 <= len(names):
                level[child] = level[parent] + 1
                changed = True
        if not changed:
            break
    return level


def render_schema_svg(schema: dict, *, include_data: bool = False) -> str:
    """ER-диаграмма: карточки таблиц, связи с кардинальностью 1:N и N:M.

    Раскладка — по уровням связей (корни слева, «дети» справа), поэтому
    даже на базе с десятками таблиц связи не пересекают карточки.
    """
    tables = schema.get("tables") or []
    from core.i18n import t

    if not tables:
        return f'<p class="role">{escape(t("sql.empty_schema"))}</p>'

    by_name = {t["name"]: t for t in tables}
    # Ребро: ссылающаяся таблица → ссылаемся на неё (parent ссылается на child? нет:)
    # (parent — на кого ссылаются, child — у кого FK).
    edges: list[tuple[str, str, dict]] = []
    for table in tables:
        for fk in table.get("fks") or []:
            target = str(fk.get("table") or "")
            if target in by_name and target != table["name"]:
                edges.append((target, table["name"], fk))

    junctions = _junction_names(tables)
    level = _levels(set(by_name), [(p, c) for p, c, _ in edges])

    card_w, header_h, row_h, sample_h = 250, 24, 18, 14
    h_gap, v_gap, margin = 140, 44, 24

    def card_height(table: dict) -> float:
        n = len(table["columns"])
        extra = 6
        if include_data and table.get("sample_rows"):
            extra = 10 + min(len(table["sample_rows"]), 3) * sample_h
        return header_h + n * row_h + extra

    # Колонки по уровням, внутри — по алфавиту.
    columns: dict[int, list[str]] = {}
    for name in sorted(by_name, key=lambda n: (level[n], n.lower())):
        columns.setdefault(level[name], []).append(name)

    positions: dict[str, tuple[float, float]] = {}
    bottoms: dict[str, float] = {}
    x = float(margin)
    total_h = 0.0
    for lv in sorted(columns):
        y = float(margin)
        for name in columns[lv]:
            positions[name] = (x, y)
            h = card_height(by_name[name])
            bottoms[name] = y + h
            y += h + v_gap
        total_h = max(total_h, y)
        x += card_w + h_gap
    total_w = x - h_gap + margin + 40

    parts: list[str] = []

    # Карточки таблиц.
    for name, table in by_name.items():
        px, py = positions[name]
        h = card_height(table)
        rows_html = []
        for idx, col in enumerate(table["columns"]):
            # Внутри SVG нельзя использовать <b>/<i>/<p> — это breakout-теги
            # HTML-парсера: они выкидывают содержимое <svg> наружу, и названия
            # столбцов падают под диаграмму обычным текстом. Только <tspan>.
            badges = []
            for tag in (["PK"] if col.get("pk") else []) + (
                ["FK"] if any(f["from"] == col["name"] for f in table.get("fks") or []) else []
            ):
                color = "#fbbf24" if tag == "PK" else "#4ade80"
                badges.append(
                    f'<tspan font-weight="bold" fill="{color}">{escape(tag)}</tspan>'
                )
            meta = " ".join(badges)
            rows_html.append(
                f'<text x="{px + 10}" y="{py + header_h + (idx + 1) * row_h - 4}" '
                f'font-size="12" font-family="monospace" fill="#d7e3ee">'
                f'{escape(str(col["name"]))} : {escape(str(col.get("type") or ""))} {meta}</text>'
            )
        sample_html = ""
        if include_data and table.get("sample_rows"):
            sy = py + header_h + len(table["columns"]) * row_h + 12
            for sample in table["sample_rows"][:3]:
                vals = ", ".join(str(v)[:12] for v in sample.values())
                sample_html += (
                    f'<text x="{px + 10}" y="{sy}" font-size="10" font-family="monospace" '
                    f'fill="#8899aa">{escape(vals[:40])}</text>'
                )
                sy += sample_h
        badge = ""
        if name in junctions:
            badge = (
                f'<text x="{px + card_w - 10}" y="{py + 16}" font-size="11" '
                f'font-weight="bold" text-anchor="end" fill="#fbbf24" '
                f'font-family="monospace">N:M</text>'
            )
        parts.append(
            f'<g><title>{escape(name)} · {escape(t("sql.fields").format(n=len(table["columns"])))}</title>'
            f'<rect x="{px}" y="{py}" width="{card_w}" height="{h}" rx="8" '
            f'fill="#101a24" stroke="#2d4a6f" stroke-width="1.5"/>'
            f'<rect x="{px}" y="{py}" width="{card_w}" height="{header_h}" rx="8" fill="#1e3a5f"/>'
            f'<text x="{px + 10}" y="{py + 16}" font-size="13" font-weight="bold" '
            f'fill="#7dd3fc" font-family="monospace">{escape(name)}</text>'
            f'{badge}{"".join(rows_html)}{sample_html}</g>'
        )

    # Связи: «1» у ссылаемся, «N» (у связующей — «M») у ссылающегося.
    # У связующей таблицы первое ребро помечаем N, остальные — M (это и есть N:M).
    many_label: dict[tuple[str, str], str] = {}
    for junction in sorted(junctions):
        parents = sorted({p for p, c, _ in edges if c == junction})
        for idx, parent in enumerate(parents):
            many_label[(parent, junction)] = "M" if idx else "N"
    n_relations = 0
    for parent, child, fk in edges:
        n_relations += 1
        px, py = positions[parent]
        cx, cy = positions[child]
        x1, y1 = px + card_w, py + header_h + 8
        x2, y2 = cx, cy + header_h + 8
        if parent in junctions:
            many = "M"
        else:
            many = many_label.get((parent, child), "N")
        label = f'{parent} 1:{many} {child} ({fk.get("from")} → {fk.get("table")}.{fk.get("to")})'
        if x2 > x1 + 20:
            mx = (x1 + x2) / 2
            d = f"M{x1},{y1} C{mx},{y1} {mx},{y2} {x2},{y2}"
        else:
            # Обратная/петляющая связь — ведём ниже карточек.
            yb = max(bottoms[parent], bottoms[child]) + 26
            d = (
                f"M{x1},{y1} L{x1 + 34},{y1} L{x1 + 34},{yb} "
                f"L{x2 - 34},{yb} L{x2 - 34},{y2} L{x2},{y2}"
            )
        parts.append(
            f'<g><title>{escape(label)}</title>'
            f'<path d="{d}" fill="none" stroke="#34d399" stroke-width="1.6" '
            f'stroke-linejoin="round" marker-end="url(#crow)" opacity=".85"/>'
            f'<text x="{x1 + 8}" y="{y1 - 7}" font-size="12" font-weight="bold" '
            f'fill="#7dd3fc" font-family="monospace">1</text>'
            f'<text x="{x2 - 10}" y="{y2 - 7}" font-size="12" font-weight="bold" '
            f'text-anchor="end" fill="#fbbf24" font-family="monospace">{many}</text>'
            f'</g>'
        )

    legend = (
        '<div style="display:flex;gap:1rem;flex-wrap:wrap;align-items:center;'
        'margin-bottom:.5rem;font-size:.82rem;color:var(--muted)">'
        '<span><b style="color:#7dd3fc">1</b> ──<b style="color:#34d399">▶</b>── '
        f'<b style="color:#fbbf24">N</b> &nbsp;{escape(t("sql.legend_1n"))}</span>'
        '<span><b style="color:#7dd3fc">1</b> ──<b style="color:#34d399">▶</b>── '
        f'<b style="color:#fbbf24">M</b> &nbsp;{escape(t("sql.legend_nm"))}</span>'
        f'<span>{escape(t("sql.legend_counts").format(t=len(tables), r=n_relations, j=len(junctions)))}</span>'
        "</div>"
    )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{int(total_w)}" '
        f'height="{int(total_h)}" '
        f'style="background:#0d121f;border-radius:8px;max-width:100%;overflow:auto">'
        f'<defs><marker id="crow" markerWidth="10" markerHeight="10" refX="9" refY="5" '
        f'orient="auto"><path d="M9,5 L1,1 M9,5 L1,5 M9,5 L1,9" fill="none" '
        f'stroke="#34d399" stroke-width="1.4"/></marker></defs>'
        f'{"".join(parts)}</svg>'
    )
    return legend + svg
