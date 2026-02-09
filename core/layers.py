"""Opbouw van PyDeck-lagen en GeoJSON-conversie/tooltipdata."""

# core/layers.py
from __future__ import annotations

from typing import List, Dict, Any, Union, TYPE_CHECKING
import math

import pydeck as pdk
import streamlit as st
from pyproj import Transformer

from shapely.geometry import box as shapely_box, mapping as shapely_mapping

from .config import LAYER_CFG, BASEMAP_CFG, WOONPLAATS_GPKG_PATH
from .utils import (
    get_layer_colors,
    get_dynamic_line_width,
    colorize_geojson_cached,
    colorize_numeric_geojson,
    format_dutch_number,
    get_color_palette,
    extract_numeric_values,
    compute_quantile_breaks,
    format_numeric_range_labels,
)

JSONLike = Union[Dict[str, Any], List[Dict[str, Any]]]
Records = List[Dict[str, Any]]

if TYPE_CHECKING:
    import pandas as pd


_RD_TO_WGS84 = Transformer.from_crs("EPSG:28992", "EPSG:4326", always_xy=True)


@st.cache_data(show_spinner=False, max_entries=1, ttl=86400)
def _load_friesland_union() -> object | None:
    """Laad een geometrie-union van alle woonplaatsen (Friesland)."""
    if not WOONPLAATS_GPKG_PATH or not WOONPLAATS_GPKG_PATH.exists():
        return None
    try:
        import geopandas as gpd
    except Exception:
        return None
    try:
        gdf = gpd.read_file(WOONPLAATS_GPKG_PATH)
    except Exception:
        return None
    if gdf.empty:
        return None
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:28992", allow_override=True)
    gdf = gdf.to_crs("EPSG:4326")
    geom = gdf.unary_union
    if geom is None or geom.is_empty:
        return None
    return geom


def _wrap_geojson(geom) -> dict | None:
    if geom is None or getattr(geom, "is_empty", True):
        return None
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {}, "geometry": shapely_mapping(geom)}
        ],
    }


def _load_friesland_geojson() -> dict | None:
    geom = _load_friesland_union()
    return _wrap_geojson(geom)


def _load_friesland_mask_geojson() -> dict | None:
    geom = _load_friesland_union()
    if geom is None:
        return None
    minx, miny, maxx, maxy = geom.bounds
    pad = 0.35
    outer = shapely_box(minx - pad, miny - pad, maxx + pad, maxy + pad)
    mask = outer.difference(geom)
    return _wrap_geojson(mask)


def build_friesland_mask_layer(
    map_style: str | None = None, mode: str = "clip"
) -> pdk.Layer | None:
    """Maak een laag die Friesland clipt of juist Friesland afdekt."""
    if mode not in {"clip", "inverse"}:
        return None
    geojson = (
        _load_friesland_mask_geojson()
        if mode == "clip"
        else _load_friesland_geojson()
    )
    if not geojson:
        return None
    style_val = str(map_style or "").lower()
    is_dark = "dark" in style_val
    fill_color = [14, 17, 22] if is_dark else [245, 245, 245]
    return pdk.Layer(
        "GeoJsonLayer",
        geojson,
        name="Friesland mask",
        pickable=False,
        stroked=False,
        filled=True,
        get_fill_color=fill_color,
        opacity=0.9,
    )


# ------------------------------------------------------------
# Helpers: data normaliseren naar records (list[dict])
# ------------------------------------------------------------
def _to_records(data: Union[Records, "pd.DataFrame"]):
    """Accepteer DataFrame of list[dict] en geef records zonder zware kopieën."""
    if data is None:
        return []
    try:
        import pandas as pd  # lazy

        if isinstance(data, pd.DataFrame):
            if data.empty:
                return []
            # Vermijd to_dict("records") om RAM-pieken te beperken.
            return list(data.itertuples(index=False, name="Record"))
    except Exception:
        pass
    if isinstance(data, list):
        if all(isinstance(x, dict) for x in data):
            return data
        return [dict(x) for x in data]
    return []


def _fmt0(x):
    """Formatteer een getal als Nederlandse integer-string."""
    try:
        return format_dutch_number(int(x), 0)
    except Exception:
        return format_dutch_number(x, 0)


# ------------------------------------------------------------
# GeoJSON filteren op selectie (zoom 11–12)
# ------------------------------------------------------------
def filter_geojson_by_selection(
    gjson: dict, woonplaatsen: list[str] | None, zoom_level: int
):
    """Beperk GeoJSON tot geselecteerde woonplaatsen bij hogere zoomniveaus."""
    if not gjson:
        return gjson
    if zoom_level < 11:
        return gjson
    if not woonplaatsen:
        return gjson
    wp = {str(w).strip().lower() for w in woonplaatsen}
    feats = []
    for f in gjson.get("features", []):
        pr = f.get("properties") or {}
        gm = str(pr.get("gemeentenaam", "")).strip().lower()
        bn = str(pr.get("buurtnaam", "")).strip().lower()
        if gm in wp or bn in wp:
            feats.append(f)
    return {"type": "FeatureCollection", "features": feats}


# ------------------------------------------------------------
# GeoJSON conversie + laag-meta
# ------------------------------------------------------------
def _first_coordinate(coords):
    if isinstance(coords, (list, tuple)):
        if coords and isinstance(coords[0], (int, float)):
            if len(coords) >= 2:
                return coords[0], coords[1]
            return None
        for sub in coords:
            sample = _first_coordinate(sub)
            if sample:
                return sample
    return None


def _needs_rd_to_wgs_conversion(gjson: dict) -> bool:
    if not gjson or gjson.get("type") != "FeatureCollection":
        return False
    for feat in gjson.get("features", []):
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates")
        sample = _first_coordinate(coords)
        if sample:
            x, y = sample
            if abs(x) <= 180 and abs(y) <= 90:
                return False
            if abs(x) > 200 or abs(y) > 200:
                return True
    return False


