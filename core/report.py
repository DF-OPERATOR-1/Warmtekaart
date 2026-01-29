"""PDF-rapportage generatie en helpers."""

# core/report.py
from __future__ import annotations

import gc
import io
import shutil
import tempfile
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Any
import pandas as pd
import numpy as np

from core.utils import format_dutch_number
from core.io import load_wegennet_summary

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
_MAX_IMAGE_DPI = 300  # Max DPI voor ingesloten afbeeldingen om RAM te beperken.
_JPEG_QUALITY = 80
_TABEL_REGEL_AFSTAND = 1.5  # Regelafstand binnen tabelceltekst.
_TABEL_LETTERGROOTTE = _STIJL["body_size"] + 1  # Lettergrootte voor tabellen.
_TABEL_RIJ_HOOGTE = (_TABEL_LETTERGROOTTE / 72) / _STIJL["page_portrait"][
    1
]  # Basisrijhoogte.
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

_ASSET_MAP = (
    Path(__file__).resolve().parents[1] / "assets" / "report"
)  # Rapport-assets.
_VOORBLAD_ACHTERGROND = _ASSET_MAP / "Voorblad.png"  # Achtergrond voor het voorblad.
_SAMENVATTING_ACHTERGROND = (
    _ASSET_MAP / "Samenvatting.png"
)  # Achtergrond voor samenvatting.
_WOONPLAATSEN_ACHTERGROND = (
    _ASSET_MAP / "Woonplaatsen.png"
)  # Achtergrond top woonplaatsen.
_WOONPLAATSEN_PAND_ACHTERGROND = _ASSET_MAP / "Woonplaatsen_pand.png"
_WARMTENET_VRAAG_ACHTERGROND = _ASSET_MAP / "Warmtenet_vraag.png"
_WARMTENET_PANDEN_ACHTERGROND = _ASSET_MAP / "Warmtenet_panden.png"
_WARMTENET_KOSTEN_ACHTERGROND = _ASSET_MAP / "Warmtenet_kosten.png"
_KAART_ACHTERGROND = _ASSET_MAP / "Kaart.png"  # Achtergrond kaartinstellingen.
_LAAG_ACHTERGROND = _ASSET_MAP / "Laag.png"  # Achtergrond laaginstellingen.
_EXTRAQT_LOGO_PAD = (  # EXTRAQT-logo gebruikt in de lagentabel.
    Path(__file__).resolve().parents[1] / "assets" / "logo" / "Logo EXTRAQT black.png"
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
    "table_bbox": [0.10, 0.16, 0.80, 0.66],  # Tabelkader (bbox) [x, y, w, h].
}
_WOONPLAATSEN_PAND_INDELING = {
    "table_bbox": [0.10, 0.16, 0.80, 0.66],
}
_WARMTENET_INDELING = {
    "table_bbox": [0.10, 0.14, 0.80, 0.66],  # Tabelkader (bbox) [x, y, w, h].
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


def _row_height_for_font(font_size: float) -> float:
    return (font_size / 72) / _STIJL["page_portrait"][1]


def _apply_report_style(plt) -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": _STIJL["body_size"],
            "axes.edgecolor": _STIJL["grid_color"],
            "pdf.compression": 9,
        }
    )


def _close_figure(fig, plt) -> None:
    try:
        fig.clear()
    except Exception:
        pass
    plt.close(fig)
    gc.collect()


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


def _wrap_cell_text(
    value: Any,
    *,
    width: int,
    break_long_words: bool = False,
    break_on_hyphens: bool = False,
) -> str:
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
                break_long_words=break_long_words,
                break_on_hyphens=break_on_hyphens,
            )
        )
    return "\n".join(wrapped_lines)


def _wrap_table_cells(
    df: pd.DataFrame,
    col_widths: list[float],
    *,
    max_chars: int = 60,
    break_long_words: bool = False,
    break_on_hyphens: bool = False,
) -> tuple[pd.DataFrame, list[str]]:
    if df is None or df.empty:
        return df, []
    widths = col_widths or _table_column_widths(df)
    col_char_widths = [max(10, int(max_chars * width)) for width in (widths or [1.0])]
    wrapped = df.copy()
    for idx, col in enumerate(df.columns):
        width = col_char_widths[idx] if idx < len(col_char_widths) else max_chars
        wrapped[col] = [
            _wrap_cell_text(
                v,
                width=width,
                break_long_words=break_long_words,
                break_on_hyphens=break_on_hyphens,
            )
            for v in df[col].tolist()
        ]
    header_labels = []
    for idx, col in enumerate(df.columns):
        width = col_char_widths[idx] if idx < len(col_char_widths) else max_chars
        header_labels.append(
            _wrap_cell_text(
                str(col),
                width=width,
                break_long_words=break_long_words,
                break_on_hyphens=break_on_hyphens,
            )
        )
    return wrapped, header_labels


def _normalize_woonplaats(value: str | None) -> str:
    return str(value or "").strip().lower()


def _normalize_woonplaats_list(values: list[str] | None) -> set[str]:
    return {
        _normalize_woonplaats(v) for v in (values or []) if _normalize_woonplaats(v)
    }


def _to_float(value) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _build_warmtenet_summary(gjson: dict | None) -> pd.DataFrame:
    cols = [
        "woonplaats",
        "woonplaats_norm",
        "warmtenet_ingezet_mwh",
        "warmtenet_object_mwh",
        "warmtenet_lengte_m",
        "warmtenet_aansluiting_lengte_m",
        "warmtenet_aangesloten_panden",
        "warmtenet_kosten_leidingen_euro",
        "warmtenet_kosten_aansluitingen_euro",
        "warmtenet_kosten_totaal_euro",
        "warmtenet_kosten_bronnen_euro",
        "warmtenet_kosten_bron_totaal_euro",
    ]
    if not gjson or not isinstance(gjson, dict):
        return pd.DataFrame(columns=cols)

    acc: dict[str, dict] = {}
    for feat in gjson.get("features", []) or []:
        if not isinstance(feat, dict):
            continue
        props = feat.get("properties") or {}
        wp_raw = props.get("woonplaats")
        if not wp_raw:
            continue
        wp = str(wp_raw).strip()
        wp_norm = _normalize_woonplaats(wp)
        if not wp_norm:
            continue
        entry = acc.setdefault(
            wp_norm,
            {
                "woonplaats": wp,
                "warmtenet_ingezet_mwh": 0.0,
                "warmtenet_object_mwh": 0.0,
                "warmtenet_lengte_m": 0.0,
                "warmtenet_aansluiting_lengte_m": 0.0,
                "warmtenet_aangesloten_panden": None,
                "warmtenet_kosten_leidingen_vals": set(),
                "warmtenet_kosten_aansluitingen_vals": set(),
                "warmtenet_kosten_totaal_vals": set(),
                "warmtenet_kosten_bronnen_vals": set(),
                "warmtenet_kosten_bron_totaal_vals": set(),
            },
        )
        if not entry.get("woonplaats"):
            entry["woonplaats"] = wp
        layer = str(props.get("layer") or "").strip().lower()
        if layer == "bron":
            entry["warmtenet_ingezet_mwh"] += _to_float(
                props.get("ingezet_mwh_per_jaar")
            )
        elif layer == "object":
            entry["warmtenet_object_mwh"] += _to_float(props.get("vraag_mwh_per_jaar"))
            afstand_val = props.get("afstand_pad_m")
            if afstand_val not in (None, ""):
                try:
                    entry["warmtenet_aansluiting_lengte_m"] += float(afstand_val)
                except Exception:
                    pass
        elif layer == "leiding":
            lengte_val = props.get("geometrie_lengte_m")
            if lengte_val is None:
                lengte_val = props.get("padlengte_m")
            entry["warmtenet_lengte_m"] += _to_float(lengte_val)
        aangesloten_val = props.get("plaats_aangesloten_objecten")
        if aangesloten_val not in (None, ""):
            try:
                aangesloten_num = float(aangesloten_val)
            except Exception:
                aangesloten_num = None
            if aangesloten_num is not None:
                current = entry.get("warmtenet_aangesloten_panden")
                if current is None or aangesloten_num > current:
                    entry["warmtenet_aangesloten_panden"] = aangesloten_num
        kosten_leiding = props.get("plaats_kosten_leidingen_euro")
        if kosten_leiding not in (None, ""):
            try:
                entry["warmtenet_kosten_leidingen_vals"].add(float(kosten_leiding))
            except Exception:
                pass
        kosten_aansl = props.get("plaats_kosten_aansluitingen_euro")
        if kosten_aansl not in (None, ""):
            try:
                entry["warmtenet_kosten_aansluitingen_vals"].add(float(kosten_aansl))
            except Exception:
                pass
        kosten_totaal = props.get("plaats_totale_kosten_euro")
        if kosten_totaal not in (None, ""):
            try:
                entry["warmtenet_kosten_totaal_vals"].add(float(kosten_totaal))
            except Exception:
                pass
        kosten_bronnen = props.get("plaats_kosten_bronnen_euro")
        if kosten_bronnen not in (None, ""):
            try:
                entry["warmtenet_kosten_bronnen_vals"].add(float(kosten_bronnen))
            except Exception:
                pass
        if layer == "bron":
            kosten_bron_totaal = props.get("bron_totale_kosten_euro")
            if kosten_bron_totaal not in (None, ""):
                try:
                    entry["warmtenet_kosten_bron_totaal_vals"].add(
                        float(kosten_bron_totaal)
                    )
                except Exception:
                    pass

    rows = []
    for wp_norm, entry in acc.items():

        def _sum_vals(values: set[float]) -> float | None:
            if not values:
                return None
            return float(sum(values))

        rows.append(
            {
                "woonplaats": entry.get("woonplaats") or "",
                "woonplaats_norm": wp_norm,
                "warmtenet_ingezet_mwh": entry["warmtenet_ingezet_mwh"],
                "warmtenet_object_mwh": entry["warmtenet_object_mwh"],
                "warmtenet_lengte_m": entry["warmtenet_lengte_m"],
                "warmtenet_aansluiting_lengte_m": entry[
                    "warmtenet_aansluiting_lengte_m"
                ],
                "warmtenet_aangesloten_panden": entry["warmtenet_aangesloten_panden"],
                "warmtenet_kosten_leidingen_euro": _sum_vals(
                    entry["warmtenet_kosten_leidingen_vals"]
                ),
                "warmtenet_kosten_aansluitingen_euro": _sum_vals(
                    entry["warmtenet_kosten_aansluitingen_vals"]
                ),
                "warmtenet_kosten_totaal_euro": _sum_vals(
                    entry["warmtenet_kosten_totaal_vals"]
                ),
                "warmtenet_kosten_bronnen_euro": _sum_vals(
                    entry["warmtenet_kosten_bronnen_vals"]
                ),
                "warmtenet_kosten_bron_totaal_euro": _sum_vals(
                    entry["warmtenet_kosten_bron_totaal_vals"]
                ),
            }
        )
    return pd.DataFrame(rows, columns=cols)


