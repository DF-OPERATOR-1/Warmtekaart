# %% Install packages
import pandas as pd
from shapely.wkt import loads
from pyproj import Transformer

# %% Load CSV
df = pd.read_csv(
    r"/Users/anguyen/Documents/GitHub/Warmtekaart/data/safe file/data_wkt.csv",
    sep=",",
    low_memory=False,
)

# %% Define projections (EPSG:28992 is RD New, EPSG:4326 is WGS 84)
transformer = Transformer.from_crs("EPSG:28992", "EPSG:4326", always_xy=True)


# %% Functie met het inlezen van point
def extract_and_convert_lat_lon(wkt):
    try:
        if not isinstance(wkt, str) or wkt.strip() == "":
            return None, None  # Sla lege of niet-string waardes over

        geom = loads(wkt)
        if geom.geom_type == "Point":
            x, y = geom.coords[0]
        elif geom.geom_type in [
            "Polygon",
            "MultiPolygon",
            "LineString",
            "MultiLineString",
        ]:
            x, y = list(geom.centroid.coords)[0]  # Gebruik het centrum van de geometrie
        else:
            print(f"Niet-ondersteund geometrie type: {geom.geom_type}")
            return None, None

        lon, lat = transformer.transform(x, y)
        return lat, lon
    except Exception as e:
        print(f"Fout bij verwerken van WKT: {wkt} - {e}")
        return None, None


# Apply the conversion function to the 'geometry' column in the DataFrame
df["latitude"], df["longitude"] = zip(*df["WKT"].apply(extract_and_convert_lat_lon))

# %% Save the updated CSV with latitude and longitude
df.to_csv("data.csv", sep=",", index=False)
# %%
