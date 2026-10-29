#!/usr/bin/env python3
"""Convierte respuestas JSON-stat de la API de Eurostat en CSV.

Solo usa la biblioteca estándar (Python 3.8+).

Estructura de una respuesta JSON-stat de Eurostat
-------------------------------------------------
    id         lista con el orden de las dimensiones     ["freq", ..., "geo", "time"]
    size       número de categorías de cada dimensión     [1, 1, 10, 320]
    dimension  por cada dimensión: category.index (código -> posición)
                                   category.label (código -> nombre)
    value      objeto {"posición plana": número}; las posiciones sin dato no aparecen
    status     objeto {"posición plana": marca} (p. ej. "u" = poco fiable, "p" = provisional)

La posición plana se calcula en orden "row-major": la ÚLTIMA dimensión de `id`
es la que cambia más deprisa. Con size = [A, B, C] la posición de (a, b, c) es
    a*B*C + b*C + c

Uso
---
  # 1) Conversión genérica a CSV largo (una fila por observación)
  python3 eurostat_to_csv.py convert "<URL>"  -o salida.csv
  python3 eurostat_to_csv.py convert respuesta.json -o salida.csv

  # 2) Igual, pero en formato ancho (p. ej. una fila por mes y una columna por país)
  python3 eurostat_to_csv.py convert "<URL>" -o ancho.csv --rows time --cols geo

  # 3) Genera los tres CSV de la PEC 2 (pie, waffle, horizon) en ./data
  python3 eurostat_to_csv.py pec2 --outdir ../data

  # Ver la estructura de una respuesta sin generar nada
  python3 eurostat_to_csv.py inspect "<URL>"
"""

import argparse
import csv
import itertools
import json
import sys
import urllib.request
from urllib.parse import urlencode

BASE = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"

# ---------------------------------------------------------------------------
# Enlaces de la PEC 2 (los mismos que aparecen en las páginas)
# ---------------------------------------------------------------------------
PIE_SIEC = [
    "TOTAL", "RA300", "N900H", "RA420", "G3000", "RA100",           # total + 5 fuentes
    "C0000X0350-0370", "C0350-0370", "O4000XBIO", "RA130", "RA410",  # resto
    "R5110-5150_W6000RI", "R5300", "W6210", "RA500", "R5290",
    "W6100_6220", "H8000", "X9900", "BA100",
]
WAFFLE_IND = ["IND_TOTAL", "Y16_24", "Y25_34", "Y35_44", "Y45_54", "Y55_64", "Y65_74", "Y75_MAX"]
HORIZON_GEO = ["DE", "IE", "EL", "ES", "FR", "IT", "NL", "PL", "PT", "SE"]


def build_url(table, params):
    """params: lista de pares (clave, valor); las claves pueden repetirse."""
    return BASE + table + "?" + urlencode(params, safe="-_")


URL_PIE = build_url("nrg_bal_peh", [
    ("format", "JSON"), ("lang", "EN"), ("geo", "ES"), ("freq", "A"), ("unit", "GWH"),
    ("nrg_bal", "GEP"), ("time", "2024"),
] + [("siec", c) for c in PIE_SIEC])

URL_WAFFLE = build_url("isoc_ci_ifp_fu", [
    ("format", "JSON"), ("lang", "EN"), ("geo", "ES"), ("indic_is", "I_IDAY"),
    ("unit", "PC_IND"), ("time", "2024"),
] + [("ind_type", c) for c in WAFFLE_IND])

URL_HORIZON = build_url("une_rt_m", [
    ("format", "JSON"), ("lang", "EN"), ("freq", "M"), ("s_adj", "SA"), ("age", "TOTAL"),
    ("sex", "T"), ("unit", "PC_ACT"),
] + [("geo", g) for g in ["ES", "DE", "FR", "IT", "PT", "EL", "IE", "NL", "PL", "SE"]] + [
    ("sinceTimePeriod", "2000-01"), ("untilTimePeriod", "2026-08"),
])