def _build_wegennet_summary(df_raw: pd.DataFrame | None) -> pd.DataFrame:
    cols = [
        "woonplaats",
        "woonplaats_norm",
        "wegennet_vraag_mwh",
        "wegennet_lengte_m",
        "wegennet_aansluitingen",
        "wegennet_aansluiting_lengte_m",
    ]
    if df_raw is None or df_raw.empty:
        return pd.DataFrame(columns=cols)
    df = df_raw.copy()
    rename_map = {
        "Woonplaats": "woonplaats",
        "Totaal Aantal Aansluitingen": "wegennet_aansluitingen",
        "Totaal Hittevraag (MWh/jaar)": "wegennet_vraag_mwh",
        "Totale Lengte Netwerk (m)": "wegennet_lengte_m",
        "Totale Lengte Aansluitingen (m)": "wegennet_aansluiting_lengte_m",
    }
    df.rename(columns=rename_map, inplace=True)
    keep_cols = [c for c in rename_map.values() if c in df.columns]
    if not keep_cols:
        return pd.DataFrame(columns=cols)
    df = df.loc[:, keep_cols]
    df["woonplaats"] = df["woonplaats"].astype(str).str.strip()
    df["woonplaats_norm"] = df["woonplaats"].map(_normalize_woonplaats)
    for col in [
        "wegennet_aansluitingen",
        "wegennet_vraag_mwh",
        "wegennet_lengte_m",
        "wegennet_aansluiting_lengte_m",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    return df.loc[:, cols]


def _build_basislaag_summary(df_filtered: pd.DataFrame | None) -> pd.DataFrame:
    cols = [
        "woonplaats",
        "woonplaats_norm",
        "basis_vraag_mwh",
        "basis_panden",
    ]
    if df_filtered is None or df_filtered.empty:
        return pd.DataFrame(columns=cols)
    if "woonplaats" not in df_filtered.columns:
        return pd.DataFrame(columns=cols)
    mwh_col = (
        "sum_mwh_raw"
        if "sum_mwh_raw" in df_filtered.columns
        else "gemiddeld_jaarverbruik_mWh"
    )
    if mwh_col not in df_filtered.columns:
        return pd.DataFrame(columns=cols)
    if "aantal_huizen" in df_filtered.columns:
        pand_col = "aantal_huizen"
    elif "aantal_VBOs" in df_filtered.columns:
        pand_col = "aantal_VBOs"
    else:
        pand_col = None

    base_cols = ["woonplaats", mwh_col]
    if pand_col:
        base_cols.append(pand_col)
    df = df_filtered.loc[:, base_cols].copy()
    df["woonplaats"] = df["woonplaats"].astype(str).str.strip()
    df[mwh_col] = pd.to_numeric(df[mwh_col], errors="coerce").fillna(0.0)
    if pand_col:
        df[pand_col] = pd.to_numeric(df[pand_col], errors="coerce").fillna(0.0)

    grouped = (
        df.groupby("woonplaats", as_index=False, sort=False, observed=True)
        .agg({mwh_col: "sum"})
        .rename(columns={mwh_col: "basis_vraag_mwh"})
    )
    if pand_col:
        pand = (
            df.groupby("woonplaats", as_index=False, sort=False, observed=True)
            .agg({pand_col: "sum"})
            .rename(columns={pand_col: "basis_panden"})
        )
    else:
        pand = (
            df.groupby("woonplaats", as_index=False, sort=False, observed=True)
            .size()
            .rename(columns={"size": "basis_panden"})
        )
    out = grouped.merge(pand, on="woonplaats", how="left")
    out["woonplaats_norm"] = out["woonplaats"].map(_normalize_woonplaats)
    return out.loc[:, cols]


def _build_warmtenet_report_tables(
    *,
    warmtenet_gjson: dict | None,
    df_filtered: pd.DataFrame | None,
    warmtenet_wp: list[str] | None,
    wegennet_wp: list[str] | None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame] | None:
    heat_loss_pct = 0.15
    conn_length_per_pand_m = 15.0
    cost_per_meter_net = 1000.0
    cost_per_meter_conn = 346.0

    warmtenet_df = _build_warmtenet_summary(warmtenet_gjson)
    wegennet_df = _build_wegennet_summary(load_wegennet_summary())
    if warmtenet_df.empty or wegennet_df.empty:
        return None

    warm_sel = _normalize_woonplaats_list(warmtenet_wp)
    weg_sel = _normalize_woonplaats_list(wegennet_wp)
    if warm_sel:
        warmtenet_df = warmtenet_df[warmtenet_df["woonplaats_norm"].isin(warm_sel)]
    if weg_sel:
        wegennet_df = wegennet_df[wegennet_df["woonplaats_norm"].isin(weg_sel)]
    if warmtenet_df.empty or wegennet_df.empty:
        return None

    merged = warmtenet_df.merge(
        wegennet_df,
        on="woonplaats_norm",
        how="inner",
        suffixes=("_warmtenet", "_wegennet"),
    )
    if merged.empty:
        return None

    basis_df = _build_basislaag_summary(df_filtered)
    if basis_df.empty:
        merged["basis_vraag_mwh"] = np.nan
        merged["basis_panden"] = np.nan
    else:
        merged = merged.merge(
            basis_df.loc[:, ["woonplaats_norm", "basis_vraag_mwh", "basis_panden"]],
            on="woonplaats_norm",
            how="left",
        )

    wp_warm = merged.get("woonplaats_warmtenet").fillna("").astype(str)
    wp_weg = merged.get("woonplaats_wegennet").fillna("").astype(str)
    merged["woonplaats_display"] = np.where(
        wp_warm.str.strip() != "",
        wp_warm,
        wp_weg,
    )

    warmtebron_raw = pd.to_numeric(
        merged.get("warmtenet_ingezet_mwh"), errors="coerce"
    ).fillna(0.0)
    warmtebron_fallback = pd.to_numeric(
        merged.get("warmtenet_object_mwh"), errors="coerce"
    ).fillna(0.0)
    warmtebron_mwh = np.where(warmtebron_raw > 0, warmtebron_raw, warmtebron_fallback)
    warmtebron_mwh = pd.Series(warmtebron_mwh, index=merged.index)
    warmtebron_mwh_loss = warmtebron_mwh * (1.0 - heat_loss_pct)

    basis_mwh = pd.to_numeric(merged.get("basis_vraag_mwh"), errors="coerce")
    wegennet_mwh = pd.to_numeric(
        merged.get("wegennet_vraag_mwh"), errors="coerce"
    ).fillna(0.0)
    basis_panden = pd.to_numeric(merged.get("basis_panden"), errors="coerce")
    wegennet_panden = pd.to_numeric(
        merged.get("wegennet_aansluitingen"), errors="coerce"
    ).fillna(0.0)
    warmtebron_panden = pd.to_numeric(
        merged.get("warmtenet_aangesloten_panden"), errors="coerce"
    ).fillna(0.0)

    onbenut_mwh = wegennet_mwh - warmtebron_mwh_loss
    dekking_pct = np.where(
        (wegennet_mwh > 0) & (~pd.isna(wegennet_mwh)),
        (warmtebron_mwh_loss / wegennet_mwh) * 100.0,
        np.nan,
    )

    panden_niet = wegennet_panden - warmtebron_panden
    panden_pct = np.where(
        (wegennet_panden > 0) & (~pd.isna(wegennet_panden)),
        (warmtebron_panden / wegennet_panden) * 100.0,
        np.nan,
    )

    wegennet_lengte_m = pd.to_numeric(
        merged.get("wegennet_lengte_m"), errors="coerce"
    ).fillna(0.0)
    warmtenet_lengte_m = pd.to_numeric(
        merged.get("warmtenet_lengte_m"), errors="coerce"
    ).fillna(0.0)
    wegennet_conn_m = pd.to_numeric(
        merged.get("wegennet_aansluiting_lengte_m"), errors="coerce"
    ).fillna(0.0)
    wegennet_conn_fallback = (wegennet_panden.fillna(0.0)) * conn_length_per_pand_m
    wegennet_conn_m = np.where(
        wegennet_conn_m > 0, wegennet_conn_m, wegennet_conn_fallback
    )
    wegennet_conn_m = pd.Series(wegennet_conn_m, index=merged.index)
    warmtenet_conn_m = warmtebron_panden.fillna(0.0) * conn_length_per_pand_m

    kosten_net_wegennet = wegennet_lengte_m * cost_per_meter_net
    kosten_conn_wegennet = wegennet_conn_m * cost_per_meter_conn
    kosten_tot_wegennet = kosten_net_wegennet + kosten_conn_wegennet

    kosten_net_warmtebron = pd.to_numeric(
        merged.get("warmtenet_kosten_leidingen_euro"), errors="coerce"
    ).fillna(0.0)
    kosten_conn_warmtebron = pd.to_numeric(
        merged.get("warmtenet_kosten_aansluitingen_euro"), errors="coerce"
    ).fillna(0.0)
    kosten_tot_warmtebron = kosten_net_warmtebron + kosten_conn_warmtebron
    kosten_bron_warmtebron = pd.to_numeric(
        merged.get("warmtenet_kosten_bronnen_euro"), errors="coerce"
    )
    kosten_bron_warmtebron = kosten_bron_warmtebron.fillna(0.0)

    def _format_series(series, decimals: int, *, prefix: str = "", suffix: str = ""):
        s = pd.to_numeric(series, errors="coerce")
        return s.map(
            lambda v: (
                ""
                if pd.isna(v)
                else f"{prefix}{format_dutch_number(v, decimals)}{suffix}"
            )
        )

    out_warmte = pd.DataFrame(
        {
            "Woonplaats": merged["woonplaats_display"],
            "Totaal (MWh)": basis_mwh,
            "Warmtenet uit\nwarmtebron (MWh)": warmtebron_mwh_loss,
            "Warmtenet uit\nwarmtevraag (MWh)": wegennet_mwh,
            "Onbenut (MWh)": onbenut_mwh,
            "Dekking (%)": dekking_pct,
        }
    ).sort_values("Woonplaats")
    for col in [
        "Totaal (MWh)",
        "Warmtenet uit\nwarmtebron (MWh)",
        "Warmtenet uit\nwarmtevraag (MWh)",
        "Onbenut (MWh)",
    ]:
        out_warmte[col] = _format_series(out_warmte[col], 1)
    out_warmte["Dekking (%)"] = _format_series(out_warmte["Dekking (%)"], 1, suffix="%")

    out_panden = pd.DataFrame(
        {
            "Woonplaats": merged["woonplaats_display"],
            "Aantal": basis_panden,
            "Aangesloten\nwarmtevraag": wegennet_panden,
            "Aangesloten\nwarmtebron": warmtebron_panden,
            "Niet\naangesloten": panden_niet,
            "% aangesloten": panden_pct,
        }
    ).sort_values("Woonplaats")
    for col in [
        "Aantal",
        "Aangesloten\nwarmtevraag",
        "Aangesloten\nwarmtebron",
        "Niet\naangesloten",
    ]:
        out_panden[col] = _format_series(out_panden[col], 0)
    out_panden["% aangesloten"] = _format_series(
        out_panden["% aangesloten"], 1, suffix="%"
    )

    def _fmt_len_cost(
        length_val,
        cost_val,
        length_label: str,
        cost_label: str,
        *,
        length_placeholder: str | None = None,
    ) -> str:
        length_txt = _format_series(pd.Series([length_val]), 0).iloc[0]
        cost_txt = _format_series(pd.Series([cost_val]), 0, prefix="€ ").iloc[0]
        parts = []
        if length_txt:
            parts.append(f"{length_label}: {length_txt}")
        elif length_placeholder is not None:
            parts.append(f"{length_label}: {length_placeholder}")
        if cost_txt:
            parts.append(f"{cost_label}: {cost_txt}")
        return "\n".join(parts)

    def _fmt_euro_only(value) -> str:
        return _format_series(pd.Series([value]), 0, prefix="€ ").iloc[0]

    def _cost_to_length(value):
        if value is None or pd.isna(value):
            return None
        return value / 1000.0

    out_leidingen_bron = pd.DataFrame(
        {
            "Woonplaats": merged["woonplaats_display"],
            "Type": "Bron",
            "Kosten netwerk": [
                _fmt_len_cost(
                    _cost_to_length(v_cost),
                    v_cost,
                    "Netwerk (m)",
                    "Kosten",
                    length_placeholder="-",
                )
                for v_cost in kosten_net_warmtebron
            ],
            "Kosten aansluiting": [
                _fmt_len_cost(
                    None,
                    v_cost,
                    "Aansluiting (m)",
                    "Kosten",
                    length_placeholder="-",
                )
                for v_cost in kosten_conn_warmtebron
            ],
            "Totale kosten": [_fmt_euro_only(v) for v in kosten_tot_warmtebron],
        }
    )
    out_leidingen_vraag = pd.DataFrame(
        {
            "Woonplaats": merged["woonplaats_display"],
            "Type": "Vraag",
            "Kosten netwerk": [
                _fmt_len_cost(
                    v_len,
                    v_cost,
                    "Netwerk (m)",
                    "Kosten",
                )
                for v_len, v_cost in zip(wegennet_lengte_m, kosten_net_wegennet)
            ],
            "Kosten aansluiting": [
                _fmt_len_cost(
                    v_len,
                    v_cost,
                    "Aansluiting (m)",
                    "Kosten",
                )
                for v_len, v_cost in zip(wegennet_conn_m, kosten_conn_wegennet)
            ],
            "Totale kosten": [_fmt_euro_only(v) for v in kosten_tot_wegennet],
        }
    )
    out_leidingen = pd.concat(
        [out_leidingen_bron, out_leidingen_vraag], ignore_index=True
    ).sort_values(["Woonplaats", "Type"])

    return out_warmte, out_panden, out_leidingen


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


def _row_weight(values: list[Any], line_spacing: float, padding_weight: float) -> float:
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
    return dpi


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


def _max_page_side_px(dpi: int) -> int:
    try:
        width_px = int(round(_STIJL["page_portrait"][0] * dpi))
        height_px = int(round(_STIJL["page_portrait"][1] * dpi))
    except Exception:
        return int(round(max(_STIJL["page_portrait"]) * _STIJL["dpi"]))
    return max(width_px, height_px)


def _normalize_image_array(img):
    if img is None:
        return img
    arr = img
    if np.issubdtype(arr.dtype, np.floating):
        try:
            max_val = float(np.nanmax(arr))
        except Exception:
            max_val = 1.0
        if max_val <= 1.0:
            arr = np.clip(arr, 0.0, 1.0)
            arr = (arr * 255.0).round().astype(np.uint8)
        else:
            arr = np.clip(arr, 0.0, 255.0).round().astype(np.uint8)
    elif arr.dtype != np.uint8:
        arr = arr.astype(np.uint8, copy=False)
    if arr.ndim == 3 and arr.shape[2] == 4:
        try:
            if np.all(arr[:, :, 3] == 255):
                arr = arr[:, :, :3]
        except Exception:
            pass
    return arr


def _downscale_image_array(img, max_side_px: int | None):
    if img is None or not max_side_px:
        return _normalize_image_array(img)
    try:
        height, width = int(img.shape[0]), int(img.shape[1])
    except Exception:
        return _normalize_image_array(img)
    if max(height, width) <= max_side_px:
        return _normalize_image_array(img)
    scale = max_side_px / float(max(height, width))
    new_w = max(1, int(round(width * scale)))
    new_h = max(1, int(round(height * scale)))
    if new_w == width and new_h == height:
        return _normalize_image_array(img)
    x_idx = np.linspace(0, width - 1, new_w).astype(int)
    y_idx = np.linspace(0, height - 1, new_h).astype(int)
    return _normalize_image_array(img[np.ix_(y_idx, x_idx)])


def _load_image_array(path: Path, *, max_side_px: int | None = None):
    if not path.exists():
        return None
    plt, _ = _lazy_matplotlib()
    try:
        img = plt.imread(str(path))
    except Exception:
        return None
    return _downscale_image_array(img, max_side_px)


def _load_image_from_bytes(image_bytes: bytes, *, max_side_px: int | None = None):
    if not image_bytes:
        return None
    try:
        import matplotlib.image as mpimg

        img = mpimg.imread(io.BytesIO(image_bytes))
    except Exception:
        return None
    return _downscale_image_array(img, max_side_px)


def _encode_png_bytes(img) -> bytes | None:
    if img is None:
        return None
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        if img.ndim == 2:
            mode = "L"
        elif img.ndim == 3 and img.shape[2] == 3:
            mode = "RGB"
        elif img.ndim == 3 and img.shape[2] == 4:
            mode = "RGBA"
        else:
            return None
        im = Image.fromarray(img, mode=mode)
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception:
        return None


def prepare_report_image_bytes(
    image_bytes: bytes | None, *, dpi: int | None = None
) -> bytes | None:
    if not image_bytes:
        return None
    max_side_px = _max_page_side_px(min(_select_dpi(dpi), _MAX_IMAGE_DPI))
    img = _load_image_from_bytes(image_bytes, max_side_px=max_side_px)
    encoded = _encode_png_bytes(img)
    return encoded or image_bytes


def write_bytes_to_tempfile(data: bytes, suffix: str) -> str:
    tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp_file.write(data)
        tmp_file.flush()
    finally:
        tmp_file.close()
    return tmp_file.name


def transcode_to_jpeg(in_path: str, out_path: str, quality: int = _JPEG_QUALITY) -> str:
    try:
        from PIL import Image
    except Exception as exc:
        raise RuntimeError("Pillow is vereist voor JPEG-transcoding.") from exc
    with Image.open(in_path) as img:
        if img.mode in ("RGBA", "LA") or (
            img.mode == "P" and "transparency" in img.info
        ):
            alpha = img.convert("RGBA")
            bg = Image.new("RGB", alpha.size, (255, 255, 255))
            bg.paste(alpha, mask=alpha.split()[-1])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        img.save(
            out_path,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=True,
        )
    return out_path


def embed_image_in_pdf(
    canvas,
    img_path: str,
    dpi_user: int,
    *,
    box: tuple[float, float, float, float],
    fit: str = "contain",
) -> None:
    try:
        from PIL import Image
    except Exception as exc:
        raise RuntimeError("Pillow is vereist voor PDF-embedding.") from exc
    with Image.open(img_path) as img:
        px_w, px_h = img.size
    if not px_w or not px_h:
        return
    dpi_safe = dpi_user if dpi_user and dpi_user > 0 else _STIJL["dpi"]
    width_pt = (px_w / dpi_safe) * 72.0
    height_pt = (px_h / dpi_safe) * 72.0
    if width_pt <= 0 or height_pt <= 0:
        return
    box_x, box_y, box_w, box_h = box
    if fit == "cover":
        scale = max(box_w / width_pt, box_h / height_pt)
    elif fit == "contain":
        scale = min(box_w / width_pt, box_h / height_pt, 1.0)
    else:
        scale = 1.0
    draw_w = width_pt * scale
    draw_h = height_pt * scale
    x = box_x + (box_w - draw_w) / 2
    y = box_y + (box_h - draw_h) / 2
    if fit == "cover":
        canvas.saveState()
        path = canvas.beginPath()
        path.rect(box_x, box_y, box_w, box_h)
        canvas.clipPath(path, stroke=0, fill=0)
        canvas.drawImage(img_path, x, y, width=draw_w, height=draw_h)
        canvas.restoreState()
    else:
        canvas.drawImage(img_path, x, y, width=draw_w, height=draw_h)


def _overlay_map_image_on_pdf(
    base_pdf_path: Path,
    map_image_path: str,
    dpi_user: int,
    summary_page_index: int | None,
    map_page_index: int | None,
) -> None:
    if not map_image_path or (summary_page_index is None and map_page_index is None):
        return
    if not Path(map_image_path).exists():
        return
    try:
        from reportlab.pdfgen import canvas
    except Exception as exc:
        raise RuntimeError("reportlab is vereist voor PDF-embedding.") from exc
    try:
        from pypdf import PdfReader, PdfWriter
    except Exception as exc:
        raise RuntimeError("pypdf is vereist voor PDF-embedding.") from exc
    base_reader = PdfReader(str(base_pdf_path))
    overlay_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    overlay_path = overlay_tmp.name
    overlay_tmp.close()
    c = canvas.Canvas(overlay_path)
    for idx, page in enumerate(base_reader.pages):
        width_pt = float(page.mediabox.width)
        height_pt = float(page.mediabox.height)
        c.setPageSize((width_pt, height_pt))
        if summary_page_index is not None and idx == summary_page_index:
            map_cfg = _SAMENVATTING_INDELING["map"]
            box = (
                map_cfg["x"] * width_pt,
                map_cfg["y"] * height_pt,
                map_cfg["w"] * width_pt,
                map_cfg["h"] * height_pt,
            )
            embed_image_in_pdf(
                c,
                map_image_path,
                dpi_user,
                box=box,
                fit="cover",
            )
        if map_page_index is not None and idx == map_page_index:
            box = (
                _STIJL["margin_left"] * width_pt,
                0.08 * height_pt,
                0.88 * width_pt,
                0.8 * height_pt,
            )
            embed_image_in_pdf(
                c,
                map_image_path,
                dpi_user,
                box=box,
                fit="contain",
            )
        c.showPage()
    c.save()
    try:
        overlay_reader = PdfReader(overlay_path)
        writer = PdfWriter()
        for idx, page in enumerate(base_reader.pages):
            if idx < len(overlay_reader.pages):
                page.merge_page(overlay_reader.pages[idx])
            writer.add_page(page)
        with open(base_pdf_path, "wb") as handle:
            writer.write(handle)
    finally:
        try:
            Path(overlay_path).unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass


def _as_schaal(
    ax, x: float, y: float, width: float, height: float
) -> tuple[float, float]:
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


def _render_cover_page(
    title: str,
    *,
    month_year: str,
    background: Path | None = None,
    image_max_side_px: int | None = None,
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
    bg = (
        _load_image_array(background, max_side_px=image_max_side_px)
        if background
        else None
    )
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
    image_max_side_px: int | None = None,
    show_missing_map_text: bool = True,
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
    bg = (
        _load_image_array(background, max_side_px=image_max_side_px)
        if background
        else None
    )
    if bg is not None:
        ax.set_aspect("auto")
        ax.imshow(bg, extent=[0, 1, 0, 1], aspect="auto", interpolation="lanczos")
    for idx, (label, value, unit) in enumerate(kpis):
        layout = (
            _SAMENVATTING_INDELING["kpi_left"]
            if idx == 0
            else _SAMENVATTING_INDELING["kpi_right"]
        )
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
            img = _load_image_from_bytes(map_image, max_side_px=image_max_side_px)
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
    elif show_missing_map_text:
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
        s_panden = pd.to_numeric(df_filtered["aantal_huizen"], errors="coerce").fillna(
            0
        )
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
    df_filtered: pd.DataFrame,
    pandtype_counts_by_woonplaats: pd.DataFrame | None = None,
    *,
    top_n: int = 15,
    woonplaats_summary: pd.DataFrame | None = None,
    pandtype_mwh_by_woonplaats: pd.DataFrame | None = None,
) -> pd.DataFrame:
    area_display_col = "Gebiedsoppervlakte (ha)"
    density_display_col = "Warmtevraag per ha (MWh)"
    top_wp = None
    if (
        isinstance(woonplaats_summary, pd.DataFrame)
        and not woonplaats_summary.empty
        and {"woonplaats", "MWh"}.issubset(woonplaats_summary.columns)
    ):
        top_wp = woonplaats_summary.copy()
        top_wp["woonplaats"] = top_wp["woonplaats"].astype(str).str.strip()
        top_wp = (
            top_wp.sort_values("MWh", ascending=False)
            .head(top_n)
            .rename(columns={"woonplaats": "Woonplaats"})
        )
        if "area_ha" in top_wp.columns:
            top_wp[area_display_col] = pd.to_numeric(top_wp["area_ha"], errors="coerce")
    else:
        if df_filtered is None or df_filtered.empty:
            return pd.DataFrame()
        col_wp = "woonplaats"
        col_mwh = (
            "sum_mwh_raw"
            if "sum_mwh_raw" in df_filtered.columns
            else "gemiddeld_jaarverbruik_mWh"
        )
        col_area = "area_ha"
        available_cols = set(df_filtered.columns)
        if col_wp not in available_cols or col_mwh not in available_cols:
            return pd.DataFrame()
        use_area = col_area in available_cols
        base_cols = [col_wp, col_mwh]
        if use_area:
            base_cols.append(col_area)
        df_wp = df_filtered.loc[:, base_cols].copy()
        if df_wp.empty:
            return pd.DataFrame()
        df_wp[col_mwh] = pd.to_numeric(df_wp[col_mwh], errors="coerce").fillna(0)
        agg_map: dict[str, str] = {col_mwh: "sum"}
        if use_area:
            df_wp[col_area] = pd.to_numeric(df_wp[col_area], errors="coerce").fillna(0)
            agg_map[col_area] = "sum"
        top_wp = (
            df_wp.groupby(col_wp, as_index=False, sort=False, observed=True)
            .agg(agg_map)
            .rename(columns={col_mwh: "MWh"})
            .sort_values("MWh", ascending=False)
            .head(top_n)
        )
        top_wp.rename(columns={col_wp: "Woonplaats"}, inplace=True)
        if use_area and col_area in top_wp.columns:
            top_wp[area_display_col] = pd.to_numeric(top_wp[col_area], errors="coerce")

    if top_wp is None or top_wp.empty:
        return pd.DataFrame()
    if pandtype_mwh_by_woonplaats is None or pandtype_mwh_by_woonplaats.empty:
        return pd.DataFrame()

    breakdown = pandtype_mwh_by_woonplaats.copy()
    if "woonplaats" not in breakdown.columns or "type_code" not in breakdown.columns:
        return pd.DataFrame()
    top_norms = top_wp["Woonplaats"].map(_normalize_woonplaats)
    top_order_map = {wp_norm: idx for idx, wp_norm in enumerate(top_norms)}
    breakdown["woonplaats_norm"] = breakdown["woonplaats"].map(_normalize_woonplaats)
    breakdown = breakdown[breakdown["woonplaats_norm"].isin(set(top_norms))]
    breakdown["top_rank"] = breakdown["woonplaats_norm"].map(top_order_map)
    if breakdown.empty:
        return pd.DataFrame()

    type_map = {
        "A": "Kleinverbruik",
        "B": "Middel- en grootverbruik",
        "C": "Middel- en grootverbruik",
    }
    breakdown["Type pand"] = breakdown["type_code"].map(type_map)
    breakdown = breakdown[breakdown["Type pand"].notna()]
    breakdown["Woonplaats"] = breakdown["woonplaats"].astype(str).str.strip()

    agg_map: dict[str, str] = {"MWh": "sum"}
    if "aantal_panden" in breakdown.columns:
        agg_map["aantal_panden"] = "sum"
    group_cols = ["Woonplaats", "Type pand", "top_rank"]
    breakdown = (
        breakdown.groupby(group_cols, as_index=False, sort=False, observed=True)
        .agg(agg_map)
        .reset_index(drop=True)
    )

    if area_display_col in top_wp.columns:
        area_series = pd.to_numeric(
            top_wp.set_index("Woonplaats")[area_display_col], errors="coerce"
        )
        breakdown["area_ha"] = breakdown["Woonplaats"].map(area_series)
    else:
        breakdown["area_ha"] = pd.NA

    area_vals = pd.to_numeric(breakdown["area_ha"], errors="coerce").replace({0: pd.NA})
    breakdown["MWh_per_ha"] = breakdown["MWh"].div(area_vals)

    breakdown["type_order"] = breakdown["Type pand"].map(
        {"Kleinverbruik": 0, "Middel- en grootverbruik": 1}
    )
    breakdown.sort_values(["top_rank", "type_order"], inplace=True)

    rename_map = {
        "aantal_panden": "Panden",
        "area_ha": area_display_col,
        "MWh_per_ha": density_display_col,
    }
    breakdown.rename(columns=rename_map, inplace=True)

    ordered_cols = ["Woonplaats", "Type pand", "MWh", "Panden"]
    if area_display_col in breakdown.columns:
        ordered_cols.append(area_display_col)
    if density_display_col in breakdown.columns:
        ordered_cols.append(density_display_col)
    breakdown = breakdown.loc[:, [c for c in ordered_cols if c in breakdown.columns]]

    for col in breakdown.columns:
        if col == "MWh":
            breakdown[col] = breakdown[col].map(_fmt_int)
        elif col == "Panden":
            breakdown[col] = breakdown[col].map(_fmt_int)
        elif col in (area_display_col, density_display_col):
            breakdown[col] = breakdown[col].map(lambda v: _fmt_float(v, 2))
    return breakdown


def _build_top_woonplaatsen_pand_table(
    pandtype_mwh_by_woonplaats: pd.DataFrame | None,
    top_wp: pd.DataFrame | None = None,
    *,
    top_n: int = 15,
) -> pd.DataFrame:
    if pandtype_mwh_by_woonplaats is None or pandtype_mwh_by_woonplaats.empty:
        return pd.DataFrame()
    breakdown = pandtype_mwh_by_woonplaats.copy()
    if "woonplaats" not in breakdown.columns or "type_code" not in breakdown.columns:
        return pd.DataFrame()
    if top_wp is not None and "Woonplaats" in top_wp.columns:
        top_norms = top_wp["Woonplaats"].map(_normalize_woonplaats)
        top_order_map = {wp_norm: idx for idx, wp_norm in enumerate(top_norms)}
        breakdown["woonplaats_norm"] = breakdown["woonplaats"].map(
            _normalize_woonplaats
        )
        breakdown = breakdown[breakdown["woonplaats_norm"].isin(set(top_norms))]
        breakdown["top_rank"] = breakdown["woonplaats_norm"].map(top_order_map)
    else:
        breakdown["woonplaats_norm"] = breakdown["woonplaats"].map(
            _normalize_woonplaats
        )
        breakdown = breakdown.sort_values("woonplaats_norm").head(top_n * 3)
        breakdown["top_rank"] = (
            breakdown["woonplaats_norm"].factorize(sort=True)[0].astype(int)
        )

    if breakdown.empty:
        return pd.DataFrame()

    type_map = {
        "A": "Kleinverbruik",
        "B": "Middel- en grootverbruik",
        "C": "Klein-, middel- en grootverbruik",
    }
    breakdown["Type pand"] = (
        breakdown["type_code"].map(type_map).fillna(breakdown["type_code"])
    )
    breakdown["Woonplaats"] = breakdown["woonplaats"].astype(str).str.strip()
    breakdown["type_order"] = breakdown["type_code"].map({"A": 0, "B": 1, "C": 2})
    breakdown["type_order"] = breakdown["type_order"].fillna(9)

    sort_cols = ["top_rank", "type_order"]
    breakdown.sort_values(sort_cols, inplace=True)

    rename_map = {"aantal_panden": "Panden"}
    if "area_ha" in breakdown.columns:
        rename_map["area_ha"] = "Oppervlakte (ha)"
    if "MWh_per_ha" in breakdown.columns:
        rename_map["MWh_per_ha"] = "Warmtevraag per ha (MWh)"
    breakdown.rename(columns=rename_map, inplace=True)

    ordered_cols = ["Woonplaats", "Type pand", "MWh", "Panden"]
    if "Oppervlakte (ha)" in breakdown.columns:
        ordered_cols.append("Oppervlakte (ha)")
    if "Warmtevraag per ha (MWh)" in breakdown.columns:
        ordered_cols.append("Warmtevraag per ha (MWh)")

    breakdown = breakdown.loc[:, [c for c in ordered_cols if c in breakdown.columns]]

    for col in breakdown.columns:
        if col == "MWh":
            breakdown[col] = breakdown[col].map(_fmt_int)
        elif col == "Panden":
            breakdown[col] = breakdown[col].map(_fmt_int)
        elif col in ("Oppervlakte (ha)", "Warmtevraag per ha (MWh)"):
            breakdown[col] = breakdown[col].map(lambda v: _fmt_float(v, 2))
    return breakdown


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
        "site_rank": "Voorziening",
        "gebied_label": "Gebied",
        "connected_buildings": "Aangesloten gebouwen",
        "connected_MWh": "Aangesloten MWh",
        "utilization_pct": "Benutting (%)",
    }
    out.rename(columns=rename_map, inplace=True)
    for col in ["Voorziening", "Aangesloten gebouwen", "Aangesloten MWh"]:
        if col in out.columns:
            out[col] = out[col].map(_fmt_int)
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
    image_max_side_px: int | None = None,
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
    font_size: float | None = None,
    wrap_max_chars: int | None = None,
    line_spacing: float | None = None,
    padding_weight: float | None = None,
    cell_pad: float | None = None,
    break_long_words: bool = False,
    break_on_hyphens: bool = False,
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
    bg = (
        _load_image_array(background, max_side_px=image_max_side_px)
        if background
        else None
    )
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

    line_spacing = line_spacing if line_spacing is not None else _TABEL_REGEL_AFSTAND
    padding_weight = (
        padding_weight if padding_weight is not None else _TABEL_RIJ_PADDING_FACTOR
    )
    wrap_max_chars = wrap_max_chars if wrap_max_chars is not None else 60
    df_wrapped, header_labels = _wrap_table_cells(
        df,
        col_widths,
        max_chars=wrap_max_chars,
        break_long_words=break_long_words,
        break_on_hyphens=break_on_hyphens,
    )

    row_lines = []
    if show_header:
        row_lines.append(_row_weight(header_labels, line_spacing, padding_weight))
    for _, row in df_wrapped.iterrows():
        row_lines.append(_row_weight(row.tolist(), line_spacing, padding_weight))
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

    def _normalize_currency_lines(value: Any) -> str:
        text = str(value or "")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return ""
        if len(lines) == 1:
            return lines[0]
        if lines[0] == "€":
            return "€ " + " ".join(lines[1:]).strip()
        return "\n".join(lines)

    cell_rows = [
        [_normalize_currency_lines(v) for v in row]
        for row in df_wrapped.values.tolist()
    ]
    table = ax.table(
        cellText=cell_rows,
        colLabels=header_labels if show_header else None,
        cellLoc="left",
        colLoc="left",
        colWidths=col_widths or None,
        bbox=table_bbox,
    )
    table.auto_set_font_size(False)
    table.set_fontsize(font_size or _TABEL_LETTERGROOTTE)

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
        cell.PAD = cell_pad if cell_pad is not None else 0.05
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
            img = _load_image_array(Path(path), max_side_px=image_max_side_px)
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
                box_height = min(
                    box_height, text_height * 1.2 if text_height else box_height
                )
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


