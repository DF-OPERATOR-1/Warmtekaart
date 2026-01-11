# app.py
from __future__ import annotations

# ========== Imports ==========
import gc
from pathlib import Path
import sys

import h3
import pandas as pd
import pydeck as pdk
import streamlit as st

# Ensure the project root is first on sys.path for local package imports.
_ROOT_DIR = Path(__file__).resolve().parent
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))
_core_mod = sys.modules.get("core")
if _core_mod is not None:
    _core_file = getattr(_core_mod, "__file__", None)
    _core_paths = getattr(_core_mod, "__path__", None)
    _core_is_local = False
    if _core_file:
        _core_is_local = str(Path(_core_file).resolve()).startswith(str(_ROOT_DIR))
    elif _core_paths:
        _core_is_local = any(
            str(Path(p).resolve()).startswith(str(_ROOT_DIR)) for p in _core_paths
        )
    if not _core_is_local:
        sys.modules.pop("core", None)

# ---- interne modules ----
from core.config import (
    LAYER_CFG,
    ENERGIEARMOEDE_PATH,
    KOOPWONINGEN_PATH,
    WOONCORPORATIE_PATH,
    WATER_POTENTIE_PATH,
    BUURT_POTENTIE_PATH,
    WARMTENET_PATH,
)
from core.utils import format_dutch_number, get_heat_color, build_deck_tooltip
from core.layers import (
    build_base_layers,
    create_layers_by_zoom,
    create_indicative_area_layer,
    create_site_layers,
    create_extra_layers,
    create_warmtenet_layers,
    create_wegennet_layers,
    build_water_potential_meta,
    build_buurt_potential_meta,
    build_warmtenet_meta,
    build_wegennet_meta,
    convert_geojson_to_wgs84_if_needed,
)
from core.h3sites import (
    shortlist_centers,
    filters_fingerprint,
    compute_clusters_cached,
    select_sites_from_clusters,
)
from core.map_data import (
    build_map_dataframe,
    build_site_records,
    extract_selected_hex_from_payload,
)
from core.io import load_geojson, load_data, resolve_wegennet_path
from core.report import build_report_pdf, prepare_report_image_bytes
from ui.sidebar import build_sidebar
from ui.kpis_and_tables import render_kpis, render_tabs

# Flow: load data -> build sidebar -> compute aggregates -> render layers.


# (optioneel) live RAM-meting in sidebar
# try:
#    import psutil, os
#    mem = psutil.Process(os.getpid()).memory_info().rss / 1e6
#    st.sidebar.write(f"RAM-gebruik: {mem:.1f} MB")
# except Exception:
#    pass


# TODO_RAMDEBUG: verwijder deze helper zodra RAM-diagnose is afgerond.
def _log_ram(label: str) -> None:
    """Log het huidige RAM-gebruik voor snelle diagnosestappen."""
    try:
        import os
        import psutil
    except Exception:
        return
    try:
        mem_mb = psutil.Process(os.getpid()).memory_info().rss / 1e6
        print(f"[RAM_DEBUG] {label}: {mem_mb:.1f} MB")
    except Exception:
        pass


# ========== Eerste init (NIET cache leegmaken) ==========
# Initialiseer een Streamlit-sessie éénmalig per gebruiker
if "app_initialized" not in st.session_state:
    st.session_state["app_initialized"] = True

