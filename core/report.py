# core/report.py
from __future__ import annotations

import io
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Any
import pandas as pd

from core.utils import format_dutch_number

# Stijl- en lay-outconstanten (A4 in inches en genormaliseerde as-coördinaten).
_STIJL = {
    "page_portrait": (8.27, 11.69),  # A4-portret paginaformaat in inches.
    "page_landscape": (11.69, 8.27),  # A4-liggend paginaformaat in inches.
    "title_size": 18,  # Lettergrootte voor voorblad/titel.
    "subtitle_size": 11,  # Lettergrootte voor ondertitel.
    "section_title_size": 12,  # Lettergrootte voor sectietitels (paginatitels).
    "body_size": 9,  # Standaard lettergrootte voor bodytekst.
    "line_height": 0.022,  # Regelhoogte voor tekstblokken (as-eenheden).
    "section_gap": 0.018,  # Verticale ruimte tussen secties (as-eenheden).
    "margin_left": 0.06,  # Standaard linkermarge (as-eenheden).
    "margin_right": 0.94,  # Standaard rechtermarge (as-eenheden).
    "top": 0.94,  # Standaard bovenmarge (as-eenheden).
    "bottom": 0.06,  # Standaard ondermarge (as-eenheden).
    "grid_color": "#e5e7eb",  # Kleur van tabelrasterlijnen.
    "header_bg": "#d1d5db",  # Standaard achtergrond van tabelkop.
    "zebra_bg": "#f1f1f1",  # Achtergrondkleur voor afwisselende rijen.
    "row_bg": "#ffffff",  # Standaard achtergrondkleur van rij.
    "brand_blue": "#0b7ea3",  # Merkkleur voor koppen/headers.
    "muted": "#6b7280",  # Gedempte tekstkleur.
    "dpi": 300,  # Standaard DPI voor PDF-rendering.
}
_TABEL_REGEL_AFSTAND = 1.5  # Regelafstand binnen tabelceltekst.
_TABEL_LETTERGROOTTE = _STIJL["body_size"] + 1  # Lettergrootte voor tabellen.
_TABEL_RIJ_HOOGTE = (_TABEL_LETTERGROOTTE / 72) / _STIJL["page_portrait"][1]  # Basisrijhoogte.
_TABEL_RIJ_PADDING_FACTOR = 2.0  # Extra rijpadding (boven/onder) in rijhoogte-eenheden.

_NEDERLANDSE_MAANDEN = {  # Nederlandse maandnamen voor datumopmaak.
    1: "januari",
    2: "februari",
    3: "maart",
    4: "april",
    5: "mei",
    6: "juni",
    7: "juli",
    8: "augustus",
    9: "september",
    10: "oktober",
    11: "november",
    12: "december",
}

_ASSET_MAP = Path(__file__).resolve().parents[1] / "assets" / "report"  # Rapport-assets.
_VOORBLAD_ACHTERGROND = _ASSET_MAP / "Voorblad.png"  # Achtergrond voor het voorblad.
_SAMENVATTING_ACHTERGROND = _ASSET_MAP / "Samenvatting.png"  # Achtergrond voor samenvatting.
_WOONPLAATSEN_ACHTERGROND = _ASSET_MAP / "Woonplaatsen.png"  # Achtergrond top woonplaatsen.
_KAART_ACHTERGROND = _ASSET_MAP / "Kaart.png"  # Achtergrond kaartinstellingen.
_LAAG_ACHTERGROND = _ASSET_MAP / "Laag.png"  # Achtergrond laaginstellingen.
_EXTRAQT_LOGO_PAD = (  # EXTRAQT-logo gebruikt in de lagentabel.
    Path(__file__).resolve().parents[1]
    / "assets"
    / "logo"
    / "Logo EXTRAQT black.png"
)
_SAMENVATTING_INDELING = {
    "created": {"x": 0.1004, "y": 0.857},  # "Aangemaakt op"-labelpositie.
    "kpi_left": {
        "x": 0.1004,  # X-positie voor KPI-kolom links.
        "label_y": 0.81,  # Y-positie voor KPI-label.
        "value_y": 0.755,  # Y-positie voor KPI-waarde.
        "unit_y": 0.71,  # Y-positie voor KPI-eenheid.
    },
    "kpi_right": {
        "x": 0.48,  # X-positie voor KPI-kolom rechts.
        "label_y": 0.81,  # Y-positie voor KPI-label.
        "value_y": 0.755,  # Y-positie voor KPI-waarde.
        "unit_y": 0.71,  # Y-positie voor KPI-eenheid.
    },
    "map": {"x": 0.1070, "y": 0.105, "w": 0.7908, "h": 0.515},  # Kaartkader (bbox).
}
_WOONPLAATSEN_INDELING = {
    "table_bbox": [0.10, 0.18, 0.80, 0.66],  # Tabelkader (bbox) [x, y, w, h].
}
_KAART_INDELING = {
    "table_bbox": [0.10, 0.18, 0.80, 0.66],  # Tabelkader (bbox) [x, y, w, h].
}
_LAAG_INDELING = {
    "table_bbox": [0.10, 0.18, 0.80, 0.66],  # Tabelkader (bbox) [x, y, w, h].
}


def _lazy_matplotlib():
    import importlib

    mpl = importlib.import_module("matplotlib")
    try:
        mpl.use("Agg")
    except Exception:
        pass
    plt = importlib.import_module("matplotlib.pyplot")
    PdfPages = importlib.import_module("matplotlib.backends.backend_pdf").PdfPages
    return plt, PdfPages


def _apply_report_style(plt) -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": _STIJL["body_size"],
            "axes.edgecolor": _STIJL["grid_color"],
        }
    )


