# core/io.py
import json
import gzip
from pathlib import Path
import re
import unicodedata
import math

import orjson
import pandas as pd
import streamlit as st

# pandas copy-on-write voorkomt verborgen kopieën bij bewerkingen
pd.set_option("mode.copy_on_write", True)

from .config import (
    LAYER_CFG,
    DATA_CSV_PATH,
    ENERGIEARMOEDE_PATH,
    KOOPWONINGEN_PATH,
    WOONCORPORATIE_PATH,
    GJ_COMMON_PROPS,
    WEGENNET_PATH,
    WEGENNET_SUMMARY_PATH,
)


# ============================================================
# GeoJSON loader (met property-filter & coördinaat-precisie)
# ============================================================
def _resolve_layer_path(path: Path) -> Path | None:
    if path.exists():
        return path
    candidate = None
    if path.suffix == ".gz" and path.name.endswith(".geojson.gz"):
        candidate = path.with_suffix("")
        candidate = candidate.with_suffix(".parquet")
    elif path.suffix in {".geojson", ".json"}:
        candidate = path.with_suffix(".parquet")
    if candidate and candidate.exists():
        return candidate
    return None


@st.cache_data(show_spinner=False, max_entries=8, ttl=86400)
def load_geojson(path: str | Path, keep_props=None, coord_precision: int = 3, ttl=3600):
    """
    Laadt een GeoJSON- of Parquet-bestand als dict.
    - keep_props: lijst met property-namen die je wilt behouden (alles daarbuiten wordt gestript)
    - coord_precision: aantal decimalen voor coördinaten (reductie van bestandsgrootte)
    """
    if not path:
        return None
    p = _resolve_layer_path(Path(path))
    if not p:
        return None

    if p.suffix == ".parquet":
        try:
            from shapely import geometry as _shapely_geom
            from shapely import wkb as _shapely_wkb
        except Exception as exc:
            raise RuntimeError(
                "shapely is vereist voor het lezen van parquet-lagen."
            ) from exc
        df = pd.read_parquet(p)
        if df.empty:
            return {"type": "FeatureCollection", "features": []}
        geom_col = "geometry" if "geometry" in df.columns else None
        if not geom_col:
            return None
        kp = set(keep_props or [])
        factor = 10**coord_precision

        def _round_coords(obj):
            if isinstance(obj, (list, tuple)):
                return [_round_coords(x) for x in obj]
            if isinstance(obj, float):
                return int(obj * factor) / factor
            return obj

        cols = list(df.columns)
        geom_idx = cols.index(geom_col)
        prop_indices = [
            idx
            for idx, name in enumerate(cols)
            if name != geom_col and (not kp or name in kp)
        ]

        def _coerce_prop(value):
            if value is None:
                return None
            if isinstance(value, pd.Timestamp):
                return value.isoformat()
            if isinstance(value, (float, int)):
                if isinstance(value, float) and not math.isfinite(value):
                    return None
                return value
            if isinstance(value, bytes):
                return value.decode("utf-8", errors="ignore")
            if hasattr(value, "item"):
                try:
                    value = value.item()
                except Exception:
                    return value
                if isinstance(value, float) and not math.isfinite(value):
                    return None
            return value

        feats = []
        for row in df.itertuples(index=False, name=None):
            geom_val = row[geom_idx]
            if geom_val is None:
                geom = None
            else:
                if isinstance(geom_val, memoryview):
                    geom_val = geom_val.tobytes()
                if isinstance(geom_val, (bytes, bytearray)):
                    geom_obj = _shapely_wkb.loads(geom_val)
                elif hasattr(geom_val, "geom_type"):
                    geom_obj = geom_val
                else:
                    geom_obj = None
                if geom_obj is None:
                    geom = None
                else:
                    geom = _shapely_geom.mapping(geom_obj)
                    coords = geom.get("coordinates")
                    if coords is not None:
                        geom["coordinates"] = _round_coords(coords)
            props = {}
            for idx in prop_indices:
                key = cols[idx]
                props[key] = _coerce_prop(row[idx])
            feats.append({"type": "Feature", "properties": props, "geometry": geom})
        return {"type": "FeatureCollection", "features": feats}
    if p.suffix == ".gz":
        with gzip.open(p, "rb") as fh:
            raw = fh.read()
    else:
        raw = p.read_bytes()

    try:
        gj = orjson.loads(raw)
    except Exception:
        gj = json.loads(raw.decode("utf-8"))

    if not (gj and isinstance(gj, dict) and gj.get("type") == "FeatureCollection"):
        return gj

    feats = []
    kp = set(keep_props or [])
    factor = 10**coord_precision

    def _round_coords(obj):
        if isinstance(obj, (list, tuple)):
            return [_round_coords(x) for x in obj]
        if isinstance(obj, float):
            return int(obj * factor) / factor
        return obj

    for feat in gj.get("features", []):
        geom = feat.get("geometry")
        props = feat.get("properties", {}) or {}
        if kp:
            props = {k: props.get(k) for k in kp if k in props}
        if geom and geom.get("coordinates") is not None:
            geom = {
                "type": geom.get("type"),
                "coordinates": _round_coords(geom.get("coordinates")),
            }
        feats.append({"type": "Feature", "properties": props, "geometry": geom})

    return {"type": "FeatureCollection", "features": feats}