def _compute_table_layout(
    df: pd.DataFrame,
    *,
    col_widths: list[float] | None,
    show_header: bool,
    max_row_height: float,
    wrap_max_chars: int,
    line_spacing: float,
    padding_weight: float,
    break_long_words: bool,
    break_on_hyphens: bool,
) -> tuple[pd.DataFrame, list[str], list[float], float]:
    df_wrapped, header_labels = _wrap_table_cells(
        df,
        col_widths or _table_column_widths(df),
        max_chars=wrap_max_chars,
        break_long_words=break_long_words,
        break_on_hyphens=break_on_hyphens,
    )
    row_lines: list[float] = []
    if show_header:
        row_lines.append(_row_weight(header_labels, line_spacing, padding_weight))
    for _, row in df_wrapped.iterrows():
        row_lines.append(_row_weight(row.tolist(), line_spacing, padding_weight))
    total_weight = sum(row_lines) or 1
    used_height = max_row_height * total_weight
    return df_wrapped, header_labels, row_lines, used_height


def _draw_table_on_ax(
    ax,
    df: pd.DataFrame,
    *,
    table_bbox: list[float],
    col_widths: list[float] | None,
    show_header: bool,
    header_bg: str | None,
    header_text_color: str | None,
    font_size: float,
    max_row_height: float,
    wrap_max_chars: int,
    line_spacing: float,
    padding_weight: float,
    cell_pad: float,
    break_long_words: bool,
    break_on_hyphens: bool,
) -> float:
    df_wrapped, header_labels, row_lines, used_height = _compute_table_layout(
        df,
        col_widths=col_widths,
        show_header=show_header,
        max_row_height=max_row_height,
        wrap_max_chars=wrap_max_chars,
        line_spacing=line_spacing,
        padding_weight=padding_weight,
        break_long_words=break_long_words,
        break_on_hyphens=break_on_hyphens,
    )
    x, y, w, h = table_bbox
    y_top = y + h
    table_bbox = [x, y_top - used_height, w, used_height]

    table = ax.table(
        cellText=df_wrapped.values.tolist(),
        colLabels=header_labels if show_header else None,
        cellLoc="left",
        colLoc="left",
        colWidths=col_widths or None,
        bbox=table_bbox,
    )
    table.auto_set_font_size(False)
    table.set_fontsize(font_size)

    row_heights = [max_row_height * weight for weight in row_lines]
    row_offset = 1 if show_header else 0
    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor(_STIJL["grid_color"])
        cell.set_linewidth(0.4)
        cell.PAD = cell_pad
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
            cell.set_facecolor(
                _STIJL["row_bg"] if data_row % 2 == 0 else _STIJL["zebra_bg"]
            )
        text = cell.get_text()
        text.set_va("center")
        text.set_linespacing(line_spacing)
    return used_height


