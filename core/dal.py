"""DuckDB data-access laag (DAL) voor filters en aggregaties op data.parquet."""

# core/dal.py
from __future__ import annotations

from typing import Any

import duckdb
import h3
import pandas as pd
import streamlit as st

from .config import DATA_CSV_PATH, BASE_H3_RES


def _data_path_str() -> str:
    return str(DATA_CSV_PATH)


@st.cache_resource
def get_con() -> duckdb.DuckDBPyConnection:
    """Maak en cache één DuckDB-verbinding per proces."""
    con = duckdb.connect(database=":memory:")
    con.execute("PRAGMA threads=1")
    path = _data_path_str().replace("'", "''")
    con.execute(f"CREATE OR REPLACE VIEW base AS SELECT * FROM read_parquet('{path}')")

    try:
        con.create_function(
            "h3_latlng_to_cell",
            lambda lat, lon, res: h3.latlng_to_cell(float(lat), float(lon), int(res)),
            ["DOUBLE", "DOUBLE", "INTEGER"],
            "VARCHAR",
        )
    except Exception:
        pass

    try:
        con.create_function(
            "h3_cell_to_parent",
            lambda cell, res: h3.cell_to_parent(str(cell), int(res)),
            ["VARCHAR", "INTEGER"],
            "VARCHAR",
        )
    except Exception:
        pass

    try:
        con.create_function(
            "h3_cell_area_ha",
            lambda cell: float(h3.cell_area(str(cell), unit="km^2")) * 100.0,
            ["VARCHAR"],
            "DOUBLE",
        )
    except Exception:
        pass

    return con


def _normalize_list(values: Any) -> list[str]:
    if not values:
        return []
    if isinstance(values, (list, tuple, set)):
        seq = values
    else:
        seq = [values]
    out = []
    for val in seq:
        if val is None:
            continue
        text = str(val).strip()
        if text:
            out.append(text)
    return out


def _placeholders(n: int) -> str:
    return ", ".join(["?"] * n)


