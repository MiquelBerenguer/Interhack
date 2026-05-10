# -*- coding: utf-8 -*-
"""
Emit dashboard-compatible JSON under Versio1/output/ for dashboard.html fetch().
"""

from __future__ import annotations

import json
import os
import random
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from priority_cluster import PRIORITY_WEIGHTS
from pipeline_utils import priority_factor_breakdown_eur


FUEL_PRICE_EUR_L = 1.35
CO2_KG_PER_L_DIESEL = 2.64


def dashboard_output_root(pipeline_dir: Optional[str] = None) -> str:
    base = pipeline_dir or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "output")


def safe_fname_date(fecha_ddmmyyyy: str) -> str:
    return str(fecha_ddmmyyyy).strip().replace("/", "-").replace("\\", "-")


def _liters_from_fuel_eur(eur: float) -> float:
    if eur <= 0:
        return 0.0
    return round(eur / FUEL_PRICE_EUR_L, 2)


def _co2_kg_from_liters(l: float) -> float:
    return round(l * CO2_KG_PER_L_DIESEL, 2)


def _estimated_priority_breakdown(priority_score: float, weights: Dict[str, float]) -> Dict[str, float]:
    ps = float(priority_score or 0.0)
    base = sum(max(0.0, float(v)) for v in weights.values()) or 1.0
    mag = abs(ps) / (abs(ps) + 0.45) if ps else 0.0
    sign = 1.0 if ps >= 0 else -0.8
    return {k: round(float(weights[k]) / base * mag * sign, 4) for k in weights.keys()}


def _cluster_summaries_and_files(
    b1: Dict[str, Any],
    b2: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Tuple[str, Dict[str, Any]]]]:
    fecha = str(b1.get("fecha") or "")
    route_code = str(b1.get("ruta") or "")
    rows: List[Dict[str, Any]] = []
    route_files: List[Tuple[str, Dict[str, Any]]] = []

    for zona, zb2_raw in (b2 or {}).items():
        if not isinstance(zb2_raw, dict):
            continue
        rs = zb2_raw.get("route_summary") or {}
        fr = zb2_raw.get("final_route")
        tid_check = zb2_raw.get("transporter_id")
        if tid_check is None and not rs and fr is None:
            continue

        baseline_eur = float(rs.get("baseline_rand_maps_total_eur") or 0.0)
        opt_eur = float(rs.get("total_fuel_cost") or 0.0) + float(rs.get("total_operational_cost") or 0.0)
        saving_eur = round(baseline_eur - opt_eur, 4)
        pct = round(100.0 * saving_eur / baseline_eur, 2) if baseline_eur > 1e-6 else 0.0

        tid = str(zb2_raw.get("transporter_id") or zona or "UNKNOWN")
        tname = str(zb2_raw.get("transporter_name") or "")

        stop_list = fr if isinstance(fr, list) else []

        summary_row = {
            "route_code": route_code,
            "date": fecha,
            "transport_id": tid,
            "transporter_name": tname,
            "stops": int(rs.get("total_stops") or len(stop_list)),
            "baseline_eur": round(baseline_eur, 4),
            "optimized_eur": round(opt_eur, 4),
            "saving_eur": saving_eur,
            "improvement_pct": pct,
            "distance_baseline_km": float(rs.get("baseline_rand_maps_distance_km") or 0.0),
            "distance_optimized_km": float(rs.get("total_distance_km") or 0.0),
            "revenue_eur": float(rs.get("total_revenue") or 0.0),
        }
        rows.append(summary_row)

        enriched_stops_detail: List[Dict[str, Any]] = []
        for s in stop_list:
            sd = dict(s)
            sd["priority_breakdown_estimated"] = _estimated_priority_breakdown(float(s.get("priority_score") or 0.0), PRIORITY_WEIGHTS)
            enriched_stops_detail.append(sd)

        baseline_lit = _liters_from_fuel_eur(float(rs.get("baseline_rand_maps_fuel_cost") or 0.0))
        opt_lit = _liters_from_fuel_eur(float(rs.get("total_fuel_cost") or 0.0))

        dash_route: Dict[str, Any] = {
            "route_code": route_code,
            "date": fecha,
            "transport_id": tid,
            "transporter_name": tname,
            "truck_type": str(zb2_raw.get("truck_type") or ""),
            "truck_slots": int(zb2_raw.get("truck_slots") or 8),
            "zona_key": str(zona),
            "priority_weights_used": dict(PRIORITY_WEIGHTS),
            "depot": zb2_raw.get("depot") or {},
            "baseline_visit_order_global": zb2_raw.get("baseline_visit_order_global") or [],
            "optimized_visit_order_global": zb2_raw.get("optimized_visit_order_global") or [],
            "all_stops": b1.get("stops") or [],
            "final_route_detail": enriched_stops_detail,
            "metrics_comparison": {
                "baseline": {
                    "distance_km": round(float(rs.get("baseline_rand_maps_distance_km") or 0.0), 3),
                    "duration_min": round(float(rs.get("baseline_rand_maps_drive_service_min") or 0.0), 2),
                    "fuel_liters_est": baseline_lit,
                    "co2_kg_est": _co2_kg_from_liters(baseline_lit),
                    "cost_eur": round(baseline_eur, 4),
                    "fuel_cost_eur": round(float(rs.get("baseline_rand_maps_fuel_cost") or 0.0), 4),
                    "operational_cost_eur": round(float(rs.get("baseline_rand_maps_operational_cost") or 0.0), 4),
                },
                "optimized": {
                    "distance_km": round(float(rs.get("total_distance_km") or 0.0), 3),
                    "duration_min": round(float(rs.get("total_duration_min") or 0.0), 2),
                    "fuel_liters_est": opt_lit,
                    "co2_kg_est": _co2_kg_from_liters(opt_lit),
                    "cost_eur": round(opt_eur, 4),
                    "fuel_cost_eur": round(float(rs.get("total_fuel_cost") or 0.0), 4),
                    "operational_cost_eur": round(float(rs.get("total_operational_cost") or 0.0), 4),
                },
            },
            "route_summary": rs,
            "unload_time_saved_min_estimate": round(float(rs.get("estimated_time_saved_min") or 0.0), 2),
        }
        fname = f"{safe_fname_date(fecha)}_{tid}".replace(".", "_")
        route_files.append((fname, dash_route))

    return rows, route_files


