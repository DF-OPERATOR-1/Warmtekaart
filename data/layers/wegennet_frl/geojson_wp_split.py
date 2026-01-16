import os
import re
import geopandas as gpd
INPUT = "/vsigzip//Users/anguyen/Documents/GitHub/Warmtekaart/data/layers/wegennet_frl.geojson.gz"

gdf = gpd.read_file(INPUT, engine="pyogrio")
OUTDIR = "wegennet_frl"
FIELD = "area_name"

os.makedirs(OUTDIR, exist_ok=True)

def safe_filename(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^a-z0-9_=-]+", "", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "unknown"

print(f"Reading: {INPUT}")

# Probeer eerst direct lezen (meestal werkt dit)
try:
    gdf = gpd.read_file(INPUT, engine="pyogrio")
except Exception:
    # Fallback zonder pyogrio
    gdf = gpd.read_file(INPUT)

if FIELD not in gdf.columns:
    raise ValueError(f"Field '{FIELD}' not found. Available columns: {list(gdf.columns)}")

# Lege/NULL area_name eruit
gdf = gdf[gdf[FIELD].notna()]
gdf = gdf[gdf[FIELD] != ""]

n_unique = int(gdf[FIELD].nunique())
print(f"Rows: {len(gdf):,} | Unique {FIELD}: {n_unique:,}")

for area_name, subset in gdf.groupby(FIELD, sort=True):
    out_path = os.path.join(OUTDIR, f"{safe_filename(str(area_name))}.parquet")

    print(f"Writing {out_path}  (features: {len(subset):,})")

    subset.to_parquet(
        out_path,
        compression="zstd",
        index=False
    )

print("Done.")
