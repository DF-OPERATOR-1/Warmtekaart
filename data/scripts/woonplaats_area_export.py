"""Exporteer woonplaats-oppervlakte (ha) uit de geopackage naar CSV/Parquet."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Iterable

import pandas as pd
from shapely import wkb
from shapely import ops as shapely_ops

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import WOONPLAATS_GPKG_PATH, WOONPLAATS_AREA_PATH


def _gpkg_geom_to_wkb(blob: bytes | memoryview | None) -> bytes | None:
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
    for table_name, col_name, srs_id in rows:
        if str(table_name).strip().lower() == "woonplaats":
            return table_name, col_name, srs_id
    return next(iter(rows), None)


def _pick_name_column(cols: list[tuple]) -> str | None:
    col_names = [c[1] for c in cols]
    for candidate in ("woonplaats", "naam", "name", "wpl_naam", "plaatsnaam"):
        if candidate in col_names:
            return candidate
    for _, name, col_type, *_ in cols:
        if isinstance(col_type, str) and "text" in col_type.lower():
            return name
    return None


def _build_area_df(gpkg_path: Path) -> pd.DataFrame:
    conn = sqlite3.connect(gpkg_path)
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Exporteer woonplaats-oppervlakte (ha) uit een geopackage."
    )
    parser.add_argument(
        "--input",
        default=str(WOONPLAATS_GPKG_PATH),
        help="Pad naar de GPKG met woonplaatsgeometrie.",
    )
    parser.add_argument(
        "--output",
        default=str(WOONPLAATS_AREA_PATH),
        help="Uitvoerpad (csv of parquet).",
    )
    args = parser.parse_args()

    gpkg_path = Path(args.input)
    if not gpkg_path.exists():
        print(f"Bestand niet gevonden: {gpkg_path}")
        return 1

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    df = _build_area_df(gpkg_path)
    if df.empty:
        print("Geen records gevonden.")
        return 1

    if out_path.suffix.lower() == ".parquet":
        df.to_parquet(out_path, index=False)
    else:
        df.to_csv(out_path, index=False)

    print(f"Opgeslagen: {out_path} ({len(df)} woonplaatsen)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
