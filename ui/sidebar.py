"""Sidebar UI: filters, toggles en uitleg."""

# ui/sidebar.py
from __future__ import annotations

from typing import Dict, Any, List

import base64
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from core.config import LAYER_CFG, BASEMAP_CFG
from core.dal import dal_query
from core.io import (
    resolve_wegennet_path,
    geojson_unique_props,
    list_wegennet_woonplaatsen,
    normalize_wegennet_name,
)
from core.report import write_bytes_to_tempfile
from core.utils import (
    format_dutch_number,
    get_dynamic_resolution,
    get_layer_colors,
    legend_labels_from_breaks,
    render_mini_legend,
    text_input_int,
    parse_dutch_int,
    MWH_HA_BREAKS,
    MWH_HA_COLORS,
    KWH_M2_BREAKS,
    KWH_M2_COLORS,
)
from ui.kpis_and_tables import render_participation_kpis


# ---------------------------
# Dark mode (helper)
# ---------------------------
def _is_dark_mode() -> bool:
    try:
        base = st.get_option("theme.base")
        if isinstance(base, str) and base.lower() == "dark":
            return True
        base_is_defined = isinstance(base, str)
    except Exception:
        base_is_defined = False
    try:
        bg_color = st.get_option("theme.backgroundColor")
        if _is_dark_color(bg_color):
            return True
    except Exception:
        if not base_is_defined:
            pass
    map_style = st.session_state.get("map_style")
    if isinstance(map_style, str) and "dark" in map_style.lower():
        return True
    return False


def _legend_theme_colors(dark_mode: bool) -> dict[str, str]:
    if dark_mode:
        return {
            "bg": "#111827",
            "border": "#374151",
            "text": "#f9fafb",
            "muted": "#d1d5db",
        }
    return {
        "bg": "#ffffff",
        "border": "#e5e7eb",
        "text": "#111827",
        "muted": "#4b5563",
    }


def _warmtenet_legend_items(
    meta: dict | None,
    woonplaatsen: list[str],
    allowed_keys: list[str] | None = None,
    allowed_types: list[str] | None = None,
) -> list[tuple[list[int], str, str]]:
    """Selecteer legenda-items voor warmtenet op basis van huidige woonplaatsen."""
    if not meta:
        return []
    color_map = meta.get("color_map") or {}
    label_meta = meta.get("labels") or {}
    allowed = {str(k).strip() for k in allowed_keys} if allowed_keys else None
    type_by_key = meta.get("type_by_key") or {}
    type_filter = (
        {str(t).strip().lower() for t in allowed_types} if allowed_types else None
    )
    wp_filter = {str(w).strip().lower() for w in woonplaatsen} if woonplaatsen else None
    items: list[tuple[list[int], str, str]] = []
    for key in sorted(label_meta.keys()):
        info = label_meta.get(key) or {}
        wp_norm = str(info.get("woonplaats_norm") or "").strip().lower()
        if wp_filter and wp_norm and wp_norm not in wp_filter:
            continue
        if allowed and key not in allowed:
            continue
        if type_filter:
            tb = str(type_by_key.get(key) or "").strip().lower()
            if tb not in type_filter:
                continue
        color = color_map.get(key)
        label = info.get("label") or key
        if color:
            items.append((color, label, key))
    return items


# ---------------------------
# Kleine helpers (RAM-zuinig)
# ---------------------------
def _fillna_categorical(
    df_in: pd.DataFrame, col: str, value: str = "Onbekend"
) -> pd.DataFrame:
    """Veilige NA -> 'Onbekend' voor categoricals zonder onnodige kopieën."""
    if col not in df_in.columns:
        return df_in
    s = df_in[col]
    try:
        from pandas.api.types import CategoricalDtype

        is_cat = isinstance(s.dtype, CategoricalDtype)
    except Exception:
        is_cat = False

    if is_cat:
        if value not in s.cat.categories:
            s = s.cat.add_categories([value])
        s = s.fillna(value)
    else:
        # cast naar category pas ná fill (voorkomt dubbele alloc)
        s = s.fillna(value).astype("category")
    df_in[col] = s
    return df_in


def _rgba_to_css(color: list[int]) -> str:
    """Zet [r,g,b,a] om naar rgba() string met alpha 0-1."""
    try:
        r, g, b, a = color
        alpha = round(float(a) / 255.0, 3)
        return f"rgba({int(r)},{int(g)},{int(b)},{alpha})"
    except Exception:
        return "rgba(220,220,220,0.6)"


def _render_big_legend(
    current_threshold_display: float,
    heat_unit: str,
    *,
    dark_mode: bool,
    show_threshold: bool,
    show_pandtype_legend: bool = False,
    pandtype_zoom_level: int | None = None,
):
    """Render de hoofdlegenda voor de warmtevraaglaag."""
    colors = _legend_theme_colors(dark_mode)
    bg = colors["bg"]
    border = colors["border"]
    text = colors["text"]
    muted = colors["muted"]
    unit_norm = (heat_unit or "").strip().lower()
    pot_color = "#144A3A"

    if unit_norm in ("mwh/ha", "mwh_per_ha", "mwh_ha"):
        labels = [
            "0-50 MWh/ha",
            "50-100 MWh/ha",
            "100-150 MWh/ha",
            "150-200 MWh/ha",
            "200-300 MWh/ha",
            "300-500 MWh/ha",
            "> 500 MWh/ha",
        ]
        info_items = [
            ("0-50 MWh/ha", "Niet haalbaar"),
            ("50-100 MWh/ha", "Niet haalbaar"),
            ("100-150 MWh/ha", "Lastig (indien genoeg warmte beschikbaar)"),
            ("150-200 MWh/ha", "Potentie"),
            ("200-300 MWh/ha", "Goed"),
            ("300-500 MWh/ha", "Heel goed"),
            ("> 500 MWh/ha", "Altijd doen"),
        ]
        info_text = "\n".join(f"- **{rng}**: {uitleg}" for rng, uitleg in info_items)
        legend_rows = [
            (_rgba_to_css(color), label) for color, label in zip(MWH_HA_COLORS, labels)
        ]
        if show_threshold:
            pot_label_value = current_threshold_display
            pot_label = f"Potentie grenswaarde: {format_dutch_number(pot_label_value, 0)} MWh/ha"
        else:
            pot_label = None
        title = "Warmtevraag (MWh/ha)"
    else:
        labels = [
            "0-50 kWh/m²",
            "50-100 kWh/m²",
            "100-150 kWh/m²",
            "150-200 kWh/m²",
            "> 200 kWh/m²",
        ]
        legend_rows = [
            (_rgba_to_css(color), label) for color, label in zip(KWH_M2_COLORS, labels)
        ]
        if show_threshold:
            pot_label_value = current_threshold_display
            pot_label = f"Potentie grenswaarde: {format_dutch_number(pot_label_value, 0)} kWh/m²"
        else:
            pot_label = None
        title = "Warmtevraag (kWh/m²)"
        info_text = "- Lagere warmtevraag naar hogere warmtevraag."

    legend_html_rows = "".join(
        f"<div><span class='color-box' style='background-color: {color};'></span> {label}</div>"
        for color, label in legend_rows
    )
    pandtype_html = ""
    letter_bg = "#ffffff" if not dark_mode else "#111827"
    if show_pandtype_legend:
        pandtype_html = "\n".join(
            [
                "<div class='legend-subtitle'>Type pand in hexagoon</div>",
                "<div><span class='letter-box'>A</span> Kleinverbruik</div>",
                "<div><span class='letter-box'>B</span> Middel- en grootverbruik</div>",
                "<div><span class='letter-box'>C</span> Klein-, middel- en grootverbruik</div>",
            ]
        )
    legend_html = f"""<!doctype html>
<html>
  <head>
    <style>
      html, body {{ margin: 0; padding: 0; }}
      .legend {{
        width: 100%;
        box-sizing: border-box;
        background: {bg}; padding: 10px; border-radius: 8px;
        font-family: Arial, sans-serif; font-size: 12px; color: {text};
        border: 1px solid {border}; margin-bottom: 0;
      }}
      .legend-title {{ font-weight: bold; margin-bottom: 10px; display: block; color:{text}; }}
      .color-box {{ width: 15px; height: 15px; display: inline-block; margin-right: 5px; border-radius:3px; border:1px solid {border}; }}
      .legend-subtitle {{ font-weight: 600; margin: 8px 0 6px 0; color: {text}; }}
      .letter-box {{
        width: 18px; height: 18px; display: inline-flex;
        align-items: center; justify-content: center; margin-right: 6px;
        border-radius: 3px; border: 1px solid {border};
        font-size: 11px; font-weight: 600; background: {letter_bg}; color: {text};
      }}
      .legend-text-muted {{ color: {muted}; }}
    </style>
  </head>
  <body>
    <div class="legend">
      <div class="legend-title">{title}</div>
      {legend_html_rows}
      {f"<div><span class='color-box' style='background-color: {pot_color};'></span> {pot_label}</div>" if pot_label else ""}
      {pandtype_html}
    </div>
  </body>
</html>
"""
    row_count = len(legend_rows) + (1 if pot_label else 0) + 1
    if show_pandtype_legend:
        row_count += 5
    legend_height = max(140, min(320, 40 + row_count * 18))
    components.html(legend_html, height=legend_height, scrolling=False)
    if unit_norm in ("mwh/ha", "mwh_per_ha", "mwh_ha"):
        with st.expander("Uitleg legenda"):
            st.caption(info_text)


