# -*- coding: utf-8 -*-
"""Helpers for full pipeline: enumerate CSV days, cost baselines, savings aggregation."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

# Same tariffs as Block 2 routing (see block2_maps_routing)
FUEL_EUR_PER_KM = 0.12
OP_EUR_PER_MIN = 0.35


def normalize_fecha_str(val: Any) -> Optional[str]:
    if pd.isna(val):
        return None
    if isinstance(val, str) and val.strip():
        try:
            return pd.to_datetime(val, dayfirst=True).strftime("%d/%m/%Y")
        except Exception:
            return val.strip()
    ts = pd.to_datetime(val, dayfirst=True, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.strftime("%d/%m/%Y")


def iter_route_fecha_pairs(
    hackaton_xlsx: str,
    sheet: str = "Detalle entrega",
    ruta_filter: Optional[str] = None,
) -> List[Tuple[str, str]]:
    """Unique (Ruta, fecha) pairs from Hackaton, fecha as dd/mm/yyyy."""
    df = pd.read_excel(hackaton_xlsx, sheet_name=sheet)
    if "Ruta" not in df.columns or "FECHA" not in df.columns:
        raise ValueError("Detalle entrega must contain Ruta and FECHA")
    df = df.copy()
    df["_fs"] = df["FECHA"].map(normalize_fecha_str)
    df = df.dropna(subset=["_fs", "Ruta"])
    df["Ruta"] = df["Ruta"].astype(str).str.strip()
    if ruta_filter:
        df = df[df["Ruta"] == str(ruta_filter).strip()]
    pairs = df[["Ruta", "_fs"]].drop_duplicates()
    out = [(str(r).strip(), str(f)) for r, f in pairs.itertuples(index=False)]
    out.sort(key=lambda x: (datetime.strptime(x[1], "%d/%m/%Y"), x[0]))
    return out


def count_comandas_rows(hackaton_xlsx: str, ruta: str, fecha: str, sheet: str = "Detalle entrega") -> int:
    df = pd.read_excel(hackaton_xlsx, sheet_name=sheet)
    df["_fs"] = df["FECHA"].map(normalize_fecha_str)
    mask = (df["Ruta"].astype(str).str.strip() == str(ruta).strip()) & (df["_fs"] == fecha)
    return int(mask.sum())


def block2_route_totals(block2: Dict[str, Any]) -> Dict[str, float]:
    """Sum km, min, fuel EUR, operational EUR across transporter clusters."""
    km = 0.0
    dur = 0.0
    fuel = 0.0
    op = 0.0
    for v in block2.values():
        if not isinstance(v, dict):
            continue
        sm = v.get("route_summary") or {}
        km += float(sm.get("total_distance_km") or 0.0)
        dur += float(sm.get("total_duration_min") or 0.0)
        fuel += float(sm.get("total_fuel_cost") or 0.0)
        op += float(sm.get("total_operational_cost") or 0.0)
    return {
        "total_km": round(km, 3),
        "total_duration_min": round(dur, 2),
        "total_fuel_eur": round(fuel, 4),
        "total_operational_eur": round(op, 4),
        "total_eur": round(fuel + op, 4),
    }


def baseline_eur_from_block1_rand(block1: Dict[str, Any]) -> Dict[str, float]:
    """
    Baseline cost: random (unoptimized) tour distance/time from Block 1,
    priced with the same EUR/km and EUR/min as Block 2 for comparability.
    """
    rd = float(block1.get("rand_dist") or 0.0)
    rt = float(block1.get("rand_time") or 0.0)
    fuel = rd * FUEL_EUR_PER_KM
    op = rt * OP_EUR_PER_MIN
    return {
        "rand_dist_km": round(rd, 3),
        "rand_time_min": round(rt, 2),
        "baseline_fuel_eur": round(fuel, 4),
        "baseline_operational_eur": round(op, 4),
        "baseline_total_eur": round(fuel + op, 4),
    }


def savings_vs_baseline(baseline_total_eur: float, optimized_total_eur: float) -> float:
    return round(baseline_total_eur - optimized_total_eur, 4)


def interval_months_from_strings(fechas: Iterable[str]) -> Tuple[int, float]:
    """Calendar span in days and approximate months (30.44 d/month)."""
    parsed: List[datetime] = []
    for fs in fechas:
        try:
            parsed.append(datetime.strptime(str(fs), "%d/%m/%Y"))
        except Exception:
            continue
    if len(parsed) < 1:
        return 1, 1.0 / 30.44
    span_days = (max(parsed) - min(parsed)).days + 1
    months = span_days / 30.44
    return span_days, max(months, 1e-6)


def annualize_savings(total_savings_eur: float, months_in_data: float) -> float:
    if months_in_data <= 0:
        return 0.0
    return round((total_savings_eur / months_in_data) * 12.0, 2)


def priority_factor_breakdown_eur(
    total_savings_eur: float,
    weights: Dict[str, float],
) -> Dict[str, float]:
    """
    Attribute total savings across priority dimensions in proportion to configured weights.
    Interpretation: illustrative share aligned with the scoring mix, not causal SHAP.
    """
    if total_savings_eur <= 0:
        return {k: 0.0 for k in weights}
    return {k: round(total_savings_eur * float(v), 4) for k, v in weights.items()}
