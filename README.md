# Warmtekaart
## Doel
Kaarttool voor warmtevraag en potentiele warmtenetten in Friesland.
De code is gericht op performance en kaartinteractie via PyDeck.

## Snel starten
- Run: streamlit run app.py
- Data: data/ (CSV/Parquet + GeoJSON lagen)

## Belangrijkste bestanden
- app.py: hoofdflow van de app (load -> UI -> berekenen -> render).
- ui/sidebar.py: alle UI controls, filters en toggles.
- core/dal.py: DuckDB data access (filters/aggregaties tegen data.parquet).
- core/io.py: data/geojson loaders + caching helpers.
- core/layers.py: opbouw van PyDeck lagen + GeoJSON conversie + laag-meta helpers.
- core/utils.py: helpers voor kleur, tooltip, formatting.
- core/h3sites.py: logica voor warmtenet-analyse (warmte hotspots).
- core/h3agg.py: H3 aggregaties en groepering van data.
- core/map_data.py: site records en H3-hulpfuncties.
- core/report.py: PDF-rapportage (samenvatting, tabellen, kaartpagina).
- core/woonplaats.py: woonplaats-aggregaties en oppervlakte uit geopackage.
- data/scripts/geojson.py: hulpscript voor GeoJSON bewerkingen/conversie.

## Waar pas je wat aan?
**App-titel en introductietekst**
- app.py: st.set_page_config(...) en de st.markdown(...) header.

**Tooltip labels/velden per laag**
- core/layers.py: in de create_*_layers functies.

**Kleuren van lagen en legenda**
- core/config.py: (`LAYER_CFG`)
- core/utils.py: (kleurhelpers)

**Data paden + defaults**
- core/config.py: bij *_PATH
- core/config.py: WOONPLAATS_GPKG_PATH / WARMTE_LYR_WOONPLAATSEN
- core/config.py: WOONPLAATS_AREA_PATH / WARMTE_LYR_WOONPLAATSEN_AREA

**DuckDB / DAL (filters & aggregaties)**
- core/dal.py: dal_query(...) en get_con() (view op data.parquet)

**GeoJSON precisie en payload**
- core/io.py: (load_geojson, coord_precision)

**Warmtenet hotspots**
- core/h3sites.py

**H3 aggregaties/rollups**
- core/h3agg.py: (engine)
- core/map_data.py: site records en H3 helpers

**UI filters/toggles/teksten**
- ui/sidebar.py

**KPI tabellen en data tabel overzicht**
- ui/kpis_and_tables.py

**Rapportage**
- core/report.py: PDF-rapportage opbouwen.
- ui/sidebar.py: upload/knoppen voor rapportage.

## Structuur per map
**core/**
- `config.py`: centrale configuratie (paden, kleuren, layer-metadata).
- `dal.py`: DuckDB data-access layer (SQL filters/aggregaties).
- `io.py`: inlezen/cachen van GeoJSON en tabellen.
- `layers.py`: PyDeck lagen + tooltip opbouw + GeoJSON conversie.
- `utils.py`: algemene helpers (kleur, formatting, tooltip snippets).
- `h3agg.py`: pure H3 aggregaties (snel en herbruikbaar).
- `map_data.py`: bouwt de map-dataframes voor de kaart (filter/rollup/tooltip data).
- `h3sites.py`: warmtenet selectie/cluster-logica.
- `report.py`: PDF-rapportage generator.
- `woonplaats.py`: woonplaats totalen + oppervlakte uit geopackage.

**ui/**
- `sidebar.py`: alle filters, toggles en waarschuwingen.
- `kpis_and_tables.py`: KPI kaarten en tabellen onder de kaart.

**data/scripts/**
- `geojson.py`: CRS-conversie + GeoJSON comprimeren.
- `parquet_conv.py`: CSV -> Parquet en basis afgeleide velden.
- `shrink_dataset.py`: maak compacte parquet met juiste dtypes.
- `woonplaats_area_export.py`: schrijf woonplaats_area.csv of .parquet uit de gpkg.

**app.py**
- Orchestrator: laadt data, leest UI, berekent aggregaties, bouwt lagen en rendert.

## Dataflow (hoog niveau)
1) Data en lagen laden.
2) Sidebar bepaalt filters/toggles en schrijft naar `st.session_state`.
3) DAL (DuckDB) voert filters/aggregaties uit tegen `data.parquet`.
4) (Optioneel) warmtenet/hotspot-analyse wordt gebouwd.
5) PyDeck lagen + tooltip samenstellen en renderen.

## Woonplaats logica
- Tabellen en rapporten gebruiken woonplaats totalen uit puntdata (stabiel over zoom).
- Oppervlakte per woonplaats komt uit `data/layers/woonplaats_area.csv` (voorbewerkt).
- Hexagonen zijn alleen visualisatie; H3-oppervlakte bepaalt MWh/ha op de kaart.
- Voorbewerken om RAM te sparen: `python data/scripts/woonplaats_area_export.py`
- Bronbestand voor export: `data/layers/BAG_WOONPLAATSEN_EX_WATER.gpkg`

## Kaartlagen
- Basislaag: warmtevraag (H3).
- Extra lagen: energiearmoede, koopwoningen, wooncorporatie.
- Potentie: water/buurtpotentie.
- Warmtenet + Wegennet.

## Performance tips
- Grote GeoJSONs alleen laden bij toggle (zie app.py).
- Wegennet werkt best per gemeente-bestand (scheelt RAM en payload).
- Coord precision lager = kleinere payload, maar minder detail.

## Debugging
- Streamlit errors: check de terminal traceback.
- RAM issues: kijk naar logregels met _log_ram(...) in app.py.

## Update
conda update -c conda-forge streamlit
pip install --upgrade pip
pip install -r requirements.txt