def _fmt_int(value: Any) -> str:
    try:
        return format_dutch_number(int(round(float(value))), 0)
    except Exception:
        return ""


def _fmt_float(value: Any, decimals: int = 2) -> str:
    try:
        return format_dutch_number(float(value), decimals)
    except Exception:
        return ""


def _format_selection(
    values: Any, *, max_items: int = 6, empty_label: str = "Alle"
) -> str:
    if values is None:
        return empty_label
    if isinstance(values, (list, tuple, set)):
        items = [str(v).strip() for v in values if str(v).strip()]
        if not items:
            return empty_label
        if len(items) <= max_items:
            return ", ".join(items)
        extra = len(items) - max_items
        return f"{', '.join(items[:max_items])} (+{extra} meer)"
    text = str(values).strip()
    return text or empty_label


def _toggle_label(value: bool) -> str:
    return "Aan" if value else "Uit"


def _format_range(values: Any) -> str:
    if isinstance(values, (list, tuple)) and len(values) == 2:
        return f"{_fmt_int(values[0])} - {_fmt_int(values[1])}"
    return _format_selection(values, empty_label="-")


def _format_year_range(values: Any) -> str:
    if isinstance(values, (list, tuple)) and len(values) == 2:
        try:
            start = int(round(float(values[0])))
        except Exception:
            start = values[0]
        try:
            end = int(round(float(values[1])))
        except Exception:
            end = values[1]
        return f"{start} - {end}"
    return _format_selection(values, empty_label="-")


def _wrap_cell_text(value: Any, *, width: int) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lines = text.splitlines() or [""]
    wrapped_lines: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if width <= 0:
            wrapped_lines.append(line)
            continue
        wrapped_lines.extend(
            textwrap.wrap(
                line,
                width=width,
                break_long_words=False,
                break_on_hyphens=False,
            )
        )
    return "\n".join(wrapped_lines)


def _wrap_table_cells(
    df: pd.DataFrame, col_widths: list[float], *, max_chars: int = 60
) -> tuple[pd.DataFrame, list[str]]:
    if df is None or df.empty:
        return df, []
    widths = col_widths or _table_column_widths(df)
    col_char_widths = [
        max(10, int(max_chars * width)) for width in (widths or [1.0])
    ]
    wrapped = df.copy()
    for idx, col in enumerate(df.columns):
        width = col_char_widths[idx] if idx < len(col_char_widths) else max_chars
        wrapped[col] = [_wrap_cell_text(v, width=width) for v in df[col].tolist()]
    header_labels = []
    for idx, col in enumerate(df.columns):
        width = col_char_widths[idx] if idx < len(col_char_widths) else max_chars
        header_labels.append(_wrap_cell_text(str(col), width=width))
    return wrapped, header_labels


def _expand_long_rows(
    rows: list[tuple[str, str]],
    *,
    labels: set[str],
    width: int = 60,
    max_lines_per_row: int = 6,
) -> list[tuple[str, str]]:
    expanded: list[tuple[str, str]] = []
    for label, value in rows:
        if label in labels:
            lines = _wrap_cell_text(value, width=width).splitlines()
            if not lines:
                expanded.append((label, value))
                continue
            if len(lines) <= max_lines_per_row:
                expanded.append((label, "\n".join(lines)))
            else:
                for idx in range(0, len(lines), max_lines_per_row):
                    chunk = lines[idx : idx + max_lines_per_row]
                    expanded.append((label if idx == 0 else "", "\n".join(chunk)))
        else:
            expanded.append((label, value))
    return expanded


def _row_weight(
    values: list[Any], line_spacing: float, padding_weight: float
) -> float:
    max_lines = 1
    for val in values:
        max_lines = max(max_lines, len(str(val).splitlines()))
    return 1 + (max_lines - 1) * line_spacing + padding_weight


def _split_table_by_weight(
    df: pd.DataFrame,
    col_widths: list[float],
    *,
    max_chars: int,
    max_weight: float,
    line_spacing: float,
    padding_weight: float,
) -> list[pd.DataFrame]:
    if df is None or df.empty:
        return [df]
    if max_weight <= 0:
        return [df]
    wrapped, _ = _wrap_table_cells(df, col_widths, max_chars=max_chars)
    chunks: list[pd.DataFrame] = []
    start = 0
    current_weight = 0.0
    for idx, row in enumerate(wrapped.itertuples(index=False)):
        weight = _row_weight(list(row), line_spacing, padding_weight)
        if current_weight + weight > max_weight and idx != start:
            chunks.append(df.iloc[start:idx])
            start = idx
            current_weight = 0.0
        current_weight += weight
    if start < len(df):
        chunks.append(df.iloc[start:])
    return chunks


def _normalize_unit(unit: str) -> str:
    unit_norm = (unit or "").strip()
    unit_norm = unit_norm.replace("²", "2")
    return unit_norm or "MWh/ha"


def _select_dpi(value: Any) -> int:
    try:
        dpi = int(round(float(value)))
    except Exception:
        return _STIJL["dpi"]
    return max(150, min(dpi, 600))


def _format_dutch_month_year(dt: datetime) -> str:
    month = _NEDERLANDSE_MAANDEN.get(dt.month, "")
    if not month:
        return str(dt.year)
    return f"{month} {dt.year}"


def _format_dutch_date(dt: datetime) -> str:
    month = _NEDERLANDSE_MAANDEN.get(dt.month, "")
    if not month:
        return dt.strftime("%Y-%m-%d")
    return f"{dt.day} {month} {dt.year}"