def _render_image_page(
    title: str,
    image_bytes: bytes | None,
    *,
    image_max_side_px: int | None = None,
    show_missing_text: bool = True,
):
    plt, _ = _lazy_matplotlib()
    _apply_report_style(plt)
    page_size = _STIJL["page_portrait"]
    img = None
    try:
        img = _load_image_from_bytes(image_bytes, max_side_px=image_max_side_px)
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
            img = _load_image_from_bytes(image_bytes, max_side_px=image_max_side_px)
        ax.imshow(img, interpolation="lanczos")
        ax.set_aspect("equal", adjustable="box")
    except Exception:
        if show_missing_text:
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
    warmtenet_gjson: dict | None = None,
    heat_unit: str | None = None,
    threshold_display: float | None = None,
    map_image_path: str | None = None,
    woonplaats_summary: pd.DataFrame | None = None,
    pandtype_counts_by_woonplaats: pd.DataFrame | None = None,
    pandtype_mwh_by_woonplaats: pd.DataFrame | None = None,
    report_title: str = "Friese Warmteatlas",
) -> str:
    plt, PdfPages = _lazy_matplotlib()
    tmp_file = tempfile.NamedTemporaryFile(
        prefix="warmteatlas_",
        suffix=".pdf",
        delete=False,
    )
    tmp_path = Path(tmp_file.name)
    tmp_file.close()
    now = datetime.now()
    heat_unit = heat_unit or ui.get("heat_unit") or "MWh/ha"
    unit_display = _normalize_unit(heat_unit)
    threshold_display = (
        threshold_display
        if threshold_display is not None
        else ui.get("threshold_display", ui.get("threshold", ""))
    )
    dpi = _select_dpi(ui.get("report_dpi"))
    image_max_side_px = _max_page_side_px(dpi)
    if map_image_path and not Path(map_image_path).exists():
        map_image_path = None
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
    pand_selectie_display = _format_selection(
        ui.get("pand_selectie"),
        max_items=999,
        empty_label="-",
    )
    if pand_selectie_display.strip().lower() in {"alle types", "alle"}:
        pand_selectie_display = "Klein-, middel- en grootverbruik"
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
        ("Type pand", pand_selectie_display),
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
        (
            idx
            for idx, (label, _, _) in enumerate(layers_rows)
            if label == "Aquathermie"
        ),
        None,
    )

    cover_date = _format_dutch_date(now)
    kpi_items = [
        ("WARMTEVRAAG", _fmt_int(totaal_mwh), "MWh"),
        ("PANDEN", _fmt_int(totaal_panden), "Aantal"),
    ]

    page_num = 0
    summary_page_index = None
    map_page_index = None
    warmtenet_tables = None
    if layer_state.get("warmtenet") and layer_state.get("wegennet"):
        warmtenet_tables = _build_warmtenet_report_tables(
            warmtenet_gjson=warmtenet_gjson,
            df_filtered=df_filtered,
            warmtenet_wp=ui.get("warmtenet_wp_selectie"),
            wegennet_wp=ui.get("wegennet_wp_selectie"),
        )
    show_warmtenet_pages = bool(warmtenet_tables)
    with PdfPages(tmp_path) as pdf:
        if _VOORBLAD_ACHTERGROND.exists():
            fig = _render_cover_page(
                report_title,
                month_year=cover_date,
                background=_VOORBLAD_ACHTERGROND,
                image_max_side_px=image_max_side_px,
            )
            page_num += 1
            pdf.savefig(fig, dpi=dpi)
            _close_figure(fig, plt)

        if _SAMENVATTING_ACHTERGROND.exists():
            fig = _render_summary_page(
                kpis=kpi_items,
                map_image=None,
                background=_SAMENVATTING_ACHTERGROND,
                image_max_side_px=image_max_side_px,
                show_missing_map_text=not map_image_path,
            )
            page_num += 1
            if map_image_path:
                summary_page_index = page_num - 1
            _add_page_number(fig, page_num)
            pdf.savefig(fig, dpi=dpi)
            _close_figure(fig, plt)
        else:
            fig = _render_sections_page(report_title, sections)
            page_num += 1
            _add_page_number(fig, page_num)
            pdf.savefig(fig, dpi=dpi)
            _close_figure(fig, plt)
            if map_image_path:
                fig = _render_image_page(
                    "Kaart",
                    None,
                    image_max_side_px=image_max_side_px,
                    show_missing_text=False,
                )
                page_num += 1
                map_page_index = page_num - 1
                _add_page_number(fig, page_num)
                pdf.savefig(fig, dpi=dpi)
                _close_figure(fig, plt)

        top_wp = _build_top_woonplaatsen_table(
            df_filtered,
            pandtype_counts_by_woonplaats,
            woonplaats_summary=woonplaats_summary,
            pandtype_mwh_by_woonplaats=pandtype_mwh_by_woonplaats,
        )
        top_wp_col_widths = None
        if top_wp is not None and not top_wp.empty:
            top_wp_widths_map = {
                "Woonplaats": 0.16,
                "Type pand": 0.20,
                "MWh": 0.12,
                "Panden": 0.10,
                "Gebiedsoppervlakte (ha)": 0.26,
                "Warmtevraag per ha (MWh)": 0.18,
            }
            top_weights = [top_wp_widths_map.get(col, 0.16) for col in top_wp.columns]
            top_total = sum(top_weights)
            if top_total:
                top_wp_col_widths = [w / top_total for w in top_weights]
        max_row_height = (
            _TABEL_RIJ_HOOGTE if _WOONPLAATSEN_ACHTERGROND.exists() else None
        )
        table_bbox = (
            _WOONPLAATSEN_INDELING["table_bbox"]
            if _WOONPLAATSEN_ACHTERGROND.exists()
            else None
        )
        show_title = not _WOONPLAATSEN_ACHTERGROND.exists()
        show_header = True
        if top_wp is None or top_wp.empty:
            fig = _render_table_page(
                "Top woonplaatsen (MWh)",
                top_wp,
                "Geen gegevens om te tonen.",
                background=(
                    _WOONPLAATSEN_ACHTERGROND
                    if _WOONPLAATSEN_ACHTERGROND.exists()
                    else None
                ),
                image_max_side_px=image_max_side_px,
                table_bbox=table_bbox,
                show_title=show_title,
                show_header=show_header,
                max_row_height=max_row_height,
                header_bg=_STIJL["brand_blue"],
                header_text_color=_STIJL["row_bg"],
                col_widths=top_wp_col_widths,
            )
            page_num += 1
            _add_page_number(fig, page_num)
            pdf.savefig(fig, dpi=dpi)
            _close_figure(fig, plt)
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
                    background=(
                        _WOONPLAATSEN_ACHTERGROND
                        if _WOONPLAATSEN_ACHTERGROND.exists()
                        else None
                    ),
                    image_max_side_px=image_max_side_px,
                    table_bbox=table_bbox,
                    show_title=show_title,
                    show_header=show_header,
                    max_row_height=max_row_height,
                    header_bg=_STIJL["brand_blue"],
                    header_text_color=_STIJL["row_bg"],
                    col_widths=top_wp_col_widths,
                )
                page_num += 1
                _add_page_number(fig, page_num)
                pdf.savefig(fig, dpi=dpi)
                _close_figure(fig, plt)

        if show_warmtenet_pages:
            warmtenet_font_base = max(_TABEL_LETTERGROOTTE - 2, 7)
            warmtenet_label_size = max(_STIJL["subtitle_size"] - 1, 8)
            warmtenet_line_spacing = 1.2
            warmtenet_padding_weight = 1.2
            warmtenet_cell_pad = 0.03
            warmtenet_label_gap = _row_height_for_font(warmtenet_label_size) * 0.6

            def _start_warmtenet_fig(
                background: Path,
                show_title: bool,
                title: str,
                caption_lines: list[str] | None = None,
            ):
                fig = plt.figure(figsize=_STIJL["page_portrait"])
                fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
                ax = fig.add_axes([0, 0, 1, 1])
                ax.set_position([0, 0, 1, 1])
                ax.set_xlim(0, 1)
                ax.set_ylim(0, 1)
                ax.axis("off")
                bg = (
                    _load_image_array(background, max_side_px=image_max_side_px)
                    if background.exists()
                    else None
                )
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
                caption_height = 0.0
                if caption_lines:
                    y_top = (
                        _WARMTENET_INDELING["table_bbox"][1]
                        + _WARMTENET_INDELING["table_bbox"][3]
                    )
                    y_cursor = y_top - 0.004
                    for line in caption_lines:
                        ax.text(
                            _WARMTENET_INDELING["table_bbox"][0],
                            y_cursor,
                            line,
                            fontsize=_STIJL["body_size"],
                            color=_STIJL["muted"],
                            va="top",
                        )
                        y_cursor -= _STIJL["line_height"]
                    caption_height = len(caption_lines) * _STIJL["line_height"]
                return fig, ax, caption_height

            def _render_warmtenet_page(
                title: str,
                df: pd.DataFrame | None,
                background: Path,
            ) -> None:
                nonlocal page_num
                if title in ("Warmtenet_vraag", "Warmtenet_panden"):
                    table_font_size = max(warmtenet_font_base + 2, 7)
                elif title == "Warmtenet_kosten":
                    table_font_size = max(warmtenet_font_base + 2, 7)
                else:
                    table_font_size = warmtenet_font_base
                if df is None or df.empty:
                    fig = _render_table_page(
                        title,
                        df,
                        "Geen gegevens om te tonen.",
                        background=background if background.exists() else None,
                        image_max_side_px=image_max_side_px,
                        table_bbox=(
                            _WARMTENET_INDELING["table_bbox"]
                            if background.exists()
                            else None
                        ),
                        show_title=not background.exists(),
                        show_header=True,
                        max_row_height=(
                            _row_height_for_font(table_font_size)
                            if background.exists()
                            else None
                        ),
                        header_bg=_STIJL["brand_blue"],
                        header_text_color=_STIJL["row_bg"],
                        font_size=table_font_size,
                        line_spacing=warmtenet_line_spacing,
                        padding_weight=warmtenet_padding_weight,
                        cell_pad=warmtenet_cell_pad,
                        break_long_words=True,
                    )
                    page_num += 1
                    _add_page_number(fig, page_num)
                    pdf.savefig(fig, dpi=dpi)
                    _close_figure(fig, plt)
                    return

                max_row_height = _row_height_for_font(table_font_size)
                if background.exists():
                    table_bbox = _WARMTENET_INDELING["table_bbox"]
                else:
                    x_left = _STIJL["margin_left"]
                    x_right = _STIJL["margin_right"]
                    table_bbox = [x_left, 0.08, x_right - x_left, 0.8]
                show_title = not background.exists()
                show_header = True
                col_widths = None
                wrap_max_chars = 80
                if title == "Warmtenet_panden":
                    col_widths = [0.18, 0.12, 0.18, 0.18, 0.18, 0.16]
                elif title == "Warmtenet_vraag":
                    col_widths = [0.18, 0.14, 0.19, 0.19, 0.15, 0.15]
                elif title == "Warmtenet_kosten":
                    col_widths = [0.12, 0.34, 0.30, 0.24]
                    wrap_max_chars = 90

                group_by_woonplaats = title == "Warmtenet_kosten"
                if not group_by_woonplaats:
                    if table_bbox and max_row_height:
                        header_rows = 1 if show_header else 0
                        row_weight = 1 + warmtenet_padding_weight
                        max_total_rows = int(
                            table_bbox[3] / (max_row_height * row_weight)
                        )
                        max_rows = max(1, max_total_rows - header_rows)
                    else:
                        max_rows = len(df)
                    for start in range(0, len(df), max_rows):
                        chunk = df.iloc[start : start + max_rows]
                        fig = _render_table_page(
                            title,
                            chunk,
                            "Geen gegevens om te tonen.",
                            background=background if background.exists() else None,
                            image_max_side_px=image_max_side_px,
                            table_bbox=table_bbox,
                            show_title=show_title,
                            show_header=show_header,
                            max_row_height=max_row_height,
                            header_bg=_STIJL["brand_blue"],
                            header_text_color=_STIJL["row_bg"],
                            col_widths=col_widths,
                            font_size=table_font_size,
                            wrap_max_chars=wrap_max_chars,
                            line_spacing=warmtenet_line_spacing,
                            padding_weight=warmtenet_padding_weight,
                            cell_pad=warmtenet_cell_pad,
                            break_long_words=False,
                        )
                        page_num += 1
                        _add_page_number(fig, page_num)
                        pdf.savefig(fig, dpi=dpi)
                        _close_figure(fig, plt)
                    return
                if group_by_woonplaats and "Woonplaats" in df.columns:
                    grouped = [
                        (wp, grp.drop(columns=["Woonplaats"]))
                        for wp, grp in df.groupby("Woonplaats", sort=False)
                    ]
                else:
                    grouped = [("", df)]

                caption_lines = None
                if title == "Warmtenet_kosten":
                    caption_lines = [
                        "Kostenberekening: €1.000/m leidingnet en €346/m aansluitingen.",
                    ]
                fig, ax, caption_height = _start_warmtenet_fig(
                    background, show_title, title, caption_lines
                )
                y_top = table_bbox[1] + table_bbox[3]
                y_bottom = table_bbox[1]
                cursor = (
                    y_top
                    - caption_height
                    - (warmtenet_label_gap if caption_height else 0.0)
                )
                for wp, tbl in grouped:
                    label = str(wp).strip().upper() if group_by_woonplaats else ""
                    label_height = (
                        _row_height_for_font(warmtenet_label_size) * 1.6
                        if label
                        else 0.0
                    )
                    _, _, _, used_height = _compute_table_layout(
                        tbl,
                        col_widths=col_widths,
                        show_header=show_header,
                        max_row_height=max_row_height,
                        wrap_max_chars=wrap_max_chars,
                        line_spacing=warmtenet_line_spacing,
                        padding_weight=warmtenet_padding_weight,
                        break_long_words=False,
                        break_on_hyphens=False,
                    )
                    required = label_height + used_height
                    if cursor - required < y_bottom:
                        page_num += 1
                        _add_page_number(fig, page_num)
                        pdf.savefig(fig, dpi=dpi)
                        _close_figure(fig, plt)
                        fig, ax, caption_height = _start_warmtenet_fig(
                            background, show_title, title, caption_lines
                        )
                        cursor = (
                            y_top
                            - caption_height
                            - (warmtenet_label_gap if caption_height else 0.0)
                        )
                    if label:
                        ax.text(
                            table_bbox[0],
                            cursor - label_height * 0.1,
                            label,
                            fontsize=warmtenet_label_size,
                            fontweight="bold",
                            va="top",
                        )
                    table_y = cursor - label_height - used_height
                    _draw_table_on_ax(
                        ax,
                        tbl,
                        table_bbox=[table_bbox[0], table_y, table_bbox[2], used_height],
                        col_widths=col_widths,
                        show_header=show_header,
                        header_bg=_STIJL["brand_blue"],
                        header_text_color=_STIJL["row_bg"],
                        font_size=table_font_size,
                        max_row_height=max_row_height,
                        wrap_max_chars=wrap_max_chars,
                        line_spacing=warmtenet_line_spacing,
                        padding_weight=warmtenet_padding_weight,
                        cell_pad=warmtenet_cell_pad,
                        break_long_words=False,
                        break_on_hyphens=False,
                    )
                    cursor = table_y - warmtenet_label_gap

                page_num += 1
                _add_page_number(fig, page_num)
                pdf.savefig(fig, dpi=dpi)
                _close_figure(fig, plt)

            warmte_df = panden_df = kosten_df = None
            if warmtenet_tables:
                warmte_df, panden_df, kosten_df = warmtenet_tables
            _render_warmtenet_page(
                "Warmtenet_vraag", warmte_df, _WARMTENET_VRAAG_ACHTERGROND
            )
            _render_warmtenet_page(
                "Warmtenet_panden", panden_df, _WARMTENET_PANDEN_ACHTERGROND
            )
            _render_warmtenet_page(
                "Warmtenet_kosten", kosten_df, _WARMTENET_KOSTEN_ACHTERGROND
            )

        kaart_bbox = (
            _KAART_INDELING["table_bbox"] if _KAART_ACHTERGROND.exists() else None
        )
        kaart_show_title = not _KAART_ACHTERGROND.exists()
        kaart_show_header = not _KAART_ACHTERGROND.exists()
        kaart_max_row_height = (
            _TABEL_RIJ_HOOGTE if _KAART_ACHTERGROND.exists() else None
        )
        kaart_col_widths = [0.3, 0.7]
        kaart_chunks = [kaart_table]
        if kaart_bbox and kaart_max_row_height:
            header_weight = 1 + _TABEL_RIJ_PADDING_FACTOR if kaart_show_header else 0
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
                image_max_side_px=image_max_side_px,
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
            _close_figure(fig, plt)

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
            image_max_side_px=image_max_side_px,
            table_bbox=(
                _LAAG_INDELING["table_bbox"] if _LAAG_ACHTERGROND.exists() else None
            ),
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
        _close_figure(fig, plt)

    if map_image_path:
        snapshot_png_file = tempfile.NamedTemporaryFile(
            delete=False,
            suffix=Path(map_image_path).suffix or ".png",
        )
        snapshot_png = snapshot_png_file.name
        snapshot_png_file.close()
        snapshot_jpg_file = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
        snapshot_jpg = snapshot_jpg_file.name
        snapshot_jpg_file.close()
        try:
            shutil.copyfile(map_image_path, snapshot_png)
            transcode_to_jpeg(snapshot_png, snapshot_jpg, quality=_JPEG_QUALITY)
            _overlay_map_image_on_pdf(
                tmp_path,
                snapshot_jpg,
                dpi,
                summary_page_index,
                map_page_index,
            )
        finally:
            for path in (snapshot_png, snapshot_jpg):
                try:
                    Path(path).unlink()
                except FileNotFoundError:
                    pass
                except Exception:
                    pass

    return str(tmp_path)
