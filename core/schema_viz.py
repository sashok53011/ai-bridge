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
                    cur = conn.execute(f"SELECT * FROM {q} LIMIT 3")
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


def render_schema_svg(schema: dict, *, include_data: bool = False) -> str:
    """Рисует SVG-диаграмму: таблицы — карточки, FK — стрелки."""
    tables = schema.get("tables") or []
    if not tables:
        return '<p class="role">Схема пуста.</p>'

    # Размещаем таблицы сеткой: 3 колонки.
    card_w, card_h_min, gap, cols = 240, 80, 40, 3
    positions: dict[str, tuple[float, float]] = {}
    row_max_h: list[float] = []
    for i, t in enumerate(tables):
        r, c = divmod(i, cols)
        n_cols = max(len(t["columns"]), 2)
        h = 40 + n_cols * 18 + 10 + (len(t.get("sample_rows", [])) * 16 if include_data else 0)
        x = c * (card_w + gap)
        y = sum(row_max_h[:r]) + r * gap
        positions[t["name"]] = (x, y)
        if r >= len(row_max_h):
            row_max_h.append(h)
        else:
            row_max_h[r] = max(row_max_h[r], h)

    total_w = cols * (card_w + gap)
    total_h = sum(row_max_h) + len(row_max_h) * gap + 20
    # Небольшой отступ для стрелок.
    total_w += 60

    svg_parts: list[str] = []
    # Таблицы.
    for t in tables:
        x, y = positions[t["name"]]
        n = len(t["columns"])
        h = 40 + n * 18 + 10 + (len(t.get("sample_rows", [])) * 16 if include_data else 0)
        rows = []
        for col in t["columns"]:
            tags = []
            if col["pk"]:
                tags.append("PK")
            if any(f["from"] == col["name"] for f in t["fks"]):
                tags.append("FK")
            meta = " ".join(f"<b>{escape(str(x))}</b>" for x in tags)
            rows.append(
                f'<text x="{x + 10}" y="{y + 24 + (t["columns"].index(col) + 1) * 18}" '
                f'font-size="12" font-family="monospace" fill="#d7e3ee">'
                f'{escape(col["name"])} : {escape(col["type"])} {meta}</text>'
            )
        sample_html = ""
        if include_data and t.get("sample_rows"):
            sy = y + 24 + n * 18 + 14
            for sr in t["sample_rows"][:2]:
                vals = ", ".join(str(v)[:12] for v in sr.values())
                sample_html += (
                    f'<text x="{x + 10}" y="{sy}" font-size="10" font-family="monospace" '
                    f'fill="#8899aa">{escape(vals[:36])}</text>'
                )
                sy += 14
        svg_parts.append(
            f'<g>'
            f'<rect x="{x}" y="{y}" width="{card_w}" height="{h}" rx="8" '
            f'fill="#101a24" stroke="#2d4a6f" stroke-width="1.5"/>'
            f'<rect x="{x}" y="{y}" width="{card_w}" height="22" rx="8" fill="#1e3a5f"/>'
            f'<text x="{x + 10}" y="{y + 15}" font-size="13" font-weight="bold" '
            f'fill="#7dd3fc" font-family="monospace">{escape(t["name"])}</text>'
            f'{"".join(rows)}'
            f'{sample_html}'
            f'</g>'
        )

    # Стрелки FK.
    for t in tables:
        for fk in t["fks"]:
            src = positions.get(t["name"])
            dst = positions.get(fk["table"])
            if not src or not dst:
                continue
            x1, y1 = src[0] + card_w, src[1] + 30
            x2, y2 = dst[0], dst[1] + 30
            mx = (x1 + x2) / 2
            svg_parts.append(
                f'<path d="M{x1},{y1} C{mx},{y1} {mx},{y2} {x2},{y2}" '
                f'fill="none" stroke="#34d399" stroke-width="1.5" '
                f'marker-end="url(#arrow)"/>'
            )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_w}" height="{total_h}" '
        f'style="background:#0d121f;border-radius:8px;max-width:100%;overflow:auto">'
        f'<defs><marker id="arrow" markerWidth="8" markerHeight="6" refX="7" refY="3" '
        f'orient="auto"><path d="M0,0 L8,3 L0,6 Z" fill="#34d399"/></marker></defs>'
        f'{"".join(svg_parts)}'
        f'</svg>'
    )