def _format_dutch_datetime(dt: datetime) -> str:
    month = _NEDERLANDSE_MAANDEN.get(dt.month, "")
    if not month:
        return dt.strftime("%Y-%m-%d %H:%M")
    return f"{dt.day} {month} {dt.year} {dt:%H:%M}"


def _load_image_array(path: Path):
    if not path.exists():
        return None
    plt, _ = _lazy_matplotlib()
    try:
        return plt.imread(str(path))
    except Exception:
        return None


def _as_schaal(ax, x: float, y: float, width: float, height: float) -> tuple[float, float]:
    try:
        (x0, y0) = ax.transData.transform((x, y))
        (x1, y1) = ax.transData.transform((x + width, y))
        (x2, y2) = ax.transData.transform((x, y + height))
        schaal_x = abs(x1 - x0) / max(width, 1e-9)
        schaal_y = abs(y2 - y0) / max(height, 1e-9)
        return max(schaal_x, 1e-9), max(schaal_y, 1e-9)
    except Exception:
        return 1.0, 1.0


def _kader_ratio(ax, x: float, y: float, width: float, height: float) -> float:
    try:
        schaal_x, schaal_y = _as_schaal(ax, x, y, width, height)
        return (width * schaal_x) / max(height * schaal_y, 1e-9)
    except Exception:
        return width / height if height else 1


def _draw_image_contain(
    ax,
    img,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    zorder: int | None = None,
):
    img_h, img_w = img.shape[0], img.shape[1]
    if img_h == 0 or img_w == 0:
        return
    box_ratio = _kader_ratio(ax, x, y, width, height)
    schaal_x, schaal_y = _as_schaal(ax, x, y, width, height)
    img_ratio = img_w / img_h if img_h else 1
    if img_ratio >= box_ratio:
        new_w = width
        new_h = (new_w * schaal_x) / max(img_ratio * schaal_y, 1e-9)
    else:
        new_h = height
        new_w = img_ratio * new_h * (schaal_y / max(schaal_x, 1e-9))
    x0 = x + (width - new_w) / 2
    y0 = y + (height - new_h) / 2
    ax.imshow(
        img, extent=[x0, x0 + new_w, y0, y0 + new_h], zorder=zorder, aspect="auto"
    )