# ---------------------------
# Hoofdfunctie
# ---------------------------
def _hex_to_rgb(color: str | None):
    if not color or not isinstance(color, str):
        return None
    c = color.strip()
    if c.startswith("#"):
        c = c[1:]
    if len(c) != 6:
        return None
    try:
        r = int(c[0:2], 16)
        g = int(c[2:4], 16)
        b = int(c[4:6], 16)
        return (r, g, b)
    except ValueError:
        return None


def _is_dark_color(color: str | None) -> bool:
    rgb = _hex_to_rgb(color)
    if not rgb:
        return False
    r, g, b = rgb
    brightness = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return brightness < 0.5


def build_sidebar(
    df_in: pd.DataFrame | None = None,
    potential_meta: dict | None = None,
    warmtenet_meta: dict | None = None,
    wegennet_meta: dict | None = None,
) -> Dict[str, Any]:
    """
    Bouwt de volledige sidebar en retourneert:
      - ui (dict met alle gekozen waarden)
    """
    # defaults + migratie van bestaande grenswaarde
    st.session_state.setdefault("heat_unit", "MWh/ha")
    if (
        "grenswaarde_input" in st.session_state
        and "grenswaarde_input_kwh" not in st.session_state
    ):
        st.session_state["grenswaarde_input_kwh"] = st.session_state.get(
            "grenswaarde_input", 250
        )
    st.session_state.setdefault("grenswaarde_input_kwh", 250)
    st.session_state.setdefault("grenswaarde_input", 100)  # backward compat
    st.session_state.setdefault("grenswaarde_input_mwhha", 550)
    st.session_state.setdefault("participatie", 80)
    st.session_state.setdefault("LAYER_CFG", LAYER_CFG)
    st.session_state.setdefault("BASEMAP_CFG", BASEMAP_CFG)

    ui: Dict[str, Any] = {}
    opacity_help = "0 = transparant (onderliggende lagen zichtbaar) | 1 = dekkend"

    participatie_kpi_slot = None

    with st.sidebar:
        dark_mode = _is_dark_mode()
        st.header("Opties")

        # ---------------- Kaart ----------------
        with st.expander("Kaart", expanded=True):

            ui["zoom_level"] = st.slider(
                "Selecteer zoomniveau", min_value=9, max_value=12, value=10
            )
            ui["resolution"] = get_dynamic_resolution(ui["zoom_level"])
            zoom_copy = ui["zoom_level"]
            zoom_to_width_km = {
                9: 17,
                10: 8,
                11: 4,
                12: 2,
            }
            approx_width_km = zoom_to_width_km.get(zoom_copy)
            if approx_width_km is not None:
                headline = (
                    f"Bij <b>zoomniveau {zoom_copy}</b> is de kaart bij eerste weergave ongeveer "
                    f"<b>{approx_width_km} km</b> breed."
                )
            else:
                headline = f"Bij <b>zoomniveau {zoom_copy}</b> zoom je verder in; gebruik scroll/pinch voor extra detail."
            st.markdown(
                f"<span style='font-size: 12px;'>{headline}</span>",
                unsafe_allow_html=True,
            )
            with st.expander("Uitleg over zoomniveau"):
                st.write(
                    "Het zoomniveau bepaalt hoeveel detail de kaart toont:\n"
                    "- **9–10:** Overzicht van buurten en industriegebieden in Friesland.\n"
                    "- **11–12:** Straatniveau met H3-resolutie 12 voor maximale details.\n\n"
                    "Elk zoomniveau heeft een vaste H3-resolutie. "
                    "Je kunt in- of uitzoomen voor visueel detail, maar de H3-resolutie blijft gelijk."
                )
            ui["extruded"] = st.toggle("3D Weergave", value=False, key="extruded")
            brt_default = st.session_state.get("use_brt_basemap", False)
            brt_enabled = st.toggle(
                "Toon BRT Achtergrondkaart",
                value=bool(brt_default),
                help="Gebruik de BRT Achtergrondkaart als achtergrondlaag.",
            )
            ui["use_brt_basemap"] = brt_enabled
            st.session_state["use_brt_basemap"] = brt_enabled

            map_theme = "dark" if dark_mode else "light"
            basemap_style = "brt" if brt_enabled else map_theme
            if brt_enabled:
                style_desc = BASEMAP_CFG.get("brt", {}).get("legend_html")
                if style_desc:
                    st.caption(style_desc)
            map_style_value = (
                BASEMAP_CFG.get("brt", {}).get("map_style")
                if brt_enabled
                else map_theme
            )
            ui["map_style"] = map_style_value
            st.session_state["map_style"] = map_style_value
            ui["basemap_style"] = basemap_style
            st.session_state["basemap_style"] = basemap_style

        # ---------------- Participatie ----------------
        with st.expander("Participatie", expanded=False):
            st.session_state.participatie = st.slider(
                "Deelnamegraad (0% = niemand sluit aan, 100% = iedereen sluit aan)",
                min_value=0,
                max_value=100,
                value=st.session_state.participatie,
                step=1,
                key="participatie_slider",
            )
            ui["participatie"] = st.session_state.participatie
            participatie_kpi_slot = st.container()

        # ---------------- Lagen ----------------
        with st.expander("Lagen", expanded=True):
            st.subheader("Warmtevraag")
            if st.session_state.pop("force_show_main_layer", False):
                st.session_state["show_main_layer"] = True
            ui["show_main_layer"] = st.toggle(
                "Gasverbruik", value=True, key="show_main_layer"
            )
            ui["show_indicative_layer"] = st.toggle(
                "Aandachtsgebieden tonen", value=False, key="show_indicative_layer"
            )
            ui["show_pandtype_labels"] = st.toggle(
                "Type pand tonen",
                value=st.session_state.get("show_pandtype_labels", False),
                key="show_pandtype_labels",
                disabled=not ui["show_main_layer"],
            )

            zoom_level = int(ui.get("zoom_level", 0))
            default_unit = "MWh/ha" if zoom_level <= 10 else "kWh/m²"
            st.session_state.setdefault("heat_unit_auto", True)
            if st.session_state["heat_unit_auto"]:
                st.session_state["heat_unit"] = default_unit

            heat_unit_options = ["MWh/ha", "kWh/m²"]
            heat_unit_labels = {
                "MWh/ha": "MWh/ha (grondoppervlakte)",
                "kWh/m²": "kWh/m² (gebruiksoppervlakte)",
            }
            heat_unit_default = st.session_state.get("heat_unit", default_unit)
            if heat_unit_default not in heat_unit_options:
                heat_unit_default = default_unit
                st.session_state["heat_unit"] = heat_unit_default
            heat_unit = st.radio(
                "Eenheid warmtevraag",
                options=heat_unit_options,
                format_func=lambda v: heat_unit_labels.get(v, v),
                horizontal=True,
                key="heat_unit",
                on_change=lambda: st.session_state.__setitem__("heat_unit_auto", False),
                help="Kies of de kaart kleurt op warmtevraag per hectare grondoppervlakte (MWh/ha) of op warmtevraag per m² gebruiksoppervlakte (kWh/m²).",
            )
            ui["heat_unit"] = heat_unit

            if heat_unit == "MWh/ha":
                min_threshold_display = int(MWH_HA_BREAKS[-1])
                default_mwhha = int(
                    float(st.session_state.get("grenswaarde_input_mwhha", 550))
                )
                if ui["show_indicative_layer"]:
                    input_key = "grenswaarde_input_mwhha_str"
                    raw_val = st.session_state.get(input_key)
                    if raw_val is None:
                        st.session_state[input_key] = format_dutch_number(
                            default_mwhha, 0
                        )
                    else:
                        parsed_val = parse_dutch_int(
                            str(raw_val), fallback=default_mwhha
                        )
                        if parsed_val < min_threshold_display:
                            parsed_val = min_threshold_display
                        formatted_val = format_dutch_number(parsed_val, 0)
                        if str(raw_val) != formatted_val:
                            st.session_state[input_key] = formatted_val
                    threshold_str = st.text_input(
                        "Stel de minimale grenswaarde (threshold) in per MWh/ha:",
                        key=input_key,
                    )
                    threshold_display_raw = parse_dutch_int(
                        threshold_str or "", fallback=default_mwhha
                    )
                else:
                    threshold_display_raw = default_mwhha
                threshold_display = max(threshold_display_raw, min_threshold_display)
                st.session_state["grenswaarde_input_mwhha"] = threshold_display
                threshold_kwh = float(threshold_display) / 10.0
            else:
                min_threshold_display = KWH_M2_BREAKS[-1]
                default_kwh = int(
                    float(st.session_state.get("grenswaarde_input_kwh", 250))
                )
                if ui["show_indicative_layer"]:
                    input_key = "grenswaarde_input_kwh_str"
                    raw_val = st.session_state.get(input_key)
                    if raw_val is None:
                        st.session_state[input_key] = format_dutch_number(
                            default_kwh, 0
                        )
                    else:
                        parsed_val = parse_dutch_int(str(raw_val), fallback=default_kwh)
                        if parsed_val < min_threshold_display:
                            parsed_val = min_threshold_display
                        formatted_val = format_dutch_number(parsed_val, 0)
                        if str(raw_val) != formatted_val:
                            st.session_state[input_key] = formatted_val
                    threshold_str = st.text_input(
                        "Stel de minimale grenswaarde (threshold) in per kWh/m²:",
                        key=input_key,
                    )
                    threshold_display_raw = parse_dutch_int(
                        threshold_str or "", fallback=default_kwh
                    )
                else:
                    threshold_display_raw = default_kwh
                threshold_display = max(threshold_display_raw, min_threshold_display)
                st.session_state["grenswaarde_input_kwh"] = threshold_display
                threshold_kwh = float(threshold_display)

            ui["threshold"] = threshold_kwh
            ui["threshold_display"] = threshold_display

            _render_big_legend(
                threshold_display,
                heat_unit,
                dark_mode=dark_mode,
                show_threshold=ui["show_indicative_layer"],
                show_pandtype_legend=ui["show_main_layer"]
                and ui.get("show_pandtype_labels", True),
                pandtype_zoom_level=zoom_level,
            )
            ui["warmte_hex_opacity"] = st.slider(
                "Transparantie warmtevraag-hexagonen",
                min_value=0.0,
                max_value=1.0,
                value=st.session_state.get("warmte_hex_opacity", 0.6),
                step=0.05,
                key="warmte_hex_opacity",
                help=opacity_help,
            )
            all_types_label = "Klein-, middel- en grootverbruik"
            dataset_df = dal_query({}, "dataset_options")
            typepand = [str(x) for x in dataset_df.get("Dataset", []) if str(x).strip()]
            typepand_opties = [all_types_label] + sorted(typepand)
            pand_selectie = st.selectbox(
                "Selecteer type pand:",
                options=typepand_opties,
                key="pand_selectie",
            )
            ui["pand_selectie"] = pand_selectie
            with st.expander("Uitleg over type pand"):
                st.write(
                    "Hier kun je een specifiek type pand (bron van de data) selecteren om alleen die gegevens op de kaart te tonen. "
                    "Standaard staan alle types aan.\n\n"
                    "**Beschikbare datalagen:**\n"
                    "- **Liander / Stedin** – Kleinverbruik (woningen)\n"
                    "- **Verrijkte BAG (TNO)** – Middel- tot grootverbruik (bedrijven)\n"
                    "- **Alliander** – Middel- tot grootverbruik (bedrijven)\n"
                )
            if ui["show_indicative_layer"]:
                with st.expander("Wat doet de grenswaarde?"):
                    st.write(
                        "*Pas de grenswaarde bovenin aan om te bepalen welk verbruik jij als grens van ‘extra aandacht’ ziet.*\n\n"
                        "De legenda en kaartkleuren passen mee met de gekozen eenheid. "
                        "De grenswaarde wordt toegepast in dezelfde eenheid en wordt ook gebruikt voor de aandachtsgebieden."
                    )

            # Model
            st.subheader("Potentiële warmtenetten")
            st.markdown("Warmtenetten op basis van")
            default_warmtenet_opacity = (warmtenet_meta or {}).get(
                "default_opacity", 0.85
            )
            default_wegennet_opacity = (wegennet_meta or {}).get("default_opacity", 0.8)
            show_warmtenet = st.toggle(
                "Warmtebronnen",
                value=False,
                key=LAYER_CFG["warmtenet_model"]["toggle_key"],
            )
            if show_warmtenet:
                with st.expander("Uitleg", expanded=False):
                    st.caption(
                        "**Warmtebronnen:** deze weergave laat zien hoe een warmtenet eruit "
                        "kan zien wanneer warmte vanuit een bron wordt verdeeld binnen "
                        "de woonplaats. Het model legt leidingen langs het wegennet en "
                        "kiest daarbij verbindingen met zo laag mogelijk kosten om de "
                        "warmtevraag te bedienen."
                    )
            show_wegennet = st.toggle(
                "Warmtevraag",
                value=False,
                key=LAYER_CFG["wegennet"]["toggle_key"],
            )
            ui["show_warmtenet_model"] = show_warmtenet
            ui["show_wegennet"] = show_wegennet
            if not (show_warmtenet and show_wegennet):
                min_zoom = int(LAYER_CFG.get("wegennet", {}).get("min_zoom", 11))
                st.warning(
                    "Schakel warmtebronnen en warmtevraag in om dekking (%), "
                    "onbenutte warmte (MWh) en kosten te bekijken. Alleen beschikbaar "
                    f"vanaf zoomniveau {min_zoom}."
                )
            if show_wegennet:
                with st.expander("Uitleg", expanded=False):
                    st.caption(
                        "**Warmtevraag:** deze weergave laat zien hoe een warmtenet eruit "
                        "zou zien wanneer alle panden binnen de woonplaats worden "
                        "aangesloten op basis van de warmtevraag. De leidingen volgen "
                        "het wegennet, maar zijn niet geoptimaliseerd op kosten of "
                        "haalbaarheid. Deze weergave geeft inzicht in wat er aanvullend "
                        "nodig zou zijn ten opzichte van de getoonde warmtebronnen."
                    )
            auto_blocked_water_pot = False
            water_pot_key = LAYER_CFG["water_potentie"]["toggle_key"]
            selected_gemeenten = st.session_state.get("gemeente_selectie", [])
            is_leeuwarden = any(
                str(g).strip().lower() == "leeuwarden" for g in selected_gemeenten
            )
            min_zoom = int(LAYER_CFG.get("wegennet", {}).get("min_zoom", 11))
            zoom_level = int(ui.get("zoom_level", 0))
            water_pot_blocked = (
                show_wegennet and is_leeuwarden and zoom_level >= min_zoom
            )
            if water_pot_blocked:
                if st.session_state.get(water_pot_key):
                    st.session_state[water_pot_key] = False
                    auto_blocked_water_pot = True
            if show_warmtenet:
                default_show_sources = bool(
                    st.session_state.get("warmtenet_show_sources", True)
                )
                default_show_objects = bool(
                    st.session_state.get("warmtenet_show_objects", True)
                )
                default_show_lines = bool(
                    st.session_state.get("warmtenet_show_lines", True)
                )
                st.markdown("**Onderdelen warmtebronnen**")
                show_sources = st.checkbox(
                    "Bronnen",
                    value=default_show_sources,
                    key="warmtenet_show_sources",
                )
                show_objects = st.checkbox(
                    "Objecten",
                    value=default_show_objects,
                    key="warmtenet_show_objects",
                )
                show_lines = st.checkbox(
                    "Leidingen",
                    value=default_show_lines,
                    key="warmtenet_show_lines",
                )
                ui["warmtenet_show_sources"] = show_sources
                ui["warmtenet_show_objects"] = show_objects
                ui["warmtenet_show_lines"] = show_lines

                model_wp_options = (
                    warmtenet_meta.get("woonplaatsen", []) if warmtenet_meta else []
                )
                selected_gemeenten = st.session_state.get("gemeente_selectie", [])
                if selected_gemeenten:
                    wp_df = dal_query(
                        {"gemeente": selected_gemeenten}, "options_woonplaats"
                    )
                    allowed_wp = {
                        str(w) for w in wp_df.get("woonplaats", []) if str(w).strip()
                    }
                    if allowed_wp:
                        model_wp_options = [
                            w for w in model_wp_options if w in allowed_wp
                        ]
                prev_model_wp = [
                    w
                    for w in st.session_state.get("warmtenet_wp_selectie", [])
                    if w in model_wp_options
                ]
                base_default = [
                    w
                    for w in st.session_state.get("woonplaats_selectie", [])
                    if w in model_wp_options
                ]
                sync_sig = tuple(base_default)
                if st.session_state.get("_warmtenet_wp_sync") != sync_sig:
                    st.session_state["warmtenet_wp_selectie"] = list(base_default)
                    st.session_state["_warmtenet_wp_sync"] = sync_sig
                if st.session_state.get("warmtenet_wp_selectie") is None:
                    st.session_state["warmtenet_wp_selectie"] = list(
                        prev_model_wp or base_default or model_wp_options
                    )
                model_wp_selectie = st.multiselect(
                    "Filter op woonplaats",
                    options=model_wp_options,
                    key="warmtenet_wp_selectie",
                )
                ui["warmtenet_wp_selectie"] = model_wp_selectie

                type_opts = warmtenet_meta.get("types", []) if warmtenet_meta else []
                prev_type_sel = [
                    t
                    for t in st.session_state.get("warmtenet_type_selectie", [])
                    if t in type_opts
                ]
                default_type_sel = prev_type_sel or type_opts
                type_selectie = st.multiselect(
                    "Filter op type bron:",
                    options=type_opts,
                    default=default_type_sel,
                )
                st.session_state["warmtenet_type_selectie"] = type_selectie
                ui["warmtenet_type_selectie"] = type_selectie

                legend_items_all = _warmtenet_legend_items(
                    warmtenet_meta,
                    model_wp_selectie,
                    None,
                    type_selectie,
                )
                filter_sig = (
                    tuple(sorted(model_wp_selectie)),
                    tuple(sorted(type_selectie)),
                )
                available_keys = [k for *_, k in legend_items_all]
                # default: bewaar vorige selectie, anders alles
                prev_sel = [
                    k
                    for k in st.session_state.get("warmtenet_selected_keys", [])
                    if k in available_keys
                ]
                if st.session_state.get("_warmtenet_last_filter") != filter_sig:
                    prev_sel = available_keys
                if not prev_sel:
                    prev_sel = available_keys
                st.session_state["_warmtenet_last_filter"] = filter_sig
                selected_keys = st.multiselect(
                    "Kies bronnen om te tonen:",
                    options=available_keys,
                    format_func=lambda k: next(
                        (lbl for _, lbl, key in legend_items_all if key == k), k
                    ),
                    default=prev_sel,
                )
                ui["warmtenet_selected_keys"] = selected_keys
                st.session_state["warmtenet_selected_keys"] = selected_keys
                ui["warmtenet_opacity"] = st.slider(
                    "Transparantie warmtenet uit warmtebron",
                    min_value=0.1,
                    max_value=1.0,
                    value=float(
                        st.session_state.get(
                            "warmtenet_opacity", default_warmtenet_opacity
                        )
                    ),
                    step=0.05,
                    key="warmtenet_opacity",
                    help=opacity_help,
                )
                legend_items = _warmtenet_legend_items(
                    warmtenet_meta,
                    model_wp_selectie,
                    selected_keys,
                    type_selectie,
                )
                if legend_items:
                    wp_by_key = (warmtenet_meta or {}).get("wp_by_key", {})
                    grouped_colors: list[list[int]] = []
                    grouped_labels: list[str] = []
                    seen_wp = []
                    for color, label, key in legend_items:
                        wp = wp_by_key.get(key, "")
                        if wp and wp not in seen_wp:
                            grouped_colors.append(None)
                            grouped_labels.append(f"<strong>{wp}</strong>")
                            seen_wp.append(wp)
                        grouped_colors.append(color)
                        grouped_labels.append(label)
                    legend_parts = []
                    if ui.get("warmtenet_show_sources", True):
                        legend_parts.append(
                            "<span style='color:#111;'>&#9679;</span> bron"
                        )
                    if ui.get("warmtenet_show_objects", True):
                        legend_parts.append(
                            "<span style='color:#111;'>&#9675;</span> object"
                        )
                    if ui.get("warmtenet_show_lines", True):
                        legend_parts.append(
                            "<span style='color:#444;'>───</span> leiding"
                        )
                    header_html = "<br>".join(legend_parts)
                    legend_title = LAYER_CFG["warmtenet_model"]["legend_title"]
                    if header_html:
                        legend_title = f"{legend_title}<br><span style='font-size:12px; display:block;'>{header_html}</span>"
                    render_mini_legend(
                        legend_title,
                        grouped_colors,
                        grouped_labels,
                        dark_mode=dark_mode,
                        footer_html="",
                    )
                else:
                    st.info("Geen warmtebronnen voor de huidige selectie.")
            else:
                ui["warmtenet_opacity"] = st.session_state.setdefault(
                    "warmtenet_opacity", default_warmtenet_opacity
                )
                ui["warmtenet_selected_keys"] = st.session_state.setdefault(
                    "warmtenet_selected_keys", []
                )
                ui["warmtenet_show_sources"] = st.session_state.setdefault(
                    "warmtenet_show_sources", True
                )
                ui["warmtenet_show_objects"] = st.session_state.setdefault(
                    "warmtenet_show_objects", True
                )
                ui["warmtenet_show_lines"] = st.session_state.setdefault(
                    "warmtenet_show_lines", True
                )

            if show_wegennet:
                zoom_level = int(ui.get("zoom_level", 0))
                min_zoom = int(LAYER_CFG.get("wegennet", {}).get("min_zoom", 11))
                st.markdown("**Onderdelen warmtevraag**")
                if zoom_level < min_zoom:
                    st.info(
                        f"Warmtenet op basis van warmtevraag wordt pas getoond vanaf zoomniveau {min_zoom}."
                    )
                else:
                    geselecteerde_gemeenten = st.session_state.get(
                        "gemeente_selectie", []
                    )
                    no_data_warning = True
                    if geselecteerde_gemeenten and len(geselecteerde_gemeenten) != 1:
                        st.warning(
                            "Wegennet wordt alleen getoond bij één gemeente. Ga naar Filters en kies één gemeente om deze laag te zien."
                        )
                        wp_options = []
                        no_data_warning = False
                    else:
                        gemeente_naam = (
                            geselecteerde_gemeenten[0]
                            if geselecteerde_gemeenten
                            else ""
                        )
                        wp_options = list_wegennet_woonplaatsen()
                        if not wp_options:
                            wegennet_path = resolve_wegennet_path(gemeente_naam)
                            wp_options = geojson_unique_props(
                                wegennet_path, "area_name"
                            )
                    if len(geselecteerde_gemeenten) == 1 and wp_options:
                        wp_df = dal_query(
                            {"gemeente": geselecteerde_gemeenten}, "options_woonplaats"
                        )
                        allowed_wp = {
                            str(w)
                            for w in wp_df.get("woonplaats", [])
                            if str(w).strip()
                        }
                        if allowed_wp:
                            allowed_norm = {
                                normalize_wegennet_name(w) for w in allowed_wp
                            }
                            wp_options = [
                                w
                                for w in wp_options
                                if normalize_wegennet_name(w) in allowed_norm
                            ]

                    if not wp_options:
                        if no_data_warning:
                            st.warning("Geen wegennetdata gevonden voor deze gemeente.")
                        st.session_state["wegennet_wp_selectie"] = []
                        ui["wegennet_wp_selectie"] = []
                        st.session_state.setdefault("wegennet_type_selectie", [])
                        ui["wegennet_type_selectie"] = st.session_state.get(
                            "wegennet_type_selectie", []
                        )
                        ui["wegennet_opacity"] = st.session_state.setdefault(
                            "wegennet_opacity", default_wegennet_opacity
                        )
                    else:
                        prev_wp = [
                            w
                            for w in st.session_state.get("wegennet_wp_selectie", [])
                            if w in wp_options
                        ]
                        option_by_norm = {
                            normalize_wegennet_name(w): w for w in wp_options
                        }
                        base_default = [
                            option_by_norm.get(normalize_wegennet_name(w))
                            for w in st.session_state.get("woonplaats_selectie", [])
                        ]
                        base_default = [w for w in base_default if w in wp_options]
                        sync_sig = tuple(base_default)
                        if st.session_state.get("_wegennet_wp_sync") != sync_sig:
                            st.session_state["wegennet_wp_selectie"] = list(
                                base_default
                            )
                            st.session_state["_wegennet_wp_sync"] = sync_sig
                        if st.session_state.get("wegennet_wp_selectie") is None:
                            st.session_state["wegennet_wp_selectie"] = list(
                                prev_wp or base_default or wp_options
                            )

                        def _format_woonplaats_label(value: str) -> str:
                            cleaned = value.replace("_", " ").strip()
                            if cleaned.islower():
                                return cleaned.title()
                            return cleaned

                        wp_selectie = st.multiselect(
                            "Filter op woonplaats",
                            options=wp_options,
                            key="wegennet_wp_selectie",
                            format_func=_format_woonplaats_label,
                        )
                        ui["wegennet_wp_selectie"] = wp_selectie

                        type_opts = (
                            wegennet_meta.get("types", []) if wegennet_meta else []
                        )
                        if not type_opts:
                            cfg_types = (
                                LAYER_CFG.get("wegennet", {}).get("type_labels", {})
                                or {}
                            ).keys()
                            type_opts = sorted({str(t).lower() for t in cfg_types})
                        prev_type_sel = [
                            t
                            for t in st.session_state.get("wegennet_type_selectie", [])
                            if t in type_opts
                        ]
                        default_type_sel = prev_type_sel or type_opts
                        type_labels = (wegennet_meta or {}).get("type_labels", {})
                        type_label_map = {
                            str(k).lower(): v for k, v in type_labels.items()
                        }
                        type_selectie = st.multiselect(
                            "Filter op type:",
                            options=type_opts,
                            default=default_type_sel,
                            format_func=lambda v: type_label_map.get(str(v).lower(), v),
                        )
                        st.session_state["wegennet_type_selectie"] = type_selectie
                        ui["wegennet_type_selectie"] = type_selectie

                        ui["wegennet_opacity"] = st.slider(
                            "Transparantie warmtenet uit warmtevraag",
                            min_value=0.1,
                            max_value=1.0,
                            value=float(
                                st.session_state.get(
                                    "wegennet_opacity", default_wegennet_opacity
                                )
                            ),
                            step=0.05,
                            key="wegennet_opacity",
                            help=opacity_help,
                        )

                        cfg_wegennet = LAYER_CFG.get("wegennet", {})
                        type_colors = cfg_wegennet.get("type_colors", {})
                        if type_colors and type_opts:
                            legend_colors = [
                                type_colors.get(
                                    t,
                                    cfg_wegennet.get(
                                        "default_color", [120, 120, 120, 200]
                                    ),
                                )
                                for t in type_opts
                            ]
                            legend_labels = [
                                type_label_map.get(str(t).lower(), t) for t in type_opts
                            ]
                            render_mini_legend(
                                cfg_wegennet.get("legend_title", "Wegennet"),
                                legend_colors,
                                legend_labels,
                                dark_mode=dark_mode,
                                footer_html="",
                            )
            else:
                ui["wegennet_opacity"] = st.session_state.setdefault(
                    "wegennet_opacity", default_wegennet_opacity
                )
                ui["wegennet_wp_selectie"] = st.session_state.setdefault(
                    "wegennet_wp_selectie", []
                )
                ui["wegennet_type_selectie"] = st.session_state.setdefault(
                    "wegennet_type_selectie", []
                )

            # Potentielagen
            st.subheader("Aquathermie potentielagen EXTRAQT")
            pot_meta = potential_meta or {}

            def _potentie_footer_html():
                logo_path = Path("assets/logo") / "Logo EXTRAQT black.png"
                if not logo_path.exists():
                    return "Bron: EXTRAQT"
                try:
                    b64_logo = base64.b64encode(logo_path.read_bytes()).decode("ascii")
                except Exception:
                    return "Bron: EXTRAQT"
                return f"Bron: <img src='data:image/png;base64,{b64_logo}' alt='EXTRAQT logo' />"

            show_water_pot = st.toggle(
                "Waterlichamen potentie",
                value=False,
                key=LAYER_CFG["water_potentie"]["toggle_key"],
                disabled=water_pot_blocked,
            )
            ui["show_water_potentie"] = show_water_pot
            if auto_blocked_water_pot or water_pot_blocked:
                st.warning(
                    "Het warmtenet op basis van de warmtevraag van gemeente Leeuwarden "
                    "is te zwaar om tegelijk met de waterpotentielaag te gebruiken. "
                    "Schakel het warmtenet uit om de waterpotentielaag te tonen."
                )
            default_water_opacity = pot_meta.get("water_potentie", {}).get(
                "default_opacity", 0.7
            )
            if show_water_pot:
                ui["water_potentie_opacity"] = st.slider(
                    "Transparantie waterpotentie",
                    min_value=0.1,
                    max_value=1.0,
                    value=float(
                        st.session_state.get(
                            "water_potentie_opacity", default_water_opacity
                        )
                    ),
                    step=0.05,
                    key="water_potentie_opacity",
                    help=opacity_help,
                )
                meta = pot_meta.get("water_potentie")
                if meta and meta.get("labels"):
                    render_mini_legend(
                        LAYER_CFG["water_potentie"]["legend_title"],
                        meta.get("colors", []),
                        meta.get("labels", []),
                        dark_mode=dark_mode,
                        footer_html=_potentie_footer_html(),
                    )
            else:
                ui["water_potentie_opacity"] = st.session_state.setdefault(
                    "water_potentie_opacity", default_water_opacity
                )

            show_buurt_pot = st.toggle(
                "Buurtpotentie uit aquathermie",
                value=False,
                key=LAYER_CFG["buurt_potentie"]["toggle_key"],
            )
            ui["show_buurt_potentie"] = show_buurt_pot
            default_buurt_opacity = pot_meta.get("buurt_potentie", {}).get(
                "default_opacity", 0.7
            )
            if show_buurt_pot:
                ui["buurt_potentie_opacity"] = st.slider(
                    "Transparantie buurtpotentie uit aquathermie",
                    min_value=0.1,
                    max_value=1.0,
                    value=float(
                        st.session_state.get(
                            "buurt_potentie_opacity", default_buurt_opacity
                        )
                    ),
                    step=0.05,
                    key="buurt_potentie_opacity",
                    help=opacity_help,
                )
                meta = pot_meta.get("buurt_potentie")
                if meta and meta.get("labels"):
                    render_mini_legend(
                        LAYER_CFG["buurt_potentie"]["legend_title"],
                        meta.get("colors", []),
                        meta.get("labels", []),
                        dark_mode=dark_mode,
                        footer_html=_potentie_footer_html(),
                    )
            else:
                ui["buurt_potentie_opacity"] = st.session_state.setdefault(
                    "buurt_potentie_opacity", default_buurt_opacity
                )

            # Woonlagen + mini-legenda's
            st.subheader("Sociale indicatoren")
            show_energiearmoede = st.toggle(
                "Energiearmoede",
                value=False,
                key=LAYER_CFG["energiearmoede"]["toggle_key"],
            )
            if show_energiearmoede:
                c = LAYER_CFG["energiearmoede"]
                colors = get_layer_colors(c)
                labels = legend_labels_from_breaks(c["breaks"])
                render_mini_legend(
                    c["legend_title"],
                    colors,
                    labels,
                    dark_mode=dark_mode,
                    footer_html="Bron: DataFryslân (2022)",
                )

            show_koopwoningen = st.toggle(
                "Koopwoningen", value=False, key=LAYER_CFG["koopwoningen"]["toggle_key"]
            )
            if show_koopwoningen:
                c = LAYER_CFG["koopwoningen"]
                colors = get_layer_colors(c)
                labels_kw = legend_labels_from_breaks(c["breaks"])
                render_mini_legend(
                    c["legend_title"],
                    colors,
                    labels_kw,
                    dark_mode=dark_mode,
                    footer_html="Bron: CBS (2023)",
                )

            show_corporatie = st.toggle(
                "Wooncorporatie",
                value=False,
                key=LAYER_CFG["wooncorporatie"]["toggle_key"],
            )
            if show_corporatie:
                c = LAYER_CFG["wooncorporatie"]
                colors = get_layer_colors(c)
                labels_wc = legend_labels_from_breaks(c["breaks"])
                render_mini_legend(
                    c["legend_title"],
                    colors,
                    labels_wc,
                    dark_mode=dark_mode,
                    footer_html="Bron: CBS (2023)",
                )

            if show_energiearmoede or show_koopwoningen or show_corporatie:
                ui["extra_opacity"] = st.slider(
                    "Transparantie woonlagen",
                    min_value=0.1,
                    max_value=1.0,
                    value=st.session_state.get("extra_opacity", 0.55),
                    key="extra_opacity",
                    help=opacity_help,
                )
            else:
                ui["extra_opacity"] = st.session_state.setdefault("extra_opacity", 0.55)

        # ---------------- Filters ----------------
        df_filtered = None
        with st.expander("Filters", expanded=False):
            st.subheader("Gemeente")
            gemeente_df = dal_query({}, "options_gemeente")
            gemeente_opties = sorted(
                [
                    str(x).strip()
                    for x in gemeente_df.get("gemeentenaam", [])
                    if str(x).strip()
                ]
            )
            prev_gemeente_selectie = st.session_state.get("_prev_gemeente_selectie", [])
            gemeente_default = [
                g for g in prev_gemeente_selectie if g in gemeente_opties
            ]
            if not gemeente_default:
                if "Leeuwarden" in gemeente_opties:
                    gemeente_default = ["Leeuwarden"]
                elif gemeente_opties:
                    gemeente_default = [gemeente_opties[0]]
                else:
                    gemeente_default = []

            if ui["zoom_level"] <= 10:
                _ = st.multiselect(
                    "Filter op gemeente:",
                    options=gemeente_opties,
                    default=gemeente_default,
                    key="gemeente_selectie",
                    disabled=True,
                    help="Gemeentefilter is beschikbaar vanaf zoomniveau 11.",
                )
                gemeente_selectie = gemeente_default
                gemeente_changed = False
            else:
                gemeente_selectie = st.multiselect(
                    "Filter op gemeente:",
                    options=gemeente_opties,
                    default=gemeente_default,
                    key="gemeente_selectie",
                )
                if not gemeente_selectie:
                    st.warning("Selecteer minimaal één gemeente.")
                    gemeente_selectie = gemeente_default or gemeente_opties
                prev_gemeente_set = set(prev_gemeente_selectie or [])
                current_gemeente_set = set(gemeente_selectie)
                gemeente_changed = current_gemeente_set != prev_gemeente_set
                st.session_state["_prev_gemeente_selectie"] = gemeente_selectie

            if ui["zoom_level"] <= 10:
                ui["gemeente_selectie"] = []
            else:
                ui["gemeente_selectie"] = gemeente_selectie

            st.subheader("Woonplaats")
            wp_filters = (
                {"gemeente": gemeente_selectie} if ui["zoom_level"] > 10 else {}
            )
            wp_df = dal_query(wp_filters, "options_woonplaats")
            woonplaatsen_sorted = sorted(
                [str(x).strip() for x in wp_df.get("woonplaats", []) if str(x).strip()]
            )

            if 1 <= ui["zoom_level"] <= 10:
                _ = st.multiselect(
                    "Filter op woonplaats:",
                    options=woonplaatsen_sorted,
                    default=woonplaatsen_sorted,
                    disabled=True,
                    help="Woonplaatsfilter is beschikbaar vanaf zoomniveau 11.",
                )
                woonplaats_selectie = woonplaatsen_sorted
            else:
                prev_wp = st.session_state.get("woonplaats_selectie", [])
                prev_wp_filtered = [wp for wp in prev_wp if wp in woonplaatsen_sorted]
                if gemeente_changed and woonplaatsen_sorted:
                    default_wp = woonplaatsen_sorted
                else:
                    default_wp = prev_wp_filtered
                if not default_wp:
                    default_wp = woonplaatsen_sorted or ["Leeuwarden"]
                woonplaats_selectie = st.multiselect(
                    "Filter op woonplaats:",
                    options=woonplaatsen_sorted,
                    default=default_wp,
                )
                if not woonplaats_selectie:
                    st.warning("Selecteer minimaal één woonplaats.")
                    woonplaats_selectie = woonplaatsen_sorted or ["Leeuwarden"]

            ui["woonplaats_selectie"] = woonplaats_selectie
            st.session_state["woonplaats_selectie"] = woonplaats_selectie

            st.subheader("Energieklasse")
            en_filters = {
                "gemeente": gemeente_selectie if ui["zoom_level"] > 10 else [],
                "woonplaats": (
                    woonplaats_selectie
                    if ui["zoom_level"] > 10
                    else woonplaatsen_sorted
                ),
            }
            energie_df = dal_query(en_filters, "options_energieklasse")
            energie_series = energie_df.get("Energieklasse", [])
            energieklassen = [str(x).strip() for x in energie_series if str(x).strip()]
            energieklasse_selectie = st.multiselect(
                "Filter op energieklasse:",
                options=energieklassen,
                default=energieklassen,
            )
            if not energieklasse_selectie:
                energieklasse_selectie = energieklassen
            ui["energieklasse_selectie"] = energieklasse_selectie

            st.subheader("Bouwjaar")
            bouwjaar_filters = {
                "gemeente": gemeente_selectie if ui["zoom_level"] > 10 else [],
                "woonplaats": (
                    woonplaats_selectie
                    if ui["zoom_level"] > 10
                    else woonplaatsen_sorted
                ),
                "energieklasse": energieklasse_selectie,
            }
            bouwjaar_df = dal_query(bouwjaar_filters, "bouwjaar_range")
            try:
                min_year_val = bouwjaar_df["min_year"].iloc[0]
            except Exception:
                min_year_val = None
            try:
                max_year_val = bouwjaar_df["max_year"].iloc[0]
            except Exception:
                max_year_val = None
            try:
                min_year = int(min_year_val)
            except Exception:
                min_year = 1900
            try:
                max_year = int(max_year_val)
            except Exception:
                max_year = 2025
            if min_year > max_year:
                min_year, max_year = 1900, 2025
            by_lo, by_hi = st.slider(
                "Filter op bouwjaar:", min_year, max_year, (min_year, max_year)
            )
            ui["bouwjaar_range"] = (by_lo, by_hi)

            pand_selectie = ui.get("pand_selectie", "Klein-, middel- en grootverbruik")
            if pand_selectie != "Klein-, middel- en grootverbruik":
                ui["pand_selectie"] = pand_selectie
        if participatie_kpi_slot is not None:
            kpi_filters = {
                "gemeente": gemeente_selectie if ui["zoom_level"] > 10 else [],
                "woonplaats": (
                    woonplaats_selectie
                    if ui["zoom_level"] > 10
                    else woonplaatsen_sorted
                ),
                "energieklasse": energieklasse_selectie,
                "bouwjaar_range": ui.get("bouwjaar_range"),
                "pand_selectie": ui.get("pand_selectie"),
            }
            summary_df = dal_query(kpi_filters, "woonplaats_summary")
            if summary_df.empty:
                df_kpi = pd.DataFrame(columns=["aantal_huizen", "sum_mwh_raw"])
            else:
                df_kpi = summary_df.rename(columns={"MWh": "sum_mwh_raw"})
            with participatie_kpi_slot:
                render_participation_kpis(df_kpi, ui["participatie"])

        # ---------------- Warmtevraag hotspots ----------------
        selected_places_prior = ui.get("woonplaats_selectie") or st.session_state.get(
            "woonplaats_selectie", []
        )
        can_analyse = (ui["zoom_level"] >= 11) and bool(selected_places_prior)
        info_html = "<p style='font-size:12px; color:#6b7280; margin-bottom:8px;'>Warmtevraag hotspots beschikbaar vanaf zoomniveau 11.</p>"

        with st.expander("Warmtevraag-hotspots", expanded=False):
            default_site_opacity = st.session_state.get("sites_hex_opacity", 0.85)
            compute_sites = False
            reset_manual = False
            if not can_analyse:
                st.markdown(info_html, unsafe_allow_html=True)
                ui["show_sites_layer"] = False
                st.session_state["show_sites_layer"] = False
                ui["sites_hex_opacity"] = default_site_opacity
            else:
                st.markdown(info_html, unsafe_allow_html=True)
                ui["show_sites_layer"] = st.toggle(
                    "Warmte-hotspots", value=False, key="show_sites_layer"
                )
                if ui["show_sites_layer"] and not ui.get("show_main_layer"):
                    st.session_state["force_show_main_layer"] = True
                    ui["show_main_layer"] = True

                if ui["show_sites_layer"]:
                    mode_options = {
                        "auto": "Automatisch berekenen (hoogste MWh)",
                        "manual": "Handmatig kiezen op de kaart",
                    }
                    default_mode = st.session_state.get("sites_mode", "auto")
                    if default_mode not in mode_options:
                        default_mode = "auto"
                    ui["sites_mode"] = st.radio(
                        "Kies de methode",
                        options=list(mode_options.keys()),
                        index=list(mode_options.keys()).index(default_mode),
                        format_func=lambda key: mode_options[key],
                        key="sites_mode",
                    )

                    if ui["sites_mode"] == "auto":
                        if not st.session_state.get("sites_ready"):
                            st.info(
                                "Stel eerst de filters in en zorg dat de kaart zichtbaar is om warmte-hotspots te tonen."
                            )
                        compute_sites = st.button(
                            "Bereken warmte-hotspots", key="compute_sites_button"
                        )
                    else:
                        st.info(
                            "Klik op een hexagon in de kaart om deze als startpunt voor de warmte-hotspot te gebruiken."
                        )
                        reset_manual = st.button(
                            "Wis handmatige selectie", key="reset_manual_site"
                        )
                        current_manual = st.session_state.get("manual_site_h3")
                        st.caption(
                            f"Geselecteerde H3-index: `{current_manual or 'geen'}`"
                        )

                    ui["sites_hex_opacity"] = st.slider(
                        "Transparantie warmte-hotspot",
                        min_value=0.0,
                        max_value=1.0,
                        value=float(
                            st.session_state.get(
                                "sites_hex_opacity", default_site_opacity
                            )
                        ),
                        step=0.05,
                        key="sites_hex_opacity",
                        help=opacity_help,
                    )

                    max_k_ring = 10 if ui["sites_mode"] == "manual" else 5
                    prev_kring = int(st.session_state.get("kring_radius", 3))
                    if prev_kring > max_k_ring:
                        prev_kring = max_k_ring
                        st.session_state.kring_radius = prev_kring
                    if prev_kring < 1:
                        prev_kring = 1
                        st.session_state.kring_radius = prev_kring
                    ui["kring_radius"] = st.slider(
                        "Bereik van de warmte-hotspots",
                        1,
                        max_k_ring,
                        prev_kring,
                        1,
                        key="kring_radius",
                    )

                    if ui["sites_mode"] == "auto":
                        prev_min_sep = int(st.session_state.get("min_sep", 3))
                        if prev_min_sep > 5:
                            prev_min_sep = 5
                            st.session_state.min_sep = prev_min_sep
                        ui["min_sep"] = st.slider(
                            "Minimale afstand tussen warmte-hotspots",
                            1,
                            5,
                            prev_min_sep,
                            1,
                            key="min_sep",
                        )
                        prev_n_sites = int(st.session_state.get("n_sites", 10))
                        if prev_n_sites > 20:
                            prev_n_sites = 20
                            st.session_state.n_sites = prev_n_sites
                        ui["n_sites"] = st.number_input(
                            "Aantal warmte-hotspots",
                            min_value=1,
                            max_value=10,
                            value=prev_n_sites,
                            step=1,
                            key="n_sites",
                        )
                    else:
                        ui["min_sep"] = int(st.session_state.get("min_sep", 3))
                        ui["n_sites"] = int(st.session_state.get("n_sites", 1))

                    ui["cap_mwh"] = text_input_int(
                        "Capaciteit per voorziening (MWh)",
                        key="cap_mwh",
                        default=50_000,
                    )
                    ui["cap_buildings"] = text_input_int(
                        "Maximaal aantal panden per voorziening",
                        key="cap_buildings",
                        default=1_000,
                    )

            ui["compute_sites"] = compute_sites
            ui["reset_manual_site"] = reset_manual
            if "sites_mode" not in ui:
                ui["sites_mode"] = st.session_state.get("sites_mode", "auto")
            if "sites_hex_opacity" not in ui:
                ui["sites_hex_opacity"] = default_site_opacity

        # ---------------- Rapport samenstellen ----------------
        report_slot_container = st.container()
        ui["report_slot_container"] = report_slot_container

        # ---------------- Uitleg-blokken ----------------
        st.header("Uitleg")
        with st.expander("Uitleg H3", expanded=False):
            st.write(
                "H3 is een hexagonaal raster dat gebieden verdeelt in zeshoeken van verschillende resoluties. "
                "Elke hexagoon krijgt een unieke ID en bevat gegevens over de warmtebehoefte."
            )

        with st.expander("Warmtevraag-hotspots", expanded=False):
            st.markdown(
                """\
**Doel van de analyse**  
De analyse laat zien waar een **collectieve warmtevoorziening** (zoals een warmtenet) kansrijk kan zijn. Dit gebeurt door te kijken hoeveel gasverbruik en gebouwen er binnen de directe omgeving van een mogelijke locatie liggen en of deze plek past binnen de capaciteit van een potentiële bron.

**Werkwijze in hoofdlijnen**  
1. Rondom een centrale plek wordt gekeken naar omliggende buurten in de vorm van zeshoekige vakjes (hexagonen). De straal bepaalt hoe groot de omgeving is die wordt meegenomen.  
2. Per locatie wordt berekend hoeveel gasverbruik en hoeveel gebouwen in die omgeving aanwezig zijn. Daarbij geldt een maximum: een voorziening kan maar een bepaalde hoeveelheid warmte leveren en een maximum aantal gebouwen aansluiten.  
3. Alle locaties worden gerangschikt op de hoeveelheid warmte die daadwerkelijk kan worden aangesloten.  
4. Vervolgens worden de beste locaties geselecteerd, met een minimale onderlinge afstand zodat voorzieningen niet te dicht bij elkaar liggen.  

**Begrippen**  
- **k-ring:** de directe omgeving van een plek, gemeten in stappen van hexagonen. Hoe hoger de waarde, hoe verder de omgeving reikt.  
- **Minimale afstand tussen voorzieningen:** de onderlinge ruimte die wordt aangehouden, zodat meerdere voorzieningen niet op (bijna) dezelfde plek terechtkomen.  

**Verschil tussen deze twee**  
- De *k-ring* bepaalt hoe ver je kijkt om het totale gasverbruik en aantal gebouwen rond één plek te bepalen (de invloedsstraal van een plek).  
- De *minimale afstand* zorgt ervoor dat twee geselecteerde voorzieningen niet te dicht naast elkaar komen te liggen (de spreiding tussen verschillende plekken).  

**K-ring in de praktijk (k = 1 t/m 5)**  
- **k = 1** – directe buren, circa 7 hexagonen. Denk aan een cluster van enkele panden binnen ~200 meter.  
- **k = 3** – kleine buurt, ±37 hexagonen. Bestrijkt ongeveer een paar straten.  
- **k = 5** – grotere buurt, ±91 hexagonen. Omvat een deelwijk of bedrijventerrein van enkele hectares.  
"""
            )

    return ui