def _randomised_loading_plan(loading_plan: List[Dict[str, Any]], seed: int = 41) -> List[Dict[str, Any]]:
    rnd = random.Random(seed)
    plan = json.loads(json.dumps(loading_plan))
    rnd.shuffle(plan)
    for e in plan:
        if isinstance(e.get("items"), list) and e["items"]:
            rnd.shuffle(e["items"])
    return plan


def _synthetic_loading_plan_from_stops(
    dash_route: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Demo-friendly LIFO-style parcel layout from optimized stop order when Block 3
    did not emit a shard for this (transport, day). Not a substitute for ZM040 logic.
    """
    stops = dash_route.get("final_route_detail") or []
    slots = max(6, min(10, int(dash_route.get("truck_slots") or 8)))
    max_p = max(2, slots - 1)
    n = len(stops)
    by_parcel: Dict[int, Dict[str, Any]] = {}
    total_ret = sum(float(s.get("return_caj") or 0) + float(s.get("return_brl") or 0) for s in stops)
    if total_ret > 1e-6:
        by_parcel[1] = {
            "parcela": 1,
            "customer_name": "Devoluciones (consolidado)",
            "parcela_volume_pct": min(100.0, round(8.0 + total_ret * 2.0, 1)),
            "items": [{"material": "RET", "quantity": int(round(total_ret))}],
        }
    for i, s in enumerate(stops):
        if n <= 1:
            p = 2
        else:
            p = 2 + int(round((max_p - 2) * i / (n - 1)))
        p = max(2, min(max_p, p))
        name = str(s.get("customer_name") or "").strip() or f"Parada {i+1}"
        caj = float(s.get("delivery_caj") or 0)
        brl = float(s.get("delivery_brl") or 0)
        qty = max(1, int(round(caj + brl))) if (caj + brl) > 0 else 1
        vol_hint = min(95.0, round(12.0 + qty * 3.5, 1))
        if p not in by_parcel:
            by_parcel[p] = {
                "parcela": p,
                "customer_name": name,
                "parcela_volume_pct": vol_hint,
                "items": [{"material": "ENT", "quantity": qty}],
            }
        else:
            cur = by_parcel[p]
            cur["customer_name"] = (str(cur["customer_name"]) + " · " + name).strip()
            cur["parcela_volume_pct"] = min(100.0, float(cur["parcela_volume_pct"]) + vol_hint * 0.35)
            if isinstance(cur.get("items"), list):
                cur["items"].append({"material": "ENT", "quantity": qty})
    parcel_list = sorted(by_parcel.values(), key=lambda e: int(e["parcela"]))
    for e in parcel_list:
        e["parcela_volume_pct"] = round(float(e["parcela_volume_pct"]), 1)
    max_pct = max((float(e["parcela_volume_pct"]) for e in parcel_list), default=0.0)
    tw = round(sum(float(e["parcela_volume_pct"]) * 18.5 for e in parcel_list), 1)
    tv = round(tw * 12.4, 1)
    summary = {
        "total_weight_kg": tw,
        "total_volume_l": tv,
        "most_loaded_parcela_pct": round(max_pct, 1),
        "synthetic_from_route": True,
    }
    return parcel_list, summary


def _write_synthetic_loading_file(
    dash_route: Dict[str, Any],
    *,
    fname: str,
    out_loading_dir: str,
) -> None:
    stops_plan, summary = _synthetic_loading_plan_from_stops(dash_route)
    payload = {
        "route_code": str(dash_route.get("route_code") or ""),
        "date": str(dash_route.get("date") or ""),
        "transport_id": str(dash_route.get("transport_id") or ""),
        "transporter_name": str(dash_route.get("transporter_name") or ""),
        "truck_type": str(dash_route.get("truck_type") or ""),
        "truck_slots": int(dash_route.get("truck_slots") or 8),
        "loading_after_lifo": stops_plan,
        "loading_before_randomized": stops_plan,
        "warehouse_pick_sequence": [],
        "plan_summary": summary,
        "warnings": ["Distribución sintética (orden de visita · demo). Ejecutar Block 3 para plan almacén + ZM040."],
    }
    path = os.path.join(out_loading_dir, f"{fname}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)


def _write_loading_shard(
    b3_cluster: Dict[str, Any],
    *,
    fecha: str,
    route_code: str,
    out_loading_dir: str,
) -> str:
    tid = str(b3_cluster.get("transporter_id") or "")
    fname = f"{safe_fname_date(fecha)}_{tid}".replace(".", "_")
    path = os.path.join(out_loading_dir, f"{fname}.json")
    after_plan = b3_cluster.get("loading_plan") or []
    payload = {
        "route_code": route_code,
        "date": fecha,
        "transport_id": tid,
        "transporter_name": b3_cluster.get("transporter_name", ""),
        "truck_type": b3_cluster.get("truck_type", ""),
        "truck_slots": int(b3_cluster.get("truck_slots") or 6),
        "loading_after_lifo": after_plan,
        "loading_before_randomized": _randomised_loading_plan(after_plan),
        "warehouse_pick_sequence": b3_cluster.get("warehouse_pick_sequence") or [],
        "plan_summary": b3_cluster.get("plan_summary") or {},
        "warnings": b3_cluster.get("warnings") or [],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    return fname


def write_dashboard_outputs(
    row_outs: List[Dict[str, Any]],
    *,
    scope: Dict[str, Any],
    b3_sample: Optional[Dict[str, Any]] = None,
    b3_sample_context: Optional[Dict[str, str]] = None,
    pipeline_dir: Optional[str] = None,
    clear_existing: bool = True,
) -> Dict[str, str]:
    """
    Write output/summary.json, output/route_index.json, output/routes/*.json (+ optional loading).
    Returns paths written keyed by logical name.
    """
    root = dashboard_output_root(pipeline_dir)
    routes_dir = os.path.join(root, "routes")
    loading_dir = os.path.join(root, "loading")
    os.makedirs(routes_dir, exist_ok=True)
    os.makedirs(loading_dir, exist_ok=True)
    if clear_existing:
        for d in (routes_dir, loading_dir):
            if os.path.isdir(d):
                for fn in os.listdir(d):
                    if fn.endswith(".json"):
                        try:
                            os.remove(os.path.join(d, fn))
                        except OSError:
                            pass

    route_rows_list: List[Dict[str, Any]] = []
    route_index: List[Dict[str, str]] = []
    written_route_paths: Dict[str, str] = {}
    written_loading_fnames: set = set()

    for out in row_outs:
        rec = out.get("rec") or {}
        if rec.get("status") != "ok":
            continue
        b1 = out.get("b1") or {}
        b2 = out.get("b2") or {}
        rows, rf = _cluster_summaries_and_files(b1, b2)
        route_rows_list.extend(rows)

        for fname, dash_route in rf:
            rpath = os.path.join(routes_dir, f"{fname}.json")
            with open(rpath, "w", encoding="utf-8") as f:
                json.dump(dash_route, f, indent=2, default=str)
            written_route_paths[fname] = rpath

            meta = dash_route.get("date") or str(b1.get("fecha"))
            tid = dash_route.get("transport_id") or ""
            route_index.append(
                {
                    "date": meta,
                    "transport_id": tid,
                    "route_code": str(dash_route.get("route_code") or ""),
                    "transporter_name": str(dash_route.get("transporter_name") or ""),
                }
            )

    positive_savings = sum(float(r["saving_eur"]) for r in route_rows_list if float(r["saving_eur"]) > 1e-6)
    routes_with_saving = sum(1 for r in route_rows_list if float(r["saving_eur"]) > 1e-6)
    total_baseline = sum(float(r["baseline_eur"]) for r in route_rows_list)
    total_opt = sum(float(r["optimized_eur"]) for r in route_rows_list)
    total_rev = sum(float(r["revenue_eur"]) for r in route_rows_list)
    total_dist_b = sum(float(r["distance_baseline_km"]) for r in route_rows_list)
    total_dist_o = sum(float(r["distance_optimized_km"]) for r in route_rows_list)

    annual_linear = round(positive_savings * 52.0, 2)
    prio_money = priority_factor_breakdown_eur(max(0.0, positive_savings), PRIORITY_WEIGHTS)

    best_pct_row: Optional[Dict[str, Any]] = None
    for r in sorted(route_rows_list, key=lambda x: float(x.get("improvement_pct") or 0), reverse=True):
        if float(r.get("saving_eur") or 0) > 1e-6:
            best_pct_row = r
            break
    if best_pct_row is None and route_rows_list:
        best_pct_row = max(route_rows_list, key=lambda x: float(x.get("improvement_pct") or 0))

    best_route_block: Dict[str, Any] = {}
    if best_pct_row:
        best_route_block = {
            "route_code": best_pct_row["route_code"],
            "date": best_pct_row["date"],
            "saving_eur": round(float(best_pct_row["saving_eur"]), 2),
            "improvement_pct": round(float(best_pct_row["improvement_pct"]), 2),
            "transport_id": best_pct_row.get("transport_id", ""),
        }

    summary: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "scope": dict(scope),
        "totals": {
            "routes_processed": len(route_rows_list),
            "stops_served": sum(int(r.get("stops") or 0) for r in route_rows_list),
            "baseline_cost_eur": round(total_baseline, 2),
            "optimized_cost_eur": round(total_opt, 2),
            "routes_with_saving": int(routes_with_saving),
            "total_saving_positive_routes_eur": round(positive_savings, 2),
            "annual_projection_eur": annual_linear,
            "annual_projection_method": "Extracción lineal: suma ahorros con saving_eur > 0 en el ámbito analizado × 52 semanas.",
            "total_revenue_eur": round(total_rev, 2),
            "total_distance_baseline_km": round(total_dist_b, 3),
            "total_distance_optimized_km": round(total_dist_o, 3),
        },
        "priority_weights": dict(PRIORITY_WEIGHTS),
        "priority_breakdown_eur_linear_positive_routes": prio_money,
        "best_route": best_route_block,
        "routes": route_rows_list,
    }

    with open(os.path.join(root, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    route_index_sorted = sorted(route_index, key=lambda x: (x["date"], x["route_code"], x["transport_id"]))
    with open(os.path.join(root, "route_index.json"), "w", encoding="utf-8") as f:
        json.dump(route_index_sorted, f, indent=2, default=str)

    if b3_sample and isinstance(b3_sample, dict) and b3_sample_context:
        fc = str(b3_sample_context.get("fecha") or "")
        rc = str(b3_sample_context.get("ruta") or "")
        for _tid_key, zb3 in b3_sample.items():
            if not isinstance(zb3, dict):
                continue
            fn_shard = _write_loading_shard(zb3, fecha=fc, route_code=rc, out_loading_dir=loading_dir)
            written_loading_fnames.add(str(fn_shard).replace(".json", "").strip())

    for fname_only, path in sorted(written_route_paths.items()):
        base = fname_only.replace(".json", "").strip()
        if base in written_loading_fnames:
            continue
        try:
            with open(path, encoding="utf-8") as rf:
                dr = json.load(rf)
        except (OSError, json.JSONDecodeError):
            continue
        _write_synthetic_loading_file(dr, fname=fname_only.replace(".json", ""), out_loading_dir=loading_dir)

    return {
        "summary": os.path.join(root, "summary.json"),
        "route_index": os.path.join(root, "route_index.json"),
        "routes_dir": routes_dir,
        "loading_dir": loading_dir,
    }