def _build_where(
    filters: dict,
    *,
    use_gemeente: bool = True,
    use_woonplaats: bool = True,
    use_energieklasse: bool = True,
    use_bouwjaar: bool = True,
    use_dataset: bool = True,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if use_gemeente:
        gemeenten = _normalize_list(filters.get("gemeente"))
        if gemeenten:
            clauses.append(f"gemeentenaam IN ({_placeholders(len(gemeenten))})")
            params.extend(gemeenten)

    if use_woonplaats:
        woonplaatsen = _normalize_list(filters.get("woonplaats"))
        if woonplaatsen:
            clauses.append(f"woonplaats IN ({_placeholders(len(woonplaatsen))})")
            params.extend(woonplaatsen)

    if use_energieklasse:
        energie = _normalize_list(filters.get("energieklasse"))
        if energie:
            clauses.append(
                f"COALESCE(Energieklasse, 'Onbekend') IN ({_placeholders(len(energie))})"
            )
            params.extend(energie)

    if use_bouwjaar:
        bouwjaar = filters.get("bouwjaar_range") or filters.get("bouwjaar")
        if isinstance(bouwjaar, (list, tuple)) and len(bouwjaar) == 2:
            clauses.append("bouwjaar BETWEEN ? AND ?")
            params.extend([int(bouwjaar[0]), int(bouwjaar[1])])

    if use_dataset:
        dataset = str(filters.get("pand_selectie") or "").strip()
        if dataset and dataset != "Klein-, middel- en grootverbruik":
            clauses.append('"Dataset" = ?')
            params.append(dataset)

    if clauses:
        return " WHERE " + " AND ".join(clauses), params
    return "", params


@st.cache_data(show_spinner=False, max_entries=64, ttl=900)
def dal_query(filters: dict, mode: str) -> pd.DataFrame:
    """DAL-functie voor alle queries op data.parquet."""
    con = get_con()

    if mode == "options_gemeente":
        sql = (
            "SELECT DISTINCT gemeentenaam "
            "FROM base "
            "WHERE gemeentenaam IS NOT NULL "
            "ORDER BY gemeentenaam"
        )
        return con.execute(sql).df()

    if mode == "options_woonplaats":
        where_sql, params = _build_where(
            filters,
            use_gemeente=True,
            use_woonplaats=False,
            use_energieklasse=False,
            use_bouwjaar=False,
            use_dataset=False,
        )
        if where_sql:
            sql = (
                "SELECT DISTINCT woonplaats "
                "FROM base "
                f"{where_sql} "
                "AND woonplaats IS NOT NULL "
                "ORDER BY woonplaats"
            )
        else:
            sql = (
                "SELECT DISTINCT woonplaats "
                "FROM base "
                "WHERE woonplaats IS NOT NULL "
                "ORDER BY woonplaats"
            )
        return con.execute(sql, params).df()

    if mode == "options_energieklasse":
        where_sql, params = _build_where(
            filters,
            use_gemeente=True,
            use_woonplaats=True,
            use_energieklasse=False,
            use_bouwjaar=False,
            use_dataset=False,
        )
        sql = (
            "SELECT DISTINCT COALESCE(Energieklasse, 'Onbekend') AS Energieklasse "
            "FROM base "
            f"{where_sql} "
            "ORDER BY Energieklasse"
        )
        return con.execute(sql, params).df()

    if mode == "bouwjaar_range":
        where_sql, params = _build_where(
            filters,
            use_gemeente=True,
            use_woonplaats=True,
            use_energieklasse=True,
            use_bouwjaar=False,
            use_dataset=False,
        )
        sql = (
            "SELECT MIN(bouwjaar) AS min_year, MAX(bouwjaar) AS max_year "
            "FROM base "
            f"{where_sql}"
        )
        return con.execute(sql, params).df()

    if mode == "dataset_options":
        sql = (
            'SELECT DISTINCT "Dataset" AS Dataset '
            "FROM base "
            'WHERE "Dataset" IS NOT NULL '
            "ORDER BY Dataset"
        )
        return con.execute(sql).df()

    if mode == "map_hex":
        res = int(filters.get("resolution") or BASE_H3_RES)
        where_sql, params = _build_where(filters)
        geo_where = (
            f"{where_sql} AND latitude IS NOT NULL AND longitude IS NOT NULL"
            if where_sql
            else " WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
        )
        sql = f"""
            SELECT
                h3_cell_to_parent(h3_latlng_to_cell(latitude, longitude, {BASE_H3_RES}), {res}) AS h3_index,
                MIN(woonplaats) AS woonplaats,
                SUM(gemiddeld_jaarverbruik_mWh) AS sum_mwh_raw,
                ROUND(SUM(gemiddeld_jaarverbruik_mWh), 0) AS gemiddeld_jaarverbruik_mWh,
                ROUND(SUM(totale_oppervlakte), 0) AS totale_oppervlakte,
                CAST(ROUND(AVG(bouwjaar), 0) AS INTEGER) AS bouwjaar,
                CAST(ROUND(SUM(COALESCE(aantal_VBOs, 0)), 0) AS INTEGER) AS aantal_VBOs,
                CAST(COUNT(*) AS INTEGER) AS aantal_huizen,
                ROUND(
                    SUM(COALESCE(kWh_per_m2, 0))
                    / NULLIF(COUNT(*), 0),
                    0
                ) AS kWh_per_m2
            FROM base
            {geo_where}
            GROUP BY h3_index
        """
        return con.execute(sql, params).df()

    if mode == "pandtype_counts_by_hex":
        res = int(filters.get("resolution") or BASE_H3_RES)
        where_sql, params = _build_where(filters)
        geo_where = (
            f"{where_sql} AND latitude IS NOT NULL AND longitude IS NOT NULL"
            if where_sql
            else " WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
        )
        sql = f"""
            SELECT
                h3_cell_to_parent(h3_latlng_to_cell(latitude, longitude, {BASE_H3_RES}), {res}) AS h3_index,
                SUM(CASE WHEN LOWER(COALESCE("Dataset", '')) = 'kleinverbruik' THEN 1 ELSE 0 END) AS woningen,
                SUM(CASE WHEN LOWER(COALESCE("Dataset", '')) IN (
                    'middel- en grootverbruik alliander en tno',
                    'middel- en grootverbruik tno'
                ) THEN 1 ELSE 0 END) AS bedrijven
            FROM base
            {geo_where}
            GROUP BY h3_index
        """
        return con.execute(sql, params).df()

    if mode == "pandtype_counts_by_woonplaats":
        where_sql, params = _build_where(filters)
        sql = f"""
            SELECT
                woonplaats,
                SUM(CASE WHEN LOWER(COALESCE("Dataset", '')) = 'kleinverbruik' THEN 1 ELSE 0 END) AS woningen,
                SUM(CASE WHEN LOWER(COALESCE("Dataset", '')) IN (
                    'middel- en grootverbruik alliander en tno',
                    'middel- en grootverbruik tno'
                ) THEN 1 ELSE 0 END) AS bedrijven
            FROM base
            {where_sql}
            GROUP BY woonplaats
        """
        return con.execute(sql, params).df()

    if mode == "pandtype_mwh_by_hex":
        where_sql, params = _build_where(filters)
        geo_where = (
            f"{where_sql} AND latitude IS NOT NULL AND longitude IS NOT NULL"
            if where_sql
            else " WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
        )
        sql = f"""
            SELECT
                h3_latlng_to_cell(latitude, longitude, {BASE_H3_RES}) AS h3_index,
                MIN(woonplaats) AS woonplaats,
                SUM(gemiddeld_jaarverbruik_mWh) AS MWh,
                SUM(CASE WHEN LOWER(COALESCE("Dataset", '')) = 'kleinverbruik' THEN 1 ELSE 0 END) AS woningen,
                SUM(CASE WHEN LOWER(COALESCE("Dataset", '')) IN (
                    'middel- en grootverbruik alliander en tno',
                    'middel- en grootverbruik tno'
                ) THEN 1 ELSE 0 END) AS bedrijven
            FROM base
            {geo_where}
            GROUP BY h3_index
        """
        return con.execute(sql, params).df()

    if mode == "pandtype_mwh_by_woonplaats":
        where_sql, params = _build_where(filters)
        geo_where = (
            f"{where_sql} AND latitude IS NOT NULL AND longitude IS NOT NULL"
            if where_sql
            else " WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
        )
        sql = f"""
            WITH per_hex AS (
                SELECT
                    h3_latlng_to_cell(latitude, longitude, {BASE_H3_RES}) AS h3_index,
                    MIN(woonplaats) AS woonplaats,
                    SUM(gemiddeld_jaarverbruik_mWh) AS MWh,
                    SUM(CASE WHEN LOWER(COALESCE("Dataset", '')) = 'kleinverbruik' THEN 1 ELSE 0 END) AS woningen,
                    SUM(CASE WHEN LOWER(COALESCE("Dataset", '')) IN (
                        'middel- en grootverbruik alliander en tno',
                        'middel- en grootverbruik tno'
                    ) THEN 1 ELSE 0 END) AS bedrijven
                FROM base
                {geo_where}
                GROUP BY h3_index
            ),
            typed AS (
                SELECT
                    woonplaats,
                    CASE
                        WHEN woningen > 0 AND bedrijven > 0 THEN 'C'
                        WHEN woningen > 0 THEN 'A'
                        WHEN bedrijven > 0 THEN 'B'
                        ELSE ''
                    END AS type_code,
                    MWh,
                    CASE
                        WHEN woningen > 0 AND bedrijven > 0 THEN woningen + bedrijven
                        WHEN woningen > 0 THEN woningen
                        WHEN bedrijven > 0 THEN bedrijven
                        ELSE 0
                    END AS panden_count,
                    h3_cell_area_ha(h3_index) AS area_ha
                FROM per_hex
                WHERE woonplaats IS NOT NULL AND TRIM(woonplaats) != ''
            )
            SELECT
                woonplaats,
                type_code,
                SUM(MWh) AS MWh,
                SUM(panden_count) AS aantal_panden,
                SUM(area_ha) AS area_ha
            FROM typed
            WHERE type_code != ''
            GROUP BY woonplaats, type_code
        """
        return con.execute(sql, params).df()

    if mode == "woonplaats_summary":
        where_sql, params = _build_where(filters)
        sql = f"""
            SELECT
                woonplaats,
                SUM(gemiddeld_jaarverbruik_mWh) AS MWh,
                COUNT(*) AS aantal_huizen,
                SUM(COALESCE(aantal_VBOs, 0)) AS aantal_VBOs
            FROM base
            {where_sql}
            GROUP BY woonplaats
        """
        return con.execute(sql, params).df()

    if mode == "view_bounds":
        where_sql, params = _build_where(filters)
        geo_where = (
            f"{where_sql} AND latitude IS NOT NULL AND longitude IS NOT NULL"
            if where_sql
            else " WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
        )
        sql = f"""
            SELECT
                AVG(latitude) AS lat_mean,
                AVG(longitude) AS lon_mean,
                MIN(latitude) AS lat_min,
                MAX(latitude) AS lat_max,
                MIN(longitude) AS lon_min,
                MAX(longitude) AS lon_max
            FROM base
            {geo_where}
        """
        return con.execute(sql, params).df()

    raise ValueError(f"Unknown DAL mode: {mode}")
