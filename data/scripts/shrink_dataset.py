"""
Hulpscript om een compacte parquet-versie te maken voor de Streamlit-app.

Het script houdt alleen de kolommen die de UI en analyses gebruiken, zet deze
om naar compactere types en slaat het resultaat op naast de originele data.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

import pandas as pd


def _resolve_root() -> Path:
    here = Path(__file__).resolve()
    if here.parent.name == "scripts" and here.parent.parent.name == "data":
        return here.parents[2]
    return here.parents[1]


ROOT = _resolve_root()
DEFAULT_PARQUET_PATH = ROOT / "data" / "safe file" / "data.parquet"
DEFAULT_COMPACT_PATH = ROOT / "data" / "data_compact.parquet"

COLUMNS_KEEP: Sequence[str] = [
    "aantal_VBOs",
    "totale_oppervlakte",
    "woonplaats",
    "gemeentenaam",
    "Energieklasse",
    "latitude",
    "longitude",
    "bouwjaar",
    "pandstatus",
    "kWh_per_m2",
    "gemiddeld_jaarverbruik",
    "gemiddeld_jaarverbruik_mWh",
    "afname_betekenis",
    "opwek_betekenis",
    "Dataset",
]

FLOAT32_COLS = {
    "latitude",
    "longitude",
    "kWh_per_m2",
    "gemiddeld_jaarverbruik",
    "gemiddeld_jaarverbruik_mWh",
}

INT32_COLS = {
    "aantal_VBOs",
    "totale_oppervlakte",
    "bouwjaar",
}

CATEGORY_COLS = {
    "woonplaats",
    "gemeentenaam",
    "Energieklasse",
    "afname_betekenis",
    "opwek_betekenis",
    "Dataset",
}


def _select_parquet_engine() -> str:
    try:
        import pyarrow  # noqa: F401

        return "pyarrow"
    except Exception:
        try:
            import fastparquet  # noqa: F401

            return "fastparquet"
        except Exception as exc:
            raise RuntimeError(
                "Geen parquet-engine gevonden. Installeer pyarrow of fastparquet."
            ) from exc


def _load_dataframe(src_path: Path, columns_keep: Sequence[str]) -> pd.DataFrame:
    if not src_path.exists():
        raise FileNotFoundError(f"Bronbestand niet gevonden: {src_path}")

    suffix = src_path.suffix.lower()
    if suffix not in {".parquet", ".pq"}:
        raise ValueError(
            f"Niet-ondersteund inputtype: {src_path.suffix}. Gebruik een parquet-bestand."
        )

    engine = _select_parquet_engine()
    try:
        return pd.read_parquet(src_path, columns=list(columns_keep), engine=engine)
    except Exception:
        df = pd.read_parquet(src_path, engine=engine)
        keep_cols = [col for col in columns_keep if col in df.columns]
        missing = [col for col in columns_keep if col not in df.columns]
        if missing:
            print(f"Waarschuwing: ontbrekende kolommen: {missing}")
        return df[keep_cols]


def _resolve_paths(source_arg: str | None, dest_arg: str | None) -> tuple[Path, Path]:
    if source_arg:
        src_path = Path(source_arg)
    else:
        src_path = DEFAULT_PARQUET_PATH

    if dest_arg:
        dest_path = Path(dest_arg)
    else:
        dest_path = DEFAULT_COMPACT_PATH

    return src_path, dest_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Maak een compacte parquet-versie op basis van parquet."
        )
    )
    parser.add_argument(
        "--source",
        help=(
            "Pad naar input parquet. Standaard: data/safe file/data.parquet."
        ),
    )
    parser.add_argument(
        "--dest",
        help=(
            "Pad naar output parquet. Standaard: data/data_compact.parquet."
        ),
    )
    args = parser.parse_args()

    src_path, dest_path = _resolve_paths(args.source, args.dest)
    df = _load_dataframe(src_path, COLUMNS_KEEP)

    for col in FLOAT32_COLS & set(df.columns):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float32")

    for col in INT32_COLS & set(df.columns):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int32")

    for col in CATEGORY_COLS & set(df.columns):
        df[col] = df[col].astype("category")

    if "pandstatus" in df.columns:
        df = df[df["pandstatus"] == "Pand in gebruik"].reset_index(drop=True)

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    engine = _select_parquet_engine()
    df.to_parquet(dest_path, engine=engine, compression="snappy", index=False)

    orig_size = src_path.stat().st_size / (1024 * 1024)
    new_size = dest_path.stat().st_size / (1024 * 1024)
    print(f"Originele dataset: {orig_size:.1f} MB")
    print(f"Nieuwe dataset: {new_size:.1f} MB")
    print(f"Kolommen behouden: {list(df.columns)}")


if __name__ == "__main__":
    main()
