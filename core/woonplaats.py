"""Hulpfuncties voor woonplaats-aggregaties en oppervlakten."""

# core/woonplaats.py
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from .config import WOONPLAATS_AREA_PATH


def normalize_woonplaats(value: str | None) -> str:
    """Normaliseer een woonplaatsnaam voor vergelijkingen."""
    return str(value or "").strip().lower()


@st.cache_data(show_spinner=False, max_entries=2, ttl=86400)
def load_woonplaats_areas(path: str | Path | None = None) -> pd.DataFrame:
    """Laad woonplaats-oppervlakte (ha) uit voorbewerkt bestand."""
    path_obj = Path(path) if path else None
    if path_obj and path_obj.suffix.lower() in {".csv", ".parquet"}:
        return _load_woonplaats_areas_from_file(path_obj)
    if not path_obj and WOONPLAATS_AREA_PATH and WOONPLAATS_AREA_PATH.exists():
        return _load_woonplaats_areas_from_file(WOONPLAATS_AREA_PATH)
    return pd.DataFrame(columns=["woonplaats", "area_ha"])


def _load_woonplaats_areas_from_file(path: Path) -> pd.DataFrame:
    """Lees woonplaats-oppervlakte uit csv/parquet."""
    if not path.exists():
        return pd.DataFrame(columns=["woonplaats", "area_ha"])
    if path.suffix.lower() == ".parquet":
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)
    if df.empty:
        return pd.DataFrame(columns=["woonplaats", "area_ha"])
    if not {"woonplaats", "area_ha"}.issubset(df.columns):
        return pd.DataFrame(columns=["woonplaats", "area_ha"])
    out = df.loc[:, ["woonplaats", "area_ha"]].copy()
    out["woonplaats"] = out["woonplaats"].astype(str).str.strip()
    out["area_ha"] = pd.to_numeric(out["area_ha"], errors="coerce").fillna(0.0)
    return out[out["area_ha"] > 0].reset_index(drop=True)


def build_woonplaats_summary(
    df_points: pd.DataFrame,
    area_df: pd.DataFrame | None = None,
    *,
    woonplaats_col: str = "woonplaats",
) -> pd.DataFrame:
    """Bouw een woonplaats-samenvatting op basis van puntdata."""
    if df_points is None or df_points.empty or woonplaats_col not in df_points.columns:
        return pd.DataFrame()
    mwh_col = (
        "gemiddeld_jaarverbruik_mWh"
        if "gemiddeld_jaarverbruik_mWh" in df_points.columns
        else "sum_mwh_raw" if "sum_mwh_raw" in df_points.columns else None
    )
    if not mwh_col:
        return pd.DataFrame()
    base_cols = [woonplaats_col, mwh_col]
    if "aantal_VBOs" in df_points.columns:
        base_cols.append("aantal_VBOs")
    df = df_points.loc[:, base_cols].copy()
    df[woonplaats_col] = df[woonplaats_col].astype(str).str.strip()
    df[mwh_col] = pd.to_numeric(df[mwh_col], errors="coerce").fillna(0.0)
    agg_map: dict[str, tuple[str, str]] = {
        "MWh": (mwh_col, "sum"),
        "aantal_huizen": (woonplaats_col, "size"),
    }
    if "aantal_VBOs" in df.columns:
        df["aantal_VBOs"] = pd.to_numeric(df["aantal_VBOs"], errors="coerce").fillna(0)
        agg_map["aantal_VBOs"] = ("aantal_VBOs", "sum")
    grouped = (
        df.groupby(woonplaats_col, as_index=False, sort=False, observed=True)
        .agg(**agg_map)
        .rename(columns={woonplaats_col: "woonplaats"})
    )
    if area_df is not None and not area_df.empty and "woonplaats" in area_df.columns:
        area_df = area_df.copy()
        area_df["woonplaats_norm"] = area_df["woonplaats"].map(normalize_woonplaats)
        grouped["woonplaats_norm"] = grouped["woonplaats"].map(normalize_woonplaats)
        grouped = grouped.merge(
            area_df[["woonplaats_norm", "area_ha"]],
            on="woonplaats_norm",
            how="left",
        )
        grouped.drop(columns=["woonplaats_norm"], inplace=True)
        area_vals = grouped["area_ha"].replace({0: pd.NA})
        grouped["MWh_per_ha"] = grouped["MWh"].div(area_vals)
    return grouped
