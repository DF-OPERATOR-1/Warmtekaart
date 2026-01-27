# ui/kpis_and_tables.py
from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st
import html

from core.io import load_wegennet_summary
from core.utils import format_dutch_number

# =========================
# KPI CARDS
# =========================


def _nl_int(x) -> str:
    try:
        return f"{int(x):,}".replace(",", ".")
    except Exception:
        # fallback voor floats/NaN
        try:
            return format_dutch_number(x, 0)
        except Exception:
            return "0"


def _kpi_css():
    """Add CSS die de KPI-kaarten vormgeeft."""
    st.markdown(
        """
    <style>
    .kpi-row { display:grid; gap:10px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); align-items:stretch; margin:6px 0 14px; }
    .kpi-row.kpi-row-compact { gap:6px; grid-template-columns: repeat(2, minmax(0, 1fr)); margin:6px 0 10px; }
    .kpi-card { background:#f6f8fb; border:1px solid #e5e7eb; border-radius:14px; padding:12px 14px; margin-bottom: 0; width: 100%; min-height: 108px; box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05); }
    .kpi-card.kpi-compact { padding:6px 8px; border-radius:10px; min-height: 58px; box-shadow: 0 1px 1px rgba(15, 23, 42, 0.04); }
    .kpi-title { margin:0 0 6px 0; font-size:14px; color:#6b7280; font-weight:600; letter-spacing:.2px }
    .kpi-card.kpi-compact .kpi-title { font-size:11px; margin-bottom:2px; }
    .kpi-value { font-size:32px; font-weight:800; color:#0b1324; letter-spacing:.3px }
    .kpi-card.kpi-compact .kpi-value { font-size:18px; line-height:1.1; }
    .kpi-sub { margin-top:6px; color:#6b7280; font-size:12px }
    .kpi-card.kpi-compact .kpi-sub { font-size:10px; margin-top:2px; }
    .kpi-center { text-align:center; }
    </style>
    """,
        unsafe_allow_html=True,
    )


def _kpi_card_html(title: str, value: str, sub: str, classes: str) -> str:
    sub_html = f"<div class='kpi-sub'>{sub}</div>" if sub else ""
    return (
        f"<div class='{classes}'>"
        f"<div class='kpi-title'>{title}</div>"
        f"<div class='kpi-value'>{value}</div>"
        f"{sub_html}"
        "</div>"
    )


def _kpi_card(
    title: str,
    value: str,
    sub: str,
    compact: bool = False,
    center: bool = False,
):
    """Render één KPI-kaart met titel, hoofdwaarde en subtitel."""
    classes = ["kpi-card"]
    if compact:
        classes.append("kpi-compact")
    if center:
        classes.append("kpi-center")
    st.markdown(
        _kpi_card_html(title, value, sub, " ".join(classes)),
        unsafe_allow_html=True,
    )


def _compute_kpi_totals(
    df_filtered: pd.DataFrame, participatie_pct: int
) -> tuple[int, int, int, int]:
    # Gebruik .get met default Series om KeyError te vermijden (RAM-zuinig)
    if "aantal_huizen" in df_filtered.columns:
        s_panden = pd.to_numeric(
            df_filtered.get("aantal_huizen", pd.Series([], dtype="int32")),
            errors="coerce",
        ).fillna(0)
        totaal_panden = int(s_panden.sum()) if len(s_panden) else 0
    else:
        totaal_panden = int(len(df_filtered))
    mwh_col = (
        "sum_mwh_raw"
        if "sum_mwh_raw" in df_filtered.columns
        else "gemiddeld_jaarverbruik_mWh"
    )
    s_mwh = pd.to_numeric(
        df_filtered.get(mwh_col, pd.Series([], dtype="float32")),
        errors="coerce",
    ).fillna(0)

    totaal_mwh = int(round(float(s_mwh.sum()))) if len(s_mwh) else 0

    pct = int(participatie_pct)
    panden_part = round(totaal_panden * pct / 100)
    mwh_part = round(totaal_mwh * pct / 100)
    return totaal_panden, totaal_mwh, panden_part, mwh_part


def render_participation_kpis(df_filtered: pd.DataFrame, participatie_pct: int) -> None:
    _kpi_css()
    totaal_panden, totaal_mwh, panden_part, mwh_part = _compute_kpi_totals(
        df_filtered, participatie_pct
    )
    pct = int(participatie_pct)
    html = (
        "<div class='kpi-row kpi-row-compact'>"
        + _kpi_card_html(
            f"Deelnamegraad: {pct}%",
            _nl_int(panden_part),
            "Aantal panden",
            "kpi-card kpi-compact kpi-center",
        )
        + _kpi_card_html(
            f"Deelnamegraad: {pct}%",
            _nl_int(mwh_part),
            "MWh",
            "kpi-card kpi-compact kpi-center",
        )
        + "</div>"
    )
    st.markdown(html, unsafe_allow_html=True)


