# Warmtekaart
## Doel
Kaarttool voor warmtevraag en potentiele warmtenetten in Friesland.
De code is gericht op performance en kaartinteractie via PyDeck.

## Snel starten
- Run: streamlit run app.py
- Run (test only): WARMTE_USE_COMPACT=1 streamlit run app.py
- Data: data/ (CSV/Parquet + GeoJSON lagen)

## Belangrijkste bestanden
- app.py: hoofdflow van de app (load -> UI -> berekenen -> render).
- ui/sidebar.py: alle UI controls, filters en toggles.
- core/io.py: data/geojson loaders + caching helpers.
- core/layers.py: opbouw van PyDeck lagen + GeoJSON conversie + laag-meta helpers.
- core/utils.py: helpers voor kleur, tooltip, formatting.
- core/h3sites.py: logica voor warmtenet-analyse (warmte hotspots).
- core/h3agg.py: H3 aggregaties en groepering van data.
- core/map_data.py: kaartdata voorbereiden + site records opbouwen.
- scripts/precompute_h3_rollups.py: offline aggregaties voor H3 (sneller starten).
- scripts/geojson.py: hulpscript voor GeoJSON bewerkingen/conversie.

## Waar pas je wat aan?
<<<<<<< HEAD
** App-titel en introductietekst/**
-> app.py: st.set_page_config(...) en de st.markdown(...) header.

** Tooltip labels/velden per laag/**
-> core/layers.py: in de create_*_layers functies.

** Kleuren van lagen en legenda/**
-> core/config.py (`LAYER_CFG`)
-> core/utils.py (kleurhelpers)

** Data paden + defaults/**
-> core/config.py: bij *_PATH

** GeoJSON precisie en payload/**
-> core/io.py (load_geojson, coord_precision)

** Warmtenet hotspots/**
-> core/h3sites.py

** H3 aggregaties/rollups/**
-> core/h3agg.py (engine) + core/map_data.py (kaart-voorbereiding)

** UI filters/toggles/teksten/**
-> ui/sidebar.py

** KPI tabellen en data tabel overzicht/**
-> ui/kpis_and_tables.py
=======
**App-titel en introductietekst**
- app.py: st.set_page_config(...) en de st.markdown(...) header.

**Tooltip labels/velden per laag**
- core/layers.py: in de create_*_layers functies.

**Kleuren van lagen en legenda**
- core/config.py: (`LAYER_CFG`)
- core/utils.py: (kleurhelpers)

**Data paden + defaults**
- core/config.py: bij *_PATH

**GeoJSON precisie en payload**
- core/io.py: (load_geojson, coord_precision)

**Warmtenet hotspots**
- core/h3sites.py

**H3 aggregaties/rollups**
- core/h3agg.py: (engine) + core/map_data.py (kaart-voorbereiding)

**UI filters/toggles/teksten**
- ui/sidebar.py

**KPI tabellen en data tabel overzicht**
- ui/kpis_and_tables.py
>>>>>>> upstream/main

## Structuur per map
**core/**
- `config.py`: centrale configuratie (paden, kleuren, layer-metadata).
- `io.py`: inlezen/cachen van GeoJSON en tabellen.
- `layers.py`: PyDeck lagen + tooltip opbouw + GeoJSON conversie.
- `utils.py`: algemene helpers (kleur, formatting, tooltip snippets).
- `h3agg.py`: pure H3 aggregaties (snel en herbruikbaar).
- `map_data.py`: bouwt de map-dataframes voor de kaart (filter/rollup/tooltip data).
- `h3sites.py`: warmtenet selectie/cluster-logica.

**ui/**
- `sidebar.py`: alle filters, toggles en waarschuwingen.
- `kpis_and_tables.py`: KPI kaarten en tabellen onder de kaart.

**app.py**
- Orchestrator: laadt data, leest UI, berekent aggregaties, bouwt lagen en rendert.

## Dataflow (hoog niveau)
1) Data en lagen laden.
2) Sidebar bepaalt filters/toggles en schrijft naar `st.session_state`.
3) H3 aggregaties en (optioneel) warmtenet-analyse worden gebouwd.
4) PyDeck lagen + tooltip samenstellen en renderen.

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
