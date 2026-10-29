# PEC 2 · Visualización de datos (UOC)

Tres visualizaciones estáticas, sin dependencias externas (HTML + SVG + JavaScript puro). Datos abiertos de Eurostat.

- `pages/pie.html`: pie chart, generación eléctrica bruta de España en 2024 por fuente (nrg_bal_peh).
- `pages/waffle.html`: waffle chart, uso diario de internet en España en 2024 por grupo de edad (isoc_ci_ifp_fu).
- `pages/horizon.html`: horizon graph, paro mensual en 10 países europeos 2000–2026 (une_rt_m).
- `data/`: los mismos datos en CSV. Cada página lleva además sus datos incrustados, así que se abre también directamente desde el disco.

## Regenerar los CSV

`scripts/eurostat_to_csv.py` (solo biblioteca estándar) descarga las respuestas JSON-stat de Eurostat y genera los CSV de `data/`:

```
python3 scripts/eurostat_to_csv.py pec2 --outdir data
python3 scripts/eurostat_to_csv.py inspect "<URL de la API>"
python3 scripts/eurostat_to_csv.py convert "<URL>" -o salida.csv [--rows time --cols geo]
```