# ---------------------------------------------------------------------------
# Lectura y aplanado de JSON-stat
# ---------------------------------------------------------------------------
def load(source):
    """`source` puede ser una URL o la ruta de un fichero .json."""
    if source.startswith(("http://", "https://")):
        req = urllib.request.Request(source, headers={"User-Agent": "pec2-uoc/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    with open(source, encoding="utf-8") as f:
        return json.load(f)


def _ordered_codes(category):
    """Códigos de una dimensión ordenados por su posición (index puede ser dict o lista)."""
    idx = category["index"]
    if isinstance(idx, dict):
        return [c for c, _ in sorted(idx.items(), key=lambda kv: kv[1])]
    return list(idx)


def dimensions(js):
    """[(id_dimensión, etiqueta, [códigos en orden], {código: etiqueta})]"""
    out = []
    for dim_id, size in zip(js["id"], js["size"]):
        d = js["dimension"][dim_id]
        codes = _ordered_codes(d["category"])
        labels = d["category"].get("label", {})
        if len(codes) != size:
            raise ValueError(f"La dimensión {dim_id} declara {size} categorías y tiene {len(codes)}")
        out.append((dim_id, d.get("label", dim_id), codes, labels))
    return out


def get_value(js, i):
    """`value` puede ser objeto disperso {"i": x} o lista densa."""
    v = js["value"]
    if isinstance(v, list):
        return v[i]
    return v.get(str(i))


def get_status(js, i):
    s = js.get("status")
    if not s:
        return None
    if isinstance(s, list):
        return s[i]
    return s.get(str(i))


def observations(js, keep_missing=False):
    """Genera un dict por observación con código y etiqueta de cada dimensión,
    el valor y la marca de estado."""
    dims = dimensions(js)
    # El orden de itertools.product coincide con la posición plana row-major
    # (la última dimensión cambia más deprisa).
    ranges = [range(len(codes)) for _, _, codes, _ in dims]
    for i, combo in enumerate(itertools.product(*ranges)):
        value = get_value(js, i)
        if value is None and not keep_missing:
            continue
        row = {}
        for (dim_id, _, codes, labels), k in zip(dims, combo):
            code = codes[k]
            row[dim_id] = code
            row[dim_id + "_label"] = labels.get(code, code)
        row["value"] = value
        row["status"] = get_status(js, i)
        yield row


def num(v):
    """Formato numérico compacto: 4.0 -> 4, 8.2 -> 8.2 (sin perder decimales)."""
    if v is None:
        return ""
    return str(int(v)) if float(v) == int(v) else repr(float(v))


# ---------------------------------------------------------------------------
# Subcomando: inspect
# ---------------------------------------------------------------------------
def cmd_inspect(args):
    js = load(args.source)
    print("label      :", js.get("label"))
    print("actualizado:", js.get("updated"))
    total = 1
    for s in js["size"]:
        total *= s
    print(f"observaciones: {len(js['value'])} de {total} posibles")
    print()
    for dim_id, label, codes, labels in dimensions(js):
        extra = ""
        if len(codes) > 1:
            extra = f"  {codes[0]} ... {codes[-1]}" if len(codes) > 6 else "  " + ", ".join(codes)
        else:
            extra = f"  {codes[0]} = {labels.get(codes[0], '')}"
        print(f"{dim_id:10s} {len(codes):4d}  {label}{extra}")


# ---------------------------------------------------------------------------
# Subcomando: convert (genérico)
# ---------------------------------------------------------------------------
def cmd_convert(args):
    js = load(args.source)
    dims = dimensions(js)
    varying = [d[0] for d in dims if len(d[2]) > 1]

    if args.rows and args.cols:
        write_wide(js, args.rows, args.cols, args.output)
        return

    # CSV largo. Por defecto se omiten las dimensiones constantes (freq, unit, ...)
    keep = [d[0] for d in dims] if args.keep_constant else varying
    cols = []
    for k in keep:
        cols += [k, k + "_label"]
    cols += ["value", "status"]
    n = 0
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(cols)
        for r in observations(js, keep_missing=args.keep_missing):
            w.writerow([num(r[c]) if c == "value" else (r[c] or "") for c in cols])
            n += 1
    print(f"{args.output}: {n} filas, columnas {cols}")


def pivot(js, rows_dim, cols_dim):
    """Devuelve (códigos_filas, códigos_columnas, {(fila, col): valor})."""
    dims = {d[0]: d for d in dimensions(js)}
    for name in (rows_dim, cols_dim):
        if name not in dims:
            sys.exit(f"Dimensión desconocida: {name}. Disponibles: {', '.join(dims)}")
    table = {}
    for r in observations(js):
        table[(r[rows_dim], r[cols_dim])] = r["value"]
    return dims[rows_dim][2], dims[cols_dim][2], table


def write_wide(js, rows_dim, cols_dim, output, col_order=None):
    row_codes, col_codes, table = pivot(js, rows_dim, cols_dim)
    if col_order:
        col_codes = [c for c in col_order if c in col_codes]
    with open(output, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow([rows_dim] + col_codes)
        for rc in row_codes:
            w.writerow([rc] + [num(table.get((rc, cc))) for cc in col_codes])
    print(f"{output}: {len(row_codes)} filas x {len(col_codes)} columnas")


# ---------------------------------------------------------------------------
# Subcomando: pec2 (los tres CSV de la entrega)
# ---------------------------------------------------------------------------
def values_by(js, dim_id):
    return {r[dim_id]: r["value"] for r in observations(js)}


def make_waffle(source, output):
    js = load(source)
    v = values_by(js, "ind_type")
    names = {"IND_TOTAL": "Total", "Y16_24": "16–24", "Y25_34": "25–34", "Y35_44": "35–44",
             "Y45_54": "45–54", "Y55_64": "55–64", "Y65_74": "65–74", "Y75_MAX": "75 o más"}
    missing = [c for c in names if c not in v]
    if missing:
        sys.exit(f"Faltan categorías en la respuesta: {missing}")
    with open(output, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["grupo", "porcentaje_uso_diario"])
        for code, name in names.items():
            w.writerow([name, num(v[code])])
    print(f"{output}: {len(names)} filas")


def make_pie(source, output):
    js = load(source)
    v = values_by(js, "siec")
    total = v["TOTAL"]
    main = [("Eólica", "RA300"), ("Nuclear", "N900H"), ("Solar fotovoltaica", "RA420"),
            ("Gas natural", "G3000"), ("Hidráulica", "RA100")]
    main_codes = {c for _, c in main}

    # Comprobación: las 19 categorías hoja suman el total (no hay solapes ni huecos)
    leaves = sum(val for c, val in v.items() if c != "TOTAL")
    if abs(leaves - total) > 0.01:
        sys.exit(f"Las categorías suman {leaves:.3f} y el total es {total:.3f}: hay solapes o faltan códigos")

    rows = [(name, v[c], "Eurostat nrg_bal_peh (siec)") for name, c in main]
    rows.sort(key=lambda r: -r[1])
    resto = total - sum(v[c] for c in main_codes)
    rows.append(("Resto", resto, "Total menos las cinco fuentes anteriores "
                 "(carbón; petróleo; bombeo; biomasa; solar térmica; residuos; otros)"))
    with open(output, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["tecnologia", "porcentaje", "gwh", "nota"])
        for name, gwh, nota in rows:
            w.writerow([name, f"{gwh / total * 100:.4f}", round(gwh), nota])
        w.writerow(["Total", "100", round(total), "Eurostat nrg_bal_peh (siec=TOTAL)"])
    print(f"{output}: {len(rows) + 1} filas (resto = {resto:.3f} GWh, {resto / total * 100:.2f} %)")


def make_horizon(source, output):
    js = load(source)
    write_wide(js, "time", "geo", output, col_order=HORIZON_GEO)


def cmd_pec2(args):
    import os
    os.makedirs(args.outdir, exist_ok=True)
    src = {"pie": args.pie or URL_PIE, "waffle": args.waffle or URL_WAFFLE,
           "horizon": args.horizon or URL_HORIZON}
    make_pie(src["pie"], os.path.join(args.outdir, "generacion_2024_eurostat.csv"))
    make_waffle(src["waffle"], os.path.join(args.outdir, "internet_diario_edad_2024.csv"))
    make_horizon(src["horizon"], os.path.join(args.outdir, "paro_eurostat.csv"))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("inspect", help="muestra las dimensiones de una respuesta")
    a.add_argument("source", help="URL de la API o fichero .json")
    a.set_defaults(fn=cmd_inspect)

    c = sub.add_parser("convert", help="JSON-stat -> CSV (largo o ancho)")
    c.add_argument("source", help="URL de la API o fichero .json")
    c.add_argument("-o", "--output", required=True)
    c.add_argument("--rows", help="dimensión para las filas (formato ancho), p. ej. time")
    c.add_argument("--cols", help="dimensión para las columnas (formato ancho), p. ej. geo")
    c.add_argument("--keep-constant", action="store_true", help="incluir también las dimensiones con 1 sola categoría")
    c.add_argument("--keep-missing", action="store_true", help="incluir observaciones sin valor")
    c.set_defaults(fn=cmd_convert)

    e = sub.add_parser("pec2", help="genera los tres CSV de la PEC 2")
    e.add_argument("--outdir", default="data")
    e.add_argument("--pie", help="URL o .json alternativo para el pie")
    e.add_argument("--waffle", help="URL o .json alternativo para el waffle")
    e.add_argument("--horizon", help="URL o .json alternativo para el horizon")
    e.set_defaults(fn=cmd_pec2)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
