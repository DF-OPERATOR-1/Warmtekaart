from __future__ import annotations

import argparse
import re
from pathlib import Path

import geopandas as gpd

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INPUT = ROOT / "data" / "layers" / "wegennet_frl.geojson.gz"
DEFAULT_FIELD = "area_name"


def safe_filename(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"[^a-z0-9_=-]+", "", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "unknown"


def _strip_geojson_suffix(name: str) -> str:
    for suffix in (".geojson.gz", ".json.gz", ".geojson", ".json"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return Path(name).stem


def _read_geo(path: Path) -> gpd.GeoDataFrame:
    candidates = [path]
    if path.suffix.lower() == ".gz":
        candidates.append(f"/vsigzip/{path}")
    for candidate in candidates:
        try:
            return gpd.read_file(candidate, engine="pyogrio")
        except Exception:
            pass
    for candidate in candidates:
        try:
            return gpd.read_file(candidate)
        except Exception:
            pass
    raise RuntimeError(f"Kon bestand niet lezen: {path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Split wegennet per woonplaats (area_name) naar parquet."
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT),
        help="Pad naar wegennet .geojson/.geojson.gz.",
    )
    parser.add_argument(
        "--outdir",
        default="",
        help="Output map (standaard naast input).",
    )
    parser.add_argument(
        "--field",
        default=DEFAULT_FIELD,
        help="Kolom met woonplaatsnaam (default: area_name).",
    )
    args = parser.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input niet gevonden: {input_path}")

    if args.outdir:
        out_dir = Path(args.outdir).expanduser().resolve()
    else:
        base_name = _strip_geojson_suffix(input_path.name)
        out_dir = input_path.parent / base_name
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Reading: {input_path}")
    gdf = _read_geo(input_path)

    if args.field not in gdf.columns:
        raise ValueError(
            f"Field '{args.field}' not found. Available: {list(gdf.columns)}"
        )

    gdf = gdf[gdf[args.field].notna()]
    gdf = gdf[gdf[args.field] != ""]

    n_unique = int(gdf[args.field].nunique())
    print(f"Rows: {len(gdf):,} | Unique {args.field}: {n_unique:,}")

    for area_name, subset in gdf.groupby(args.field, sort=True):
        out_path = out_dir / f"{safe_filename(str(area_name))}.parquet"
        print(f"Writing {out_path}  (features: {len(subset):,})")
        subset.to_parquet(out_path, compression="zstd", index=False)

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