def _transform_coords_rd_to_wgs(coords):
    if isinstance(coords, (list, tuple)):
        if coords and isinstance(coords[0], (int, float)):
            if len(coords) >= 2:
                lon, lat = _RD_TO_WGS84.transform(coords[0], coords[1])
                return [float(lon), float(lat)]
            return coords
        return [_transform_coords_rd_to_wgs(c) for c in coords]
    return coords


def convert_geojson_to_wgs84_if_needed(gjson: dict) -> dict:
    if not gjson or gjson.get("type") != "FeatureCollection":
        return gjson
    if not _needs_rd_to_wgs_conversion(gjson):
        return gjson
    feats_new = []
    for feat in gjson.get("features", []):
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates")
        new_geom = {
            "type": geom.get("type"),
            "coordinates": _transform_coords_rd_to_wgs(coords),
        }
        feats_new.append(
            {
                "type": "Feature",
                "properties": feat.get("properties"),
                "geometry": new_geom,
            }
        )
    return {"type": "FeatureCollection", "features": feats_new}


def _format_kwh_value(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{format_dutch_number(value, 0)} kWh"


def _format_mwh_value(value: float | None) -> str:
    if value is None:
        return "-"
    try:
        mwh_value = float(value) / 1000.0
        if not math.isfinite(mwh_value):
            return "-"
    except (TypeError, ValueError):
        return "-"
    return f"{format_dutch_number(mwh_value, 0)} MWh"


def _format_percent_value(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{format_dutch_number(value * 100, 1)}%"


def _buurt_extra_rows(props: dict) -> str:
    demand = props.get("Demand_ontvangen_kWh")
    heat = props.get("heatDemand_kWh")
    rows = []
    if demand not in (None, ""):
        try:
            demand_val = float(demand)
        except (TypeError, ValueError):
            demand_val = None
        rows.append(
            f"<div class='tooltip-row'>Ontvangen warmtevraag: {_format_kwh_value(demand_val)}</div>"
        )
    if heat not in (None, ""):
        try:
            heat_val = float(heat)
        except (TypeError, ValueError):
            heat_val = None
        rows.append(
            f"<div class='tooltip-row'>Totale heat demand: {_format_kwh_value(heat_val)}</div>"
        )
    return "".join(rows)


def build_water_potential_meta(gjson: dict) -> dict:
    cfg = LAYER_CFG["water_potentie"]
    n_colors = cfg.get("n_colors", 5)
    colors = get_color_palette(
        cfg.get("palette", "BuGn"), n_colors, cfg.get("alpha", 210)
    )
    values = extract_numeric_values(gjson, cfg["prop_name"])
    breaks = compute_quantile_breaks(values, n_colors)
    if breaks and len(breaks) > n_colors - 1:
        breaks = breaks[: n_colors - 1]
    display_breaks = [b / 1000.0 for b in breaks] if breaks else []
    legend_labels = format_numeric_range_labels(
        display_breaks, suffix=cfg.get("tooltip_unit", "MWh"), decimals=0
    )
    return {
        "breaks": breaks,
        "colors": colors,
        "labels": legend_labels,
        "value_formatter": lambda v: _format_mwh_value(v),
        "extra_rows_fn": None,
        "default_opacity": 0.7,
        "location_row_display": "none",
    }


def build_buurt_potential_meta(gjson: dict) -> dict:
    cfg = LAYER_CFG["buurt_potentie"]
    n_colors = cfg.get("n_colors", 5)
    colors = get_color_palette(
        cfg.get("palette", "YlOrRd"), n_colors, cfg.get("alpha", 210)
    )
    breaks = [i / n_colors for i in range(1, n_colors)]
    legend_labels = format_numeric_range_labels(
        [b * 100 for b in breaks], suffix="%", decimals=0
    )
    return {
        "breaks": breaks,
        "colors": colors,
        "labels": legend_labels,
        "value_formatter": lambda v: _format_percent_value(v),
        "extra_rows_fn": _buurt_extra_rows,
        "default_opacity": 0.7,
        "location_row_display": "block",
    }


def build_warmtenet_meta(gjson: dict | None) -> dict:
    """Maak kleur- en legenda-info voor warmtenet model."""
    base_meta = {
        "color_map": {},
        "labels": {},
        "default_opacity": 0.85,
        "woonplaatsen": [],
        "types": [],
        "type_by_key": {},
        "wp_by_key": {},
    }
    if not gjson or not isinstance(gjson, dict):
        return base_meta

    def _distinct_colors(n: int, alpha: int) -> list[list[int]]:
        # verdeel tinten via golden ratio pm vergelijkbare tinten te voorkomen
        import colorsys

        colors = []
        if n <= 0:
            return colors
        for i in range(n):
            hue = (i * 0.618033988749895) % 1.0
            sat = 0.58
            val = 0.78 - (0.10 * ((i % 3) / 3))  # kleine variatie in helderheid
            r, g, b = colorsys.hsv_to_rgb(hue, sat, val)
            colors.append([int(r * 255), int(g * 255), int(b * 255), alpha])
        return colors

    labels: dict[str, dict[str, str]] = {}
    all_keys: list[str] = []
    woonplaatsen_all: set[str] = set()
    types_all: set[str] = set()
    type_by_key: dict[str, str] = {}
    wp_by_key: dict[str, str] = {}
    for feat in gjson.get("features", []):
        props = feat.get("properties") or {}
        key = str(props.get("bron_key") or "").strip()
        if not key:
            continue
        woonplaats_raw = str(props.get("woonplaats") or "").strip()
        bron_label = str(props.get("bron_id") or "").strip()
        type_bron = str(
            props.get("type_bron") or props.get("gegevensbron") or ""
        ).strip()
        if not bron_label and "__" in key:
            bron_label = key.split("__", 1)[1]
        pretty_label = bron_label or key
        labels.setdefault(
            key,
            {
                "label": pretty_label,
                "woonplaats_norm": woonplaats_raw.lower(),
            },
        )
        all_keys.append(key)
        wp_by_key[key] = woonplaats_raw
        type_by_key[key] = type_bron
        if woonplaats_raw:
            woonplaatsen_all.add(woonplaats_raw)
        if type_bron:
            types_all.add(type_bron)

    unique_keys = sorted(set(all_keys))
    if not unique_keys:
        return base_meta

    cfg = LAYER_CFG.get("warmtenet_model", {})
    alpha = cfg.get("alpha", 230)
    colors = _distinct_colors(len(unique_keys), alpha)
    color_map = {key: colors[idx % len(colors)] for idx, key in enumerate(unique_keys)}

    return {
        "color_map": color_map,
        "labels": labels,
        "default_opacity": 0.85,
        "woonplaatsen": sorted(woonplaatsen_all),
        "types": sorted(types_all),
        "type_by_key": type_by_key,
        "wp_by_key": wp_by_key,
    }


def build_wegennet_meta(gjson: dict | None) -> dict:
    """Maak filter-opties voor wegennetlaag."""
    type_labels = LAYER_CFG.get("wegennet", {}).get("type_labels", {})
    base_meta = {
        "woonplaatsen": [],
        "types": sorted({str(k).lower() for k in type_labels.keys()}),
        "type_labels": type_labels,
        "default_opacity": LAYER_CFG.get("wegennet", {}).get("default_opacity", 0.8),
    }
    if not gjson or not isinstance(gjson, dict):
        return base_meta

    woonplaatsen_all: set[str] = set()
    types_all: set[str] = set()
    for feat in gjson.get("features", []):
        props = feat.get("properties") or {}
        wp = str(props.get("area_name") or "").strip()
        if wp:
            woonplaatsen_all.add(wp)
        t = str(props.get("type") or "").strip().lower()
        if t:
            types_all.add(t)

    base_meta["woonplaatsen"] = sorted(woonplaatsen_all)
    base_meta["types"] = sorted(types_all) if types_all else base_meta["types"]
    return base_meta


# ------------------------------------------------------------
# Basemap
# ------------------------------------------------------------
def build_base_layers(style_key: str, hide_basemap: bool):
    """
    Basemap via TileLayer(s); 'Geen achtergrondkaart'.
    - hide_flag=True -> geen achtergrondlagen
    """
    if hide_basemap:
        return []

    conf = BASEMAP_CFG.get(style_key, {})
    layers_local = []

    if conf.get("tile"):
        tile_kwargs = {
            "data": conf["tile"],
            "min_zoom": 0,
            "max_zoom": 19,
            "tile_size": 256,
        }
        attribution = conf.get("attribution")
        if attribution:
            tile_kwargs["attribution"] = f"'{attribution}'"
        layers_local.append(pdk.Layer("TileLayer", **tile_kwargs))

    if conf.get("labels"):
        label_kwargs = {
            "data": conf["labels"],
            "min_zoom": 0,
            "max_zoom": 19,
            "tile_size": 256,
        }
        label_attrib = conf.get("labels_attribution")
        if label_attrib:
            label_kwargs["attribution"] = f"'{label_attrib}'"
        layers_local.append(pdk.Layer("TileLayer", **label_kwargs))

    return layers_local


# ------------------------------------------------------------
# H3 hoofdlaag + indicatieve laag
# ------------------------------------------------------------
def create_main_layer(
    data_hex_df,
    show: bool,
    extruded: bool,
    zoom_level: int,
    elevation_scale: float,
    layer_opacity: float = 1.0,
):
    """Bouw de primaire H3-laag met kleur en hoogte op basis van energievraag."""
    # verwacht een DataFrame (geen list[dict])
    return pdk.Layer(
        "H3HexagonLayer",
        data_hex_df,
        pickable=True,
        filled=True,
        extruded=extruded,
        coverage=1,
        auto_highlight=False,
        get_hexagon="h3_index",
        get_fill_color="color",
        get_elevation="scaled_elevation",
        elevation_scale=elevation_scale if extruded else 0,
        elevation_range=[0, 800.0],
        get_line_width=get_dynamic_line_width(zoom_level),
        visible=show,
        opacity=float(layer_opacity),
    )


def create_indicative_area_layer(
    data, extruded: bool, zoom_level: int, layer_opacity: float = 1.0
):
    """
    H3 laag voor indicatieve aandachtsgebieden. Verwacht een reeds gefilterde bron
    (DataFrame of list[dict]) met minimaal de kolom h3_index.
    """
    data_src = data
    return pdk.Layer(
        "H3HexagonLayer",
        data_src,
        pickable=True,
        filled=True,
        extruded=extruded,
        get_hexagon="h3_index",
        get_fill_color=[58, 27, 47, 200],
        get_line_color=[0, 0, 0, 0],
        get_line_width=get_dynamic_line_width(zoom_level),
        visible=True,
        opacity=float(layer_opacity),
    )


def create_layers_by_zoom(
    data_hex_df,
    show_main: bool,
    extruded: bool,
    zoom_level: int,
    layer_opacity: float = 1.0,
):
    """Stel de hoofdlaag samen met een passende elevatieschaal per zoomniveau."""
    # Verwacht hier een DataFrame (geen list[dict])
    layers = []
    if zoom_level <= 3:
        layers.append(
            create_main_layer(
                data_hex_df, show_main, extruded, zoom_level, 0.04, layer_opacity
            )
        )
    elif 4 <= zoom_level <= 7:
        layers.append(
            create_main_layer(
                data_hex_df, show_main, extruded, zoom_level, 0.20, layer_opacity
            )
        )
    elif 8 <= zoom_level <= 11:
        layers.append(
            create_main_layer(
                data_hex_df, show_main, extruded, zoom_level, 0.32, layer_opacity
            )
        )
    elif zoom_level == 12:
        layers.append(
            create_main_layer(
                data_hex_df, show_main, extruded, zoom_level, 0.40, layer_opacity
            )
        )
    else:  # zoom_level >= 13
        layers.append(
            create_main_layer(
                data_hex_df, show_main, extruded, zoom_level, 0.48, layer_opacity
            )
        )
    return layers


# ------------------------------------------------------------
# Sites (H3 contour + scatter markers)
# ------------------------------------------------------------
def create_site_layers(
    sites_data: Union[Records, "pd.DataFrame"],
    sites_costed: Union[Records, "pd.DataFrame", None] = None,
    site_hex_opacity: float = 1.0,
):
    """
    Maakt:
      - PolygonLayer (contour + semitransparant vlak) per warmtevoorziening
      - H3HexagonLayer met contrasterende vulling en zwarte omlijning voor de hexagonen
      - ScatterplotLayer markers met alle tooltip-velden (incl. *_fmt)
    """
    site_layers = []
    records = _to_records(sites_data)
    if not records:
        return site_layers

    def _get(rec, key, default=None):
        if isinstance(rec, dict):
            return rec.get(key, default)
        try:
            return getattr(rec, key)
        except Exception:
            return default

    base_fill = [225, 86, 48, 140]
    base_line = [0, 0, 0, 220]

    polygon_records = []
    hexagon_records = []

    for r in records:
        site_rank = int(_get(r, "site_rank") or 0) or 0
        coverage_summary = _get(r, "coverage_summary") or {}
        polygons = _get(r, "coverage_polygons") or []
        hexes = _get(r, "coverage_hexes") or []

        for poly in polygons:
            polygon_records.append(
                {
                    "polygon": poly,
                    "site_rank": site_rank,
                    "fill_color": base_fill,
                    "line_color": base_line,
                    "site_rank_label": coverage_summary.get(
                        "site_rank_label", site_rank
                    ),
                    "woonplaats": _get(r, "woonplaats", ""),
                    "cluster_buildings": _get(r, "cluster_buildings"),
                    "cap_buildings": _get(r, "cap_buildings"),
                    "connected_buildings": _get(r, "connected_buildings"),
                    "cluster_MWh": _get(r, "cluster_MWh"),
                    "cap_MWh": _get(r, "cap_MWh"),
                    "connected_MWh": _get(r, "connected_MWh"),
                    "utilization_pct": _get(r, "utilization_pct"),
                    "cluster_buildings_fmt": _get(r, "cluster_buildings_fmt"),
                    "cap_buildings_fmt": _get(r, "cap_buildings_fmt"),
                    "connected_buildings_fmt": _get(r, "connected_buildings_fmt"),
                    "cluster_MWh_fmt": _get(r, "cluster_MWh_fmt"),
                    "cap_MWh_fmt": _get(r, "cap_MWh_fmt"),
                    "connected_MWh_fmt": _get(r, "connected_MWh_fmt"),
                    "utilization_pct_fmt": _get(r, "utilization_pct_fmt"),
                    # aggregaties voor tooltip
                    "aantal_huizen": coverage_summary.get("aantal_huizen"),
                    "aantal_VBOs": coverage_summary.get("aantal_VBOs"),
                    "gemiddeld_jaarverbruik_mWh_r": coverage_summary.get(
                        "gemiddeld_jaarverbruik_mWh_r"
                    ),
                    "area_ha_r": coverage_summary.get("area_ha_r"),
                    "area_m2": coverage_summary.get("area_m2"),
                    "totale_oppervlakte": coverage_summary.get("totale_oppervlakte"),
                    "area_ha_total": coverage_summary.get("area_ha_total"),
                    "area_ha_total_fmt": coverage_summary.get("area_ha_total_fmt"),
                    "area_m2_total": coverage_summary.get("area_m2_total"),
                    "area_m2_total_fmt": coverage_summary.get("area_m2_total_fmt"),
                    "kWh_per_m2": coverage_summary.get("kWh_per_m2"),
                    "MWh_per_ha_r": coverage_summary.get("MWh_per_ha_r"),
                    "bouwjaar": coverage_summary.get("bouwjaar"),
                    "aantal_huizen_fmt": coverage_summary.get("aantal_huizen_fmt"),
                    "aantal_VBOs_fmt": coverage_summary.get("aantal_VBOs_fmt"),
                    "woningen": coverage_summary.get("woningen"),
                    "bedrijven": coverage_summary.get("bedrijven"),
                    "woningen_fmt": coverage_summary.get("woningen_fmt"),
                    "bedrijven_fmt": coverage_summary.get("bedrijven_fmt"),
                    "gemiddeld_jaarverbruik_mWh_r_fmt": coverage_summary.get(
                        "gemiddeld_jaarverbruik_mWh_r_fmt"
                    ),
                    "area_ha_r_fmt": coverage_summary.get("area_ha_r_fmt"),
                    "area_m2_fmt": coverage_summary.get("area_m2_fmt"),
                    "totale_oppervlakte_fmt": coverage_summary.get(
                        "totale_oppervlakte_fmt"
                    ),
                    "kWh_per_m2_fmt": coverage_summary.get("kWh_per_m2_fmt"),
                    "MWh_per_ha_r_fmt": coverage_summary.get("MWh_per_ha_r_fmt"),
                    "bouwjaar_fmt": coverage_summary.get("bouwjaar_fmt"),
                    "hex_section_display": coverage_summary.get(
                        "hex_section_display", "block"
                    ),
                    "site_section_display": coverage_summary.get(
                        "site_section_display", "block"
                    ),
                    "geo_section_display": coverage_summary.get(
                        "geo_section_display", "none"
                    ),
                }
            )

        for cov in hexes:
            cov_rec = dict(cov)
            cov_rec["site_rank"] = site_rank
            cov_rec["fill_color"] = cov_rec.get("fill_color", base_fill)
            cov_rec["line_color"] = cov_rec.get("line_color", base_line)
            hexagon_records.append(cov_rec)

    if polygon_records:
        site_layers.append(
            pdk.Layer(
                "PolygonLayer",
                polygon_records,
                pickable=True,
                stroked=True,
                filled=False,
                extruded=False,
                wireframe=False,
                get_polygon="polygon",
                get_fill_color=[0, 0, 0, 0],
                get_line_color=[0, 0, 0, 180],
                lineWidthMinPixels=2.5,
                lineWidthMaxPixels=10,
                opacity=1.0,
            )
        )

    if hexagon_records:
        site_layers.append(
            pdk.Layer(
                "H3HexagonLayer",
                hexagon_records,
                pickable=True,
                filled=False,
                stroked=True,
                extruded=False,
                get_hexagon="h3_index",
                get_fill_color="fill_color",
                get_line_color="line_color",
                lineWidthMinPixels=1.2,
                lineWidthMaxPixels=6,
                opacity=float(site_hex_opacity),
            )
        )

    # Scatter markers: gebruik 'costed' records indien aanwezig
    use_records = _to_records(sites_costed) if sites_costed is not None else records

    scatter_records = []
    for r in use_records:
        lon, lat = _get(r, "lon"), _get(r, "lat")
        if lon is None or lat is None:
            continue
        site_rank = int(_get(r, "site_rank") or 0) or 0
        color = base_fill

        coverage_summary = _get(r, "coverage_summary") or {}

        # ruwe waarden
        cluster_buildings = _get(r, "cluster_buildings")
        cap_buildings = _get(r, "cap_buildings")
        connected_buildings = _get(r, "connected_buildings")
        cluster_MWh = _get(r, "cluster_MWh")
        cap_MWh = _get(r, "cap_MWh")
        connected_MWh = _get(r, "connected_MWh")
        utilization_pct = _get(r, "utilization_pct")

        scatter_records.append(
            {
                "lon": lon,
                "lat": lat,
                "woonplaats": _get(r, "woonplaats", ""),
                "site_rank": site_rank,
                # raw
                "cluster_buildings": cluster_buildings,
                "cap_buildings": cap_buildings,
                "connected_buildings": connected_buildings,
                "cluster_MWh": cluster_MWh,
                "cap_MWh": cap_MWh,
                "connected_MWh": connected_MWh,
                "utilization_pct": utilization_pct,
                "area_ha": coverage_summary.get("area_ha_r"),
                "area_ha_fmt": coverage_summary.get("area_ha_r_fmt"),
                "area_m2": coverage_summary.get("area_m2"),
                "area_m2_fmt": coverage_summary.get("area_m2_fmt"),
                "area_ha_total": coverage_summary.get("area_ha_total"),
                "area_ha_total_fmt": coverage_summary.get("area_ha_total_fmt"),
                "area_m2_total": coverage_summary.get("area_m2_total"),
                "area_m2_total_fmt": coverage_summary.get("area_m2_total_fmt"),
                # formatted for tooltip (maak ze indien niet aanwezig)
                "cluster_buildings_fmt": _get(r, "cluster_buildings_fmt")
                or _fmt0(cluster_buildings),
                "cap_buildings_fmt": _get(r, "cap_buildings_fmt")
                or _fmt0(cap_buildings),
                "connected_buildings_fmt": _get(r, "connected_buildings_fmt")
                or _fmt0(connected_buildings),
                "cluster_MWh_fmt": _get(r, "cluster_MWh_fmt") or _fmt0(cluster_MWh),
                "cap_MWh_fmt": _get(r, "cap_MWh_fmt") or _fmt0(cap_MWh),
                "connected_MWh_fmt": _get(r, "connected_MWh_fmt")
                or _fmt0(connected_MWh),
                "utilization_pct_fmt": _get(r, "utilization_pct_fmt")
                or (str(int(utilization_pct)) if utilization_pct is not None else ""),
                # display-velden voor tooltip-secties
                "hex_section_display": coverage_summary.get(
                    "hex_section_display", _get(r, "hex_section_display", "none")
                ),
                "site_section_display": _get(r, "site_section_display", "block"),
                "geo_section_display": _get(r, "geo_section_display", "none"),
                # aggregated hex data
                "aantal_huizen": coverage_summary.get("aantal_huizen"),
                "aantal_VBOs": coverage_summary.get("aantal_VBOs"),
                "gemiddeld_jaarverbruik_mWh_r": coverage_summary.get(
                    "gemiddeld_jaarverbruik_mWh_r"
                ),
                "area_ha_r": coverage_summary.get("area_ha_r"),
                "totale_oppervlakte": coverage_summary.get("totale_oppervlakte"),
                "kWh_per_m2": coverage_summary.get("kWh_per_m2"),
                "MWh_per_ha_r": coverage_summary.get("MWh_per_ha_r"),
                "bouwjaar": coverage_summary.get("bouwjaar"),
                "aantal_huizen_fmt": coverage_summary.get("aantal_huizen_fmt"),
                "aantal_VBOs_fmt": coverage_summary.get("aantal_VBOs_fmt"),
                "gemiddeld_jaarverbruik_mWh_r_fmt": coverage_summary.get(
                    "gemiddeld_jaarverbruik_mWh_r_fmt"
                ),
                "area_ha_r_fmt": coverage_summary.get("area_ha_r_fmt"),
                "totale_oppervlakte_fmt": coverage_summary.get(
                    "totale_oppervlakte_fmt"
                ),
                "kWh_per_m2_fmt": coverage_summary.get("kWh_per_m2_fmt"),
                "MWh_per_ha_r_fmt": coverage_summary.get("MWh_per_ha_r_fmt"),
                "bouwjaar_fmt": coverage_summary.get("bouwjaar_fmt"),
                "site_rank_label": coverage_summary.get("site_rank_label", site_rank),
                "fill_color": color,
            }
        )

    return site_layers


# ------------------------------------------------------------
# Woonlagen (energiearmoede/koop/corporatie)
# ------------------------------------------------------------
def _geojson_layer(data, name, fill_color, line_color, opacity=0.5):
    if data is None:
        return None
    return pdk.Layer(
        "GeoJsonLayer",
        data=data,
        pickable=True,
        stroked=True,
        filled=True,
        extruded=False,
        get_fill_color=fill_color,
        get_line_color=line_color,
        get_line_width=1,
        lineWidthMinPixels=1,
        opacity=float(opacity),
    )


def create_extra_layers(
    geojson_dict: dict,
    woonplaats_selectie: list[str],
    zoom_level: int,
    extra_opacity: float = 0.4,
    potential_meta: dict | None = None,
):
    """
    Woonlagen:
    - filteren op zoom+woonplaats
    - kleurtoekenning (cached)
    - labels/props voor tooltip meegeven
    """
    layers = []
    cfg = LAYER_CFG
    potential_meta = potential_meta or {}

    # Energiearmoede
    if st.session_state.get(cfg["energiearmoede"]["toggle_key"]):
        c = cfg["energiearmoede"]
        colors = get_layer_colors(c)
        gjson_src = filter_geojson_by_selection(
            geojson_dict.get("energiearmoede"), woonplaats_selectie, zoom_level
        )
        gjson_colored = colorize_geojson_cached(
            gjson_src,
            c["prop_name"],
            c["out_prop"],
            c["breaks"],
            colors,
            layer_label=c["legend_title"],
        )
        lyr = _geojson_layer(
            gjson_colored,
            "energiearmoede",
            fill_color=f"properties.{c['out_prop']}",
            line_color=c["line_color"],
            opacity=st.session_state.get("extra_opacity", extra_opacity),
        )
        if lyr:
            layers.append(lyr)

    # Koopwoningen
    if st.session_state.get(cfg["koopwoningen"]["toggle_key"]):
        c = cfg["koopwoningen"]
        colors = get_layer_colors(c)
        gjson_src = filter_geojson_by_selection(
            geojson_dict.get("koopwoningen"), woonplaats_selectie, zoom_level
        )
        gjson_colored = colorize_geojson_cached(
            gjson_src,
            c["prop_name"],
            c["out_prop"],
            c["breaks"],
            colors,
            layer_label=c["legend_title"],
        )
        lyr = _geojson_layer(
            gjson_colored,
            "koopwoningen",
            fill_color=f"properties.{c['out_prop']}",
            line_color=c["line_color"],
            opacity=st.session_state.get("extra_opacity", extra_opacity),
        )
        if lyr:
            layers.append(lyr)

    # Wooncorporatie
    if st.session_state.get(cfg["wooncorporatie"]["toggle_key"]):
        c = cfg["wooncorporatie"]
        colors = get_layer_colors(c)
        gjson_src = filter_geojson_by_selection(
            geojson_dict.get("corporatie"), woonplaats_selectie, zoom_level
        )
        gjson_colored = colorize_geojson_cached(
            gjson_src,
            c["prop_name"],
            c["out_prop"],
            c["breaks"],
            colors,
            layer_label=c["legend_title"],
        )
        lyr = _geojson_layer(
            gjson_colored,
            "wooncorporatie",
            fill_color=f"properties.{c['out_prop']}",
            line_color=c["line_color"],
            opacity=st.session_state.get("extra_opacity", extra_opacity),
        )
        if lyr:
            layers.append(lyr)

    # Waterpotentie
    if st.session_state.get(cfg["water_potentie"]["toggle_key"]):
        meta = potential_meta.get("water_potentie")
        gjson_src = geojson_dict.get("water_potentie")
        if meta and gjson_src and meta.get("breaks"):
            colored = colorize_numeric_geojson(
                gjson_src,
                cfg["water_potentie"]["prop_name"],
                cfg["water_potentie"]["out_prop"],
                meta["breaks"],
                meta["colors"],
                cfg["water_potentie"]["legend_title"],
                meta["value_formatter"],
                meta.get("extra_rows_fn"),
                meta.get("location_row_display", "block"),
            )
            lyr = _geojson_layer(
                colored,
                "water_potentie",
                fill_color=f"properties.{cfg['water_potentie']['out_prop']}",
                line_color=cfg["water_potentie"].get("line_color", [255, 255, 255, 60]),
                opacity=st.session_state.get(
                    "water_potentie_opacity", meta.get("default_opacity", 0.7)
                ),
            )
            if lyr:
                layers.append(lyr)

    # Buurtpotentie
    if st.session_state.get(cfg["buurt_potentie"]["toggle_key"]):
        meta = potential_meta.get("buurt_potentie")
        gjson_src = filter_geojson_by_selection(
            geojson_dict.get("buurt_potentie"),
            woonplaats_selectie,
            zoom_level,
        )
        if meta and gjson_src and meta.get("breaks"):
            colored = colorize_numeric_geojson(
                gjson_src,
                cfg["buurt_potentie"]["prop_name"],
                cfg["buurt_potentie"]["out_prop"],
                meta["breaks"],
                meta["colors"],
                cfg["buurt_potentie"]["legend_title"],
                meta["value_formatter"],
                meta.get("extra_rows_fn"),
                meta.get("location_row_display", "block"),
            )
            lyr = _geojson_layer(
                colored,
                "buurt_potentie",
                fill_color=f"properties.{cfg['buurt_potentie']['out_prop']}",
                line_color=cfg["wooncorporatie"].get("line_color", [0, 0, 0, 120]),
                opacity=st.session_state.get(
                    "buurt_potentie_opacity", meta.get("default_opacity", 0.7)
                ),
            )
            if lyr:
                layers.append(lyr)

    return layers


# ------------------------------------------------------------
# Wegennet (vraagkant)
# ------------------------------------------------------------
def create_wegennet_layers(
    gjson: dict | None,
    woonplaatsen: list[str],
    allowed_types: list[str] | None = None,
    opacity: float = 0.8,
    zoom_level: int | None = None,
):
    """Bouw wegennetlaag met filters op woonplaats en type."""
    if not gjson or not isinstance(gjson, dict):
        return []

    cfg = LAYER_CFG.get("wegennet", {})
    min_zoom = int(cfg.get("min_zoom", 11))
    if zoom_level is not None and zoom_level < min_zoom:
        return []
    type_colors = cfg.get("type_colors", {})
    type_labels = cfg.get("type_labels", {})
    default_color = cfg.get("default_color", [120, 120, 120, 200])
    layer_label = cfg.get("legend_title", "Wegennet")
    line_width = float(cfg.get("line_width", 2.0))

    allowed_types_set = (
        {str(t).strip().lower() for t in allowed_types} if allowed_types else None
    )
    wp_filter = {str(w).strip().lower() for w in woonplaatsen} if woonplaatsen else None

    feats: list[dict] = []

    for feat in gjson.get("features", []):
        if not isinstance(feat, dict):
            continue
        props = dict(feat.get("properties") or {})
        woonplaats = str(
            props.get("area_name") or props.get("woonplaats") or ""
        ).strip()
        if wp_filter and woonplaats.lower() not in wp_filter:
            continue
        type_raw = str(props.get("type") or "").strip().lower()
        if allowed_types_set is not None and type_raw not in allowed_types_set:
            continue

        type_label = type_labels.get(type_raw, type_raw)
        length_val = props.get("length_m")
        try:
            length_num = float(length_val)
        except (TypeError, ValueError):
            length_num = None
        if length_num is None or math.isnan(length_num) or math.isinf(length_num):
            length_display = "-"
        else:
            length_display = format_dutch_number(length_num, 0)

        rows = []
        if type_label:
            rows.append(f"<div class='tooltip-row'>Type: {type_label}</div>")
        if woonplaats:
            rows.append(f"<div class='tooltip-row'>Woonplaats: {woonplaats}</div>")
        rows.append(
            f"<div class='tooltip-row'>Lengte wegdeel (m): {length_display}</div>"
        )

        props["woonplaats"] = woonplaats
        props["_layer_label"] = layer_label
        props["geo_extra_rows"] = "".join(rows)
        props["gemeentenaam"] = ""
        props["buurtnaam"] = ""
        props["gemeente_row_display"] = "none"
        props["buurt_row_display"] = "none"
        props["geo_section_display"] = "block"
        props["hex_section_display"] = "none"
        props["site_section_display"] = "none"
        props["line_color"] = type_colors.get(type_raw, default_color)

        geom = feat.get("geometry")
        feats.append({"type": "Feature", "properties": props, "geometry": geom})

    if not feats:
        return []

    layer = pdk.Layer(
        "GeoJsonLayer",
        data={"type": "FeatureCollection", "features": feats},
        pickable=True,
        stroked=True,
        filled=False,
        get_line_color="properties.line_color",
        get_line_width=line_width,
        lineWidthMinPixels=1,
        opacity=float(opacity),
    )
    return [layer]


# ------------------------------------------------------------
# Warmtenet model (bronnen + leidingen)
# ------------------------------------------------------------
def _warmtenet_extra_rows(props: dict) -> str:
    """Stel tooltip-rijen samen voor de warmtenetlaag."""

    def _to_float(val):
        try:
            num = float(val)
        except (TypeError, ValueError):
            return None
        if math.isnan(num) or math.isinf(num):
            return None
        return num

    def _fmt(val, decimals: int = 1):
        num = _to_float(val)
        if num is None:
            return None
        return format_dutch_number(num, decimals)

    def _add_row(
        label: str, value, *, decimals: int = 1, suffix: str = "", prefix: str = ""
    ):
        fmt_val = _fmt(value, decimals=decimals)
        suffix_txt = f" {suffix}" if suffix else ""
        display = f"{prefix}{fmt_val}{suffix_txt}" if fmt_val is not None else "-"
        rows.append(f"<div class='tooltip-row'>{label}: {display}</div>")

    def _add_currency(label: str, value):
        fmt_val = _fmt(value, decimals=0)
        display = f"€ {fmt_val}" if fmt_val is not None else "-"
        rows.append(f"<div class='tooltip-row'>{label}: {display}</div>")

    rows = []
    layer_raw = str(props.get("layer") or "").strip().lower()
    geom_type = (
        str(props.get("_geometry_type") or "").strip().lower()
    )  # optional helper
    layer_type = layer_raw
    if layer_type not in {"bron", "object", "leiding"}:
        if geom_type == "linestring":
            layer_type = "leiding"
        elif (
            props.get("bron_mwh_per_jaar") is not None
            or props.get("ingezet_mwh_per_jaar") is not None
        ):
            layer_type = "bron"
        elif props.get("vraag_mwh_per_jaar") is not None:
            layer_type = "object"
    layer_label = {"bron": "Bron", "object": "Object", "leiding": "Leiding"}.get(
        layer_type
    )
    woonplaats = props.get("woonplaats")
    bron_id = props.get("bron_id") or props.get("bron_key")
    gegevensbron = props.get("type_bron") or props.get("gegevensbron")

    if layer_label:
        rows.append(f"<div class='tooltip-row'>Type: {layer_label}</div>")
    if woonplaats:
        rows.append(f"<div class='tooltip-row'>Woonplaats: {woonplaats}</div>")
    if bron_id:
        rows.append(f"<div class='tooltip-row'>Bron: {bron_id}</div>")
    if gegevensbron:
        rows.append(f"<div class='tooltip-row'>Gegevensbron: {gegevensbron}</div>")

    if layer_type == "bron":
        _add_row(
            "Beschikbare warmte (MWh/jaar)", props.get("bron_mwh_per_jaar"), decimals=0
        )
        _add_row(
            "Ingezette warmte (MWh/jaar)", props.get("ingezet_mwh_per_jaar"), decimals=0
        )
        _add_row(
            "Benutting percentage", props.get("benutting_pct"), decimals=1, suffix="%"
        )
        _add_row("Aangesloten objecten", props.get("aangesloten_objecten"), decimals=0)
        _add_currency("Kosten bron", props.get("kosten_bron_euro"))
        _add_currency("Kosten aansluitingen", props.get("kosten_aansluitingen_euro"))
    elif layer_type == "object":
        _add_row("Warmtevraag (MWh/jaar)", props.get("vraag_mwh_per_jaar"), decimals=1)
        _add_currency("Kosten aansluiting", props.get("kosten_aansluiting_euro"))
        _add_row("Afstand object tot bron (m)", props.get("afstand_pad_m"), decimals=0)
    else:
        _add_row("Warmtevraag (MWh/jaar)", props.get("vraag_mwh_per_jaar"), decimals=1)

    return "".join(rows)


def _prepare_warmtenet_props(
    props: dict, *, color: list[int], layer_label: str
) -> dict:
    """Verrijk properties voor tooltip en kleurgebruik."""
    prepared = dict(props or {})
    prepared["color"] = color
    prepared["_layer_label"] = layer_label
    prepared["gemeente_row_display"] = "none"
    prepared["buurt_row_display"] = "none"
    prepared["geo_section_display"] = "block"
    prepared["hex_section_display"] = "none"
    prepared["site_section_display"] = "none"
    prepared["geo_extra_rows"] = _warmtenet_extra_rows(prepared)
    return prepared


def create_warmtenet_layers(
    gjson: dict | None,
    woonplaatsen: list[str],
    color_map: dict[str, list[int]],
    allowed_keys: list[str] | None = None,
    type_by_key: dict[str, str] | None = None,
    allowed_types: list[str] | None = None,
    opacity: float = 0.85,
    show_lines: bool = True,
    show_sources: bool = True,
    show_objects: bool = True,
):
    """
    Bouw lagen voor warmtenet-model:
    - GeoJsonLayer voor leidingen (LineString)
    - ScatterplotLayer voor bron-/object-punten
    """
    if not gjson or not isinstance(gjson, dict):
        return []
    if not (show_lines or show_sources or show_objects):
        return []

    def _line_hash(geom: dict) -> tuple:
        """Maak een hashbare representatie van een LineString (coördinaten afgerond)."""
        coords = geom.get("coordinates") or []
        hashed = []
        for pt in coords:
            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                try:
                    hashed.append((round(float(pt[0]), 6), round(float(pt[1]), 6)))
                except Exception:
                    continue
        return tuple(hashed)

    allowed = {str(k).strip() for k in allowed_keys} if allowed_keys else None
    allowed_types_set = (
        {str(t).strip().lower() for t in allowed_types} if allowed_types else None
    )
    wp_filter = {str(w).strip().lower() for w in woonplaatsen} if woonplaatsen else None
    layer_label = LAYER_CFG.get("warmtenet_model", {}).get(
        "legend_title", "Warmtebronnen (model)"
    )
    default_color = [120, 120, 120, 220]

    line_feats = []
    point_records = []
    filtered_feats: list[tuple[dict, dict, dict]] = []

    for feat in gjson.get("features", []):
        if not isinstance(feat, dict):
            continue
        props = feat.get("properties") or {}
        wp = str(props.get("woonplaats") or "").strip().lower()
        if wp_filter and wp not in wp_filter:
            continue
        bron_key = str(props.get("bron_key") or "").strip()
        if allowed and bron_key not in allowed:
            continue
        if allowed_types_set:
            tb = (type_by_key or {}).get(bron_key, "")
            if str(tb).strip().lower() not in allowed_types_set:
                continue
        color = color_map.get(bron_key, default_color)
        prepared_props = _prepare_warmtenet_props(
            props, color=color, layer_label=layer_label
        )

        geom = feat.get("geometry") or {}
        geom_type = geom.get("type")
        layer_type = str(prepared_props.get("layer") or "").strip().lower()
        if layer_type not in {"bron", "object", "leiding"}:
            if geom_type == "LineString":
                layer_type = "leiding"
            elif (
                props.get("bron_mwh_per_jaar") is not None
                or props.get("ingezet_mwh_per_jaar") is not None
            ):
                layer_type = "bron"
            elif props.get("vraag_mwh_per_jaar") is not None:
                layer_type = "object"
        if geom_type == "LineString" and not show_lines:
            continue
        if geom_type == "Point":
            if layer_type == "bron" and not show_sources:
                continue
            if layer_type == "object" and not show_objects:
                continue
        prepared_props["_layer_type"] = layer_type
        filtered_feats.append((prepared_props, geom, feat))

    # Bepaal overlap-telling voor leidingen (zelfde traject -> dikkere lijn)
    line_counts: dict[tuple, int] = {}
    for prepared_props, geom, _ in filtered_feats:
        if geom.get("type") != "LineString":
            continue
        key = _line_hash(geom)
        line_counts[key] = line_counts.get(key, 0) + 1

    for prepared_props, geom, _ in filtered_feats:
        geom_type = geom.get("type")
        layer_type = (
            str(prepared_props.get("_layer_type") or prepared_props.get("layer") or "")
            .strip()
            .lower()
        )
        prepared_props["_geometry_type"] = str(geom_type or "").strip()
        point_radius = 12 if layer_type == "bron" else 6
        point_line_width = (
            3.0 if layer_type == "bron" else 2.2
        )  # dikkere rand voor zichtbaarheid
        base_color = prepared_props.get("color", default_color)
        if layer_type == "object":
            fill_color = [255, 255, 255, 235]  # wit binnenvlak
            line_color = base_color  # gekleurde rand
        else:
            fill_color = base_color
            line_color = [25, 25, 25, 210]  # donkere rand voor contrast
        if geom_type == "Point":
            coords = geom.get("coordinates") or [None, None]
            record = {
                "position": coords,
                "point_radius": point_radius,
                "point_line_width": point_line_width,
                "fill_color": fill_color,
                "line_color": line_color,
                **prepared_props,
            }
            point_records.append(record)
        else:
            key = _line_hash(geom)
            overlap = line_counts.get(key, 1)
            # dikker bij overlap, met max om extreme breedte te voorkomen
            width = min(2.0 + (overlap - 1) * 1.4, 8.0)
            props_with_width = dict(prepared_props)
            props_with_width["line_overlap"] = overlap
            props_with_width["line_width"] = width
            line_feats.append(
                {
                    "type": "Feature",
                    "properties": props_with_width,
                    "geometry": geom,
                }
            )

    layers = []
    if line_feats:
        layers.append(
            pdk.Layer(
                "GeoJsonLayer",
                data={"type": "FeatureCollection", "features": line_feats},
                pickable=True,
                stroked=True,
                filled=False,
                get_line_color=[0, 0, 0, 220],
                get_line_width="properties.line_width",
                lineWidthMinPixels=2,
                opacity=float(opacity),
            )
        )

    if point_records:
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                point_records,
                pickable=True,
                get_position="position",
                get_fill_color="fill_color",
                get_line_color="line_color",
                get_line_width="point_line_width",
                get_radius="point_radius",
                radius_units="pixels",
                radius_min_pixels=4,
                radius_max_pixels=18,
                stroked=True,
                opacity=float(opacity),
            )
        )

    return layers