def _draw_image_cover(
    ax,
    img,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    zorder: int | None = None,
):
    img_h, img_w = img.shape[0], img.shape[1]
    if img_h == 0 or img_w == 0:
        return
    box_ratio = _kader_ratio(ax, x, y, width, height)
    img_ratio = img_w / img_h if img_h else 1
    if img_ratio >= box_ratio:
        new_w = int(round(img_h * box_ratio))
        x0 = max(0, (img_w - new_w) // 2)
        cropped = img[:, x0 : x0 + new_w]
    else:
        new_h = int(round(img_w / box_ratio))
        y0 = max(0, (img_h - new_h) // 2)
        cropped = img[y0 : y0 + new_h, :]
    ax.imshow(
        cropped,
        extent=[x, x + width, y, y + height],
        zorder=zorder,
        aspect="auto",
        interpolation="lanczos",
    )


def _render_cover_page(title: str, *, month_year: str, background: Path | None = None):
    plt, _ = _lazy_matplotlib()
    _apply_report_style(plt)
    fig = plt.figure(figsize=_STIJL["page_portrait"])
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_position([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    bg = _load_image_array(background) if background else None
    if bg is not None:
        ax.set_aspect("auto")
        ax.imshow(bg, extent=[0, 1, 0, 1], aspect="auto", interpolation="lanczos")
    ax.text(
        0.92,
        0.94,
        month_year,
        fontsize=12,
        color="#ffffff",
        fontweight="bold",
        ha="right",
        va="top",
    )
    return fig


def _render_summary_page(
    *,
    kpis: list[tuple[str, str, str]],
    map_image: bytes | None,
    background: Path | None = None,
):
    plt, _ = _lazy_matplotlib()
    _apply_report_style(plt)
    fig = plt.figure(figsize=_STIJL["page_portrait"])
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_position([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    bg = _load_image_array(background) if background else None
    if bg is not None:
        ax.set_aspect("auto")
        ax.imshow(bg, extent=[0, 1, 0, 1], aspect="auto", interpolation="lanczos")
    for idx, (label, value, unit) in enumerate(kpis):
        layout = _SAMENVATTING_INDELING["kpi_left"] if idx == 0 else _SAMENVATTING_INDELING["kpi_right"]
        ax.text(
            layout["x"],
            layout["label_y"],
            label,
            fontsize=16,
            color="#ffffff",
            fontweight="bold",
            va="center",
        )
        ax.text(
            layout["x"],
            layout["value_y"],
            value,
            fontsize=32,
            color="#ffffff",
            fontweight="bold",
            va="center",
        )
        ax.text(
            layout["x"],
            layout["unit_y"],
            unit,
            fontsize=16,
            color="#ffffff",
            va="center",
        )

    map_cfg = _SAMENVATTING_INDELING["map"]
    map_left = map_cfg["x"]
    map_bottom = map_cfg["y"]
    map_width = map_cfg["w"]
    map_height = map_cfg["h"]
    from matplotlib.patches import Rectangle

    ax.add_patch(
        Rectangle(
            (map_left, map_bottom),
            map_width,
            map_height,
            linewidth=0.8,
            edgecolor="#e5e7eb",
            facecolor="none",
        )
    )
    if map_image:
        try:
            import matplotlib.image as mpimg

            img = mpimg.imread(io.BytesIO(map_image))
            _draw_image_cover(
                ax,
                img,
                x=map_left,
                y=map_bottom,
                width=map_width,
                height=map_height,
            )
        except Exception:
            ax.text(
                map_left + map_width / 2,
                map_bottom + map_height / 2,
                "Kaartafbeelding kon niet worden geladen.",
                fontsize=9,
                color=_STIJL["muted"],
                ha="center",
                va="center",
            )
    else:
        ax.text(
            map_left + map_width / 2,
            map_bottom + map_height / 2,
            "Kaartafbeelding ontbreekt.",
            fontsize=9,
            color=_STIJL["muted"],
            ha="center",
            va="center",
        )
    return fig


def _add_page_number(fig, page_num: int) -> None:
    fig.text(
        0.90,
        0.045,
        f"{page_num:02d}",
        fontsize=13,
        color="#04232e",
        fontweight="bold",
        ha="right",
        va="bottom",
    )


def _table_column_widths(df: pd.DataFrame, max_chars: int = 24) -> list[float]:
    if df is None or df.empty:
        return []
    widths = []
    for col in df.columns:
        values = [str(col)]
        values.extend([str(v) for v in df[col].head(20)])
        max_len = max(len(v) for v in values if v is not None)
        widths.append(min(max_len, max_chars))
    total = sum(widths) or 1
    return [w / total for w in widths]


def _compute_totals(
    df_filtered: pd.DataFrame, participatie_pct: int
) -> tuple[int, int, int, int, int]:
    if df_filtered is None or df_filtered.empty:
        return 0, 0, 0, 0, 0
    if "aantal_huizen" in df_filtered.columns:
        s_panden = pd.to_numeric(
            df_filtered["aantal_huizen"], errors="coerce"
        ).fillna(0)
        totaal_panden = int(s_panden.sum())
    else:
        totaal_panden = int(len(df_filtered))

    mwh_col = "sum_mwh_raw" if "sum_mwh_raw" in df_filtered.columns else None
    if mwh_col is None and "gemiddeld_jaarverbruik_mWh" in df_filtered.columns:
        mwh_col = "gemiddeld_jaarverbruik_mWh"
    if mwh_col:
        s_mwh = pd.to_numeric(df_filtered[mwh_col], errors="coerce").fillna(0)
        totaal_mwh = int(round(float(s_mwh.sum())))
    else:
        totaal_mwh = 0
    pct = int(participatie_pct) if participatie_pct is not None else 0
    panden_part = round(totaal_panden * pct / 100)
    mwh_part = round(totaal_mwh * pct / 100)
    records = int(len(df_filtered))
    return records, totaal_panden, totaal_mwh, panden_part, mwh_part


def _build_top_woonplaatsen_table(
    df_filtered: pd.DataFrame, *, top_n: int = 15
) -> pd.DataFrame:
    if df_filtered is None or df_filtered.empty:
        return pd.DataFrame()
    col_wp = "woonplaats"
    col_mwh = "gemiddeld_jaarverbruik_mWh"
    col_area = "area_ha"
    col_density = "MWh_per_ha"
    available_cols = set(df_filtered.columns)
    use_area = col_area in available_cols
    use_density_col = col_density in available_cols
    base_cols = [col_wp, col_mwh]
    if use_area:
        base_cols.append(col_area)
    elif use_density_col:
        base_cols.append(col_density)
    if not set(base_cols).issubset(available_cols):
        return pd.DataFrame()
    df_wp = df_filtered.loc[:, base_cols].copy()
    if df_wp.empty:
        return pd.DataFrame()
    df_wp[col_mwh] = pd.to_numeric(df_wp[col_mwh], errors="coerce").fillna(0)
    agg_map: dict[str, str] = {col_mwh: "sum"}
    density_source = None
    if use_area:
        df_wp[col_area] = pd.to_numeric(df_wp[col_area], errors="coerce").fillna(0)
        agg_map[col_area] = "sum"
        density_source = "area"
    elif use_density_col:
        df_wp[col_density] = pd.to_numeric(df_wp[col_density], errors="coerce")
        agg_map[col_density] = "mean"
        density_source = "col"
    top_wp = (
        df_wp.groupby(col_wp, as_index=False, sort=False, observed=True)
        .agg(agg_map)
        .rename(columns={col_mwh: "MWh"})
        .sort_values("MWh", ascending=False)
        .head(top_n)
    )
    out_cols = ["Woonplaats", "MWh"]
    top_wp.rename(columns={col_wp: "Woonplaats"}, inplace=True)
    if use_area and col_area in top_wp.columns:
        top_wp["Warmtevraag per ha (MWh)"] = top_wp["MWh"].div(
            top_wp[col_area].replace(0, pd.NA)
        )
        top_wp.rename(columns={col_area: "Oppervlakte (ha)"}, inplace=True)
        out_cols.extend(["Oppervlakte (ha)", "Warmtevraag per ha (MWh)"])
    elif density_source == "col" and col_density in top_wp.columns:
        top_wp.rename(columns={col_density: "Warmtevraag per ha (MWh)"}, inplace=True)
        out_cols.append("Warmtevraag per ha (MWh)")
    for col in top_wp.columns:
        if col == "MWh":
            top_wp[col] = top_wp[col].map(_fmt_int)
        elif col in ("Oppervlakte (ha)", "Warmtevraag per ha (MWh)"):
            top_wp[col] = top_wp[col].map(lambda v: _fmt_float(v, 2))
    return top_wp.loc[:, [c for c in out_cols if c in top_wp.columns]]


def _build_sites_table(
    sites_costed: pd.DataFrame | list | None, *, max_rows: int = 12
) -> pd.DataFrame:
    if sites_costed is None:
        return pd.DataFrame()
    if isinstance(sites_costed, list):
        df_sites = pd.DataFrame(sites_costed)
    else:
        df_sites = sites_costed.copy()
    if df_sites.empty:
        return pd.DataFrame()
    cols_keep = [
        "site_rank",
        "gebied_label",
        "connected_buildings",
        "connected_MWh",
        "utilization_pct",
        "indicatieve_kosten_site",
    ]
    have = [c for c in cols_keep if c in df_sites.columns]
    if not have:
        return pd.DataFrame()
    out = df_sites.loc[:, have].copy()
    if "site_rank" in out.columns:
        out = out.sort_values("site_rank", ascending=True)
    if max_rows:
        out = out.head(max_rows)

    rename_map = {
        "site_rank": "Voorziening #",
        "gebied_label": "Gebied",
        "connected_buildings": "Aangesloten gebouwen",
        "connected_MWh": "Aangesloten MWh",
        "utilization_pct": "Benutting (%)",
        "indicatieve_kosten_site": "Indicatieve jaarkosten (EUR)",
    }
    out.rename(columns=rename_map, inplace=True)
    for col in ["Voorziening #", "Aangesloten gebouwen", "Aangesloten MWh"]:
        if col in out.columns:
            out[col] = out[col].map(_fmt_int)
    if "Indicatieve jaarkosten (EUR)" in out.columns:
        out["Indicatieve jaarkosten (EUR)"] = out[
            "Indicatieve jaarkosten (EUR)"
        ].map(_fmt_int)
    if "Benutting (%)" in out.columns:
        out["Benutting (%)"] = out["Benutting (%)"].map(lambda v: _fmt_float(v, 1))
    return out


def _render_sections_page(title: str, sections: list[dict[str, Any]]):
    plt, _ = _lazy_matplotlib()
    _apply_report_style(plt)
    fig, ax = plt.subplots(figsize=_STIJL["page_portrait"])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    x_left = _STIJL["margin_left"]
    x_right = _STIJL["margin_right"]
    y = _STIJL["top"]
    ax.text(
        x_left,
        y,
        title,
        fontsize=_STIJL["title_size"],
        fontweight="bold",
        va="top",
    )
    y -= 0.032
    ax.text(
        x_left,
        y,
        "Rapportoverzicht",
        fontsize=_STIJL["subtitle_size"],
        color=_STIJL["muted"],
        va="top",
    )
    y -= 0.02
    ax.hlines(y, x_left, x_right, color=_STIJL["grid_color"], linewidth=0.8)
    y -= 0.02

    for section in sections:
        section_title = section.get("title", "")
        lines = section.get("lines", []) or []
        ax.text(
            x_left,
            y,
            section_title,
            fontsize=_STIJL["section_title_size"],
            fontweight="bold",
            va="top",
        )
        y -= 0.022
        for line in lines:
            wrapped = textwrap.wrap(str(line), 95) or [""]
            for entry in wrapped:
                ax.text(
                    x_left + 0.01,
                    y,
                    f"- {entry}",
                    fontsize=_STIJL["body_size"],
                    va="top",
                )
                y -= _STIJL["line_height"]
        y -= _STIJL["section_gap"]
        if y < _STIJL["bottom"]:
            break
    return fig


def _render_table_page(
    title: str,
    df: pd.DataFrame,
    empty_message: str,
    *,
    background: Path | None = None,
    table_bbox: list[float] | None = None,
    show_title: bool = True,
    show_header: bool = True,
    bold_first_col: bool = False,
    bold_first_col_rows: list[int] | None = None,
    overlay_images: list[dict[str, Any]] | None = None,
    max_row_height: float | None = None,
    col_widths: list[float] | None = None,
    start_with_row_bg: bool = False,
    bold_rows_white: bool = False,
    header_bg: str | None = None,
    header_text_color: str | None = None,
):
    plt, _ = _lazy_matplotlib()
    _apply_report_style(plt)
    fig = plt.figure(figsize=_STIJL["page_portrait"])
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_position([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    bg = _load_image_array(background) if background else None
    if bg is not None:
        ax.imshow(bg, extent=[0, 1, 0, 1], aspect="auto")
    if show_title:
        x_left = _STIJL["margin_left"]
        ax.text(
            x_left,
            _STIJL["top"],
            title,
            fontsize=_STIJL["section_title_size"] + 2,
            fontweight="bold",
            va="top",
        )
    if df is None or df.empty:
        msg_x = _STIJL["margin_left"]
        msg_y = 0.88
        ax.text(msg_x, msg_y, empty_message, fontsize=_STIJL["body_size"], va="top")
        return fig

    col_widths = col_widths or _table_column_widths(df)
    if table_bbox is None:
        x_left = _STIJL["margin_left"]
        x_right = _STIJL["margin_right"]
        table_bbox = [x_left, 0.08, x_right - x_left, 0.8]

    line_spacing = _TABEL_REGEL_AFSTAND
    df_wrapped, header_labels = _wrap_table_cells(df, col_widths, max_chars=60)

    row_lines = []
    if show_header:
        row_lines.append(
            _row_weight(header_labels, line_spacing, _TABEL_RIJ_PADDING_FACTOR)
        )
    for _, row in df_wrapped.iterrows():
        row_lines.append(
            _row_weight(row.tolist(), line_spacing, _TABEL_RIJ_PADDING_FACTOR)
        )
    total_weight = sum(row_lines) or 1
    base_row_height = None
    if max_row_height is not None:
        base_row_height = max_row_height
        used_height = base_row_height * total_weight
        if used_height > table_bbox[3]:
            scale = table_bbox[3] / used_height
            base_row_height *= scale
            used_height = table_bbox[3]
        y_top = table_bbox[1] + table_bbox[3]
        table_bbox = [table_bbox[0], y_top - used_height, table_bbox[2], used_height]

    table = ax.table(
        cellText=df_wrapped.values.tolist(),
        colLabels=header_labels if show_header else None,
        cellLoc="left",
        colLoc="left",
        colWidths=col_widths or None,
        bbox=table_bbox,
    )
    table.auto_set_font_size(False)
    table.set_fontsize(_TABEL_LETTERGROOTTE)

    if base_row_height is not None:
        row_heights = [base_row_height * weight for weight in row_lines]
    else:
        row_heights = [table_bbox[3] * (weight / total_weight) for weight in row_lines]
    row_offset = 1 if show_header else 0
    col_widths = col_widths or []
    col_positions = []
    x_cursor = table_bbox[0]
    if col_widths:
        for width in col_widths:
            col_positions.append((x_cursor, table_bbox[2] * width))
            x_cursor += table_bbox[2] * width
    bold_rows = set(bold_first_col_rows or [])

    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor(_STIJL["grid_color"])
        cell.set_linewidth(0.4)
        cell.PAD = 0.05
        if row < len(row_heights):
            cell.set_height(row_heights[row])
        data_row = row - row_offset
        if show_header and row == 0:
            if header_text_color:
                cell.set_text_props(weight="bold", color=header_text_color)
            else:
                cell.set_text_props(weight="bold")
            cell.set_facecolor(header_bg or _STIJL["header_bg"])
        else:
            if bold_rows_white and data_row in bold_rows:
                cell.set_facecolor(_STIJL["row_bg"])
            elif bold_rows_white:
                cell.set_facecolor(_STIJL["zebra_bg"])
            elif start_with_row_bg:
                cell.set_facecolor(
                    _STIJL["row_bg"] if data_row % 2 == 0 else _STIJL["zebra_bg"]
                )
            else:
                cell.set_facecolor(
                    _STIJL["zebra_bg"] if data_row % 2 == 0 else _STIJL["row_bg"]
                )
            if col == 0 and (bold_first_col or data_row in bold_rows):
                cell.set_text_props(weight="bold")
        text = cell.get_text()
        text.set_va("center")
        text.set_linespacing(line_spacing)

    renderer = None
    if overlay_images:
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        for item in overlay_images:
            path = item.get("path")
            if not path:
                continue
            img = _load_image_array(Path(path))
            if img is None:
                continue
            row_index = item.get("row")
            if row_index is None:
                continue
            table_row = row_index + row_offset
            if table_row >= len(row_heights):
                continue
            row_top = table_bbox[1] + table_bbox[3] - sum(row_heights[:table_row])
            row_height = row_heights[table_row]
            height_ratio = float(item.get("height_ratio", 0.55))
            width_ratio = float(item.get("width_ratio", 0.22))
            col_idx = int(item.get("col", 0))
            if col_positions and 0 <= col_idx < len(col_positions):
                col_x, col_w = col_positions[col_idx]
            else:
                col_x, col_w = table_bbox[0], table_bbox[2]
            pad_x = float(item.get("pad_x", 0.01))
            anchor = str(item.get("anchor", "")).lower()
            if anchor == "text_end" and renderer is not None:
                cell = table.get_celld().get((table_row, col_idx))
                if cell is None:
                    continue
                text = cell.get_text()
                bbox = text.get_window_extent(renderer=renderer)
                (x0, y0), (x1, y1) = ax.transAxes.inverted().transform(
                    [[bbox.x0, bbox.y0], [bbox.x1, bbox.y1]]
                )
                text_height = y1 - y0
                img_ratio = img.shape[1] / img.shape[0] if img.shape[0] else 1
                width_scale = float(item.get("width_scale", 1.0))
                box_height = row_height * height_ratio
                box_height = min(box_height, text_height * 1.2 if text_height else box_height)
                box_width = box_height * img_ratio * width_scale
                x = x1 + pad_x
                col_right = col_x + col_w
                if x + box_width > col_right - pad_x:
                    x = col_right - box_width - pad_x
                y = y0 + (text_height - box_height) / 2
            else:
                box_height = row_height * height_ratio
                box_width = col_w * width_ratio
                align = str(item.get("align", "right")).lower()
                if align == "left":
                    x = col_x + pad_x
                elif align == "center":
                    x = col_x + (col_w - box_width) / 2
                else:
                    x = col_x + col_w - box_width - pad_x
                y = row_top - row_height + (row_height - box_height) / 2
            _draw_image_contain(
                ax, img, x=x, y=y, width=box_width, height=box_height, zorder=3
            )
    return fig


def _render_image_page(title: str, image_bytes: bytes):
    plt, _ = _lazy_matplotlib()
    _apply_report_style(plt)
    page_size = _STIJL["page_portrait"]
    try:
        import matplotlib.image as mpimg

        img = mpimg.imread(io.BytesIO(image_bytes))
        if getattr(img, "shape", None) is not None:
            height = img.shape[0]
            width = img.shape[1]
            if width > height:
                page_size = _STIJL["page_landscape"]
    except Exception:
        img = None

    fig = plt.figure(figsize=page_size)
    fig.text(
        _STIJL["margin_left"],
        _STIJL["top"],
        title,
        fontsize=_STIJL["section_title_size"] + 2,
        fontweight="bold",
        va="top",
    )
    ax = fig.add_axes([_STIJL["margin_left"], 0.08, 0.88, 0.8])
    ax.axis("off")
    try:
        if img is None:
            import matplotlib.image as mpimg

            img = mpimg.imread(io.BytesIO(image_bytes))
        ax.imshow(img, interpolation="lanczos")
        ax.set_aspect("equal", adjustable="box")
    except Exception:
        ax.text(
            0.02,
            0.5,
            "Kaartafbeelding kon niet worden geladen.",
            fontsize=_STIJL["body_size"],
            va="center",
        )
    return fig


def build_report_pdf(
    df_filtered: pd.DataFrame,
    *,
    ui: dict[str, Any],
    layer_state: dict[str, Any],
    sites_costed: pd.DataFrame | list | None,
    heat_unit: str | None = None,
    threshold_display: float | None = None,
    map_image: bytes | None = None,
    report_title: str = "Friese Warmteatlas",
) -> bytes:
    plt, PdfPages = _lazy_matplotlib()
    buffer = io.BytesIO()
    now = datetime.now()
    heat_unit = heat_unit or ui.get("heat_unit") or "MWh/ha"
    unit_display = _normalize_unit(heat_unit)
    threshold_display = (
        threshold_display
        if threshold_display is not None
        else ui.get("threshold_display", ui.get("threshold", ""))
    )
    dpi = _select_dpi(ui.get("report_dpi"))
    participatie_pct = ui.get("participatie", 0)
    records, totaal_panden, totaal_mwh, panden_part, mwh_part = _compute_totals(
        df_filtered, participatie_pct
    )
    zoom_level = ui.get("zoom_level")
    gemeente_display = (
        "Alle (filter pas vanaf zoom 11)"
        if isinstance(zoom_level, int) and zoom_level <= 10
        else _format_selection(ui.get("gemeente_selectie"), max_items=999)
    )
    woonplaats_display = (
        "Alle (filter pas vanaf zoom 11)"
        if isinstance(zoom_level, int) and zoom_level <= 10
        else _format_selection(ui.get("woonplaats_selectie"), max_items=999)
    )
    deelname_detail = (
        f"{participatie_pct}%\n"
        f"Warmtevraag: {_fmt_int(mwh_part)} MWh\n"
        f"Aantal panden: {_fmt_int(panden_part)}"
    )
    kaart_rows = [
        ("Zoomniveau", _format_selection(zoom_level, empty_label="-")),
        ("Eenheid warmtevraag", unit_display),
        ("Gemeente", gemeente_display),
        ("Woonplaats", woonplaats_display),
        (
            "Energieklasse",
            _format_selection(
                ui.get("energieklasse_selectie"),
                max_items=999,
            ),
        ),
        ("Bouwjaar", _format_year_range(ui.get("bouwjaar_range"))),
        ("Type pand", _format_selection(ui.get("pand_selectie"), max_items=999, empty_label="-")),
        ("Deelnamegraad", deelname_detail),
    ]
    kaart_table = pd.DataFrame(
        {
            "Instelling": [label for label, _ in kaart_rows],
            "Waarde": [value for _, value in kaart_rows],
        }
    )

    aquathermie_status = ""
    layers_rows: list[tuple[str, str, bool]] = [
        ("Warmtevraag", "", True),
        ("  Gasverbruik", _toggle_label(bool(ui.get("show_main_layer"))), False),
        (
            "  Aandachtsgebieden",
            _toggle_label(bool(ui.get("show_indicative_layer"))),
            False,
        ),
        ("Potentiële warmtenetten", "", True),
        (
            "  Warmtenet op basis van warmtebron",
            _toggle_label(bool(layer_state.get("warmtenet"))),
            False,
        ),
        (
            "  Warmtenet op basis van warmtevraag",
            _toggle_label(bool(layer_state.get("wegennet"))),
            False,
        ),
        ("Aquathermie", aquathermie_status, True),
        (
            "  Waterlichamen",
            _toggle_label(bool(layer_state.get("water_potentie"))),
            False,
        ),
        (
            "  Buurtpotentie uit Aquathermie",
            _toggle_label(bool(layer_state.get("buurt_potentie"))),
            False,
        ),
        ("Sociale indicatoren", "", True),
        (
            "  Energiearmoede",
            _toggle_label(bool(layer_state.get("energiearmoede"))),
            False,
        ),
        ("  Koopwoningen", _toggle_label(bool(layer_state.get("koopwoningen"))), False),
        (
            "  Wooncorporatie",
            _toggle_label(bool(layer_state.get("wooncorporatie"))),
            False,
        ),
    ]
    layers_table = pd.DataFrame(
        {
            "Laag": [label for label, _, _ in layers_rows],
            "Status": [status for _, status, _ in layers_rows],
        }
    )
    layers_bold_rows = [
        idx for idx, (_, _, is_header) in enumerate(layers_rows) if is_header
    ]
    extraqt_row = next(
        (idx for idx, (label, _, _) in enumerate(layers_rows) if label == "Aquathermie"),
        None,
    )

    cover_date = _format_dutch_date(now)
    kpi_items = [
        ("WARMTEVRAAG", _fmt_int(totaal_mwh), "MWh"),
        ("PANDEN", _fmt_int(totaal_panden), "Aantal"),
    ]

    page_num = 0
    with PdfPages(buffer) as pdf:
        if _VOORBLAD_ACHTERGROND.exists():
            fig = _render_cover_page(
                report_title, month_year=cover_date, background=_VOORBLAD_ACHTERGROND
            )
            page_num += 1
            pdf.savefig(fig, dpi=dpi)
            plt.close(fig)

        if _SAMENVATTING_ACHTERGROND.exists():
            fig = _render_summary_page(
                kpis=kpi_items,
                map_image=map_image,
                background=_SAMENVATTING_ACHTERGROND,
            )
            page_num += 1
            _add_page_number(fig, page_num)
            pdf.savefig(fig, dpi=dpi)
            plt.close(fig)
        else:
            fig = _render_sections_page(report_title, sections)
            page_num += 1
            _add_page_number(fig, page_num)
            pdf.savefig(fig, dpi=dpi)
            plt.close(fig)
            if map_image:
                fig = _render_image_page("Kaart", map_image)
                page_num += 1
                _add_page_number(fig, page_num)
                pdf.savefig(fig, dpi=dpi)
                plt.close(fig)

        top_wp = _build_top_woonplaatsen_table(df_filtered)
        max_row_height = _TABEL_RIJ_HOOGTE if _WOONPLAATSEN_ACHTERGROND.exists() else None
        table_bbox = (
            _WOONPLAATSEN_INDELING["table_bbox"] if _WOONPLAATSEN_ACHTERGROND.exists() else None
        )
        show_title = not _WOONPLAATSEN_ACHTERGROND.exists()
        show_header = True
        if top_wp is None or top_wp.empty:
            fig = _render_table_page(
                "Top woonplaatsen (MWh)",
                top_wp,
                "Geen gegevens om te tonen.",
                background=_WOONPLAATSEN_ACHTERGROND if _WOONPLAATSEN_ACHTERGROND.exists() else None,
                table_bbox=table_bbox,
                show_title=show_title,
                show_header=show_header,
                max_row_height=max_row_height,
                header_bg=_STIJL["brand_blue"],
                header_text_color=_STIJL["row_bg"],
            )
            page_num += 1
            _add_page_number(fig, page_num)
            pdf.savefig(fig, dpi=dpi)
            plt.close(fig)
        else:
            if table_bbox and max_row_height:
                header_rows = 1 if show_header else 0
                row_weight = 1 + _TABEL_RIJ_PADDING_FACTOR
                max_total_rows = int(table_bbox[3] / (max_row_height * row_weight))
                max_rows = max(1, max_total_rows - header_rows)
            else:
                max_rows = len(top_wp)
            for start in range(0, len(top_wp), max_rows):
                chunk = top_wp.iloc[start : start + max_rows]
                fig = _render_table_page(
                    "Top woonplaatsen (MWh)",
                    chunk,
                    "Geen gegevens om te tonen.",
                    background=_WOONPLAATSEN_ACHTERGROND if _WOONPLAATSEN_ACHTERGROND.exists() else None,
                    table_bbox=table_bbox,
                    show_title=show_title,
                    show_header=show_header,
                    max_row_height=max_row_height,
                    header_bg=_STIJL["brand_blue"],
                    header_text_color=_STIJL["row_bg"],
                )
                page_num += 1
                _add_page_number(fig, page_num)
                pdf.savefig(fig, dpi=dpi)
                plt.close(fig)

        kaart_bbox = _KAART_INDELING["table_bbox"] if _KAART_ACHTERGROND.exists() else None
        kaart_show_title = not _KAART_ACHTERGROND.exists()
        kaart_show_header = not _KAART_ACHTERGROND.exists()
        kaart_max_row_height = _TABEL_RIJ_HOOGTE if _KAART_ACHTERGROND.exists() else None
        kaart_col_widths = [0.3, 0.7]
        kaart_chunks = [kaart_table]
        if kaart_bbox and kaart_max_row_height:
            header_weight = (
                1 + _TABEL_RIJ_PADDING_FACTOR if kaart_show_header else 0
            )
            max_total_weight = (kaart_bbox[3] / kaart_max_row_height) - header_weight
            kaart_chunks = _split_table_by_weight(
                kaart_table,
                kaart_col_widths,
                max_chars=60,
                max_weight=max_total_weight,
                line_spacing=_TABEL_REGEL_AFSTAND,
                padding_weight=_TABEL_RIJ_PADDING_FACTOR,
            )
        for chunk in kaart_chunks:
            fig = _render_table_page(
                "Kaart instellingen",
                chunk,
                "Geen gegevens om te tonen.",
                background=_KAART_ACHTERGROND if _KAART_ACHTERGROND.exists() else None,
                table_bbox=kaart_bbox,
                show_title=kaart_show_title,
                show_header=kaart_show_header,
                bold_first_col=True,
                max_row_height=kaart_max_row_height,
                start_with_row_bg=True,
                col_widths=kaart_col_widths,
            )
            page_num += 1
            _add_page_number(fig, page_num)
            pdf.savefig(fig, dpi=dpi)
            plt.close(fig)

        overlay_images = []
        if extraqt_row is not None and _EXTRAQT_LOGO_PAD.exists():
            overlay_images.append(
                {
                    "path": _EXTRAQT_LOGO_PAD,
                    "row": extraqt_row,
                    "col": 0,
                    "height_ratio": 0.6,
                    "anchor": "text_end",
                    "pad_x": 0.01,
                    "width_scale": 1.0,
                }
            )
        fig = _render_table_page(
            "Laag instellingen",
            layers_table,
            "Geen gegevens om te tonen.",
            background=_LAAG_ACHTERGROND if _LAAG_ACHTERGROND.exists() else None,
            table_bbox=_LAAG_INDELING["table_bbox"] if _LAAG_ACHTERGROND.exists() else None,
            show_title=not _LAAG_ACHTERGROND.exists(),
            show_header=not _LAAG_ACHTERGROND.exists(),
            bold_first_col_rows=layers_bold_rows,
            overlay_images=overlay_images,
            max_row_height=_TABEL_RIJ_HOOGTE if _LAAG_ACHTERGROND.exists() else None,
            col_widths=[0.82, 0.18] if _LAAG_ACHTERGROND.exists() else None,
            start_with_row_bg=True,
            bold_rows_white=True,
        )
        page_num += 1
        _add_page_number(fig, page_num)
        pdf.savefig(fig, dpi=dpi)
        plt.close(fig)

    return buffer.getvalue()
