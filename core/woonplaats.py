"""Hulpfuncties voor woonplaats-aggregaties en oppervlakten."""

# core/woonplaats.py
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

import pandas as pd
import streamlit as st
from shapely import wkb
from shapely import ops as shapely_ops

from .config import WOONPLAATS_GPKG_PATH, WOONPLAATS_AREA_PATH


def normalize_woonplaats(value: str | None) -> str:
    """Normaliseer een woonplaatsnaam voor vergelijkingen."""
    return str(value or "").strip().lower()


def _gpkg_geom_to_wkb(blob: bytes | memoryview | None) -> bytes | None:
    """Zet een GPKG-geometry blob om naar WKB (of None bij fout)."""
    if blob is None:
        return None
    if isinstance(blob, memoryview):
        blob = blob.tobytes()
    if len(blob) < 8:
        return None
    if blob[0:2] != b"GP":
        return blob
    flags = blob[3]
    envelope_indicator = (flags >> 1) & 0x07
    envelope_sizes = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}
    envelope_size = envelope_sizes.get(envelope_indicator, 0)
    wkb_offset = 8 + envelope_size
    return blob[wkb_offset:]


def _pick_layer(
    rows: Iterable[tuple[str, str, int]],
) -> tuple[str, str, int] | None:
    """Kies de woonplaatslaag als die bestaat, anders de eerste."""
    for table_name, col_name, srs_id in rows:
        if str(table_name).strip().lower() == "woonplaats":
            return table_name, col_name, srs_id
    return next(iter(rows), None)


def _pick_name_column(cols: list[tuple]) -> str | None:
    """Zoek een kolom die de woonplaatsnaam bevat."""
    col_names = [c[1] for c in cols]
    for candidate in ("woonplaats", "naam", "name", "wpl_naam", "plaatsnaam"):
        if candidate in col_names:
            return candidate
    for _, name, col_type, *_ in cols:
        if isinstance(col_type, str) and "text" in col_type.lower():
            return name
    return None


@st.cache_data(show_spinner=False, max_entries=2, ttl=86400)
def load_woonplaats_areas(path: str | Path | None = None) -> pd.DataFrame:
    """Laad woonplaats-oppervlakte (ha) uit voorbewerkt bestand."""
    path_obj = Path(path) if path else None
    if path_obj and path_obj.suffix.lower() in {".csv", ".parquet"}:
        return _load_woonplaats_areas_from_file(path_obj)
    if (
        not path_obj
        and WOONPLAATS_AREA_PATH
        and WOONPLAATS_AREA_PATH.exists()
    ):
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


def _load_woonplaats_areas_from_gpkg(path: Path) -> pd.DataFrame:
    """Bereken woonplaats-oppervlakte op basis van de geopackage."""
    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT table_name, column_name, srs_id FROM gpkg_geometry_columns"
        )
        rows = cur.fetchall()
        picked = _pick_layer(rows)
        if not picked:
            return pd.DataFrame(columns=["woonplaats", "area_ha"])
        table_name, geom_col, srs_id = picked
        cur.execute(f"PRAGMA table_info('{table_name}')")
        cols = cur.fetchall()
        name_col = _pick_name_column(cols)
        if not name_col:
            return pd.DataFrame(columns=["woonplaats", "area_ha"])

        transformer = None
        if srs_id and int(srs_id) != 28992:
            try:
                from pyproj import Transformer
            except Exception:
                transformer = None
            else:
                transformer = Transformer.from_crs(
                    f"EPSG:{int(srs_id)}", "EPSG:28992", always_xy=True
                )

        cur.execute(
            f'SELECT "{geom_col}", "{name_col}" FROM "{table_name}"'
        )
        records = []
        for geom_blob, name in cur.fetchall():
            if not name:
                continue
            wkb_blob = _gpkg_geom_to_wkb(geom_blob)
            if not wkb_blob:
                continue
            try:
                geom = wkb.loads(wkb_blob)
            except Exception:
                continue
            if transformer is not None:
                try:
                    geom = shapely_ops.transform(transformer.transform, geom)
                except Exception:
                    continue
            area_ha = float(geom.area) / 10_000.0
            if area_ha > 0:
                records.append((str(name).strip(), area_ha))
        if not records:
            return pd.DataFrame(columns=["woonplaats", "area_ha"])
        df = pd.DataFrame(records, columns=["woonplaats", "area_ha"])
        df["woonplaats"] = df["woonplaats"].astype(str).str.strip()
        df = (
            df.groupby("woonplaats", as_index=False, sort=False)["area_ha"]
            .sum()
            .reset_index(drop=True)
        )
        return df
    finally:
        conn.close()


def build_woonplaats_summary(
    df_points: pd.DataFrame,
    area_df: pd.DataFrame | None = None,
    *,
    woonplaats_col: str = "woonplaats",
) -> pd.DataFrame:
    """Bouw een woonplaats-samenvatting op basis van puntdata."""
    if (
        df_points is None
        or df_points.empty
        or woonplaats_col not in df_points.columns
    ):
        return pd.DataFrame()
    mwh_col = (
        "gemiddeld_jaarverbruik_mWh"
        if "gemiddeld_jaarverbruik_mWh" in df_points.columns
        else "sum_mwh_raw"
        if "sum_mwh_raw" in df_points.columns
        else None
    )
    if not mwh_col:
        return pd.DataFrame()
    base_cols = [woonplaats_col, mwh_col]
    if "aantal_VBOs" in df_points.columns:
        base_cols.append("aantal_VBOs")
    df = df_points.loc[:, base_cols].copy()
    df[woonplaats_col] = df[woonplaats_col].astype(str).str.strip()
    df[mwh_col] = (
        pd.to_numeric(df[mwh_col], errors="coerce").fillna(0.0)
    )
    agg_map: dict[str, tuple[str, str]] = {
        "MWh": (mwh_col, "sum"),
        "aantal_huizen": (woonplaats_col, "size"),
    }
    if "aantal_VBOs" in df.columns:
        df["aantal_VBOs"] = pd.to_numeric(
            df["aantal_VBOs"], errors="coerce"
        ).fillna(0)
        agg_map["aantal_VBOs"] = ("aantal_VBOs", "sum")
    grouped = (
        df.groupby(woonplaats_col, as_index=False, sort=False, observed=True)
        .agg(**agg_map)
        .rename(columns={woonplaats_col: "woonplaats"})
    )
    if (
        area_df is not None
        and not area_df.empty
        and "woonplaats" in area_df.columns
    ):
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
