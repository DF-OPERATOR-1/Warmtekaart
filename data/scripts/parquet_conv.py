#%%
import pandas as pd
# Read the CSV file
df = pd.read_csv('/Users/anitavn/Documents/Warmtekaart_test/streamlit/Untitled/data/data.csv', 
                 low_memory=False)

# Als MWh/jaar bestaat: hernoem naar gemiddeld_jaarverbruik_mWh
if "MWh/jaar" in df.columns and "gemiddeld_jaarverbruik_mWh" not in df.columns:
    df = df.rename(columns={"MWh/jaar": "gemiddeld_jaarverbruik_mWh"})

# kWh_per_m2 opnieuw berekenen
df["kWh_per_m2"] = (
    (df["gemiddeld_jaarverbruik"] / df["totale_oppervlakte"])
    .where(df["gemiddeld_jaarverbruik"].notna() & df["totale_oppervlakte"].notna())
    .round()
    .astype("Int64")
)

# Convert to Parquet
df.to_parquet(
    "/Users/anitavn/Documents/Warmtekaart_test/streamlit/Untitled/data/safe file/data.parquet",
    engine="pyarrow",
    index=False,
)
# %%
