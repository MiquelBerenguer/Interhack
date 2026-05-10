# -*- coding: utf-8 -*-
"""
Full pipeline: Block 1 -> Block 2 -> (optional Block 3) -> aggregate JSON -> PDFs.

  cd Versio1
  python run_full_pipeline.py                     # single day (E2E_RUTA + E2E_FECHA)
  python run_full_pipeline.py --all-dates         # all (Route, FECHA) pairs from Hackaton.xlsx
  python run_full_pipeline.py --all-dates --ruta DR0027   # one route, all dates
  python run_full_pipeline.py --all-dates --workers 12    # parallel Block1+2 (default ~8)

Default output folder is ``pipeline_out/`` (set env ``PIPELINE_OUT`` to override).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

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
    baseline_eur_from_block1_rand,
    block2_route_totals,
    count_comandas_rows,
    interval_months_from_strings,
    iter_route_fecha_pairs,
    priority_factor_breakdown_eur,
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
    bl = baseline_eur_from_block1_rand(b1)
    sav = savings_vs_baseline(bl["baseline_total_eur"], bt["total_eur"])
    n_com = count_comandas_rows(hackaton, ruta, fecha)
    n_st = len(b1["stops"])

    rec.update(
        {
            "status": "ok",
            "stops_count": n_st,
            "comandas_rows": n_com,
            "clusters": list((b1.get("clusters_zona_transp") or {}).keys()),
            "baseline_total_eur": bl["baseline_total_eur"],
            "optimized_total_eur": bt["total_eur"],
            "savings_eur": sav,
            "block2_km": bt["total_km"],
            "block2_duration_min": bt["total_duration_min"],
            "rand_dist_km": bl["rand_dist_km"],
        }
    )
    out["b1"] = b1
    out["b2"] = b2
    out["sample_score"] = (n_st, n_com)
    out["fechas_ok"] = str(b1.get("fecha") or fecha)
    out["agg"] = {
        "baseline_total_eur": bl["baseline_total_eur"],
        "optimized_total_eur": bt["total_eur"],
        "savings_eur": sav,
        "comandas_rows": n_com,
        "stops": n_st,
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
        help="Ruta si no usas --all-dates",
    )
    ap.add_argument(
        "--fecha",
        default=os.environ.get("E2E_FECHA", "02/03/2026"),
        help="Fecha dd/mm/aaaa si no usas --all-dates",
    )
    ap.add_argument(
        "--ruta-filter",
        default=None,
        help="Con --all-dates: limitar a una sola ruta",
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
        help="Hilos en paralelo para --all-dates (Block 1+2 por pareja). Ignorado con --sequential.",
    )
    ap.add_argument(
        "--sequential",
        action="store_true",
        help="Forzar una pareja tras otra (depuración / evitar rate limits).",
    )
    args = ap.parse_args()

    hackaton = os.path.abspath(args.hackaton)
    out_root = os.path.abspath(args.out)
    os.makedirs(out_root, exist_ok=True)
    docs_sample = os.path.join(out_root, "docs_sample")
    os.makedirs(docs_sample, exist_ok=True)

    maps = (os.environ.get("GOOGLE_MAPS_API_KEY") or "").strip()
    if not maps:
        print("AVISO: GOOGLE_MAPS_API_KEY vacío — Block 2 usará distancias Haversine.")

    if args.all_dates:
        pairs = iter_route_fecha_pairs(hackaton, ruta_filter=args.ruta_filter)
        print(f"Modo --all-dates: {len(pairs)} ejecuciones (Ruta, FECHA)")
    else:
        pairs = [(args.ruta.strip(), args.fecha.strip())]
        print(f"Modo un día: {pairs[0]}")

    runs: list = []
    total_baseline = 0.0
    total_optimized = 0.0
    total_savings_sum = 0.0
    total_comandas = 0
    total_stops = 0
    ok = 0
    fail = 0
    fechas_ok: list[str] = []

    sample_b1: dict | None = None
    sample_b2: dict | None = None
    sample_score = (-1, -1)  # (stops, comandas)

    jobs = [(ruta, fecha, hackaton, maps) for ruta, fecha in pairs]
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
        total_baseline += ag["baseline_total_eur"]
        total_optimized += ag["optimized_total_eur"]
        total_savings_sum += ag["savings_eur"]
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
    annual_proj = round((total_savings_sum / months_span) * 12.0, 2) if months_span > 0 else 0.0

    prio_break = priority_factor_breakdown_eur(max(0.0, total_savings_sum), PRIORITY_WEIGHTS)

    sample_meta = {
        "ruta": sample_b1.get("ruta") if sample_b1 else "",
        "fecha": sample_b1.get("fecha") if sample_b1 else "",
        "reason": "máx. paradas (empate por líneas comanda)" if sample_b1 else "sin muestra",
    }

    aggregate = {
        "totals": {
            "runs_ok": ok,
            "runs_failed": fail,
            "total_comandas_rows": total_comandas,
            "total_stops_served": total_stops,
            "total_baseline_eur": round(total_baseline, 2),
            "total_optimized_eur": round(total_optimized, 2),
            "total_savings_eur": round(total_savings_sum, 2),
        },
        "interval": {
            "fecha_min": min(fechas_ok) if fechas_ok else "",
            "fecha_max": max(fechas_ok) if fechas_ok else "",
            "span_days": span_days,
            "months_span": round(months_span, 4),
            "annual_savings_projection_eur": annual_proj,
        },
        "weights": dict(PRIORITY_WEIGHTS),
        "priority_breakdown_eur": prio_break,
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
            hackaton_xlsx=hackaton,
            zm040_xlsx=os.path.abspath(zm040),
            materiales_xlsx=hackaton,
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
