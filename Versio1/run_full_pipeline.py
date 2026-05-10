# -*- coding: utf-8 -*-
"""
Full pipeline: Block 1 -> Block 2 -> (optional Block 3) -> aggregate JSON -> PDFs.

  cd Versio1
  python run_full_pipeline.py                             # alcance prod: TARGET_WEEK (Cabecera Creado el)
  python run_full_pipeline.py --date 02/02/2026           # un solo día (Cabecera)
  python run_full_pipeline.py --route DR0027               # opcional: sólo esa ruta (sobre TARGET_WEEK)
  python run_full_pipeline.py --all-dates                   # dataset completo
  python run_full_pipeline.py --single-pair                 # sólo (--ruta, --fecha): depuración
  python run_full_pipeline.py --workers 16

Default output folder is ``pipeline_out/`` (set env ``PIPELINE_OUT`` to override).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

# Versio1 as cwd (Excel + .env)
_HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(_HERE)
sys.path.insert(0, _HERE)

from block2_maps_routing import load_dotenv
from block3_loading import Block3Paths, run_block3
from damm_engine import optimise, run_block2
from generate_comparativa_pdf import generate_comparativa_pdf
from generate_docs import generate_three_pdfs
from pipeline_utils import (
    HACKATON_SHEET_CABECERA,
    HACKATON_SHEET_DETALLE,
    PRIMARY_SAVINGS_MIN_STOPS,
    TARGET_WEEK,
    apply_partition_env_and_resolve_hackaton,
    block2_route_totals,
    classify_route_value_segment,
    clear_partition_hackaton_env,
    count_comandas_rows,
    interval_months_from_strings,
    iter_route_fecha_pairs,
    partition_routes,
    priority_factor_breakdown_eur,
    resolve_pipeline_baseline_eur,
    savings_vs_baseline,
)
from priority_cluster import PRIORITY_WEIGHTS


def _default_out_dir() -> str:
    return os.environ.get("PIPELINE_OUT", os.path.join(_HERE, "pipeline_out"))


def _run_one_pair(ruta: str, fecha: str, hackaton: str, maps: str) -> dict:
    """
    Block 1 + Block 2 for a single (ruta, fecha). Safe to run concurrently (threads)
    after damm_engine.optimise switched to a local Random instance.
    """
    rec: dict = {"ruta": ruta, "fecha": fecha, "status": "pending"}
    out: dict = {
        "rec": rec,
        "b1": None,
        "b2": None,
        "sample_score": (-1, -1),
        "fechas_ok": None,
        "agg": None,
    }
    try:
        b1 = optimise(ruta, fecha)
    except Exception as e:
        rec["status"] = "error"
        rec["error"] = str(e)
        return out

    if not b1 or not b1.get("stops"):
        rec["status"] = "empty"
        return out

    b2 = run_block2(b1, maps_api_key=maps)
    bt = block2_route_totals(b2)
    bl_fin = resolve_pipeline_baseline_eur(b1, b2)
    sav = savings_vs_baseline(bl_fin["baseline_total_eur"], bt["total_eur"])
    n_com = count_comandas_rows(hackaton, ruta, fecha)
    n_st = len(b1["stops"])
    seg = classify_route_value_segment(n_st)
    use_primary = n_st >= PRIMARY_SAVINGS_MIN_STOPS

    rec.update(
        {
            "status": "ok",
            "stops_count": n_st,
            "comandas_rows": n_com,
            "clusters": list((b1.get("clusters_zona_transp") or {}).keys()),
            "baseline_total_eur": bl_fin["baseline_total_eur"],
            "baseline_method": bl_fin["baseline_method"],
            "baseline_rand_maps_distance_km_agg": bl_fin.get("baseline_rand_maps_distance_km"),
            "legacy_haversine_baseline_total_eur": bl_fin.get(
                "legacy_haversine_baseline_total_eur", bl_fin["baseline_total_eur"]
            ),
            "optimized_total_eur": bt["total_eur"],
            "savings_eur": sav,
            "included_in_primary_savings_metric_ge8_stops": use_primary,
            "route_value_segment": seg,
            "block2_km": bt["total_km"],
            "block2_duration_min": bt["total_duration_min"],
            "legacy_rand_dist_km": bl_fin["legacy_rand_dist_km"],
        }
    )
    out["b1"] = b1
    out["b2"] = b2
    out["sample_score"] = (n_st, n_com)
    out["fechas_ok"] = str(b1.get("fecha") or fecha)
    out["agg"] = {
        "baseline_total_eur": bl_fin["baseline_total_eur"],
        "optimized_total_eur": bt["total_eur"],
        "savings_eur_all": sav,
        "savings_eur_primary_ge8_only": sav if use_primary else 0.0,
        "savings_eur_under8_stops": sav if not use_primary else 0.0,
        "comandas_rows": n_com,
        "stops": n_st,
        "route_value_segment": seg,
    }
    print(f"  OK {ruta} {fecha} ({n_st} paradas)", flush=True)
    return out


def _run_one_pair_job(job: tuple[str, str, str, str]) -> dict:
    ruta, fecha, hackaton, maps = job
    print(f"  … {ruta} {fecha}", flush=True)
    return _run_one_pair(ruta, fecha, hackaton, maps)


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser(
        description="Pipeline Block 1+2+3, PDFs de muestra y comparativa de ahorros (presentación DAMM)."
    )
    ap.add_argument(
        "--all-dates",
        action="store_true",
        help="Procesar todas las parejas (Ruta, FECHA) del Hackaton.xlsx",
    )
    ap.add_argument(
        "--ruta",
        default=os.environ.get("E2E_RUTA", "DR0027"),
        help="Con --single-pair: sólo esa ruta; ignora partitioning",
    )
    ap.add_argument(
        "--fecha",
        default=os.environ.get("E2E_FECHA", "02/03/2026"),
        help="Con --single-pair: esa fecha dd/mm/aaaa",
    )
    ap.add_argument(
        "--single-pair",
        action="store_true",
        help="Una corrida optimise(--ruta, --fecha); sin TARGET_WEEK / --date / --all-dates.",
    )
    ap.add_argument(
        "--date",
        dest="pipeline_date",
        default=None,
        metavar="DD/MM/AAAA",
        help="Cabecera 'Creado el': sólo ese día (sustituye TARGET_WEEK).",
    )
    ap.add_argument(
        "--ruta-filter",
        "--route",
        dest="route_filter",
        default=None,
        help="Limitar a una ruta: con partition (TARGET/--date); con --all-dates igual.",
    )
    ap.add_argument("--out", default=_default_out_dir(), help="Directorio de salida")
    ap.add_argument("--no-block3", action="store_true", help="No ejecutar Block 3 (más rápido)")
    ap.add_argument("--no-pdfs", action="store_true", help="No generar PDFs")
    ap.add_argument("--hackaton", default=os.path.join(_HERE, "Hackaton.xlsx"))
    ap.add_argument(
        "--workers",
        type=int,
        default=8,
        metavar="N",
        help="Hilos en paralelo para muchas parejas (TARGET_WEEK / --all-dates). Ignorado con --sequential.",
    )
    ap.add_argument(
        "--sequential",
        action="store_true",
        help="Forzar una pareja tras otra (depuración / evitar rate limits).",
    )
    args = ap.parse_args()

    if args.single_pair and args.all_dates:
        ap.error("--single-pair no es compatible con --all-dates.")

    hackaton = os.path.abspath(args.hackaton)
    out_root = os.path.abspath(args.out)
    os.makedirs(out_root, exist_ok=True)
    docs_sample = os.path.join(out_root, "docs_sample")
    os.makedirs(docs_sample, exist_ok=True)

    maps = (os.environ.get("GOOGLE_MAPS_API_KEY") or "").strip()
    if not maps:
        print("AVISO: GOOGLE_MAPS_API_KEY vacío — Block 2 usará distancias Haversine.")

    if args.all_dates and args.pipeline_date:
        print("AVISO: --date es ignorado con --all-dates.")

    hackaton_session: str | None = None
    clear_partition_hackaton_env()

    if args.all_dates:
        hackaton_session = hackaton
        pairs = iter_route_fecha_pairs(hackaton, ruta_filter=args.route_filter)
        print(f"Modo --all-dates: {len(pairs)} ejecuciones (Ruta, FECHA)")
    elif args.single_pair:
        hackaton_session = hackaton
        pairs = [(args.ruta.strip(), args.fecha.strip())]
        print(f"Modo --single-pair: {pairs[0]}")
    else:
        xl_part = pd.read_excel(
            hackaton,
            sheet_name=[HACKATON_SHEET_CABECERA, HACKATON_SHEET_DETALLE],
        )
        if args.pipeline_date:
            ds = de = args.pipeline_date.strip()
            label = f"día Cabecera {ds}"
        else:
            ds, de = TARGET_WEEK["start"], TARGET_WEEK["end"]
            label = f"semana TARGET_WEEK {ds}–{de}"
        route_units = partition_routes(
            xl_part[HACKATON_SHEET_CABECERA],
            xl_part[HACKATON_SHEET_DETALLE],
            date_start=ds,
            date_end=de,
            ruta_filter=args.route_filter,
        )
        hackaton_session = apply_partition_env_and_resolve_hackaton(
            hackaton, out_root, route_units
        )
        pairs = route_units["pairs"]
        print(f"-> {len(pairs)} route units ({label})")

    hackaton_use = hackaton_session or hackaton

    runs: list = []
    total_baseline = 0.0
    total_optimized = 0.0
    total_savings_all = 0.0
    savings_primary_ge8 = 0.0
    savings_under8 = 0.0
    savings_segment: dict[str, float] = {"high_value": 0.0, "medium_value": 0.0, "low_value": 0.0}
    total_comandas = 0
    total_stops = 0
    ok = 0
    fail = 0
    fechas_ok: list[str] = []

    sample_b1: dict | None = None
    sample_b2: dict | None = None
    sample_score = (-1, -1)  # (stops, comandas)

    jobs = [(ruta, fecha, hackaton_use, maps) for ruta, fecha in pairs]
    use_parallel = (
        len(jobs) > 1
        and not args.sequential
    )
    if use_parallel:
        max_w = max(1, min(args.workers, len(jobs)))
        print(f"Paralelo: hasta {max_w} hilos para {len(jobs)} parejas (Ruta, FECHA)")
        with ThreadPoolExecutor(max_workers=max_w) as ex:
            row_outs = list(ex.map(_run_one_pair_job, jobs))
    else:
        if args.sequential and len(jobs) > 1:
            print("Modo secuencial (--sequential).")
        row_outs = [_run_one_pair_job(j) for j in jobs]

    for out in row_outs:
        rec = out["rec"]
        runs.append(rec)
        st = rec.get("status")
        if st == "error":
            fail += 1
            print(f"  Error {rec.get('ruta')} {rec.get('fecha')}: {rec.get('error')}")
            continue
        if st == "empty":
            fail += 1
            print(f"  Sin paradas: {rec.get('ruta')} {rec.get('fecha')}")
            continue
        if st != "ok" or not out.get("agg"):
            continue
        ok += 1
        ag = out["agg"]
        sav = float(ag["savings_eur_all"])
        total_baseline += ag["baseline_total_eur"]
        total_optimized += ag["optimized_total_eur"]
        total_savings_all += sav
        savings_primary_ge8 += float(ag["savings_eur_primary_ge8_only"])
        savings_under8 += float(ag["savings_eur_under8_stops"])
        seg = ag.get("route_value_segment")
        if seg in savings_segment:
            savings_segment[str(seg)] += sav
        total_comandas += ag["comandas_rows"]
        total_stops += ag["stops"]
        if out.get("fechas_ok"):
            fechas_ok.append(out["fechas_ok"])

        score = out["sample_score"]
        if score > sample_score:
            sample_score = score
            sample_b1 = out["b1"]
            sample_b2 = out["b2"]

    # Aggregate JSON
    span_days, months_span = interval_months_from_strings(fechas_ok)
    annual_proj_primary = round((savings_primary_ge8 / months_span) * 12.0, 2) if months_span > 0 else 0.0

    prio_break = priority_factor_breakdown_eur(max(0.0, savings_primary_ge8), PRIORITY_WEIGHTS)

    sample_meta = {
        "ruta": sample_b1.get("ruta") if sample_b1 else "",
        "fecha": sample_b1.get("fecha") if sample_b1 else "",
        "reason": "máx. paradas (empate por líneas comanda)" if sample_b1 else "sin muestra",
    }

    aggregate = {
        "reporting_notes": (
            "Baseline EUR = mismo Distance Matrix por cluster que el optimizado, visita ordenada por la permutacion "
            "aleatoria global del Block 1 (restringida a paradas efectivamente servidas). "
            "La metrica principal de ahorro y la proyeccion anual incluyen rutas con >= {} paradas; "
            "las rutas cortas (<8) se muestran aparte porque el ahorro puramente de ruta suele ser limitado.".format(
                PRIMARY_SAVINGS_MIN_STOPS
            )
        ),
        "segments_savings_eur_approx": {k: round(v, 2) for k, v in savings_segment.items()},
        "totals": {
            "runs_ok": ok,
            "runs_failed": fail,
            "total_comandas_rows": total_comandas,
            "total_stops_served": total_stops,
            "total_baseline_eur_maps_dm": round(total_baseline, 2),
            "total_optimized_eur": round(total_optimized, 2),
            "total_savings_eur_all_runs": round(total_savings_all, 2),
            "primary_savings_metric_ge8_stops_eur": round(savings_primary_ge8, 2),
            "savings_eur_under_8_stops_routes_only": round(savings_under8, 2),
        },
        "interval": {
            "fecha_min": min(fechas_ok) if fechas_ok else "",
            "fecha_max": max(fechas_ok) if fechas_ok else "",
            "span_days": span_days,
            "months_span": round(months_span, 4),
            "annual_savings_projection_eur_primary_ge8_stops": annual_proj_primary,
        },
        "weights": dict(PRIORITY_WEIGHTS),
        "priority_breakdown_eur_primary_ge8_metric": prio_break,
        "priority_breakdown_uses_primary_savings_metric": True,
        "sample": sample_meta,
        "runs": runs,
    }

    agg_path = os.path.join(out_root, "pipeline_aggregate.json")
    with open(agg_path, "w", encoding="utf-8") as f:
        json.dump(aggregate, f, indent=2, default=str)
    print(f"Guardado: {agg_path}")

    # Block 3 only for the sample run (avoids huge ZM040 joins for every day)
    if sample_b1 and sample_b2 and not args.no_block3:
        b2_path = os.path.join(out_root, "sample_block2.json")
        with open(b2_path, "w", encoding="utf-8") as f:
            json.dump(sample_b2, f, indent=2, default=str)
        zm040 = os.environ.get("ZM040_XLSX", os.path.join(_HERE, "..", "Hackaton", "ZM040.XLSX"))
        b3_paths = Block3Paths(
            block2_json=b2_path,
            hackaton_xlsx=hackaton_use,
            zm040_xlsx=os.path.abspath(zm040),
            materiales_xlsx=hackaton_use,
        )
        print("Block 3 (muestra) ...")
        b3 = run_block3(b3_paths)
        b3_out = os.path.join(out_root, "block3_sample.json")
        with open(b3_out, "w", encoding="utf-8") as f:
            json.dump(b3, f, indent=2, default=str)
        print(f"Guardado: {b3_out}")

    # DDI-style PDFs from the Block 1 sample (one representative route / day)
    if sample_b1 and not args.no_pdfs:
        res_path = os.path.join(docs_sample, "result.json")
        with open(res_path, "w", encoding="utf-8") as f:
            json.dump(sample_b1, f, indent=2, default=str)
        print("Generando PDFs de muestra (Hoja de ruta, Hoja de carga, Albaranes) ...")
        paths = generate_three_pdfs(res_path, docs_sample)
        for k, p in paths.items():
            print(f"  {k}: {p}")

    if not args.no_pdfs:
        cmp_pdf = os.path.join(out_root, "Comparativa_Ahorros.pdf")
        generate_comparativa_pdf(aggregate, cmp_pdf)
        print(f"Guardado: {cmp_pdf}")

    print("Pipeline terminado.")


if __name__ == "__main__":
    main()
