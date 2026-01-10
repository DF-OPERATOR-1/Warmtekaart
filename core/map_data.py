# core/map_data.py
from __future__ import annotations

import math
from typing import Callable, Any

import h3
import pandas as pd
import streamlit as st

from .config import BASE_H3_RES
from .h3agg import H3_RES13_COL, build_res13, build_res13_agg, rollup_to_resolution
from .utils import format_dutch_number


def extract_selected_hex_from_payload(state_obj) -> str | None:
    """Parse Streamlit/pydeck selection payload en geef h3_index terug."""
    if state_obj is None:
        return None

    objects = None
    if isinstance(state_obj, dict):
        if "objects" in state_obj:
            objects = state_obj.get("objects")
        elif "selection" in state_obj:
            sel = state_obj.get("selection", {})
            if isinstance(sel, dict):
                objects = sel.get("objects")
    else:
        sel_attr = getattr(state_obj, "selection", None)
        if isinstance(sel_attr, dict):
            objects = sel_attr.get("objects")
        elif hasattr(state_obj, "get"):
            maybe = state_obj.get("selection")
            if isinstance(maybe, dict):
                objects = maybe.get("objects")

    if not objects:
        return None

    first_obj = None
    if isinstance(objects, dict):
        first_obj = next(iter(objects.values()), None)
    elif isinstance(objects, list) and objects:
        first_obj = objects[0]

    if isinstance(first_obj, list) and first_obj:
        first_obj = first_obj[0]

    if (
        isinstance(first_obj, dict)
        and "object" in first_obj
        and isinstance(first_obj["object"], dict)
    ):
        first_obj = first_obj["object"]

    if (
        isinstance(first_obj, dict)
        and "properties" in first_obj
        and isinstance(first_obj["properties"], dict)
    ):
        candidate = first_obj["properties"].get("h3_index")
        if candidate:
            return str(candidate)

    if isinstance(first_obj, dict):
        for key in ("h3_index", "hex_id", "hexagon", "cell_id"):
            candidate = first_obj.get(key)
            if candidate:
                return str(candidate)

    return None


@st.cache_data(show_spinner=False, max_entries=2, ttl=1800)
def build_res13_cached(df_src: pd.DataFrame) -> pd.DataFrame:
    """Cache-wrapper rond build_res13 om herhaald werk te voorkomen."""
    return build_res13(df_src)


@st.cache_data(show_spinner=False, max_entries=6, ttl=1800)
def ensure_parent_series_for_cached(
    df_with_res13: pd.DataFrame, res: int
) -> pd.Series:
    """Geef h3-index op gewenste resolutie, bereken ouders indien nodig."""
    if res == BASE_H3_RES:
        return df_with_res13[H3_RES13_COL]
    parents = [h3.cell_to_parent(h, res) for h in df_with_res13[H3_RES13_COL]]
    return pd.Series(parents, index=df_with_res13.index, name=f"h3_r{res}")