def render_kpis(
    df_filtered: pd.DataFrame,
    participatie_pct: int,
    *,
    include_participation: bool = True,
    woningen_total: int | None = None,
    bedrijven_total: int | None = None,
) -> None:
    """
    Toont KPI-kaarten:
    - Totaal aantal panden
    - Totale Warmtevraag (MWh)
    - Optioneel: deelnamegraad (panden + MWh)
    """
    _kpi_css()
    totaal_panden, totaal_mwh, panden_part, mwh_part = _compute_kpi_totals(
        df_filtered, participatie_pct
    )
    pct = int(participatie_pct)

    cards = [
        _kpi_card_html(
            "Totaal aantal panden",
            _nl_int(totaal_panden),
            "",
            "kpi-card kpi-center",
        ),
        _kpi_card_html(
            "Totale Warmtevraag (MWh)",
            _nl_int(totaal_mwh),
            "",
            "kpi-card kpi-center",
        ),
    ]
    if woningen_total is not None:
        cards.append(
            _kpi_card_html(
                "Aantal woningen",
                _nl_int(woningen_total),
                "",
                "kpi-card kpi-center",
            )
        )
    if bedrijven_total is not None:
        cards.append(
            _kpi_card_html(
                "Aantal bedrijven",
                _nl_int(bedrijven_total),
                "",
                "kpi-card kpi-center",
            )
        )
    if include_participation:
        cards.extend(
            [
                _kpi_card_html(
                    f"Deelnamegraad: {pct}%",
                    _nl_int(panden_part),
                    "Aantal panden",
                    "kpi-card kpi-center",
                ),
                _kpi_card_html(
                    f"Deelnamegraad: {pct}%",
                    _nl_int(mwh_part),
                    "MWh",
                    "kpi-card kpi-center",
                ),
            ]
        )
    html = "<div class='kpi-row'>" + "".join(cards) + "</div>"
    st.markdown(html, unsafe_allow_html=True)


# =========================
# TABELLEN / TABS
# =========================


def _fmt0(x):
    try:
        return format_dutch_number(int(x), 0)
    except Exception:
        return format_dutch_number(x, 0)


def _fmt2(x):
    return format_dutch_number(x, 2)


def _render_wrapped_table_html(df: pd.DataFrame, height: int) -> None:
    safe_df = df.copy().astype("object")
    safe_df = safe_df.where(pd.notna(safe_df), "")
    try:
        dark_mode = st.get_option("theme.base") == "dark"
    except Exception:
        dark_mode = False
    border_color = "#374151" if dark_mode else "#e5e7eb"
    header_bg = "#1f2937" if dark_mode else "#f9fafb"
    header_color = "#f9fafb" if dark_mode else "#111827"
    headers = "".join(
        f"<th>{html.escape(str(col)).replace(chr(10), '<br>')}</th>"
        for col in safe_df.columns
    )
    rows_html = []
    for row in safe_df.itertuples(index=False):
        cells = "".join(f"<td>{html.escape(str(v))}</td>" for v in row)
        rows_html.append(f"<tr>{cells}</tr>")
    table_html = f"""
    <style>
      .wrapped-table {{
        border: 1px solid {border_color};
        border-radius: 10px;
        overflow: auto;
        max-height: {int(height)}px;
      }}
      .wrapped-table table {{
        width: 100%;
        border-collapse: collapse;
        font-size: 14px;
      }}
      .wrapped-table th,
      .wrapped-table td {{
        padding: 8px 10px;
        border-bottom: 1px solid {border_color};
        border-right: 1px solid {border_color};
        vertical-align: top;
        white-space: normal;
        word-break: break-word;
      }}
      .wrapped-table th {{
        background: {header_bg};
        font-weight: 600;
        color: {header_color};
      }}
      .wrapped-table th:last-child,
      .wrapped-table td:last-child {{
        border-right: none;
      }}
      .wrapped-table tr:last-child td {{
        border-bottom: none;
      }}
    </style>
    <div class="wrapped-table">
      <table>
        <thead><tr>{headers}</tr></thead>
        <tbody>{''.join(rows_html)}</tbody>
      </table>
    </div>
    """
    st.markdown(table_html, unsafe_allow_html=True)


def _render_wrapped_table(df: pd.DataFrame, height: int) -> None:
    _render_wrapped_table_html(df, height)


def _normalize_woonplaats(value: str | None) -> str:
    return str(value or "").strip().lower()


