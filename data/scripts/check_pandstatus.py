"""
Check hoeveel rijen overblijven na filter op pandstatus (interactive).
"""

from pathlib import Path
from typing import Iterable
import pandas as pd

# -----------------------------
# 1) Instellingen (pas dit aan)
# -----------------------------
PARQUET_PATH = Path("/Users/anguyen/Documents/GitHub/Warmtekaart/data/data.parquet")

STATUS_VALUE = [
    "Pand in gebruik",
    "Verbouwing pand",
]  # welke pandstatus wil je behouden
TOP_N = 10  # top N statuswaarden tonen


# -----------------------------
# 2) Helpers
# -----------------------------
def select_parquet_engine() -> str:
    try:
        import pyarrow  # noqa: F401

        return "pyarrow"
    except Exception:
        try:
            import fastparquet  # noqa: F401

            return "fastparquet"
        except Exception as exc:
            raise RuntimeError(
                "Geen parquet-engine gevonden. Installeer pyarrow of fastparquet.\n"
                "Bijv: pip install pyarrow"
            ) from exc


def load_dataframe(src_path: Path) -> pd.DataFrame:
    if not src_path.exists():
        raise FileNotFoundError(f"Bronbestand niet gevonden: {src_path.resolve()}")

    engine = select_parquet_engine()
    return pd.read_parquet(src_path, engine=engine)


def summarize_pandstatus(
    df: pd.DataFrame,
    status_value: list[str] | str = "Pand in gebruik",
    top_n: int = 10,
) -> pd.DataFrame:
    """
    Print een korte samenvatting en returnt ook de value_counts als DataFrame.
    """
    if "pandstatus" not in df.columns:
        print("Kolom 'pandstatus' ontbreekt in de dataset.")
        return pd.DataFrame()

    total_rows = len(df)
    status_counts = df["pandstatus"].value_counts(dropna=False)

    if isinstance(status_value, str):
        status_list = [status_value]
    else:
        status_list = list(status_value)

    in_use = int(status_counts[status_counts.index.isin(status_list)].sum())
    removed = total_rows - in_use
    pct_removed = (removed / total_rows) * 100 if total_rows else 0.0

    print(f"Totaal rijen: {total_rows}")
    print(f"Rijen met pandstatus in {status_list}: {in_use}")
    print(f"Rijen die wegvallen bij filter: {removed}")
    print(f"Percentage weg: {pct_removed:.2f}%")
    print()
    print(f"Top {top_n} pandstatus waarden:")
    print(status_counts.head(top_n).to_string())

    return status_counts.rename_axis("pandstatus").to_frame("count")


def filter_on_pandstatus(
    df: pd.DataFrame, status_value: Iterable[str] | str
) -> pd.DataFrame:
    if "pandstatus" not in df.columns:
        raise ValueError("Kolom 'pandstatus' ontbreekt in de dataset.")

    if isinstance(status_value, str):
        status_list = [status_value]
    else:
        status_list = list(status_value)

    return df[df["pandstatus"].isin(status_list)].copy()


# -----------------------------
# 3) Run
# -----------------------------
df = load_dataframe(PARQUET_PATH)
counts_df = summarize_pandstatus(df, status_value=STATUS_VALUE, top_n=TOP_N)

# Gefilterde dataset maken
df_filtered = filter_on_pandstatus(df, STATUS_VALUE)

len(df_filtered), df_filtered.head()