def build_map_dataframe(
    df_input: pd.DataFrame,
    res: int,
    *,
    log_fn: Callable[[str], None] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Bereid dataframes voor de kaart en tooltips."""
    df_map = build_res13_cached(df_input).reindex(df_input.index)
    if log_fn:
        log_fn("after_h3_base_raw")

    if H3_RES13_COL in df_map.columns:
        missing = df_map[H3_RES13_COL].isna()
        if missing.any():
            df_map.loc[missing, H3_RES13_COL] = [
                h3.latlng_to_cell(float(la), float(lo), BASE_H3_RES)
                for la, lo in zip(
                    df_map.loc[missing, "latitude"], df_map.loc[missing, "longitude"]
                )
            ]

    if res == BASE_H3_RES:
        df_map["h3_index"] = df_map[H3_RES13_COL]
    else:
        parent_col = f"h3_r{res}"
        parent_series = ensure_parent_series_for_cached(df_map, res).rename(parent_col)
        if parent_col not in df_map.columns:
            df_map = df_map.join(parent_series, how="left")
        df_map["h3_index"] = df_map[parent_col]

    for col, kind in [
        ("gemiddeld_jaarverbruik_mWh", "float32"),
        ("kWh_per_m2", "float32"),
        ("totale_oppervlakte", "float32"),
        ("bouwjaar", "float32"),
        ("aantal_VBOs", "int32"),
    ]:
        if col in df_map.columns:
            df_map[col] = pd.to_numeric(df_map[col], errors="coerce").astype(kind)

    df_extra_info = df_map.loc[:, ["h3_index", "woonplaats"]].drop_duplicates(
        subset=["h3_index"]
    )

    res13_agg = build_res13_agg(
        df_map[
            [
                H3_RES13_COL,
                "kWh_per_m2",
                "gemiddeld_jaarverbruik_mWh",
                "totale_oppervlakte",
                "bouwjaar",
                "aantal_VBOs",
            ]
        ]
    )
    df_filtered = rollup_to_resolution(res13_agg, res, _cache_key=res)
    if log_fn:
        log_fn("after_rollup_raw")
    del res13_agg

    return df_filtered, df_extra_info, df_map


def build_site_records(
    sites_df: pd.DataFrame,
    df_filtered: pd.DataFrame,
    k_val: int,
) -> list[dict]:
    """
    Zet een sites-DataFrame om naar records met polygonen, hexagonen en geformatteerde metrics.
    """
    if sites_df is None or sites_df.empty:
        return []

    sites = sites_df.merge(
        df_filtered[["h3_index", "woonplaats"]].drop_duplicates(),
        on="h3_index",
        how="left",
    )
    woonplaats_series = sites["woonplaats"]
    if hasattr(woonplaats_series, "cat"):
        if "Onbekend" not in woonplaats_series.cat.categories:
            woonplaats_series = woonplaats_series.cat.add_categories(["Onbekend"])
    sites["woonplaats"] = woonplaats_series
    sites["gebied_label"] = sites["woonplaats"].fillna("Onbekend")

    def _safe_int(val, default=None):
        try:
            if val is None:
                return default
            if isinstance(val, float) and math.isnan(val):
                return default
        except Exception:
            pass
        try:
            return int(round(float(val)))
        except Exception:
            return default

    def _safe_float(val, default=None):
        try:
            if val is None:
                return default
            if isinstance(val, float) and math.isnan(val):
                return default
        except Exception:
            pass
        try:
            return float(val)
        except Exception:
            return default

    def _fmt0s(x):
        val = _safe_int(x)
        if val is None:
            return "-"
        return format_dutch_number(val, 0)

    def _fmt2s(x):
        val = _safe_float(x)
        if val is None:
            return "-"
        return format_dutch_number(val, 2)

    def _fmt4s(x):
        val = _safe_float(x)
        if val is None:
            return "-"
        return format_dutch_number(val, 4)

    def _fmt_year(x):
        val = _safe_int(x)
        if val is None:
            return "-"
        return str(val)

    records: list[dict] = []

    for idx, rec in enumerate(sites.itertuples(index=False), start=1):
        record = {
            "h3_index": rec.h3_index,
            "woonplaats": rec.woonplaats,
            "gebied_label": rec.gebied_label,
            "cluster_buildings": int(rec.cluster_buildings),
            "cap_buildings": int(rec.cap_buildings),
            "connected_buildings": int(rec.connected_buildings),
            "cluster_MWh": int(rec.cluster_MWh),
            "cap_MWh": int(rec.cap_MWh),
            "connected_MWh": int(rec.connected_MWh),
            "utilization_pct": int(rec.utilization_pct),
            "cluster_buildings_fmt": _fmt0s(rec.cluster_buildings),
            "cap_buildings_fmt": _fmt0s(rec.cap_buildings),
            "connected_buildings_fmt": _fmt0s(rec.connected_buildings),
            "cluster_MWh_fmt": _fmt0s(rec.cluster_MWh),
            "cap_MWh_fmt": _fmt0s(rec.cap_MWh),
            "connected_MWh_fmt": _fmt0s(rec.connected_MWh),
            "utilization_pct_fmt": f"{int(rec.utilization_pct)}",
        }
        record["hex_section_display"] = "none"
        record["site_section_display"] = "block"
        record["geo_section_display"] = "none"
        record["geo_extra_rows"] = ""
        record["gemeente_row_display"] = "block"
        record["buurt_row_display"] = "block"
        record["site_rank"] = idx

        cluster_buildings_val = max(record["cluster_buildings"], 0)
        if cluster_buildings_val > 0:
            cluster_mwh_val = float(record["cluster_MWh"])
            avg_mwh_per_pand = cluster_mwh_val / cluster_buildings_val
        else:
            avg_mwh_per_pand = 0.0
        record["MWh_per_pand"] = avg_mwh_per_pand
        record["MWh_per_pand_fmt"] = _fmt2s(avg_mwh_per_pand)

        hex_ids = list(h3.grid_disk(rec.h3_index, int(k_val)))
        df_site_hex = df_filtered[df_filtered["h3_index"].isin(hex_ids)].copy()

        coverage_polygons = []
        coverage_summary: dict[str, Any] = {}
        coverage_hexes: list[dict[str, Any]] = []

        if hex_ids:
            try:
                multi_polys = h3.h3_set_to_multi_polygon(hex_ids, geo_json=True)
            except Exception:
                multi_polys = []
            for poly in multi_polys:
                for loop in poly:
                    coords = [[float(pt[1]), float(pt[0])] for pt in loop]
                    if coords and coords[0] != coords[-1]:
                        coords.append(coords[0])
                    coverage_polygons.append(coords)

        if not df_site_hex.empty:

            def _series_sum(df_local: pd.DataFrame, column_name: str, want_int: bool = False):
                if column_name not in df_local.columns:
                    return 0
                vals = pd.to_numeric(df_local[column_name], errors="coerce").fillna(0)
                total = float(vals.sum())
                return _safe_int(total, 0) if want_int else _safe_float(total, 0.0)

            def _series_mean(df_local: pd.DataFrame, column_name: str, want_int: bool = False):
                if column_name not in df_local.columns:
                    return 0 if want_int else 0.0
                vals = pd.to_numeric(df_local[column_name], errors="coerce").dropna()
                if vals.empty:
                    return 0 if want_int else 0.0
                avg = float(vals.mean())
                return _safe_int(avg, 0) if want_int else _safe_float(avg, 0.0)

            total_vbos = _series_sum(df_site_hex, "aantal_VBOs", True)
            total_huizen = _series_sum(df_site_hex, "aantal_huizen", True)
            total_mwh = _series_sum(df_site_hex, "gemiddeld_jaarverbruik_mWh")
            total_oppervlakte = _series_sum(df_site_hex, "totale_oppervlakte", True)
            total_area_ha = _series_sum(df_site_hex, "area_ha")
            total_area_m2 = _series_sum(df_site_hex, "area_m2")
            avg_kwh_m2 = _series_mean(df_site_hex, "kWh_per_m2")
            avg_bouwjaar = _series_mean(df_site_hex, "bouwjaar", True)
            avg_density = _series_mean(df_site_hex, "MWh_per_ha")
            avg_mwh_per_pand_summary = total_mwh / total_huizen if total_huizen else 0.0
            avg_area_ha = _series_mean(df_site_hex, "area_ha")
            avg_area_m2 = _series_mean(df_site_hex, "area_m2")

            coverage_summary = {
                "hex_count": len(df_site_hex),
                "hex_count_fmt": _fmt0s(len(df_site_hex)),
                "aantal_VBOs": total_vbos,
                "aantal_huizen": total_huizen,
                "aantal_VBOs_fmt": _fmt0s(total_vbos),
                "aantal_huizen_fmt": _fmt0s(total_huizen),
                "kWh_per_m2": avg_kwh_m2,
                "kWh_per_m2_fmt": _fmt0s(avg_kwh_m2),
                "gemiddeld_jaarverbruik_mWh": total_mwh,
                "gemiddeld_jaarverbruik_mWh_fmt": _fmt0s(total_mwh),
                "totale_oppervlakte": total_oppervlakte,
                "totale_oppervlakte_fmt": _fmt0s(total_oppervlakte),
                "area_ha_r": avg_area_ha,
                "area_ha_r_fmt": _fmt4s(avg_area_ha),
                "area_m2": avg_area_m2,
                "area_m2_fmt": _fmt0s(avg_area_m2),
                "area_ha_total": total_area_ha,
                "area_ha_total_fmt": _fmt2s(total_area_ha),
                "area_m2_total": total_area_m2,
                "area_m2_total_fmt": _fmt0s(total_area_m2),
                "MWh_per_ha": avg_density,
                "MWh_per_ha_fmt": _fmt2s(avg_density),
                "bouwjaar": avg_bouwjaar,
                "bouwjaar_fmt": _fmt_year(avg_bouwjaar),
                "MWh_per_pand": avg_mwh_per_pand_summary,
                "MWh_per_pand_fmt": _fmt2s(avg_mwh_per_pand_summary),
                "site_rank_label": record["site_rank"],
            }

            for cov_idx, cov in enumerate(df_site_hex.itertuples(index=False), start=1):
                cov_dict = {
                    "site_rank": idx,
                    "sub_rank": cov_idx,
                    "h3_index": getattr(cov, "h3_index", ""),
                    "geo_extra_rows": "",
                    "gemeente_row_display": "block",
                    "buurt_row_display": "block",
                    "woonplaats": getattr(cov, "woonplaats", "")
                    or record.get("woonplaats", ""),
                    "aantal_huizen": _safe_int(getattr(cov, "aantal_huizen", 0), 0)
                    or 0,
                    "aantal_VBOs": _safe_int(getattr(cov, "aantal_VBOs", 0), 0)
                    or 0,
                    "MWh_per_ha_r": _safe_float(getattr(cov, "MWh_per_ha_r", 0.0), 0.0)
                    or 0.0,
                    "gemiddeld_jaarverbruik_mWh_r": _safe_float(
                        getattr(cov, "gemiddeld_jaarverbruik_mWh_r", 0.0), 0.0
                    )
                    or 0.0,
                    "area_ha_r": _safe_float(getattr(cov, "area_ha_r", 0.0), 0.0)
                    or 0.0,
                    "area_m2": _safe_float(getattr(cov, "area_m2", 0.0), 0.0)
                    or 0.0,
                    "kWh_per_m2": _safe_float(getattr(cov, "kWh_per_m2", 0.0), 0.0)
                    or 0.0,
                    "totale_oppervlakte": _safe_int(
                        getattr(cov, "totale_oppervlakte", 0), 0
                    )
                    or 0,
                    "bouwjaar": _safe_int(getattr(cov, "bouwjaar", 0), 0) or 0,
                    "aantal_huizen_fmt": _fmt0s(getattr(cov, "aantal_huizen", 0)),
                    "aantal_VBOs_fmt": _fmt0s(getattr(cov, "aantal_VBOs", 0)),
                    "MWh_per_ha_r_fmt": _fmt2s(
                        getattr(cov, "MWh_per_ha_r", 0.0)
                    ),
                    "gemiddeld_jaarverbruik_mWh_r_fmt": _fmt0s(
                        getattr(cov, "gemiddeld_jaarverbruik_mWh_r", 0)
                    ),
                    "area_ha_r_fmt": _fmt4s(getattr(cov, "area_ha_r", 0.0)),
                    "area_m2_fmt": _fmt0s(getattr(cov, "area_m2", 0.0)),
                    "kWh_per_m2_fmt": _fmt0s(getattr(cov, "kWh_per_m2", 0)),
                    "totale_oppervlakte_fmt": _fmt0s(
                        getattr(cov, "totale_oppervlakte", 0)
                    ),
                    "bouwjaar_fmt": _fmt_year(getattr(cov, "bouwjaar", 0)),
                    "MWh_per_pand": _safe_float(
                        getattr(cov, "MWh_per_pand", 0.0), 0.0
                    )
                    or 0.0,
                    "MWh_per_pand_fmt": _fmt2s(
                        getattr(cov, "MWh_per_pand", 0.0)
                    ),
                    "hex_section_display": "block",
                    "site_section_display": "block",
                    "geo_section_display": "none",
                    "cluster_buildings": record["cluster_buildings"],
                    "cap_buildings": record["cap_buildings"],
                    "connected_buildings": record["connected_buildings"],
                    "cluster_MWh": record["cluster_MWh"],
                    "cap_MWh": record["cap_MWh"],
                    "connected_MWh": record["connected_MWh"],
                    "utilization_pct": record["utilization_pct"],
                    "cluster_buildings_fmt": record["cluster_buildings_fmt"],
                    "cap_buildings_fmt": record["cap_buildings_fmt"],
                    "connected_buildings_fmt": record["connected_buildings_fmt"],
                    "cluster_MWh_fmt": record["cluster_MWh_fmt"],
                    "cap_MWh_fmt": record["cap_MWh_fmt"],
                    "connected_MWh_fmt": record["connected_MWh_fmt"],
                    "utilization_pct_fmt": record["utilization_pct_fmt"],
                    "site_rank": idx,
                    "site_rank_label": idx,
                }
                coverage_hexes.append(cov_dict)

        density_value = coverage_summary.get("MWh_per_ha", 0.0) if coverage_summary else 0.0
        record["MWh_per_ha"] = float(density_value or 0.0)
        record["MWh_per_ha_fmt"] = _fmt2s(record["MWh_per_ha"])

        record["coverage_polygons"] = coverage_polygons
        record["coverage_summary"] = coverage_summary
        record["coverage_hexes"] = coverage_hexes
        record["lat"], record["lon"] = h3.cell_to_latlng(rec.h3_index)
        records.append(record)

    return records