def _normalize_woonplaats_list(values: list[str] | None) -> set[str]:
    return {
        _normalize_woonplaats(v)
        for v in (values or [])
        if _normalize_woonplaats(v)
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
            entry["warmtenet_object_mwh"] += _to_float(
                props.get("vraag_mwh_per_jaar")
            )
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


def _render_warmtenet_comparison(
    warmtenet_gjson: dict | None,
    warmtenet_wp: list[str] | None,
    wegennet_wp: list[str] | None,
    df_filtered: pd.DataFrame | None = None,
) -> None:
    heat_loss_pct = 0.15
    conn_length_per_pand_m = 15.0
    cost_per_meter_net = 1000.0
    cost_per_meter_conn = 346.0

    warmtenet_df = _build_warmtenet_summary(warmtenet_gjson)
    wegennet_df = _build_wegennet_summary(load_wegennet_summary())

    if warmtenet_df.empty or wegennet_df.empty:
        st.info("Geen warmtenet- of wegennetdata beschikbaar.")
        return

    warm_sel = _normalize_woonplaats_list(warmtenet_wp)
    weg_sel = _normalize_woonplaats_list(wegennet_wp)
    if warm_sel:
        warmtenet_df = warmtenet_df[
            warmtenet_df["woonplaats_norm"].isin(warm_sel)
        ]
    if weg_sel:
        wegennet_df = wegennet_df[wegennet_df["woonplaats_norm"].isin(weg_sel)]

    if warmtenet_df.empty or wegennet_df.empty:
        st.info("Geen data voor de huidige woonplaatsselectie.")
        return

    merged = warmtenet_df.merge(
        wegennet_df,
        on="woonplaats_norm",
        how="inner",
        suffixes=("_warmtenet", "_wegennet"),
    )

    if merged.empty:
        st.info("Geen overlap tussen warmtenet- en wegennetselectie.")
        return

    basis_df = _build_basislaag_summary(df_filtered)
    if basis_df.empty:
        merged["basis_vraag_mwh"] = np.nan
        merged["basis_panden"] = np.nan
    else:
        merged = merged.merge(
            basis_df.loc[
                :, ["woonplaats_norm", "basis_vraag_mwh", "basis_panden"]
            ],
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
    wegennet_mwh = (
        pd.to_numeric(merged.get("wegennet_vraag_mwh"), errors="coerce")
        .fillna(0.0)
    )
    basis_panden = pd.to_numeric(merged.get("basis_panden"), errors="coerce")
    wegennet_panden = (
        pd.to_numeric(merged.get("wegennet_aansluitingen"), errors="coerce")
        .fillna(0.0)
    )
    warmtebron_panden = (
        pd.to_numeric(merged.get("warmtenet_aangesloten_panden"), errors="coerce")
        .fillna(0.0)
    )

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
    warmtenet_conn_m = (
        warmtebron_panden.fillna(0.0) * conn_length_per_pand_m
    )

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
    kosten_bron_totaal_warmtebron = pd.to_numeric(
        merged.get("warmtenet_kosten_bron_totaal_euro"), errors="coerce"
    )
    kosten_bron_warmtebron = kosten_bron_warmtebron.fillna(0.0)
    kosten_bron_totaal_warmtebron = kosten_bron_totaal_warmtebron.fillna(
        kosten_bron_warmtebron + kosten_conn_warmtebron
    )

    st.caption(
        "**Warmtebronnen:** deze weergave laat zien hoe een warmtenet eruit kan zien "
        "wanneer warmte vanuit een bron wordt verdeeld binnen de woonplaats. Het "
        "model legt leidingen langs het wegennet en kiest daarbij verbindingen met "
        "zo laag mogelijk kosten om de warmtevraag te bedienen."
    )
    st.caption(
        "**Warmtevraag:** deze weergave laat zien hoe een warmtenet eruit zou zien "
        "wanneer alle panden binnen de woonplaats worden aangesloten op basis van "
        "de warmtevraag. De leidingen volgen het wegennet, maar zijn niet "
        "geoptimaliseerd op kosten of haalbaarheid. Deze weergave geeft inzicht "
        "in wat er aanvullend nodig zou zijn ten opzichte van de getoonde "
        "warmtebronnen."
    )

    tab_warmte, tab_panden, tab_leidingen = st.tabs(
        ["Warmtevraag", "Panden", "Leidingen & kosten"]
    )

    with tab_warmte:
        st.caption("Warmtenet uit warmtebron is gecorrigeerd voor 15% warmteverlies.")
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
        out_fmt = out_warmte.copy()
        for col in [
            "Totaal (MWh)",
            "Warmtenet uit\nwarmtebron (MWh)",
            "Warmtenet uit\nwarmtevraag (MWh)",
            "Onbenut (MWh)",
        ]:
            s = pd.to_numeric(out_fmt[col], errors="coerce")
            out_fmt[col] = s.map(
                lambda v: "" if pd.isna(v) else format_dutch_number(v, 1)
            )
        s = pd.to_numeric(out_fmt["Dekking (%)"], errors="coerce")
        out_fmt["Dekking (%)"] = s.map(
            lambda v: "" if pd.isna(v) else f"{format_dutch_number(v, 1)}%"
        )
        _render_wrapped_table(out_fmt, height=420)

    with tab_panden:
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
        out_fmt = out_panden.copy()
        for col in [
            "Aantal",
            "Aangesloten\nwarmtevraag",
            "Aangesloten\nwarmtebron",
            "Niet\naangesloten",
        ]:
            s = pd.to_numeric(out_fmt[col], errors="coerce")
            out_fmt[col] = s.map(
                lambda v: "" if pd.isna(v) else format_dutch_number(v, 0)
            )
        s = pd.to_numeric(out_fmt["% aangesloten"], errors="coerce")
        out_fmt["% aangesloten"] = s.map(
            lambda v: "" if pd.isna(v) else f"{format_dutch_number(v, 1)}%"
        )
        _render_wrapped_table(out_fmt, height=420)

    with tab_leidingen:
        st.caption(
            "Kostenberekening: €1.000/m leidingnet en €346/m aansluitingen. "
            "Warmtebron: vast bedrag van €5.190 per aansluiting."
        )
        out_leidingen_bron = pd.DataFrame(
            {
                "Woonplaats": merged["woonplaats_display"],
                "Type": "Bron",
                "Netwerk (m)": warmtenet_lengte_m,
                "Aansluiting (m)": warmtenet_conn_m,
                "Kosten\nnetwerk": kosten_net_warmtebron,
                "Kosten\naansluiting": kosten_conn_warmtebron,
                "Totale kosten": kosten_tot_warmtebron,
            }
        )
        out_leidingen_vraag = pd.DataFrame(
            {
                "Woonplaats": merged["woonplaats_display"],
                "Type": "Vraag",
                "Netwerk (m)": wegennet_lengte_m,
                "Aansluiting (m)": wegennet_conn_m,
                "Kosten\nnetwerk": kosten_net_wegennet,
                "Kosten\naansluiting": kosten_conn_wegennet,
                "Totale kosten": kosten_tot_wegennet,
            }
        )
        out_leidingen = pd.concat(
            [out_leidingen_bron, out_leidingen_vraag], ignore_index=True
        )
        out_leidingen.loc[
            out_leidingen["Type"] == "Bron", ["Netwerk (m)", "Aansluiting (m)"]
        ] = np.nan
        out_leidingen = out_leidingen.sort_values(["Woonplaats", "Type"])
        out_fmt = out_leidingen.copy()
        for col in ["Netwerk (m)", "Aansluiting (m)"]:
            s = pd.to_numeric(out_fmt[col], errors="coerce")
            out_fmt[col] = s.map(
                lambda v: "-" if pd.isna(v) else format_dutch_number(v, 0)
            )
        for col in [
            "Kosten\nnetwerk",
            "Kosten\naansluiting",
            "Totale kosten",
        ]:
            s = pd.to_numeric(out_fmt[col], errors="coerce")
            out_fmt[col] = s.map(
                lambda v: ""
                if pd.isna(v)
                else f"€ {format_dutch_number(v, 0)}"
            )
        _render_wrapped_table(out_fmt, height=420)


def render_tabs(
    df_filtered: pd.DataFrame,
    threshold: float,
    show_sites_layer: bool,
    sites_costed: pd.DataFrame | None,
    warmtenet_gjson: dict | None = None,
    show_warmtenet: bool = False,
    show_wegennet: bool = False,
    warmtenet_wp: list[str] | None = None,
    wegennet_wp: list[str] | None = None,
    zoom_level: int | None = None,
    min_zoom_wegennet: int = 11,
    woonplaats_summary: pd.DataFrame | None = None,
    pandtype_counts_by_woonplaats: pd.DataFrame | None = None,
    pandtype_mwh_by_woonplaats: pd.DataFrame | None = None,
    pand_selectie: str | None = None,
    show_pandtype_labels: bool | None = None,
):
    """
    Tabs:
      - Top woonplaatsen (MWh)  [altijd]
      - Kandidaat hotspots [alleen als show_sites_layer]
    RAM-zuinig: minimale kolomselecties, vectorized formatting.
    """
    if isinstance(sites_costed, list):
        sites_costed_df = pd.DataFrame(sites_costed)
    else:
        sites_costed_df = sites_costed

    zoom_ok = (zoom_level is None) or (int(zoom_level) >= int(min_zoom_wegennet))
    show_comparison_tab = True
    show_comparison = bool(show_warmtenet and show_wegennet)
    warning_text = (
        "Schakel warmtebronnen en warmtevraag in om dekking (%), "
        "onbenutte warmte (MWh) en kosten te bekijken. Alleen beschikbaar "
        f"vanaf zoomniveau {min_zoom_wegennet}."
    )
    tab_labels = ["Top woonplaatsen (MWh)"]
    if show_comparison_tab:
        tab_labels.append("Warmtenet inzicht")
    if show_sites_layer:
        tab_labels.append("Kandidaat-voorzieningen")
    tabs = st.tabs(tab_labels)
    tab_idx = 0
    tab1 = tabs[tab_idx]
    tab_idx += 1

    # --- TAB 1: Top woonplaatsen (MWh) ---
    with tab1:
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
                .head(15)
                .rename(columns={"woonplaats": "Woonplaats"})
            )
            if "area_ha" in top_wp.columns:
                top_wp.rename(columns={"area_ha": area_display_col}, inplace=True)
                area_vals = top_wp[area_display_col].replace({0: pd.NA})
                top_wp[density_display_col] = top_wp["MWh"].div(area_vals)
            elif "MWh_per_ha" in top_wp.columns:
                top_wp.rename(columns={"MWh_per_ha": density_display_col}, inplace=True)
            else:
                top_wp[density_display_col] = pd.NA
            if "aantal_huizen" in top_wp.columns:
                top_wp.rename(columns={"aantal_huizen": "Panden"}, inplace=True)
            elif "aantal_VBOs" in top_wp.columns:
                top_wp.rename(columns={"aantal_VBOs": "Panden"}, inplace=True)
        else:
            # Beperk kolommen vóór groupby
            col_wp = "woonplaats"
            col_mwh = (
                "sum_mwh_raw"
                if "sum_mwh_raw" in df_filtered.columns
                else "gemiddeld_jaarverbruik_mWh"
            )
            col_density = "MWh_per_ha"
            col_area = "area_ha"
            col_panden = None
            available_cols = set(df_filtered.columns)
            if "aantal_huizen" in available_cols:
                col_panden = "aantal_huizen"
            elif "aantal_VBOs" in available_cols:
                col_panden = "aantal_VBOs"

            use_area = col_area in available_cols
            use_density_col = col_density in available_cols

            base_cols = [col_wp, col_mwh]
            use_panden = col_panden is not None
            if use_panden:
                base_cols.append(col_panden)
            if use_area:
                base_cols.append(col_area)
            if use_density_col and not use_area:
                # alleen meenemen als area ontbreekt; anders berekenen we het zelf
                base_cols.append(col_density)

            if set(base_cols) <= available_cols:
                df_wp = df_filtered.loc[:, base_cols]
            else:
                missing_cols = set(base_cols) - available_cols
                if missing_cols:
                    # kan gebeuren bij lege datasets; maak lege df
                    df_wp = pd.DataFrame(columns=base_cols)
                else:
                    df_wp = df_filtered.loc[:, base_cols]

            if not df_wp.empty:
                s = pd.to_numeric(df_wp[col_mwh], errors="coerce").fillna(0)
                df_wp = df_wp.assign(**{col_mwh: s})

                agg_map = {col_mwh: "sum"}
                if use_panden and col_panden:
                    df_wp[col_panden] = pd.to_numeric(
                        df_wp[col_panden], errors="coerce"
                    ).fillna(0)
                    agg_map[col_panden] = "sum"
                if use_area:
                    area_series = (
                        pd.to_numeric(df_wp[col_area], errors="coerce").fillna(0)
                    )
                    df_wp[col_area] = area_series
                    agg_map[col_area] = "sum"
                    density_source = "area"
                elif use_density_col:
                    density_series = pd.to_numeric(df_wp[col_density], errors="coerce")
                    df_wp[col_density] = density_series
                    agg_map[col_density] = "mean"
                    density_source = "col"
                else:
                    density_source = None

                top_wp = (
                    df_wp.groupby(col_wp, as_index=False, sort=False, observed=True)
                    .agg(agg_map)
                    .rename(columns={col_mwh: "MWh"})
                    .sort_values("MWh", ascending=False)
                    .head(15)
                )
                top_wp.rename(columns={col_wp: "Woonplaats"}, inplace=True)

                if use_area and col_area in top_wp.columns:
                    top_wp.rename(columns={col_area: area_display_col}, inplace=True)
                    area_vals = top_wp[area_display_col].replace({0: pd.NA})
                    top_wp[density_display_col] = top_wp["MWh"].div(area_vals)
                elif density_source == "col" and col_density in top_wp.columns:
                    top_wp.rename(
                        columns={col_density: density_display_col}, inplace=True
                    )
                else:
                    # geen bron beschikbaar; maak lege kolom
                    top_wp[density_display_col] = pd.NA

                if use_panden and col_panden and col_panden in top_wp.columns:
                    top_wp.rename(columns={col_panden: "Panden"}, inplace=True)

        if top_wp is None or top_wp.empty:
            st.info("Geen gegevens om te tonen.")
        else:
            type_col_woningen = "Kleinverbruik"
            type_col_bedrijven = "Middel- en grootverbruik"
            type_col_panden = "Panden"
            if (
                isinstance(pandtype_counts_by_woonplaats, pd.DataFrame)
                and not pandtype_counts_by_woonplaats.empty
                and "woonplaats" in pandtype_counts_by_woonplaats.columns
            ):
                counts_wp = pandtype_counts_by_woonplaats.loc[
                    :, ["woonplaats", "woningen", "bedrijven"]
                ].copy()
                counts_wp["woonplaats_norm"] = counts_wp["woonplaats"].map(
                    _normalize_woonplaats
                )
                top_wp["woonplaats_norm"] = top_wp["Woonplaats"].map(
                    _normalize_woonplaats
                )
                top_wp = top_wp.merge(
                    counts_wp[["woonplaats_norm", "woningen", "bedrijven"]],
                    on="woonplaats_norm",
                    how="left",
                )
                top_wp.rename(
                    columns={
                        "woningen": type_col_woningen,
                        "bedrijven": type_col_bedrijven,
                    },
                    inplace=True,
                )
            if "woonplaats_norm" in top_wp.columns:
                top_wp.drop(columns=["woonplaats_norm"], inplace=True)
            ordered_cols = ["Woonplaats", "MWh"]
            if type_col_panden in top_wp.columns:
                ordered_cols.append(type_col_panden)
            if type_col_woningen in top_wp.columns:
                ordered_cols.append(type_col_woningen)
            if type_col_bedrijven in top_wp.columns:
                ordered_cols.append(type_col_bedrijven)
            if area_display_col in top_wp.columns:
                ordered_cols.append(area_display_col)
            if density_display_col in top_wp.columns:
                ordered_cols.append(density_display_col)
            top_wp = top_wp.loc[:, [c for c in ordered_cols if c in top_wp.columns]]

            all_types_label = "Klein-, middel- en grootverbruik"
            show_labels = True if show_pandtype_labels is None else bool(
                show_pandtype_labels
            )
            show_type_tab = show_labels and (
                (pand_selectie is None)
                or (str(pand_selectie).strip() == all_types_label)
            )
            if show_type_tab:
                tab_total, tab_type = st.tabs(
                    [
                        "Totale warmtevraag",
                        "Totale warmtevraag per type pand",
                    ]
                )
            else:
                tab_total = st.tabs(["Totale warmtevraag"])[0]
                tab_type = None

            with tab_total:
                st.caption(
                    "Deze tabel is bedoeld voor het tonen van de top woonplaatsen "
                    "en het vergelijken daarvan. De waarden zijn gebaseerd op "
                    "vaste woonplaatsgrenzen en veranderen niet bij het "
                    "aanpassen van het zoomniveau."
                )
                if (
                    isinstance(pandtype_mwh_by_woonplaats, pd.DataFrame)
                    and not pandtype_mwh_by_woonplaats.empty
                ):
                    breakdown = pandtype_mwh_by_woonplaats.copy()
                    if "Woonplaats" in top_wp.columns:
                        top_norms = top_wp["Woonplaats"].map(_normalize_woonplaats)
                        top_order_map = {
                            wp_norm: idx for idx, wp_norm in enumerate(top_norms)
                        }
                        breakdown["woonplaats_norm"] = breakdown["woonplaats"].map(
                            _normalize_woonplaats
                        )
                        breakdown = breakdown[
                            breakdown["woonplaats_norm"].isin(set(top_norms))
                        ]
                        breakdown["top_rank"] = breakdown["woonplaats_norm"].map(
                            top_order_map
                        )
                    if not breakdown.empty:
                        type_map = {
                            "A": "Kleinverbruik",
                            "B": "Middel- en grootverbruik",
                            "C": "Middel- en grootverbruik",
                        }
                        breakdown["Type pand"] = breakdown["type_code"].map(type_map)
                        breakdown = breakdown[breakdown["Type pand"].notna()]
                        breakdown["Woonplaats"] = (
                            breakdown["woonplaats"].astype(str).str.strip()
                        )

                        agg_map: dict[str, str] = {"MWh": "sum"}
                        if "aantal_panden" in breakdown.columns:
                            agg_map["aantal_panden"] = "sum"
                        if "area_ha" in breakdown.columns:
                            agg_map["area_ha"] = "sum"

                        group_cols = ["Woonplaats", "Type pand"]
                        if "top_rank" in breakdown.columns:
                            group_cols.append("top_rank")
                        breakdown = (
                            breakdown.groupby(
                                group_cols,
                                as_index=False,
                                sort=False,
                                observed=True,
                            )
                            .agg(agg_map)
                            .reset_index(drop=True)
                        )

                        if area_display_col in top_wp.columns:
                            area_series = top_wp.set_index("Woonplaats")[
                                area_display_col
                            ]
                            area_series = pd.to_numeric(
                                area_series, errors="coerce"
                            )
                            breakdown["area_ha"] = breakdown["Woonplaats"].map(
                                area_series
                            )
                        elif "area_ha" in breakdown.columns:
                            breakdown["area_ha"] = pd.NA

                        if "area_ha" in breakdown.columns:
                            area_vals = pd.to_numeric(
                                breakdown["area_ha"], errors="coerce"
                            ).replace({0: pd.NA})
                            breakdown["MWh_per_ha"] = breakdown["MWh"].div(area_vals)

                        breakdown["type_order"] = breakdown["Type pand"].map(
                            {"Kleinverbruik": 0, "Middel- en grootverbruik": 1}
                        )
                        sort_cols = (
                            ["top_rank", "type_order"]
                            if "top_rank" in breakdown.columns
                            else ["Woonplaats", "type_order"]
                        )
                        breakdown.sort_values(sort_cols, inplace=True)

                        rename_map = {"aantal_panden": "Panden"}
                        if "area_ha" in breakdown.columns:
                            rename_map["area_ha"] = area_display_col
                        if "MWh_per_ha" in breakdown.columns:
                            rename_map["MWh_per_ha"] = density_display_col
                        breakdown.rename(columns=rename_map, inplace=True)

                        ordered_cols = [
                            "Woonplaats",
                            "Type pand",
                            "MWh",
                            "Panden",
                        ]
                        if area_display_col in breakdown.columns:
                            ordered_cols.append(area_display_col)
                        if density_display_col in breakdown.columns:
                            ordered_cols.append(density_display_col)

                        breakdown = breakdown.loc[
                            :, [c for c in ordered_cols if c in breakdown.columns]
                        ]

                        breakdown_fmt = breakdown.copy()
                        breakdown_fmt["MWh"] = (
                            pd.to_numeric(breakdown_fmt["MWh"], errors="coerce")
                            .round(0)
                            .astype("Int64")
                            .map(lambda v: "" if pd.isna(v) else _fmt0(v))
                        )
                        if "Panden" in breakdown_fmt.columns:
                            breakdown_fmt["Panden"] = breakdown_fmt["Panden"].map(
                                lambda v: "" if pd.isna(v) else _fmt0(v)
                            )
                        if area_display_col in breakdown_fmt.columns:
                            breakdown_fmt[area_display_col] = breakdown_fmt[
                                area_display_col
                            ].map(lambda v: "" if pd.isna(v) else _fmt2(float(v)))
                        if density_display_col in breakdown_fmt.columns:
                            breakdown_fmt[density_display_col] = breakdown_fmt[
                                density_display_col
                            ].map(lambda v: "" if pd.isna(v) else _fmt2(float(v)))

                        _render_wrapped_table(breakdown_fmt, height=420)
                    else:
                        st.info("Geen gegevens om te tonen.")
                else:
                    st.info("Geen gegevens om te tonen.")

            if show_type_tab and tab_type is not None:
                with tab_type:
                    st.caption(
                        "Deze tabel is een samenvatting van de H3-hexagonen die je "
                        "op de kaart ziet. De gebiedsoppervlakte is gebaseerd op de "
                        "zichtbare hexagonen op de kaart en kan veranderen bij het "
                        "aanpassen van het zoomniveau. Zo heeft een hexagoon per "
                        "zoomniveau een vaste grootte, wat invloed heeft op welke "
                        "typen panden binnen één hexagoon vallen."
                    )
                    if (
                        isinstance(pandtype_mwh_by_woonplaats, pd.DataFrame)
                        and not pandtype_mwh_by_woonplaats.empty
                    ):
                        breakdown = pandtype_mwh_by_woonplaats.copy()
                        if "Woonplaats" in top_wp.columns:
                            top_norms = top_wp["Woonplaats"].map(_normalize_woonplaats)
                            top_order_map = {
                                wp_norm: idx for idx, wp_norm in enumerate(top_norms)
                            }
                            breakdown["woonplaats_norm"] = breakdown["woonplaats"].map(
                                _normalize_woonplaats
                            )
                            breakdown = breakdown[
                                breakdown["woonplaats_norm"].isin(set(top_norms))
                            ]
                            breakdown["top_rank"] = breakdown["woonplaats_norm"].map(
                                top_order_map
                            )
                            breakdown.drop(columns=["woonplaats_norm"], inplace=True)
                        if not breakdown.empty:
                            type_map = {
                                "A": "A - Kleinverbruik",
                                "B": "B - Middel- en grootverbruik",
                                "C": "C - Klein-, middel- en grootverbruik",
                            }
                            breakdown["Type pand"] = (
                                breakdown["type_code"].map(type_map).fillna(
                                    breakdown["type_code"]
                                )
                            )
                            breakdown["Woonplaats"] = (
                                breakdown["woonplaats"].astype(str).str.strip()
                            )
                            breakdown["type_order"] = (
                                breakdown["type_code"]
                                .map({"A": 0, "B": 1, "C": 2})
                                .fillna(9)
                            )
                            sort_cols = (
                                ["top_rank", "type_order"]
                                if "top_rank" in breakdown.columns
                                else ["Woonplaats", "type_order"]
                            )
                            breakdown.sort_values(sort_cols, inplace=True)

                            rename_map = {
                                "aantal_panden": "Panden",
                            }
                            if "area_ha" in breakdown.columns:
                                rename_map["area_ha"] = area_display_col
                            if "MWh_per_ha" in breakdown.columns:
                                rename_map["MWh_per_ha"] = density_display_col
                            breakdown.rename(columns=rename_map, inplace=True)

                            ordered_cols = [
                                "Woonplaats",
                                "Type pand",
                                "MWh",
                                "Panden",
                            ]
                            if area_display_col in breakdown.columns:
                                ordered_cols.append(area_display_col)
                            if density_display_col in breakdown.columns:
                                ordered_cols.append(density_display_col)

                            breakdown = breakdown.loc[
                                :, [c for c in ordered_cols if c in breakdown.columns]
                            ]

                            breakdown_fmt = breakdown.copy()
                            breakdown_fmt["MWh"] = (
                                pd.to_numeric(breakdown_fmt["MWh"], errors="coerce")
                                .round(0)
                                .astype("Int64")
                                .map(lambda v: "" if pd.isna(v) else _fmt0(v))
                            )
                            if "Panden" in breakdown_fmt.columns:
                                breakdown_fmt["Panden"] = breakdown_fmt["Panden"].map(
                                    lambda v: "" if pd.isna(v) else _fmt0(v)
                                )
                            if area_display_col in breakdown_fmt.columns:
                                breakdown_fmt[area_display_col] = breakdown_fmt[
                                    area_display_col
                                ].map(lambda v: "" if pd.isna(v) else _fmt2(float(v)))
                            if density_display_col in breakdown_fmt.columns:
                                breakdown_fmt[density_display_col] = breakdown_fmt[
                                    density_display_col
                                ].map(lambda v: "" if pd.isna(v) else _fmt2(float(v)))

                            _render_wrapped_table(breakdown_fmt, height=420)
                        else:
                            st.info("Geen gegevens om te tonen.")
                    else:
                        st.info("Geen gegevens om te tonen.")

    # --- TAB 2: Warmtenet inzicht ---
    if show_comparison_tab:
        with tabs[tab_idx]:
            if not zoom_ok:
                st.warning(warning_text)
            elif show_comparison:
                _render_warmtenet_comparison(
                    warmtenet_gjson, warmtenet_wp, wegennet_wp, df_filtered
                )
            else:
                st.warning(warning_text)
        tab_idx += 1

    # --- TAB 3: Kandidaat hotspots ---
    if show_sites_layer:
        with tabs[tab_idx]:
            if sites_costed_df is not None and not sites_costed_df.empty:
                cols_keep = [
                    "site_rank",
                    "gebied_label",
                    "cluster_buildings",
                    "cap_buildings",
                    "connected_buildings",
                    "cluster_MWh",
                    "cap_MWh",
                    "connected_MWh",
                    "utilization_pct",
                ]
                have = [c for c in cols_keep if c in sites_costed_df.columns]
                out = sites_costed_df.loc[:, have].copy()
                if "site_rank" in out.columns:
                    out["site_rank"] = pd.to_numeric(
                        out["site_rank"], errors="coerce"
                    ).astype("Int32")
                rename_map = {
                    "site_rank": "#",
                    "gebied_label": "Woonplaats",
                    "cluster_buildings": "Gebouwen\nin radar",
                    "cap_buildings": "Capaciteit\ngebouwen",
                    "connected_buildings": "Aangesloten\ngebouwen",
                    "cluster_MWh": "MWh\nin radar",
                    "cap_MWh": "Capaciteit\nMWh",
                    "connected_MWh": "Aangesloten\nMWh",
                    "utilization_pct": "Benutting\n(%)",
                }
                out.rename(
                    columns={k: v for k, v in rename_map.items() if k in out.columns},
                    inplace=True,
                )

                # Totaalrij (alleen over kolommen die bestaan)
                out_full = out.copy()
                totals_cols = [
                    "Gebouwen\nin radar",
                    "Capaciteit\ngebouwen",
                    "Aangesloten\ngebouwen",
                    "MWh\nin radar",
                    "Capaciteit\nMWh",
                    "Aangesloten\nMWh",
                ]
                available_totals = {
                    col: pd.to_numeric(out[col], errors="coerce").fillna(0).sum()
                    for col in totals_cols
                    if col in out.columns
                }
                if available_totals:
                    totals_values = []
                    for col_name in out.columns:
                        if col_name in available_totals:
                            totals_values.append(available_totals[col_name])
                        elif col_name == "Gebied":
                            totals_values.append("Totaal")
                        elif col_name == "Voorziening":
                            totals_values.append("")
                        else:
                            totals_values.append("")
                    totals_df = pd.DataFrame([totals_values], columns=out.columns)
                    out_full = pd.concat([out_full, totals_df], ignore_index=True)

                # Formatteringen (kolomsgewijs)
                out_fmt = out_full.copy()
                if "Voorziening" in out_fmt.columns:
                    col = pd.to_numeric(out_fmt["Voorziening"], errors="coerce")
                    out_fmt["Voorziening"] = ""
                    mask = col.notna()
                    if mask.any():
                        out_fmt.loc[mask, "Voorziening"] = (
                            col.loc[mask].astype("int64").astype(str)
                        )
                for col in [
                    "Gebouwen\nin radar",
                    "Capaciteit\ngebouwen",
                    "Aangesloten\ngebouwen",
                    "MWh\nin radar",
                    "Capaciteit\nMWh",
                    "Aangesloten\nMWh",
                ]:
                    if col in out_fmt.columns:
                        s = (
                            pd.to_numeric(out_fmt[col], errors="coerce")
                            .fillna(0)
                            .round(0)
                            .astype("int64")
                        )
                        out_fmt[col] = s.map(lambda v: f"{v:,}".replace(",", "."))
                if "Warmtevraag\n per pand (MWh)" in out_fmt.columns:
                    s = pd.to_numeric(
                        out_fmt["Warmtevraag\n per pand (MWh)"], errors="coerce"
                    )
                    out_fmt["Warmtevraag\n per pand (MWh)"] = s.map(
                        lambda v: ""
                        if pd.isna(v)
                        else f"{float(v):,.2f}".replace(",", "#")
                        .replace(".", ",")
                        .replace("#", ".")
                    )
                if "Benutting\n(%)" in out_fmt.columns:
                    s = pd.to_numeric(out_fmt["Benutting\n(%)"], errors="coerce")
                    out_fmt["Benutting\n(%)"] = s.map(
                        lambda v: "" if pd.isna(v) else format_dutch_number(v, 1)
                    )

                _render_wrapped_table(out_fmt, height=440)

            else:
                st.info(
                    "Geen locaties berekend. Pas instellingen aan en klik op ‘Maak Kaart’."
                )