def render_report_section(
    ui: Dict[str, Any], container: Any | None = None
) -> Dict[str, Any]:
    """Render the report upload/download section."""
    target = container if container is not None else st.sidebar
    with target:
        # ---------------- Rapport samenstellen ----------------
        with st.expander("Rapport samenstellen", expanded=False):

            def _cleanup_report_file():
                report_path = st.session_state.get("report_pdf_path")
                if report_path:
                    try:
                        Path(report_path).unlink()
                    except FileNotFoundError:
                        pass
                    except Exception:
                        pass
                st.session_state["report_pdf_path"] = None

            def _clear_report_cache():
                _cleanup_report_file()
                st.session_state["report_filename"] = None
                st.session_state["report_requested"] = False
                st.session_state["report_map_image_error"] = None

            quality_labels = {
                200: "Licht (A4, 200 dpi)",
                300: "Standaard (A4, 300 dpi)",
            }
            dpi_options = [200, 300]
            selected_dpi = st.selectbox(
                "PDF kwaliteit",
                options=dpi_options,
                index=1,
                format_func=lambda v: quality_labels[v],
                key="report_dpi",
                on_change=_clear_report_cache,
            )
            ui["report_dpi"] = int(selected_dpi)
            st.write(
                "1. Zet de kaart in de gewenste weergave.\n"
                "2. Maak een screenshot van de kaart.\n"
                "3. Upload de screenshot om deze toe te voegen aan het PDF-rapport."
            )
            st.markdown(
                """
                <style>
                [data-testid="stFileUploaderDropzone"] {
                  border: none;
                  background: transparent;
                  padding: 0;
                }
                [data-testid="stFileUploaderDropzone"] > div {
                  padding: 0;
                }
                [data-testid="stFileUploaderDropzoneInstructions"] {
                  display: none;
                }
                [data-testid="stFileUploaderDropzone"] button {
                  font-size: 0;
                }
                [data-testid="stFileUploaderDropzone"] button::after {
                  content: "Afbeelding uploaden";
                  font-size: 16px;
                  font-weight: 600;
                }
                [data-testid="stFileUploaderFile"] button {
                  font-size: 0;
                }
                [data-testid="stFileUploaderFile"] button::after {
                  content: "";
                }
                </style>
                """,
                unsafe_allow_html=True,
            )
            upload_key = int(st.session_state.get("report_upload_key", 0))
            uploaded = st.file_uploader(
                "Kaartafbeelding (PNG)",
                type=["png"],
                key=f"report_map_upload_{upload_key}",
                label_visibility="collapsed",
            )

            def _cleanup_report_map_image():
                map_path = st.session_state.get("report_map_image_path")
                if map_path:
                    try:
                        Path(map_path).unlink()
                    except FileNotFoundError:
                        pass
                    except Exception:
                        pass
                st.session_state["report_map_image_path"] = None

            if uploaded is not None:
                uploaded_bytes = uploaded.getvalue()
                upload_sig = (uploaded.name, len(uploaded_bytes))
                if st.session_state.get("report_map_image_sig") != upload_sig:
                    _cleanup_report_map_image()
                    suffix = Path(uploaded.name).suffix or ".png"
                    st.session_state["report_map_image_path"] = write_bytes_to_tempfile(
                        uploaded_bytes,
                        suffix=suffix,
                    )
                    st.session_state["report_map_image_name"] = uploaded.name
                    st.session_state["report_map_image_sig"] = upload_sig
                    st.session_state["report_image_uploaded"] = True
                    st.session_state["show_map"] = False
                    st.session_state["_map_changed"] = True
                    st.session_state["sites_ready"] = False
                    st.session_state.pop("main_map_deck_chart", None)
                    st.session_state.pop("main_map_deck_chart_selected_data", None)
                    _clear_report_cache()
                del uploaded_bytes
            elif st.session_state.get("report_map_image_sig"):
                _cleanup_report_map_image()
                st.session_state["report_map_image_name"] = None
                st.session_state["report_map_image_sig"] = None
                st.session_state["report_image_uploaded"] = False
                st.session_state["report_upload_key"] = upload_key + 1
                _clear_report_cache()
            map_image = st.session_state.get("report_map_image_path")
            map_image_name = (
                st.session_state.get("report_map_image_name") or "kaart.png"
            )
            report_image_error = st.session_state.get("report_map_image_error")
            if report_image_error:
                st.error(report_image_error)
            if map_image and Path(map_image).exists():
                st.caption(f"Afbeelding klaar: {map_image_name}")
                st.image(map_image, caption="Preview kaart", width="stretch")
                if st.button("Verwijder kaartafbeelding", key="report_map_clear"):
                    _cleanup_report_map_image()
                    st.session_state["report_map_image_name"] = None
                    st.session_state["report_map_image_sig"] = None
                    st.session_state["report_image_uploaded"] = False
                    st.session_state["report_upload_key"] = upload_key + 1
                    _clear_report_cache()
                    st.rerun()
            report_slot = st.container()
            ui["report_slot"] = report_slot

    return ui