# ========== Streamlit pagina setup ==========
st.set_page_config(page_title="Friese Warmteatlas", layout="wide")
st.markdown(
    """
    <style>
    :root { --sidebar-min-width: 340px; }
    [data-testid="stSidebar"] {
      min-width: var(--sidebar-min-width) !important;
    }
    [data-testid="stSidebar"] > div:first-child {
      min-width: var(--sidebar-min-width) !important;
    }
    .block-container {
      padding-bottom: 1.5rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
st.markdown(
    '<h1 style="font-size: 35px;">Friese Warmteatlas</h1>',
    unsafe_allow_html=True,
)
st.markdown(
    """
    <p style="font-size: 16px; margin-top: -10px;">
        De potentie voor collectieve warmtevoorzieningen in Fryslân middels inzicht in
        warmtevraag, warmtebronnen en sociale indicatoren.
    </p>
    """,
    unsafe_allow_html=True,
)

# --- containers om de gewenste volgorde te forceren ---
kpi_container = st.container()
map_container = st.container()
tables_container = st.container()
report_status_slot = kpi_container.empty()

# ========== GeoJSON / CSV laden ==========
_gj_common_props = ["buurtnaam", "gemeentenaam"]

show_energiearmoede = bool(
    st.session_state.get(LAYER_CFG["energiearmoede"]["toggle_key"], False)
)
show_koopwoningen = bool(
    st.session_state.get(LAYER_CFG["koopwoningen"]["toggle_key"], False)
)
show_corporatie = bool(
    st.session_state.get(LAYER_CFG["wooncorporatie"]["toggle_key"], False)
)
show_water_potentie = bool(
    st.session_state.get(LAYER_CFG["water_potentie"]["toggle_key"], False)
)
show_buurt_potentie = bool(
    st.session_state.get(LAYER_CFG["buurt_potentie"]["toggle_key"], False)
)
show_warmtenet = bool(
    st.session_state.get(LAYER_CFG["warmtenet_model"]["toggle_key"], False)
)

gjson_energiearmoede = None
if show_energiearmoede:
    gjson_energiearmoede = load_geojson(
        ENERGIEARMOEDE_PATH,
        keep_props=[LAYER_CFG["energiearmoede"]["prop_name"], *_gj_common_props],
        coord_precision=3,
    )

gjson_koopwoningen = None
if show_koopwoningen:
    gjson_koopwoningen = load_geojson(
        KOOPWONINGEN_PATH,
        keep_props=[LAYER_CFG["koopwoningen"]["prop_name"], *_gj_common_props],
        coord_precision=3,
    )

gjson_corporatie = None
if show_corporatie:
    gjson_corporatie = load_geojson(
        WOONCORPORATIE_PATH,
        keep_props=[LAYER_CFG["wooncorporatie"]["prop_name"], *_gj_common_props],
        coord_precision=3,
    )

gjson_water_potentie = None
if show_water_potentie:
    gjson_water_potentie = load_geojson(
        WATER_POTENTIE_PATH,
        keep_props=["Potentie_kWh", "id"],
        coord_precision=5,
    )
    gjson_water_potentie = convert_geojson_to_wgs84_if_needed(gjson_water_potentie)

gjson_buurt_potentie = None
if show_buurt_potentie:
    gjson_buurt_potentie = load_geojson(
        BUURT_POTENTIE_PATH,
        keep_props=[
            "Perc_covered",
            "Demand_ontvangen_kWh",
            "heatDemand_kWh",
            "buurtnaam",
            "gemeentenaam",
        ],
        coord_precision=5,
    )
    gjson_buurt_potentie = convert_geojson_to_wgs84_if_needed(gjson_buurt_potentie)

gjson_warmtenet = None
if show_warmtenet:
    gjson_warmtenet = load_geojson(
        WARMTENET_PATH,
        keep_props=[
            "layer",
            "woonplaats",
            "bron_key",
            "bron_id",
            "vraag_id",
            "gegevensbron",
            "type_bron",
            "bron_mwh_per_jaar",
            "vraag_mwh_per_jaar",
            "ingezet_mwh_per_jaar",
            "benutting_pct",
            "aangesloten_objecten",
            "kosten_bron_euro",
            "kosten_aansluitingen_euro",
            "bron_totale_kosten_euro",
            "kosten_aansluiting_euro",
            "afstand_pad_m",
            "plaats_aangesloten_objecten",
            "plaats_aantal_bronnen",
            "plaats_kosten_bronnen_euro",
            "plaats_kosten_aansluitingen_euro",
            "plaats_kosten_leidingen_euro",
            "plaats_totale_kosten_euro",
            "plaats_gemiddelde_kosten_euro",
            "padlengte_m",
            "geometrie_lengte_m",
        ],
        coord_precision=5,
    )
    gjson_warmtenet = convert_geojson_to_wgs84_if_needed(gjson_warmtenet)

potential_meta: dict[str, dict] = {}
if gjson_water_potentie:
    potential_meta["water_potentie"] = build_water_potential_meta(gjson_water_potentie)
if gjson_buurt_potentie:
    potential_meta["buurt_potentie"] = build_buurt_potential_meta(gjson_buurt_potentie)
warmtenet_meta = build_warmtenet_meta(gjson_warmtenet)
wegennet_meta = build_wegennet_meta(None)

df_raw = load_data()
_log_ram("after_load_data")

# ========== Sidebar / UI ==========
sidebar_out = build_sidebar(df_raw, potential_meta, warmtenet_meta, wegennet_meta)
map_button_clicked_sidebar = False
if isinstance(sidebar_out, tuple):
    if len(sidebar_out) == 3:
        df_filtered_input, ui, map_button_clicked_sidebar = sidebar_out
    else:
        df_filtered_input, ui = sidebar_out
else:
    df_filtered_input, ui = sidebar_out

report_slot = ui.get("report_slot") if isinstance(ui, dict) else None

def _handle_make_map_click() -> None:
    st.session_state["show_map"] = True
    st.session_state["_map_changed"] = False


st.markdown("<div style='height:16px;'></div>", unsafe_allow_html=True)
map_button_clicked_main = st.button("Maak kaart", on_click=_handle_make_map_click)
map_button_clicked = bool(map_button_clicked_sidebar or map_button_clicked_main)
_log_ram("after_sidebar")

# ========== State init ==========
st.session_state.setdefault("show_map", False)
st.session_state.setdefault("sites", [])
st.session_state.setdefault("sites_costed", [])
st.session_state.setdefault("sites_ready", False)
st.session_state.setdefault("manual_site_h3", None)
st.session_state.setdefault("report_pdf", None)
st.session_state.setdefault("report_pdf_path", None)
st.session_state.setdefault("report_filename", None)
st.session_state.setdefault("report_requested", False)
st.session_state.setdefault("report_map_image", None)
st.session_state.setdefault("report_map_image_name", None)
st.session_state.setdefault("report_map_image_error", None)
st.session_state.setdefault("report_upload_key", 0)
st.session_state.setdefault("report_image_uploaded", False)
st.session_state.setdefault("report_map_image_sig", None)
st.session_state.setdefault("map_raw_cache", None)

st.session_state.setdefault("first_hint_shown", False)


# ===== Helpers voor stabiele vergelijkingen =====
def _as_sorted_list(x):
    """Converteer invoer naar een gesorteerde lijst voor Jaccard-vergelijkingen."""
    if x is None:
        return []
    if isinstance(x, (list, tuple, set)):
        return sorted(list(x))
    return [x]


def _as_int(x, default=0):
    """Robuuste int-cast met fallbackwaarde."""
    try:
        return int(x)
    except Exception:
        return default


def _as_float(x, default=0.0):
    """Robuuste float-cast met fallbackwaarde."""
    try:
        return float(x)
    except Exception:
        return default


def _as_tuple_2(x, default=(0, 0)):
    """Converteer een iterabele naar tuple[int, int] voor bouwjaar slider."""
    try:
        a, b = x
        return (_as_int(a), _as_int(b))
    except Exception:
        return default


def _cleanup_report_file() -> None:
    report_path = st.session_state.get("report_pdf_path")
    if report_path:
        try:
            Path(report_path).unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass
    st.session_state["report_pdf_path"] = None


def _clear_report_state(*, clear_map_image: bool = True) -> None:
    """Verwijder rapport-uitvoer uit session_state en eventuele temp-bestanden."""
    _cleanup_report_file()
    st.session_state["report_pdf"] = None
    st.session_state["report_filename"] = None
    st.session_state["report_requested"] = False
    st.session_state["report_map_image_error"] = None
    st.session_state["report_image_uploaded"] = False
    if clear_map_image:
        st.session_state["report_map_image"] = None
        st.session_state["report_map_image_name"] = None
        st.session_state["report_map_image_sig"] = None
        st.session_state["report_upload_key"] = (
            int(st.session_state.get("report_upload_key", 0)) + 1
        )


def _request_report() -> None:
    st.session_state["report_requested"] = True


# ===== Filters-snapshot =====
def _build_filters_snapshot(ui: dict) -> dict:
    """Maak een hashbare snapshot van alle filters voor change-detectie."""
    L = st.session_state.get("LAYER_CFG", LAYER_CFG)
    return {
        "zoom_level": _as_int(ui.get("zoom_level")),
        "resolution": _as_int(ui.get("resolution")),
        "extruded": bool(ui.get("extruded")),
        "map_style": ui.get("map_style", "light"),
        "hide_basemap": bool(ui.get("hide_basemap", False)),
        "show_main_layer": bool(ui.get("show_main_layer", True)),
        "show_indicative_layer": bool(ui.get("show_indicative_layer", True)),
        "heat_unit": str(ui.get("heat_unit", "kWh/m²")),
        "warmte_hex_opacity": _as_float(
            ui.get(
                "warmte_hex_opacity", st.session_state.get("warmte_hex_opacity", 0.6)
            )
        ),
        "threshold": _as_float(ui.get("threshold", 50.0)),
        "gemeente": _as_sorted_list(ui.get("gemeente_selectie")),
        "woonplaats": _as_sorted_list(ui.get("woonplaats_selectie")),
        "Energieklasse": _as_sorted_list(
            [str(x) for x in ui.get("energieklasse_selectie", [])]
        ),
        "bouwjaar_range": _as_tuple_2(ui.get("bouwjaar_range", (0, 3000))),
        "type_pand": str(ui.get("pand_selectie", "")),
        L["energiearmoede"]["toggle_key"]: bool(
            st.session_state.get(L["energiearmoede"]["toggle_key"], False)
        ),
        L["koopwoningen"]["toggle_key"]: bool(
            st.session_state.get(L["koopwoningen"]["toggle_key"], False)
        ),
        L["wooncorporatie"]["toggle_key"]: bool(
            st.session_state.get(L["wooncorporatie"]["toggle_key"], False)
        ),
        "extra_opacity": _as_float(ui.get("extra_opacity", 0.55)),
        L["water_potentie"]["toggle_key"]: bool(
            st.session_state.get(L["water_potentie"]["toggle_key"], False)
        ),
        "water_potentie_opacity": _as_float(
            ui.get(
                "water_potentie_opacity",
                st.session_state.get("water_potentie_opacity", 0.7),
            )
        ),
        L["buurt_potentie"]["toggle_key"]: bool(
            st.session_state.get(L["buurt_potentie"]["toggle_key"], False)
        ),
        "buurt_potentie_opacity": _as_float(
            ui.get(
                "buurt_potentie_opacity",
                st.session_state.get("buurt_potentie_opacity", 0.7),
            )
        ),
        "participatie": _as_int(
            ui.get("participatie", st.session_state.get("participatie", 80))
        ),
        "show_sites_layer": bool(ui.get("show_sites_layer", False)),
        "sites_hex_opacity": _as_float(
            ui.get("sites_hex_opacity", st.session_state.get("sites_hex_opacity", 0.85))
        ),
        "kring_radius": _as_int(
            ui.get("kring_radius", st.session_state.get("kring_radius", 3))
        ),
        "min_sep": _as_int(ui.get("min_sep", st.session_state.get("min_sep", 3))),
        "n_sites": _as_int(ui.get("n_sites", st.session_state.get("n_sites", 3))),
        "cap_mwh": _as_int(ui.get("cap_mwh", st.session_state.get("cap_mwh", 100_000))),
        "cap_buildings": _as_int(
            ui.get("cap_buildings", st.session_state.get("cap_buildings", 1_000))
        ),
    }


def _build_report_filters_snapshot(ui: dict) -> dict:
    """Snapshot met extra report-relevante filters (zonder map-trigger gedrag)."""
    L = st.session_state.get("LAYER_CFG", LAYER_CFG)
    snap = _build_filters_snapshot(ui).copy()
    snap.update(
        {
            L["warmtenet_model"]["toggle_key"]: bool(
                st.session_state.get(L["warmtenet_model"]["toggle_key"], False)
            ),
            L["wegennet"]["toggle_key"]: bool(
                st.session_state.get(L["wegennet"]["toggle_key"], False)
            ),
            "warmtenet_show_sources": bool(
                ui.get(
                    "warmtenet_show_sources",
                    st.session_state.get("warmtenet_show_sources", True),
                )
            ),
            "warmtenet_show_objects": bool(
                ui.get(
                    "warmtenet_show_objects",
                    st.session_state.get("warmtenet_show_objects", True),
                )
            ),
            "warmtenet_show_lines": bool(
                ui.get(
                    "warmtenet_show_lines",
                    st.session_state.get("warmtenet_show_lines", True),
                )
            ),
            "warmtenet_wp_selectie": _as_sorted_list(
                ui.get(
                    "warmtenet_wp_selectie",
                    st.session_state.get("warmtenet_wp_selectie", []),
                )
            ),
            "warmtenet_type_selectie": _as_sorted_list(
                ui.get(
                    "warmtenet_type_selectie",
                    st.session_state.get("warmtenet_type_selectie", []),
                )
            ),
            "warmtenet_selected_keys": _as_sorted_list(
                ui.get(
                    "warmtenet_selected_keys",
                    st.session_state.get("warmtenet_selected_keys", []),
                )
            ),
            "warmtenet_opacity": _as_float(
                ui.get(
                    "warmtenet_opacity",
                    st.session_state.get("warmtenet_opacity", 0.85),
                )
            ),
            "wegennet_wp_selectie": _as_sorted_list(
                ui.get(
                    "wegennet_wp_selectie",
                    st.session_state.get("wegennet_wp_selectie", []),
                )
            ),
            "wegennet_type_selectie": _as_sorted_list(
                ui.get(
                    "wegennet_type_selectie",
                    st.session_state.get("wegennet_type_selectie", []),
                )
            ),
            "wegennet_opacity": _as_float(
                ui.get(
                    "wegennet_opacity",
                    st.session_state.get("wegennet_opacity", 0.8),
                )
            ),
        }
    )
    return snap


def _filters_without_zoom(ui: dict) -> dict:
    """Snapshot zonder zoomvelden, handig voor UI-vergelijkingen."""
    snap = _build_filters_snapshot(ui).copy()
    snap.pop("zoom_level", None)
    snap.pop("resolution", None)
    return snap


def _changed_filter_keys(prev: dict, curr: dict) -> set[str]:
    """Bepaal welke filtervelden gewijzigd zijn tussen twee snapshots."""
    keys = set(prev) | set(curr)
    return {k for k in keys if prev.get(k) != curr.get(k)}


if "prev_filters" not in st.session_state:
    st.session_state.prev_filters = _build_filters_snapshot(ui)
if "prev_report_filters" not in st.session_state:
    st.session_state.prev_report_filters = _build_report_filters_snapshot(ui)

current_filters = _build_filters_snapshot(ui)
current_report_filters = _build_report_filters_snapshot(ui)
filters_changed = current_filters != st.session_state.prev_filters
report_filters_changed = (
    current_report_filters != st.session_state.prev_report_filters
)

if filters_changed:
    changed_keys = _changed_filter_keys(st.session_state.prev_filters, current_filters)
    st.session_state.prev_filters = current_filters
    st.session_state.prev_report_filters = current_report_filters
    _clear_report_state(clear_map_image=True)
    st.session_state["map_raw_cache"] = None
    woonplaats_only_change = bool(changed_keys) and changed_keys.issubset(
        {"woonplaats"}
    )
    visual_only_change = bool(changed_keys) and changed_keys.issubset(
        {
            "warmte_hex_opacity",
            "sites_hex_opacity",
            "water_potentie_opacity",
            "buurt_potentie_opacity",
        }
    )
    if woonplaats_only_change and st.session_state.get("show_map"):
        st.session_state["_map_changed"] = False
        st.session_state["sites_ready"] = False
    elif visual_only_change:
        st.session_state["_map_changed"] = False
    else:
        st.session_state.show_map = False
        st.session_state["_map_changed"] = True
        st.session_state["sites_ready"] = False
else:
    st.session_state["_map_changed"] = False
    if report_filters_changed:
        st.session_state.prev_report_filters = current_report_filters
        _clear_report_state(clear_map_image=True)
        st.session_state["map_raw_cache"] = None

if map_button_clicked:
    st.session_state.show_map = True
    st.session_state["_map_changed"] = False

if st.session_state.get("report_requested"):
    st.session_state.show_map = True
    st.session_state["_map_changed"] = False


# ========== Hoofdscherm ==========
should_compute = st.session_state.show_map or st.session_state.get("report_requested")
if should_compute:
    res = int(ui["resolution"])
    zoom_level = int(ui.get("zoom_level", 0))
    heat_unit = str(ui.get("heat_unit", "kWh/m²"))
    threshold_kwh = float(ui.get("threshold", 50.0))
    threshold_display = float(
        ui.get(
            "threshold_display",
            threshold_kwh if heat_unit != "MWh/ha" else threshold_kwh * 10.0,
        )
    )
    value_col = "MWh_per_ha" if heat_unit == "MWh/ha" else "kWh_per_m2"

    map_raw_cache = st.session_state.get("map_raw_cache") or {}
    use_cached_raw = bool(st.session_state.get("report_image_uploaded")) and (
        map_raw_cache.get("filters") == current_filters
    )
    if use_cached_raw:
        df_filtered = map_raw_cache["df_filtered"].copy()
        df_extra_info = map_raw_cache["df_extra_info"].copy()
        df_view_source = map_raw_cache["df_view_source"].copy()
    else:
        df_filtered, df_extra_info, df_view_source = build_map_dataframe(
            df_filtered_input, res, log_fn=_log_ram
        )
        st.session_state["map_raw_cache"] = {
            "filters": current_filters.copy(),
            "df_filtered": df_filtered.copy(),
            "df_extra_info": df_extra_info.copy(),
            "df_view_source": df_view_source.copy(),
        }

    # afronden
    df_filtered["kWh_per_m2"] = df_filtered["kWh_per_m2"].round(0)
    df_filtered["gemiddeld_jaarverbruik_mWh"] = df_filtered[
        "gemiddeld_jaarverbruik_mWh"
    ].round(0)
    df_filtered["totale_oppervlakte"] = df_filtered["totale_oppervlakte"].round(0)
    df_filtered["bouwjaar"] = df_filtered["bouwjaar"].round(0)

    # Oppervlakte en dichtheid
    area_km2_lookup = {
        idx: float(h3.cell_area(idx, unit="km^2"))
        for idx in df_filtered["h3_index"].dropna().unique()
    }
    df_filtered["area_km2"] = (
        df_filtered["h3_index"].map(area_km2_lookup).astype("float32")
    )
    df_filtered["area_ha"] = (df_filtered["area_km2"] * 100.0).astype("float32")
    df_filtered["area_m2"] = (df_filtered["area_km2"] * 1_000_000.0).astype("float32")

    area_for_density = df_filtered["area_ha"].replace(0, pd.NA)
    df_filtered["MWh_per_ha"] = (
        df_filtered["gemiddeld_jaarverbruik_mWh"].div(area_for_density)
    ).fillna(0.0)
    df_filtered["area_ha_r"] = df_filtered["area_ha"]
    df_filtered["MWh_per_ha_r"] = df_filtered["MWh_per_ha"].round(2)
    df_filtered.drop(columns=["area_km2"], inplace=True)
    df_filtered["gemiddeld_jaarverbruik_mWh_r"] = (
        df_filtered["gemiddeld_jaarverbruik_mWh"].round(0).astype(int)
    )
    huizen_nonzero = df_filtered["aantal_huizen"].replace(0, pd.NA)
    df_filtered["MWh_per_pand"] = (
        df_filtered["gemiddeld_jaarverbruik_mWh"].div(huizen_nonzero)
    ).fillna(0.0)
    df_filtered["MWh_per_pand_r"] = df_filtered["MWh_per_pand"].round(2)

    # Compacte dtypes vasthouden om RAM onder controle te houden
    for col, dtype in [
        ("kWh_per_m2", "float32"),
        ("gemiddeld_jaarverbruik_mWh", "float32"),
        ("sum_mwh_raw", "float32"),
        ("totale_oppervlakte", "float32"),
        ("MWh_per_ha", "float32"),
        ("MWh_per_ha_r", "float32"),
        ("MWh_per_pand", "float32"),
        ("MWh_per_pand_r", "float32"),
        ("area_ha", "float32"),
        ("area_m2", "float32"),
        ("area_ha_r", "float32"),
    ]:
        if col in df_filtered.columns:
            df_filtered[col] = pd.to_numeric(df_filtered[col], errors="coerce").astype(
                dtype
            )

    for col in [
        "aantal_huizen",
        "aantal_VBOs",
        "gemiddeld_jaarverbruik_mWh_r",
        "bouwjaar",
    ]:
        if col in df_filtered.columns:
            df_filtered[col] = (
                pd.to_numeric(df_filtered[col], errors="coerce")
                .fillna(0)
                .astype("int32")
            )

    # Kleuren en 3D hoogte
    df_filtered["color"] = df_filtered[value_col].apply(
        lambda v: get_heat_color(v, heat_unit)
    )
    value_series = df_filtered[value_col]
    base_min = 25.0 if heat_unit == "MWh/ha" else 10.0
    max_height_ref = max(value_series.max(), threshold_display, base_min + 1.0)
    df_filtered["scaled_elevation"] = (
        (value_series - base_min) / max((max_height_ref - base_min), 1) * max_height_ref
    )
    df_filtered["scaled_elevation"] = df_filtered["scaled_elevation"].clip(
        lower=0, upper=threshold_display
    )

    # merge extra tooltip info
    df_filtered = df_filtered.merge(df_extra_info, on="h3_index", how="left")
    df_filtered = df_filtered[
        [
            "h3_index",
            "kWh_per_m2",
            "color",
            "woonplaats",
            "aantal_huizen",
            "aantal_VBOs",
            "scaled_elevation",
            "totale_oppervlakte",
            "gemiddeld_jaarverbruik_mWh",
            "sum_mwh_raw",
            "gemiddeld_jaarverbruik_mWh_r",
            "bouwjaar",
            "MWh_per_ha",
            "MWh_per_ha_r",
            "MWh_per_pand",
            "MWh_per_pand_r",
            "area_ha",
            "area_ha_r",
            "area_m2",
        ]
    ]

    # --------- Warmtevoorziening (alleen als toggle aan én woonplaats geselecteerd) ---------
    woonplaatsen_selected = [wp for wp in ui.get("woonplaats_selectie", []) if wp]
    show_sites_layer = ui.get("show_sites_layer")
    current_sites_mode = ui.get(
        "sites_mode", st.session_state.get("sites_mode", "auto")
    )
    manual_mode = current_sites_mode == "manual"

    if show_sites_layer and manual_mode:
        for payload in [
            st.session_state.get("main_map_deck_chart_selected_data"),
            st.session_state.get("main_map_deck_chart"),
        ]:
            selected_from_state = extract_selected_hex_from_payload(payload)
            if selected_from_state:
                st.session_state["manual_site_h3"] = selected_from_state
                break

    allow_sites_auto = show_sites_layer and zoom_level >= 11 and woonplaatsen_selected
    allow_sites_manual = show_sites_layer and manual_mode
    allow_sites = allow_sites_auto or allow_sites_manual
    sites_records = []
    prev_sites_mode = st.session_state.get("_prev_sites_mode")
    if prev_sites_mode != current_sites_mode:
        st.session_state["_prev_sites_mode"] = current_sites_mode
        st.session_state.sites = []
        st.session_state.sites_costed = []
        st.session_state.sites_ready = False
        if current_sites_mode != "manual":
            st.session_state.pop("manual_site_h3", None)

    if allow_sites:
        if ui.get("reset_manual_site"):
            st.session_state.pop("manual_site_h3", None)
            st.session_state.sites = []
            st.session_state.sites_costed = []
            st.session_state.sites_ready = False

        sites_mode = current_sites_mode or "auto"
        k_val = int(st.session_state.kring_radius)

        if sites_mode == "auto":
            compute_requested = ui.get("compute_sites", False)
            if compute_requested:
                shortlist_top_frac = 0.85
                threshold_kwh_m2 = float(ui["threshold"])

                centers_keep = shortlist_centers(
                    df_filtered,
                    threshold_kwh_m2=threshold_kwh_m2,
                    top_frac=shortlist_top_frac,
                )
                df_for_clusters = (
                    df_filtered.merge(centers_keep, on="h3_index", how="inner")
                    if not centers_keep.empty
                    else df_filtered
                )

                cluster_params = {
                    "k": k_val,
                    "threshold": threshold_kwh_m2,
                    "shortlist_frac": shortlist_top_frac,
                }
                cache_key = filters_fingerprint(
                    cluster_params, df_for_clusters["h3_index"].astype(str).unique()
                )

                cluster_input = df_for_clusters.loc[
                    :, ["h3_index", "gemiddeld_jaarverbruik_mWh", "aantal_huizen"]
                ]
                clusters = compute_clusters_cached(cache_key, cluster_input, k_val)
                _log_ram("after_clusters")

                clusters = clusters.merge(
                    df_filtered[
                        [
                            "h3_index",
                            "woonplaats",
                            "kWh_per_m2",
                            "aantal_VBOs",
                            "gemiddeld_jaarverbruik_mWh",
                        ]
                    ],
                    on="h3_index",
                    how="left",
                )

                sites_df = select_sites_from_clusters(
                    clusters,
                    min_sep_cells=st.session_state.min_sep,
                    topk=st.session_state.n_sites,
                    cap_mwh=float(st.session_state.cap_mwh),
                    cap_buildings=int(st.session_state.cap_buildings),
                    ttl=1800,
                )

                records = build_site_records(sites_df, df_filtered, k_val)
                st.session_state.sites = records
                st.session_state.sites_costed = records
                st.session_state.sites_ready = bool(records)
                del cluster_input, clusters, sites_df
            elif not st.session_state.get("sites_ready"):
                st.session_state.sites = []
                st.session_state.sites_costed = []
        else:
            manual_hex = st.session_state.get("manual_site_h3")
            if manual_hex:
                cluster_input_manual = df_filtered.loc[
                    :, ["h3_index", "gemiddeld_jaarverbruik_mWh", "aantal_huizen"]
                ]
                manual_cache_key = filters_fingerprint(
                    {"mode": "manual", "k": k_val},
                    cluster_input_manual["h3_index"].astype(str).unique(),
                )
                clusters_all = compute_clusters_cached(
                    manual_cache_key, cluster_input_manual, k_val
                )
                manual_cluster = clusters_all[
                    clusters_all["h3_index"] == manual_hex
                ].copy()
                if manual_cluster.empty:
                    manual_cluster = pd.DataFrame(
                        [
                            {
                                "h3_index": manual_hex,
                                "cluster_MWh": 0,
                                "cluster_buildings": 0,
                            }
                        ]
                    )

                cap_mwh_val = float(st.session_state.cap_mwh)
                cap_buildings_val = int(st.session_state.cap_buildings)

                manual_cluster["cluster_MWh"] = (
                    pd.to_numeric(manual_cluster["cluster_MWh"], errors="coerce")
                    .fillna(0)
                    .astype("int32")
                )
                manual_cluster["cluster_buildings"] = (
                    pd.to_numeric(manual_cluster["cluster_buildings"], errors="coerce")
                    .fillna(0)
                    .astype("int32")
                )

                manual_sites_df = select_sites_from_clusters(
                    manual_cluster,
                    min_sep_cells=0,
                    topk=1,
                    cap_mwh=cap_mwh_val,
                    cap_buildings=cap_buildings_val,
                    ttl=1800,
                )

                records = build_site_records(manual_sites_df, df_filtered, k_val)
                st.session_state.sites = records
                st.session_state.sites_costed = records
                st.session_state.sites_ready = bool(records)
            else:
                st.session_state.sites = []
                st.session_state.sites_costed = []
                st.session_state.sites_ready = False

        if st.session_state.get("sites_ready"):
            sites_records = st.session_state.sites
    else:
        st.session_state.sites = []
        st.session_state.sites_costed = []
        st.session_state.sites_ready = False
        st.session_state.pop("manual_site_h3", None)

    if not st.session_state.get("sites_ready"):
        sites_records = []

    if st.session_state.show_map:
        # ========== Kaartlagen ==========
        geojson_dict = {
            "energiearmoede": gjson_energiearmoede,
            "koopwoningen": gjson_koopwoningen,
            "corporatie": gjson_corporatie,
            "water_potentie": gjson_water_potentie,
            "buurt_potentie": gjson_buurt_potentie,
            "warmtenet": gjson_warmtenet,
        }
    
        extra_layers = []
        if any(
            [
                st.session_state.get(LAYER_CFG["energiearmoede"]["toggle_key"]),
                st.session_state.get(LAYER_CFG["koopwoningen"]["toggle_key"]),
                st.session_state.get(LAYER_CFG["wooncorporatie"]["toggle_key"]),
                st.session_state.get(LAYER_CFG["water_potentie"]["toggle_key"]),
                st.session_state.get(LAYER_CFG["buurt_potentie"]["toggle_key"]),
            ]
        ):
            extra_layers = create_extra_layers(
                geojson_dict,
                ui["woonplaats_selectie"],
                ui["zoom_level"],
                ui["extra_opacity"],
                potential_meta,
            )
    
        warmtenet_layers: list[pdk.Layer] = []
        if ui.get("show_warmtenet_model") and gjson_warmtenet:
            warmtenet_layers = create_warmtenet_layers(
                gjson_warmtenet,
                ui.get("warmtenet_wp_selectie", ui.get("woonplaats_selectie", [])),
                (warmtenet_meta or {}).get("color_map", {}),
                ui.get("warmtenet_selected_keys", []),
                (warmtenet_meta or {}).get("type_by_key", {}),
                ui.get("warmtenet_type_selectie", []),
                opacity=float(
                    ui.get(
                        "warmtenet_opacity",
                        (warmtenet_meta or {}).get("default_opacity", 0.85),
                    )
                ),
                show_lines=bool(ui.get("warmtenet_show_lines", True)),
                show_sources=bool(ui.get("warmtenet_show_sources", True)),
                show_objects=bool(ui.get("warmtenet_show_objects", True)),
            )
    
        wegennet_layers: list[pdk.Layer] = []
        wegennet_wp = ui.get("wegennet_wp_selectie", [])
        wegennet_cfg = LAYER_CFG.get("wegennet", {})
        min_zoom = int(wegennet_cfg.get("min_zoom", 11))
        gemeente_sel = st.session_state.get("gemeente_selectie", [])
        if (
            ui.get("show_wegennet")
            and wegennet_wp
            and int(ui.get("zoom_level", 0)) >= min_zoom
            and len(gemeente_sel) == 1
        ):
            wegennet_path = resolve_wegennet_path(gemeente_sel[0])
            coord_precision = int(wegennet_cfg.get("coord_precision", 4))
            gjson_wegennet = load_geojson(
                wegennet_path,
                keep_props=[
                    "type",
                    "length_m",
                    "area_name",
                ],
                coord_precision=coord_precision,
            )
            gjson_wegennet = convert_geojson_to_wgs84_if_needed(gjson_wegennet)
            wegennet_layers = create_wegennet_layers(
                gjson_wegennet,
                wegennet_wp,
                ui.get("wegennet_type_selectie", []),
                opacity=float(
                    ui.get(
                        "wegennet_opacity",
                        (wegennet_meta or {}).get("default_opacity", 0.8),
                    )
                ),
                zoom_level=int(ui.get("zoom_level", 0)),
            )
    
        # H3 hoofdlaag(en) per zoom
        base_hex_cols = [
            "h3_index",
            "color",
            "scaled_elevation",
            "woonplaats",
            "aantal_huizen",
            "aantal_VBOs",
            "MWh_per_ha_r",
            "gemiddeld_jaarverbruik_mWh_r",
            "MWh_per_pand_r",
            "area_ha_r",
            "area_m2",
            "kWh_per_m2",
            "totale_oppervlakte",
            "bouwjaar",
        ]
        df_hex_view = df_filtered.loc[:, base_hex_cols].copy()
        df_hex_view["geo_extra_rows"] = ""
        df_hex_view["gemeente_row_display"] = "block"
        df_hex_view["buurt_row_display"] = "block"
    
        def _fmt0_val(series):
            return series.astype("int64").map(lambda v: format_dutch_number(int(v), 0))
    
        def _fmt2_val(series):
            return series.astype("float32").map(
                lambda v: format_dutch_number(float(v), 2)
            )
    
        def _fmt4_val(series):
            return series.astype("float32").map(
                lambda v: format_dutch_number(float(v), 4)
            )
    
        df_hex_view["aantal_huizen_fmt"] = _fmt0_val(df_hex_view["aantal_huizen"])
        df_hex_view["aantal_VBOs_fmt"] = _fmt0_val(df_hex_view["aantal_VBOs"])
        df_hex_view["MWh_per_ha_r_fmt"] = _fmt2_val(df_hex_view["MWh_per_ha_r"])
        df_hex_view["gemiddeld_jaarverbruik_mWh_r_fmt"] = _fmt0_val(
            df_hex_view["gemiddeld_jaarverbruik_mWh_r"]
        )
        df_hex_view["MWh_per_pand_fmt"] = _fmt2_val(df_hex_view["MWh_per_pand_r"])
        df_hex_view["area_ha_r_fmt"] = _fmt4_val(df_hex_view["area_ha_r"])
        df_hex_view["area_m2_fmt"] = _fmt0_val(
            df_hex_view["area_m2"].round().astype("int64")
        )
        df_hex_view["kWh_per_m2_fmt"] = _fmt0_val(df_hex_view["kWh_per_m2"])
        df_hex_view["totale_oppervlakte_fmt"] = _fmt0_val(
            df_hex_view["totale_oppervlakte"]
        )
        df_hex_view["bouwjaar_fmt"] = (
            df_hex_view["bouwjaar"].astype("int64").map(lambda v: str(int(v)))
        )
    
        df_hex_view["hex_section_display"] = "block"
        df_hex_view["site_section_display"] = "none"
        df_hex_view["geo_section_display"] = "none"
    
        cols_for_hex = [
            "h3_index",
            "color",
            "scaled_elevation",
            "woonplaats",
            "aantal_huizen",
            "aantal_VBOs",
            "MWh_per_ha_r",
            "MWh_per_pand_r",
            "gemiddeld_jaarverbruik_mWh_r",
            "area_ha_r",
            "kWh_per_m2",
            "totale_oppervlakte",
            "bouwjaar",
            "aantal_huizen_fmt",
            "aantal_VBOs_fmt",
            "MWh_per_ha_r_fmt",
            "MWh_per_pand_fmt",
            "gemiddeld_jaarverbruik_mWh_r_fmt",
            "area_ha_r_fmt",
            "area_m2_fmt",
            "kWh_per_m2_fmt",
            "totale_oppervlakte_fmt",
            "bouwjaar_fmt",
            "hex_section_display",
            "site_section_display",
            "geo_section_display",
            "geo_extra_rows",
            "gemeente_row_display",
            "buurt_row_display",
        ]
        df_hex_view = df_hex_view.loc[:, cols_for_hex]
        indic_value_col = "MWh_per_ha" if heat_unit == "MWh/ha" else "kWh_per_m2"
        indic_mask = df_filtered[indic_value_col] > threshold_display
        df_indicative = df_hex_view.loc[indic_mask, :]
        _log_ram("before_pydeck_layers")
        warmte_opacity = float(
            ui.get("warmte_hex_opacity", st.session_state.get("warmte_hex_opacity", 0.6))
        )
        layers = create_layers_by_zoom(
            df_hex_view,
            ui["show_main_layer"],
            ui["extruded"],
            ui["zoom_level"],
            warmte_opacity,
        )
    
        site_layers = []
        if allow_sites and sites_records:
            sites_costed_records = (
                st.session_state.sites_costed
                if st.session_state.get("sites_ready")
                else None
            )
            site_layers = create_site_layers(
                sites_records,
                sites_costed_records,
                site_hex_opacity=float(
                    ui.get(
                        "sites_hex_opacity", st.session_state.get("sites_hex_opacity", 0.85)
                    )
                ),
            )
    
        # Indicatieve aandachtslaag
        if ui["show_indicative_layer"] and not df_indicative.empty:
            layers.append(
                create_indicative_area_layer(
                    df_indicative, ui["extruded"], ui["zoom_level"], warmte_opacity
                )
            )
    
        # Basemap
        hide_bg = bool(ui.get("hide_basemap"))
        basemap_style = ui.get("basemap_style", ui.get("map_style"))
        base_layers = build_base_layers(basemap_style, hide_bg)
    
        # Volgorde: basemap -> woonlagen -> H3/indicatief/sites
        all_layers = (
            base_layers + extra_layers + layers + wegennet_layers + warmtenet_layers + site_layers
        )
    
        # ========== ViewState ==========
        def _view_for_selection(df_full, woonplaatsen_geselecteerd):
            """Bepaal kaartcentrum en zoom op basis van de huidige selectie."""
            manual_hex_current = st.session_state.get("manual_site_h3")
            if current_sites_mode == "manual" and manual_hex_current:
                lat_manual, lon_manual = h3.cell_to_latlng(manual_hex_current)
                return lat_manual, lon_manual, 13.0
            friesland_center = (53.125, 5.75)
            friesland_zoom = 8
            min_zoom, max_zoom = 8, 13.0
            if not woonplaatsen_geselecteerd:
                return friesland_center[0], friesland_center[1], friesland_zoom
            df_sel = df_full[df_full["woonplaats"].isin(woonplaatsen_geselecteerd)]
            if df_sel.empty:
                return friesland_center[0], friesland_center[1], friesland_zoom
            lat_center = float(df_sel["latitude"].mean())
            lon_center = float(df_sel["longitude"].mean())
            if len(woonplaatsen_geselecteerd) == 1:
                return lat_center, lon_center, 13.0
            lat_min = float(df_sel["latitude"].min())
            lat_max = float(df_sel["latitude"].max())
            lon_min = float(df_sel["longitude"].min())
            lon_max = float(df_sel["longitude"].max())
            lat_span = max(0.0001, lat_max - lat_min)
            lon_span = max(0.0001, lon_max - lon_min)
            span = max(lat_span, lon_span)
            if span > 2.0:
                zoom = 8.0
            elif span > 1.0:
                zoom = 8.0
            elif span > 0.5:
                zoom = 9.0
            elif span > 0.25:
                zoom = 9.5
            elif span > 0.12:
                zoom = 10.0
            elif span > 0.06:
                zoom = 11.0
            elif span > 0.03:
                zoom = 12.0
            else:
                zoom = 13.0
            zoom = max(min_zoom, min(max_zoom, zoom))
            return lat_center, lon_center, zoom
    
        lat, lon, zoom = _view_for_selection(df_view_source, ui["woonplaats_selectie"])
        st.session_state.view_state = pdk.ViewState(
            longitude=lon,
            latitude=lat,
            zoom=zoom,
            min_zoom=7.5,
            max_zoom=16,
            pitch=0,
            bearing=0,
        )
    
        if st.session_state.show_map:
            # ========== KPI ==========
            with kpi_container:
                render_kpis(
                    df_filtered,
                    st.session_state.participatie,
                    include_participation=False,
                )
    
            # ========== Kaart render + cleanup ==========
            deck_kwargs = {"map_style": ui.get("map_style")}
    
            with map_container:
                deck = pdk.Deck(
                    layers=all_layers,
                    initial_view_state=st.session_state.view_state,
                    tooltip=build_deck_tooltip(),
                    **deck_kwargs,
                )
    
                manual_selection_active = show_sites_layer and manual_mode
                chart_key = "main_map_deck_chart"
                chart_kwargs = {
                    "width": "stretch",
                    "key": chart_key,
                }
                if manual_selection_active:
                    chart_kwargs.update(
                        selection_mode="single-object",
                        on_select="rerun",
                    )
                chart_state = st.pydeck_chart(deck, **chart_kwargs)
    
                if manual_selection_active:
                    selected_hex = None
                    payload_candidates = [
                        st.session_state.get(f"{chart_key}_selected_data"),
                        st.session_state.get(chart_key),
                        getattr(chart_state, "selection", None),
                        chart_state,
                    ]
                    for payload in payload_candidates:
                        selected_hex = extract_selected_hex_from_payload(payload)
                        if selected_hex:
                            break
                    if (
                        selected_hex
                        and st.session_state.get("manual_site_h3") != selected_hex
                    ):
                        st.session_state["manual_site_h3"] = selected_hex
    
            # Opruimen om RAM-pieken terug te geven
            del (
                deck,
                all_layers,
                layers,
                base_layers,
                extra_layers,
                warmtenet_layers,
                df_hex_view,
            )
            sites_records = None
            sites_costed_records = None
            gc.collect()
    
            # ========== Tabellen ==========
            with tables_container:
                render_tabs(
                    df_filtered,
                    threshold_kwh,
                    ui["show_sites_layer"],
                    st.session_state.get("sites_costed"),
                    warmtenet_gjson=gjson_warmtenet,
                    show_warmtenet=bool(ui.get("show_warmtenet_model")),
                    show_wegennet=bool(ui.get("show_wegennet")),
                    warmtenet_wp=ui.get("warmtenet_wp_selectie", []),
                    wegennet_wp=ui.get("wegennet_wp_selectie", []),
                )
            st.session_state["_map_changed"] = False
    if st.session_state.get("report_requested"):
        if bool(ui.get("show_warmtenet_model")) and bool(ui.get("show_wegennet")):
            st.session_state["report_map_image_error"] = (
                "PDF voor potentiële warmtenetten is tijdelijk uitgeschakeld."
            )
            st.session_state["report_requested"] = False
            st.session_state["report_image_uploaded"] = False
        else:
            layer_state = {
                "energiearmoede": show_energiearmoede,
                "koopwoningen": show_koopwoningen,
                "wooncorporatie": show_corporatie,
                "water_potentie": show_water_potentie,
                "buurt_potentie": show_buurt_potentie,
                "warmtenet": show_warmtenet,
                "wegennet": bool(ui.get("show_wegennet")),
                "sites_layer": bool(ui.get("show_sites_layer")),
                "warmtenet_parts": {
                    "bronnen": bool(ui.get("warmtenet_show_sources")),
                    "objecten": bool(ui.get("warmtenet_show_objects")),
                    "leidingen": bool(ui.get("warmtenet_show_lines")),
                },
            }
            timestamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M")
            with report_status_slot.container():
                with st.spinner("PDF rapport genereren..."):
                    _cleanup_report_file()
                    st.session_state["report_pdf"] = None
                    map_image = prepare_report_image_bytes(
                        st.session_state.get("report_map_image"),
                        dpi=ui.get("report_dpi"),
                    )
                    if map_image is not None:
                        st.session_state["report_map_image"] = map_image
                    try:
                        st.session_state["report_pdf_path"] = build_report_pdf(
                            df_filtered,
                            ui=ui,
                            layer_state=layer_state,
                            sites_costed=st.session_state.get("sites_costed"),
                            warmtenet_gjson=gjson_warmtenet,
                            heat_unit=heat_unit,
                            threshold_display=threshold_display,
                            map_image=map_image,
                        )
                        st.session_state["report_filename"] = (
                            f"FRL_WarmteAtlas_{timestamp}.pdf"
                        )
                        st.session_state["report_map_image_error"] = None
                    except Exception:
                        try:
                            st.session_state["report_pdf_path"] = build_report_pdf(
                                df_filtered,
                                ui=ui,
                                layer_state=layer_state,
                                sites_costed=st.session_state.get("sites_costed"),
                                warmtenet_gjson=gjson_warmtenet,
                                heat_unit=heat_unit,
                                threshold_display=threshold_display,
                                map_image=None,
                            )
                            st.session_state["report_filename"] = (
                                f"FRL_WarmteAtlas_{timestamp}.pdf"
                            )
                            st.session_state["report_map_image_error"] = (
                                "De kaartafbeelding kon niet worden gebruikt. "
                                "PDF is zonder afbeelding gemaakt."
                            )
                        except Exception:
                            st.session_state["report_pdf_path"] = None
                            st.session_state["report_filename"] = None
                            st.session_state["report_map_image_error"] = (
                                "PDF maken is mislukt. Probeer het opnieuw."
                            )
                    report_path = st.session_state.get("report_pdf_path")
                    if report_path and Path(report_path).exists():
                        try:
                            st.session_state["report_pdf"] = Path(report_path).read_bytes()
                        except Exception:
                            st.session_state["report_pdf"] = None
            report_status_slot.empty()
            st.session_state["report_requested"] = False
            st.session_state["report_image_uploaded"] = False
    if report_slot is not None:
        report_pdf = st.session_state.get("report_pdf")
        report_path = st.session_state.get("report_pdf_path")
        report_filename = (
            st.session_state.get("report_filename") or "warmteatlas_rapport.pdf"
        )
        with report_slot:
            if bool(ui.get("show_warmtenet_model")) and bool(ui.get("show_wegennet")):
                st.warning(
                    "Het maken van een PDF-rapport met potentiële warmtenetten is tijdelijk uitgeschakeld. "
                    "Schakel één van de twee lagen uit om een PDF te maken. "
                    "Deze functie is weer beschikbaar na de update van de warmtenetten."
                )
            if report_pdf:
                st.download_button(
                    "Download PDF rapport",
                    data=report_pdf,
                    file_name=report_filename,
                    mime="application/pdf",
                )
            elif report_path and Path(report_path).exists():
                st.download_button(
                    "Download PDF rapport",
                    data=lambda p=report_path: open(p, "rb"),
                    file_name=report_filename,
                    mime="application/pdf",
                )
            else:
                if st.session_state.get("_map_changed"):
                    st.button("Maak kaart", on_click=_handle_make_map_click)
                else:
                    st.button(
                        "Maak PDF rapport",
                        on_click=_request_report,
                        disabled=bool(
                            ui.get("show_warmtenet_model") and ui.get("show_wegennet")
                        ),
                    )

else:
    with map_container:
        # - Eerste keer openen -> initiële instructie
        # - Daarna, als filters gewijzigd zijn -> update-instructie
        # - Anders (nog niets gedaan) -> neutrale instructie
        if st.session_state.get("_map_changed"):
            st.info(
                "De filters zijn gewijzigd. Klik op 'Maak kaart' om de kaart bij te werken."
            )
        elif not st.session_state.get("first_hint_shown", False):
            st.info(
                "Selecteer de gewenste filters. Klik vervolgens op 'Maak kaart' om de kaart weer te geven."
            )
            st.session_state["first_hint_shown"] = True
        else:
            st.info("Klik op 'Maak kaart' om de kaart weer te geven.")
    if report_slot is not None:
        report_pdf = st.session_state.get("report_pdf")
        report_path = st.session_state.get("report_pdf_path")
        report_filename = (
            st.session_state.get("report_filename") or "warmteatlas_rapport.pdf"
        )
        with report_slot:
            if bool(ui.get("show_warmtenet_model")) and bool(ui.get("show_wegennet")):
                st.warning(
                    "Het maken van een PDF-rapport met potentiële warmtenetten is tijdelijk uitgeschakeld. "
                    "Schakel één van de twee lagen uit om een PDF te kunnen maken. "
                    "Deze functie is weer beschikbaar na de update van de warmtenetten."
                )
            if report_pdf:
                st.download_button(
                    "Download PDF rapport",
                    data=report_pdf,
                    file_name=report_filename,
                    mime="application/pdf",
                )
            elif report_path and Path(report_path).exists():
                st.download_button(
                    "Download PDF rapport",
                    data=lambda p=report_path: open(p, "rb"),
                    file_name=report_filename,
                    mime="application/pdf",
                )
            else:
                if st.session_state.get("_map_changed"):
                    st.button("Maak kaart", on_click=_handle_make_map_click)
                else:
                    st.button(
                        "Maak PDF rapport",
                        on_click=_request_report,
                        disabled=bool(
                            ui.get("show_warmtenet_model") and ui.get("show_wegennet")
                        ),
                    )
