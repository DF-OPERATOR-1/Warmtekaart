"""Streamlit app: laadt data, bouwt UI, voert DAL-queries uit en rendert kaart/rapporten."""

# app.py
from __future__ import annotations

# ========== Imports ==========
import gc
import os
import time
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
from core.h3agg import H3_RES13_COL
from core.map_data import (
    build_site_records,
    extract_selected_hex_from_payload,
)
from core.io import (
    load_geojson,
    resolve_wegennet_path,
    resolve_wegennet_paths,
)
from core.dal import dal_query
from core.woonplaats import (
    load_woonplaats_areas,
    build_woonplaats_summary,
    normalize_woonplaats,
)
from core.report import build_report_pdf
from ui.sidebar import build_sidebar, render_report_section
from ui.kpis_and_tables import render_kpis, render_tabs

# Flow: load data -> build sidebar -> compute aggregates -> render layers.

# ---------- Performance caps ----------
MAX_H3_CELLS = 50_000
MAX_OBJECT_ROWS = 50_000
MAX_TOTAL_ROWS_ALL_LAYERS = 80_000
MIN_H3_RES = 8


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
    div[data-testid="stForm"] {
      border: 0 !important;
      padding: 0 !important;
    }
    div[data-testid="stForm"] > div {
      padding: 0 !important;
    }
    div[data-testid="stForm"] .stFormSubmitButton {
      margin-top: 0 !important;
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
        De potentie voor collectieve warmtevoorzieningen in Fryslân door inzicht in
        warmtevraag, warmtebronnen en sociale indicatoren.
    </p>
    """,
    unsafe_allow_html=True,
)
warmtevraag_notice_slot = st.empty()

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

_log_ram("after_geojson_loads")

potential_meta: dict[str, dict] = {}
if gjson_water_potentie:
    potential_meta["water_potentie"] = build_water_potential_meta(gjson_water_potentie)
if gjson_buurt_potentie:
    potential_meta["buurt_potentie"] = build_buurt_potential_meta(gjson_buurt_potentie)
warmtenet_meta = build_warmtenet_meta(gjson_warmtenet)
wegennet_meta = build_wegennet_meta(None)

# ========== Sidebar / UI ==========
report_path = st.session_state.get("report_pdf_path")
report_pdf_exists = bool(report_path and Path(report_path).exists())
disable_map_submit = bool(
    st.session_state.get("report_image_uploaded") or report_pdf_exists
)

form = st.form("filters_form")
with form:
    ui = build_sidebar(None, potential_meta, warmtenet_meta, wegennet_meta)

    zoom_level_notice = int(ui.get("zoom_level", 0))
    heat_unit_notice = str(ui.get("heat_unit", "")).strip()
    if zoom_level_notice in (9, 10):
        if heat_unit_notice == "kWh/m²":
            notice_text = (
                "Gebruiksoppervlakte (kWh/m²), "
                "<strong>minder geschikt</strong> voor zoomniveau 9 en 10"
            )
        else:
            notice_text = (
                "Grondoppervlakte (MWh/ha), "
                "<strong>geschikt</strong> voor zoomniveau 9 en 10"
            )
    else:
        if heat_unit_notice == "MWh/ha":
            notice_text = (
                "Grondoppervlakte (MWh/ha), "
                "<strong>minder geschikt</strong> voor zoomniveau 11 en 12"
            )
        else:
            notice_text = (
                "Gebruiksoppervlakte (kWh/m²), "
                "<strong>geschikt</strong> voor zoomniveau 11 en 12 (pandniveau)"
            )
    warmtevraag_notice_slot.markdown(
        f"""
        <div style="
            background-color: #fff4e5;
            border-left: 4px solid #ff9800;
            padding: 8px 12px;
            font-size: 0.85rem;
            line-height: 1.4;
            border-radius: 4px;
            color: #3f2a00;
            margin-top: 6px;
        ">
            <strong>Getoonde warmtevraag:</strong><br>
            {notice_text}
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("<div style='height:16px;'></div>", unsafe_allow_html=True)
    map_button_clicked = st.form_submit_button(
        "Maak kaart",
        disabled=disable_map_submit,
    )

_log_ram("after_sidebar")
report_container = ui.get("report_slot_container")
ui = render_report_section(ui, report_container)
report_slot = ui.get("report_slot")

# ========== State init ==========
st.session_state.setdefault("show_map", False)
st.session_state.setdefault("sites", [])
st.session_state.setdefault("sites_costed", [])
st.session_state.setdefault("sites_ready", False)
st.session_state.setdefault("manual_site_h3", None)
st.session_state.setdefault("report_pdf_path", None)
st.session_state.setdefault("report_filename", None)
st.session_state.setdefault("report_requested", False)
st.session_state.setdefault("report_map_image_path", None)
st.session_state.setdefault("report_map_image_name", None)
st.session_state.setdefault("report_map_image_error", None)
st.session_state.setdefault("report_upload_key", 0)
st.session_state.setdefault("report_image_uploaded", False)
st.session_state.setdefault("report_map_image_sig", None)
st.session_state.setdefault("report_in_progress", False)
st.session_state.setdefault("map_initialized", False)
st.session_state.setdefault("report_pdf_has_image", False)

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


def _cleanup_report_map_image() -> None:
    map_path = st.session_state.get("report_map_image_path")
    if map_path:
        try:
            Path(map_path).unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass
    st.session_state["report_map_image_path"] = None


def _clear_report_state(*, clear_map_image: bool = True) -> None:
    """Verwijder rapport-uitvoer uit session_state en eventuele temp-bestanden."""
    _cleanup_report_file()
    st.session_state["report_filename"] = None
    st.session_state["report_requested"] = False
    st.session_state["report_in_progress"] = False
    st.session_state["report_pdf_has_image"] = False
    st.session_state["report_map_image_error"] = None
    st.session_state["report_image_uploaded"] = False
    if clear_map_image:
        _cleanup_report_map_image()
        st.session_state["report_map_image_name"] = None
        st.session_state["report_map_image_sig"] = None
        st.session_state["report_upload_key"] = (
            int(st.session_state.get("report_upload_key", 0)) + 1
        )


def _request_report() -> None:
    st.session_state["report_requested"] = True


def _handle_report_download() -> None:
    _clear_report_state(clear_map_image=True)


def _read_file_bytes(path: str | Path) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def _build_dal_filters(ui: dict, *, resolution: int | None = None) -> dict:
    """Prepare primitive filters for DAL queries."""
    return {
        "gemeente": _as_sorted_list(ui.get("gemeente_selectie", [])),
        "woonplaats": _as_sorted_list(ui.get("woonplaats_selectie", [])),
        "energieklasse": _as_sorted_list(ui.get("energieklasse_selectie", [])),
        "bouwjaar_range": ui.get("bouwjaar_range", (0, 3000)),
        "pand_selectie": ui.get("pand_selectie", "Klein-, middel- en grootverbruik"),
        "resolution": int(resolution or ui.get("resolution") or 0),
    }


def _perf_debug_enabled() -> bool:
    val = os.getenv("WARMTE_DEBUG", "").strip().lower()
    return val in {"1", "true", "yes", "y"}


def _get_fd_count() -> int | None:
    try:
        import psutil
    except Exception:
        return None
    try:
        return int(psutil.Process(os.getpid()).num_fds())
    except Exception:
        return None


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
        "show_pandtype_labels": bool(ui.get("show_pandtype_labels", True)),
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
        "n_sites": _as_int(ui.get("n_sites", st.session_state.get("n_sites", 10))),
        "cap_mwh": _as_int(ui.get("cap_mwh", st.session_state.get("cap_mwh", 50_000))),
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


def _enrich_site_records_with_pandtypes(
    sites_records: list[dict],
    pandtype_counts_by_hex: pd.DataFrame | None,
) -> None:
    if not sites_records:
        return
    woningen_map: dict[str, int] = {}
    bedrijven_map: dict[str, int] = {}
    if (
        isinstance(pandtype_counts_by_hex, pd.DataFrame)
        and not pandtype_counts_by_hex.empty
    ):
        counts_df = pandtype_counts_by_hex
        if "h3_index" in counts_df.columns:
            counts_df = counts_df.set_index("h3_index")
        counts_df = counts_df.copy()
        counts_df.index = counts_df.index.astype(str)
        if "woningen" in counts_df.columns:
            woningen_map = (
                pd.to_numeric(counts_df["woningen"], errors="coerce")
                .fillna(0)
                .astype("int64")
                .to_dict()
            )
        if "bedrijven" in counts_df.columns:
            bedrijven_map = (
                pd.to_numeric(counts_df["bedrijven"], errors="coerce")
                .fillna(0)
                .astype("int64")
                .to_dict()
            )

    for record in sites_records:
        coverage_hexes = record.get("coverage_hexes") or []
        total_woningen = 0
        total_bedrijven = 0
        for cov in coverage_hexes:
            h3_id = str(cov.get("h3_index") or "")
            woningen_val = int(woningen_map.get(h3_id, 0) or 0)
            bedrijven_val = int(bedrijven_map.get(h3_id, 0) or 0)
            cov["woningen"] = woningen_val
            cov["bedrijven"] = bedrijven_val
            cov["woningen_fmt"] = format_dutch_number(woningen_val, 0)
            cov["bedrijven_fmt"] = format_dutch_number(bedrijven_val, 0)
            total_woningen += woningen_val
            total_bedrijven += bedrijven_val
        coverage_summary = record.get("coverage_summary")
        if isinstance(coverage_summary, dict):
            coverage_summary["woningen"] = total_woningen
            coverage_summary["bedrijven"] = total_bedrijven
            coverage_summary["woningen_fmt"] = format_dutch_number(total_woningen, 0)
            coverage_summary["bedrijven_fmt"] = format_dutch_number(total_bedrijven, 0)


if "prev_filters" not in st.session_state:
    st.session_state.prev_filters = _build_filters_snapshot(ui)
if "prev_report_filters" not in st.session_state:
    st.session_state.prev_report_filters = _build_report_filters_snapshot(ui)

current_filters = _build_filters_snapshot(ui)
current_report_filters = _build_report_filters_snapshot(ui)
filters_changed = current_filters != st.session_state.prev_filters
report_filters_changed = current_report_filters != st.session_state.prev_report_filters

if filters_changed:
    changed_keys = _changed_filter_keys(st.session_state.prev_filters, current_filters)
    st.session_state.prev_filters = current_filters
    st.session_state.prev_report_filters = current_report_filters
    _clear_report_state(clear_map_image=True)
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
        st.session_state["map_initialized"] = False
else:
    st.session_state["_map_changed"] = False
    if report_filters_changed:
        st.session_state.prev_report_filters = current_report_filters
        report_path = st.session_state.get("report_pdf_path")
        report_exists = bool(report_path and Path(report_path).exists())
        if (
            not st.session_state.get("report_requested")
            and not st.session_state.get("report_in_progress")
            and not report_exists
        ):
            _clear_report_state(clear_map_image=True)

if map_button_clicked:
    st.session_state.show_map = True
    st.session_state["_map_changed"] = False
    st.session_state["map_initialized"] = True

# ========== Hoofdscherm ==========
should_compute = st.session_state.show_map or st.session_state.get("report_requested")
if should_compute:
    if st.session_state.get("report_requested"):
        st.session_state.show_map = False
        st.session_state["_map_changed"] = True
        st.session_state["sites_ready"] = False
        st.session_state["map_initialized"] = False
        st.session_state.pop("main_map_deck_chart", None)
        st.session_state.pop("main_map_deck_chart_selected_data", None)
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

    perf_info = {"query_ms": None}
    filters_for_dal = _build_dal_filters(ui, resolution=res)
    query_start = time.perf_counter()
    df_map_agg = dal_query(filters_for_dal, "map_hex")
    perf_info["query_ms"] = int((time.perf_counter() - query_start) * 1000)

    effective_res = res
    coarsened = False
    warn_detail = None
    while len(df_map_agg) > MAX_H3_CELLS and effective_res > MIN_H3_RES:
        effective_res -= 1
        filters_for_dal["resolution"] = effective_res
        df_map_agg = dal_query(filters_for_dal, "map_hex")
        coarsened = True
    if len(df_map_agg) > MAX_H3_CELLS:
        df_map_agg = df_map_agg.sample(n=MAX_H3_CELLS, random_state=42)
        warn_detail = "Detail is teruggebracht (sampling) om performance te waarborgen."
    elif coarsened:
        warn_detail = "Detailniveau is verlaagd om performance te waarborgen."
    if warn_detail:
        st.warning(warn_detail)

    df_filtered = df_map_agg.copy()
    if df_filtered.empty:
        df_filtered = pd.DataFrame(
            columns=[
                "h3_index",
                "woonplaats",
                "sum_mwh_raw",
                "gemiddeld_jaarverbruik_mWh",
                "totale_oppervlakte",
                "bouwjaar",
                "aantal_VBOs",
                "aantal_huizen",
                "kWh_per_m2",
            ]
        )

    # afronden
    df_filtered["kWh_per_m2"] = (
        pd.to_numeric(df_filtered.get("kWh_per_m2"), errors="coerce")
        .fillna(0.0)
        .round(0)
    )
    df_filtered["gemiddeld_jaarverbruik_mWh"] = (
        pd.to_numeric(df_filtered.get("gemiddeld_jaarverbruik_mWh"), errors="coerce")
        .fillna(0.0)
        .round(0)
    )
    df_filtered["totale_oppervlakte"] = (
        pd.to_numeric(df_filtered.get("totale_oppervlakte"), errors="coerce")
        .fillna(0.0)
        .round(0)
    )
    df_filtered["bouwjaar"] = (
        pd.to_numeric(df_filtered.get("bouwjaar"), errors="coerce").fillna(0.0).round(0)
    )
    df_filtered["aantal_VBOs"] = (
        pd.to_numeric(df_filtered.get("aantal_VBOs"), errors="coerce")
        .fillna(0)
        .round(0)
        .astype("int32")
    )
    df_filtered["aantal_huizen"] = (
        pd.to_numeric(df_filtered.get("aantal_huizen"), errors="coerce")
        .fillna(0)
        .astype("int32")
    )
    df_filtered["sum_mwh_raw"] = pd.to_numeric(
        df_filtered.get("sum_mwh_raw"), errors="coerce"
    ).fillna(0.0)

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
    try:
        mem_mb = df_filtered.memory_usage(deep=True).sum() / 1e6
        print(
            f"[RAM_DEBUG] df_filtered: rows={len(df_filtered)} "
            f"cols={df_filtered.shape[1]} mem={mem_mb:.1f} MB"
        )
    except Exception:
        pass

    # Minimal views to keep hotspot/site memory usage low
    df_sites_records_base = df_filtered.loc[
        :,
        [
            "h3_index",
            "woonplaats",
            "kWh_per_m2",
            "gemiddeld_jaarverbruik_mWh",
            "gemiddeld_jaarverbruik_mWh_r",
            "aantal_huizen",
            "aantal_VBOs",
            "totale_oppervlakte",
            "bouwjaar",
            "MWh_per_ha",
            "MWh_per_ha_r",
            "MWh_per_pand",
            "MWh_per_pand_r",
            "area_ha",
            "area_ha_r",
            "area_m2",
        ],
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
        df_hotspot_agg = df_map_agg
        if df_hotspot_agg.empty:
            df_hotspot_base = pd.DataFrame(
                columns=[
                    "h3_index",
                    "kWh_per_m2",
                    "gemiddeld_jaarverbruik_mWh",
                    "aantal_huizen",
                ]
            )
        else:
            df_hotspot_base = df_hotspot_agg[
                [
                    "h3_index",
                    "kWh_per_m2",
                    "gemiddeld_jaarverbruik_mWh",
                    "aantal_huizen",
                ]
            ].copy()
        del df_hotspot_agg

        if ui.get("reset_manual_site"):
            st.session_state.pop("manual_site_h3", None)
            st.session_state.sites = []
            st.session_state.sites_costed = []
            st.session_state.sites_ready = False

        sites_mode = current_sites_mode or "auto"
        k_val = int(st.session_state.get("kring_radius", 3))

        if sites_mode == "auto":
            compute_requested = ui.get("compute_sites", False)
            if compute_requested:
                shortlist_top_frac = 0.85
                threshold_kwh_m2 = float(ui["threshold"])

                centers_keep = shortlist_centers(
                    df_hotspot_base,
                    threshold_kwh_m2=threshold_kwh_m2,
                    top_frac=shortlist_top_frac,
                )
                df_for_clusters = (
                    df_hotspot_base[
                        df_hotspot_base["h3_index"].isin(centers_keep["h3_index"])
                    ]
                    if not centers_keep.empty
                    else df_hotspot_base
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

                sites_df = select_sites_from_clusters(
                    clusters,
                    min_sep_cells=st.session_state.min_sep,
                    topk=st.session_state.n_sites,
                    cap_mwh=float(st.session_state.cap_mwh),
                    cap_buildings=int(st.session_state.cap_buildings),
                    ttl=1800,
                )

                records = build_site_records(sites_df, df_sites_records_base, k_val)
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
                cluster_input_manual = df_hotspot_base.loc[
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

                records = build_site_records(
                    manual_sites_df, df_sites_records_base, k_val
                )
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

    if "df_map_agg" in locals():
        del df_map_agg

    # H3 hoofdlaag(en) per zoom (ook nodig voor PDF, dus altijd binnen should_compute)
    pandtype_counts_by_woonplaats = None
    pandtype_counts_by_hex = None
    pandtype_mwh_by_woonplaats = None
    woonplaats_summary = None
    woonplaats_area_df = load_woonplaats_areas()

    summary_df = dal_query(filters_for_dal, "woonplaats_summary")
    if not summary_df.empty and "woonplaats" in summary_df.columns:
        summary_df["woonplaats"] = summary_df["woonplaats"].astype(str).str.strip()
        summary_df = summary_df[summary_df["woonplaats"].ne("")]
        if (
            not summary_df.empty
            and woonplaats_area_df is not None
            and not woonplaats_area_df.empty
        ):
            area_df = woonplaats_area_df.copy()
            area_df["woonplaats_norm"] = area_df["woonplaats"].map(normalize_woonplaats)
            summary_df["woonplaats_norm"] = summary_df["woonplaats"].map(
                normalize_woonplaats
            )
            summary_df = summary_df.merge(
                area_df[["woonplaats_norm", "area_ha"]],
                on="woonplaats_norm",
                how="left",
            )
            summary_df.drop(columns=["woonplaats_norm"], inplace=True)
            area_vals = summary_df["area_ha"].replace({0: pd.NA})
            summary_df["MWh_per_ha"] = summary_df["MWh"].div(area_vals)
        woonplaats_summary = summary_df

    counts_hex_df = dal_query(filters_for_dal, "pandtype_counts_by_hex")
    if not counts_hex_df.empty:
        counts_hex_df["h3_index"] = counts_hex_df["h3_index"].astype(str)
        pandtype_counts_by_hex = counts_hex_df.set_index("h3_index")

    counts_wp_df = dal_query(filters_for_dal, "pandtype_counts_by_woonplaats")
    if not counts_wp_df.empty:
        counts_wp_df["woonplaats"] = counts_wp_df["woonplaats"].astype(str).str.strip()
        pandtype_counts_by_woonplaats = counts_wp_df

    pandtype_mwh_df = dal_query(filters_for_dal, "pandtype_mwh_by_woonplaats")
    if not pandtype_mwh_df.empty:
        pandtype_mwh_by_woonplaats = pandtype_mwh_df
        area_vals = pandtype_mwh_by_woonplaats["area_ha"].replace({0: pd.NA})
        pandtype_mwh_by_woonplaats["MWh_per_ha"] = pandtype_mwh_by_woonplaats[
            "MWh"
        ].div(area_vals)
    if sites_records:
        _enrich_site_records_with_pandtypes(sites_records, pandtype_counts_by_hex)
        st.session_state.sites = sites_records
        st.session_state.sites_costed = sites_records

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
            coord_precision = int(wegennet_cfg.get("coord_precision", 4))
            wegennet_paths = resolve_wegennet_paths(wegennet_wp)
            if not wegennet_paths:
                wegennet_path = resolve_wegennet_path(gemeente_sel[0])
                wegennet_paths = [wegennet_path]
            feats: list[dict] = []
            for path in wegennet_paths:
                gjson_part = load_geojson(
                    path,
                    keep_props=[
                        "type",
                        "length_m",
                        "area_name",
                    ],
                    coord_precision=coord_precision,
                )
                if (
                    gjson_part
                    and isinstance(gjson_part, dict)
                    and gjson_part.get("type") == "FeatureCollection"
                ):
                    feats.extend(gjson_part.get("features") or [])
            if feats:
                gjson_wegennet = {"type": "FeatureCollection", "features": feats}
            else:
                gjson_wegennet = None
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
        if isinstance(pandtype_counts_by_hex, pd.DataFrame):
            df_hex_view["woningen"] = (
                df_hex_view["h3_index"]
                .map(pandtype_counts_by_hex.get("woningen"))
                .fillna(0)
                .astype("int32")
            )
            df_hex_view["bedrijven"] = (
                df_hex_view["h3_index"]
                .map(pandtype_counts_by_hex.get("bedrijven"))
                .fillna(0)
                .astype("int32")
            )
        else:
            df_hex_view["woningen"] = 0
            df_hex_view["bedrijven"] = 0
        df_hex_view["woningen_fmt"] = _fmt0_val(df_hex_view["woningen"])
        df_hex_view["bedrijven_fmt"] = _fmt0_val(df_hex_view["bedrijven"])
        pandtype_label_layer = None
        show_pandtype_labels = bool(ui.get("show_main_layer")) and bool(
            ui.get("show_pandtype_labels", True)
        )
        if show_pandtype_labels and not df_hex_view.empty:
            label_size = 10
            h3_resolution = int(effective_res or ui.get("zoom_level", 0))
            if h3_resolution >= 12:
                label_size = 6
            elif h3_resolution >= 11:
                label_size = 8
            label_df = df_hex_view.loc[:, ["h3_index", "woningen", "bedrijven"]].copy()
            label_df = label_df[
                (label_df["woningen"] > 0) | (label_df["bedrijven"] > 0)
            ]
            if not label_df.empty:
                if len(label_df) > MAX_OBJECT_ROWS:
                    label_df = label_df.sample(n=MAX_OBJECT_ROWS, random_state=42)
                    st.warning(
                        "Aantal pandtype-labels is beperkt om performance te waarborgen."
                    )
                label_df["label"] = "B"
                label_df.loc[label_df["woningen"] > 0, "label"] = "A"
                label_df.loc[
                    (label_df["woningen"] > 0) & (label_df["bedrijven"] > 0), "label"
                ] = "C"
                coords = [h3.cell_to_latlng(h) for h in label_df["h3_index"]]
                label_df["position"] = [[float(lon), float(lat)] for lat, lon in coords]
                pandtype_label_layer = pdk.Layer(
                    "TextLayer",
                    label_df,
                    pickable=False,
                    get_position="position",
                    get_text="label",
                    get_size=label_size,
                    size_units="pixels",
                    size_scale=1,
                    get_color=[18, 24, 32, 220],
                    background=False,
                    billboard=True,
                    minZoom=12.2,
                )
        indic_value_col = "MWh_per_ha" if heat_unit == "MWh/ha" else "kWh_per_m2"
        indic_mask = df_filtered[indic_value_col] > threshold_display
        df_indicative = df_hex_view.loc[indic_mask, ["h3_index"]]
        cols_for_hex = [
            "h3_index",
            "color",
            "scaled_elevation",
            "woonplaats",
            "aantal_huizen_fmt",
            "aantal_VBOs_fmt",
            "woningen_fmt",
            "bedrijven_fmt",
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
        df_hex_payload = df_hex_view.loc[:, cols_for_hex]
        total_rows = len(df_hex_payload) + len(df_indicative)
        if total_rows > MAX_TOTAL_ROWS_ALL_LAYERS:
            allowed = max(0, MAX_TOTAL_ROWS_ALL_LAYERS - len(df_hex_payload))
            if allowed < len(df_indicative):
                df_indicative = df_indicative.sample(n=allowed, random_state=42)
                st.warning(
                    "Detail van de aandachtslaag is beperkt om performance te waarborgen."
                )
        debug_row_counts = {
            "hex": len(df_hex_payload),
            "indicatief": len(df_indicative),
        }
        _log_ram("before_pydeck_layers")
        warmte_opacity = float(
            ui.get(
                "warmte_hex_opacity", st.session_state.get("warmte_hex_opacity", 0.6)
            )
        )
        layers = create_layers_by_zoom(
            df_hex_payload,
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
                        "sites_hex_opacity",
                        st.session_state.get("sites_hex_opacity", 0.85),
                    )
                ),
            )
            _log_ram("after_site_layers")

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

        label_layers = [pandtype_label_layer] if pandtype_label_layer else []

        # Volgorde: basemap -> woonlagen -> H3/indicatief/sites -> labels
        all_layers = (
            base_layers
            + extra_layers
            + layers
            + wegennet_layers
            + warmtenet_layers
            + site_layers
            + label_layers
        )
        del extra_layers, wegennet_layers, warmtenet_layers

        # ========== ViewState ==========
        def _view_for_selection(view_bounds: dict | None, woonplaatsen_geselecteerd):
            """Bepaal kaartcentrum en zoom op basis van de huidige selectie."""
            manual_hex_current = st.session_state.get("manual_site_h3")
            if current_sites_mode == "manual" and manual_hex_current:
                lat_manual, lon_manual = h3.cell_to_latlng(manual_hex_current)
                return lat_manual, lon_manual, 13.0
            friesland_center = (53.125, 5.75)
            friesland_zoom = 8
            min_zoom, max_zoom = 8, 18.0
            if not woonplaatsen_geselecteerd:
                return friesland_center[0], friesland_center[1], friesland_zoom
            if not view_bounds:
                return friesland_center[0], friesland_center[1], friesland_zoom
            lat_center = float(view_bounds.get("lat_mean") or friesland_center[0])
            lon_center = float(view_bounds.get("lon_mean") or friesland_center[1])
            if len(woonplaatsen_geselecteerd) == 1:
                return lat_center, lon_center, 13.0
            lat_min = float(view_bounds.get("lat_min") or lat_center)
            lat_max = float(view_bounds.get("lat_max") or lat_center)
            lon_min = float(view_bounds.get("lon_min") or lon_center)
            lon_max = float(view_bounds.get("lon_max") or lon_center)
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

        view_df = dal_query(filters_for_dal, "view_bounds")
        view_bounds = None
        if not view_df.empty:
            view_bounds = {
                "lat_mean": view_df.at[0, "lat_mean"],
                "lon_mean": view_df.at[0, "lon_mean"],
                "lat_min": view_df.at[0, "lat_min"],
                "lat_max": view_df.at[0, "lat_max"],
                "lon_min": view_df.at[0, "lon_min"],
                "lon_max": view_df.at[0, "lon_max"],
            }
        lat, lon, zoom = _view_for_selection(view_bounds, ui["woonplaats_selectie"])
        st.session_state.view_state = pdk.ViewState(
            longitude=lon,
            latitude=lat,
            zoom=zoom,
            min_zoom=7.5,
            max_zoom=18,
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
            report_ready = (
                st.session_state.get("report_pdf_path")
                and Path(st.session_state.get("report_pdf_path")).exists()
            )

            with map_container:
                if st.session_state.get("report_in_progress"):
                    st.info("PDF wordt gegenereerd. Even geduld...")
                elif report_ready:
                    st.info(
                        "PDF gegenereerd. Klik op ‘Download PDF-rapport’ om het rapport te downloaden."
                    )
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
            for _name in (
                "deck",
                "all_layers",
                "layers",
                "base_layers",
                "extra_layers",
                "warmtenet_layers",
                "label_layers",
                "pandtype_label_layer",
                "df_hex_view",
            ):
                if _name in locals():
                    del locals()[_name]
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
                    zoom_level=ui.get("zoom_level"),
                    min_zoom_wegennet=int(
                        LAYER_CFG.get("wegennet", {}).get("min_zoom", 11)
                    ),
                    woonplaats_summary=woonplaats_summary,
                    pandtype_counts_by_woonplaats=pandtype_counts_by_woonplaats,
                    pandtype_mwh_by_woonplaats=pandtype_mwh_by_woonplaats,
                    pand_selectie=ui.get("pand_selectie"),
                    show_pandtype_labels=bool(ui.get("show_pandtype_labels", False)),
                )
            st.session_state["_map_changed"] = False
            if _perf_debug_enabled():
                mem_mb = None
                try:
                    import psutil
                except Exception:
                    mem_mb = None
                else:
                    try:
                        mem_mb = psutil.Process(os.getpid()).memory_info().rss / 1e6
                    except Exception:
                        mem_mb = None
                fd_count = _get_fd_count()
                with st.sidebar.expander("Debug", expanded=False):
                    if mem_mb is not None:
                        st.write(f"RAM (RSS): {mem_mb:.1f} MB")
                    if fd_count is not None:
                        st.write(f"Open file descriptors: {fd_count}")
                    if perf_info.get("query_ms") is not None:
                        st.write(f"Query time: {perf_info['query_ms']} ms")
                    st.write(
                        f"Rows per layer: hex={debug_row_counts.get('hex', 0)}, "
                        f"indicatief={debug_row_counts.get('indicatief', 0)}, "
                        f"sites={len(sites_records or [])}"
                    )
    if st.session_state.get("report_requested"):
        report_generated = False
        status_slot = report_slot if report_slot is not None else report_status_slot
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
        with status_slot:
            with st.spinner("PDF-rapport genereren..."):
                st.session_state["report_in_progress"] = True
                _cleanup_report_file()
                map_image_path = st.session_state.get("report_map_image_path")
                if map_image_path and not Path(map_image_path).exists():
                    map_image_path = None
                    st.session_state["report_map_image_path"] = None
                    st.session_state["report_map_image_name"] = None
                    st.session_state["report_map_image_sig"] = None
                    st.session_state["report_image_uploaded"] = False
                st.session_state["report_pdf_has_image"] = bool(map_image_path)
                if not map_image_path:
                    st.session_state.show_map = False
                    st.session_state["_map_changed"] = True
                    st.session_state["sites_ready"] = False
                    st.session_state.pop("main_map_deck_chart", None)
                    st.session_state.pop("main_map_deck_chart_selected_data", None)
                try:
                    st.session_state["report_pdf_path"] = build_report_pdf(
                        df_filtered,
                        ui=ui,
                        layer_state=layer_state,
                        sites_costed=st.session_state.get("sites_costed"),
                        warmtenet_gjson=gjson_warmtenet,
                        heat_unit=heat_unit,
                        threshold_display=threshold_display,
                        map_image_path=map_image_path,
                        woonplaats_summary=woonplaats_summary,
                        pandtype_counts_by_woonplaats=pandtype_counts_by_woonplaats,
                        pandtype_mwh_by_woonplaats=pandtype_mwh_by_woonplaats,
                    )
                    st.session_state["report_filename"] = (
                        f"FRL_WarmteAtlas_{timestamp}.pdf"
                    )
                    st.session_state["report_map_image_error"] = None
                    report_generated = True
                except Exception as exc:
                    try:
                        st.session_state["report_pdf_path"] = build_report_pdf(
                            df_filtered,
                            ui=ui,
                            layer_state=layer_state,
                            sites_costed=st.session_state.get("sites_costed"),
                            warmtenet_gjson=gjson_warmtenet,
                            heat_unit=heat_unit,
                            threshold_display=threshold_display,
                            map_image_path=None,
                            woonplaats_summary=woonplaats_summary,
                            pandtype_counts_by_woonplaats=pandtype_counts_by_woonplaats,
                            pandtype_mwh_by_woonplaats=pandtype_mwh_by_woonplaats,
                        )
                        st.session_state["report_filename"] = (
                            f"FRL_WarmteAtlas_{timestamp}.pdf"
                        )
                        err_detail = type(exc).__name__
                        if str(exc).strip():
                            err_detail = f"{err_detail}: {exc}"
                        st.session_state["report_map_image_error"] = (
                            "De kaartafbeelding kon niet worden gebruikt ("
                            f"{err_detail}). PDF is zonder afbeelding gemaakt."
                        )
                        report_generated = True
                    except Exception as exc2:
                        st.session_state["report_pdf_path"] = None
                        st.session_state["report_filename"] = None
                        err_detail = type(exc2).__name__
                        if str(exc2).strip():
                            err_detail = f"{err_detail}: {exc2}"
                        st.session_state["report_map_image_error"] = (
                            "PDF maken is mislukt. Probeer het opnieuw.\n"
                            f"Foutdetail: {err_detail}"
                        )
                        report_generated = True
                st.session_state["report_in_progress"] = False
        st.session_state["report_requested"] = False
        st.session_state["report_image_uploaded"] = False
        if report_generated:
            st.rerun()
    if report_slot is not None:
        report_path = st.session_state.get("report_pdf_path")
        report_filename = (
            st.session_state.get("report_filename") or "warmteatlas_rapport.pdf"
        )
        with report_slot:
            if report_path and Path(report_path).exists():
                st.download_button(
                    "Download PDF-rapport",
                    data=_read_file_bytes(report_path),
                    file_name=report_filename,
                    mime="application/pdf",
                    on_click=_handle_report_download,
                )
            else:
                st.button(
                    "Maak PDF-rapport",
                    on_click=_request_report,
                    disabled=st.session_state.get("report_in_progress", False),
                )

else:
    with map_container:
        # - Eerste keer openen -> initiële instructie
        # - Daarna, als filters gewijzigd zijn -> update-instructie
        # - Anders (nog niets gedaan) -> neutrale instructie
        if st.session_state.get("report_in_progress"):
            st.info("PDF wordt gegenereerd. Even geduld...")
        elif (
            st.session_state.get("report_pdf_path")
            and Path(st.session_state.get("report_pdf_path")).exists()
        ):
            st.info(
                "PDF gegenereerd. Klik op ‘Download PDF-rapport’ om het rapport te downloaden."
            )
        elif st.session_state.get("report_image_uploaded"):
            st.info(
                "Upload voltooid. Laat de instellingen ongewijzigd en klik op "
                "‘Maak PDF-rapport’."
            )
        elif st.session_state.get("_map_changed"):
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
        report_path = st.session_state.get("report_pdf_path")
        report_filename = (
            st.session_state.get("report_filename") or "warmteatlas_rapport.pdf"
        )
        with report_slot:
            if report_path and Path(report_path).exists():
                st.download_button(
                    "Download PDF-rapport",
                    data=_read_file_bytes(report_path),
                    file_name=report_filename,
                    mime="application/pdf",
                    on_click=_handle_report_download,
                )
            else:
                st.button(
                    "Maak PDF-rapport",
                    on_click=_request_report,
                    disabled=st.session_state.get("report_in_progress", False),
                )
