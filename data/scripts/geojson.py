# %%
import geopandas as gpd

# 1. Laad GeoJSON
gdf = gpd.read_file("")

# 2. Zet CRS als het niet in het bestand staat
gdf = gdf.set_crs("EPSG:28992")

# 3. Converteer naar WGS84
gdf_4326 = gdf.to_crs("EPSG:4326")

# 4. Opslaan
gdf_4326.to_file("warmtenet_full.geojson", driver="GeoJSON")

# %%
import gzip
import shutil

input_file = (
    "/Users/anguyen/Documents/GitHub/Warmtekaart/data/layers/wegennet_friesland.geojson"
)
output_file = "wegennet_frl.geojson.gz"

with open(input_file, "rb") as f_in:
    with gzip.open(output_file, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)

# %% geojson.gz to parquet
import geopandas as gpd

INPUT = (
    "/Users/anguyen/Documents/GitHub/Warmtekaart/data/layers/warmtenet_full.geojson.gz"
)
OUTPUT = "warmtenet_full.parquet"

# Gebruik GDAL gzip virtual filesystem
INPUT_VSI = f"/vsigzip/{INPUT}"

print("Reading GeoJSON.gz...")
gdf = gpd.read_file(INPUT_VSI, engine="pyogrio")

print("Writing GeoParquet...")
gdf.to_parquet(OUTPUT, compression="zstd", index=False)

print("Done.")

# %% Geopackage converter
import geopandas as gpd

# Paden
gpkg_path = "/Users/anguyen/Documents/GitHub/Warmtekaart/data/layers/wegennet_friesland.gpkg"
out_path = "/Users/anguyen/Documents/GitHub/Warmtekaart/data/layers/wegennet_friesland.geojson"

# Lees de enige laag in de geopackage
gdf = gpd.read_file(gpkg_path)

# Bekijk eerst welke kolommen beschikbaar zijn
print(gdf.columns)

gdf_4326 = gdf.to_crs("EPSG:4326")

# Opslaan
gdf_4326.to_file(out_path, driver="GeoJSON")

print("GeoJSON aangemaakt:", out_path)
# %%