def _slugify_name(value: str) -> str:
    """Maak een bestandsveilige slug op basis van een naam."""
    try:
        text = unicodedata.normalize("NFKD", value)
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
    except Exception:
        text = value
    text = re.sub(r"[^a-zA-Z0-9]+", "_", str(text)).strip("_").lower()
    return text or "onbekend"


def normalize_wegennet_name(value: str | None) -> str:
    """Normaliseer een woonplaatsnaam naar een bestandsveilige sleutel."""
    return _slugify_name(str(value or ""))


def _wegennet_dir(base_path: Path | None = None) -> Path | None:
    base = base_path or WEGENNET_PATH
    if base.is_dir():
        return base
    candidates = [
        base.parent / "wegennet_frl",
        base.parent / "wegennet",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate
    return None


def _wegennet_base_name(path: Path) -> str:
    name = path.name
    for suffix in (".geojson.gz", ".json.gz", ".geojson", ".json", ".parquet"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


def list_wegennet_woonplaatsen(base_path: Path | None = None) -> list[str]:
    base_dir = _wegennet_dir(base_path)
    if not base_dir:
        return []
    names: set[str] = set()
    for path in base_dir.iterdir():
        if not path.is_file():
            continue
        if path.suffix == ".parquet" or path.name.endswith(
            (".geojson.gz", ".json.gz", ".geojson", ".json")
        ):
            base_name = _wegennet_base_name(path)
            if base_name:
                names.add(base_name)
    return sorted(names)


def resolve_wegennet_paths(
    woonplaatsen: list[str] | None, base_path: Path | None = None
) -> list[Path]:
    if not woonplaatsen:
        return []
    base_dir = _wegennet_dir(base_path)
    if not base_dir:
        return []

    available: dict[str, tuple[int, Path]] = {}

    def _add(path: Path, priority: int) -> None:
        key = normalize_wegennet_name(_wegennet_base_name(path))
        if not key:
            return
        if key not in available or priority < available[key][0]:
            available[key] = (priority, path)

    for path in base_dir.iterdir():
        if not path.is_file():
            continue
        name_lower = path.name.lower()
        if path.suffix == ".parquet":
            _add(path, 0)
        elif name_lower.endswith((".geojson.gz", ".json.gz")):
            _add(path, 1)
        elif name_lower.endswith((".geojson", ".json")):
            _add(path, 2)

    resolved: list[Path] = []
    for woonplaats in woonplaatsen:
        key = normalize_wegennet_name(woonplaats)
        entry = available.get(key)
        if entry:
            resolved.append(entry[1])
    return resolved


def resolve_wegennet_path(gemeente: str | None, base_path: Path | None = None) -> Path:
    """Kies een gemeente-specifiek wegennetbestand als dat bestaat."""
    base = base_path or WEGENNET_PATH
    if not gemeente:
        return base
    name_raw = str(gemeente).strip()
    if not name_raw:
        return base
    suffix = "".join(base.suffixes) or base.suffix or ".geojson.gz"
    base_dir = base.parent
    name_simple = re.sub(r"\s+", "_", name_raw.strip().lower())
    variants = []
    for val in (name_simple, _slugify_name(name_raw)):
        if val and val not in variants:
            variants.append(val)
    candidates = []
    for val in variants:
        candidates.append(base_dir / f"wegennet_{val}{suffix}")
        candidates.append(base_dir / "wegennet" / f"{val}{suffix}")
        candidates.append(base_dir / f"wegennet_{val}.parquet")
        candidates.append(base_dir / "wegennet" / f"{val}.parquet")
        candidates.append(base_dir / "wegennet_frl" / f"{val}.parquet")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return base


@st.cache_data(show_spinner=False, max_entries=16, ttl=3600)
def geojson_unique_props(path: str | Path, prop_name: str) -> list[str]:
    """Lees unieke property-waarden uit een GeoJSON (zonder extra bewerkingen)."""
    if not path:
        return []
    p = _resolve_layer_path(Path(path))
    if not p:
        return []

    if p.suffix == ".parquet":
        try:
            df = pd.read_parquet(p, columns=[prop_name])
        except Exception:
            return []
        if prop_name not in df.columns:
            return []
        values = set()
        for val in df[prop_name]:
            if val is None or (isinstance(val, float) and not math.isfinite(val)):
                continue
            txt = str(val).strip()
            if txt:
                values.add(txt)
        return sorted(values)

    if p.suffix == ".gz":
        with gzip.open(p, "rb") as fh:
            raw = fh.read()
    else:
        raw = p.read_bytes()

    try:
        gj = orjson.loads(raw)
    except Exception:
        gj = json.loads(raw.decode("utf-8"))

    if not (gj and isinstance(gj, dict) and gj.get("type") == "FeatureCollection"):
        return []

    values = set()
    for feat in gj.get("features", []) or []:
        props = feat.get("properties") or {}
        val = props.get(prop_name)
        if val is None:
            continue
        txt = str(val).strip()
        if txt:
            values.add(txt)

    return sorted(values)


# ============================================================
# Wegennet samenvatting (CSV)
# ============================================================
@st.cache_data(show_spinner=False, max_entries=2, ttl=3600)
def load_wegennet_summary(path: str | Path | None = None) -> pd.DataFrame:
    """Laad samenvatting van het wegennet per woonplaats uit CSV."""
    p = Path(path or WEGENNET_SUMMARY_PATH)
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(p)
    except Exception:
        return pd.DataFrame()


# ============================================================
# Data loader (RAM-geoptimaliseerd)
# ============================================================
@st.cache_data(show_spinner=False, max_entries=1)
def load_data(src: str | Path | None = None, ttl=3600) -> pd.DataFrame:
    """
    Laadt CSV/Parquet:
      - Parquet eerst (sneller + zuiniger)
      - CSV met pyarrow-engine (RAM vriendelijk)
      - Converteert kolommen naar compacte types (float32/int32/category)
    """
    # ---------- Bron bepalen ----------
    if src is None or (isinstance(src, (str, Path)) and str(src).strip() == ""):
        local_parquet = DATA_CSV_PATH.with_suffix(".parquet")
        if local_parquet.exists():
            src = local_parquet
        elif DATA_CSV_PATH.exists():
            src = DATA_CSV_PATH
        else:
            raise FileNotFoundError(
                "Geen lokaal data-bestand gevonden voor DATA_CSV_PATH."
            )

    read_target = Path(src)

    # ---------- Kolommen & dtypes ----------
    usecols = [
        "aantal_VBOs",
        "totale_oppervlakte",
        "woonplaats",
        "Energieklasse",
        "latitude",
        "longitude",
        "bouwjaar",
        "pandstatus",
        "kWh_per_m2",
        "gemiddeld_jaarverbruik",
        "Dataset",
        "gemiddeld_jaarverbruik_mWh",
        "gemeentenaam",
        "afname_betekenis",
        "opwek_betekenis",
    ]

    csv_dtypes = {
        "aantal_VBOs": "Int32",
        "totale_oppervlakte": "Int32",
        "bouwjaar": "Int32",
        "gemiddeld_jaarverbruik": "float32",
        "gemiddeld_jaarverbruik_mWh": "float32",
        "kWh_per_m2": "float32",
        "latitude": "float32",
        "longitude": "float32",
    }

    # ---------- Inlezen ----------
    target_str = str(read_target).lower()
    if target_str.endswith(".parquet"):
        df = pd.read_parquet(read_target, columns=usecols, engine="pyarrow")
    else:
        try:
            df = pd.read_csv(
                read_target,
                engine="pyarrow",
                dtype=csv_dtypes,
                usecols=usecols,
            )
        except Exception:
            df = pd.read_csv(read_target, low_memory=False, usecols=usecols)

    # ---------- Numerieke types afdwingen ----------
    for c in [
        "latitude",
        "longitude",
        "kWh_per_m2",
        "gemiddeld_jaarverbruik_mWh",
        "gemiddeld_jaarverbruik",
    ]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("float32")

    if "aantal_VBOs" in df.columns:
        df["aantal_VBOs"] = pd.to_numeric(df["aantal_VBOs"], errors="coerce").astype(
            "Int16"
        )
    if "totale_oppervlakte" in df.columns:
        df["totale_oppervlakte"] = pd.to_numeric(
            df["totale_oppervlakte"], errors="coerce"
        ).astype("Int32")
    if "bouwjaar" in df.columns:
        df["bouwjaar"] = pd.to_numeric(df["bouwjaar"], errors="coerce").astype("Int16")

    # ---------- RAM reductie ----------
    # Strings -> categories (strenger: pas bij veel herhaling)
    for c in df.select_dtypes(include=["object"]).columns:
        try:
            nunique = df[c].nunique(dropna=True)
            if nunique and nunique <= 10000:
                if nunique <= 0.35 * len(df):  # strenger dan default
                    df[c] = df[c].astype("category")
        except Exception:
            pass

    if "pandstatus" in df.columns:
        df["pandstatus"] = df["pandstatus"].astype("category")

    # ---------- Extra safety ----------
    for c in ["lat", "lon"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("float32")

    drop_optional = [
        c for c in ["afname_betekenis", "opwek_betekenis"] if c in df.columns
    ]
    if drop_optional:
        df.drop(columns=drop_optional, inplace=True)

    return df


# ============================================================
# Convenience: alle themalagen vooraf laden
# ============================================================
@st.cache_data(show_spinner=False, max_entries=2, ttl=86400)
def preload_geo_layers(ttl=3600):
    """
    Laadt alle geojson-lagen zoals in de monolith gedaan werd, met identieke keep_props.
    Retourneert dict met keys:
      - energiearmoede, koopwoningen, corporatie
    """
    gj_energiearmoede = load_geojson(
        ENERGIEARMOEDE_PATH,
        keep_props=[LAYER_CFG["energiearmoede"]["prop_name"], *GJ_COMMON_PROPS],
        coord_precision=3,
    )
    gj_koopwoningen = load_geojson(
        KOOPWONINGEN_PATH,
        keep_props=[LAYER_CFG["koopwoningen"]["prop_name"], *GJ_COMMON_PROPS],
        coord_precision=3,
    )
    gj_corporatie = load_geojson(
        WOONCORPORATIE_PATH,
        keep_props=[LAYER_CFG["wooncorporatie"]["prop_name"], *GJ_COMMON_PROPS],
        coord_precision=3,
    )

    return {
        "energiearmoede": gj_energiearmoede,
        "koopwoningen": gj_koopwoningen,
        "corporatie": gj_corporatie,
    }
