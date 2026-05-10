# -*- coding: utf-8 -*-
"""Helpers for full pipeline: enumerate CSV days, cost baselines, savings aggregation."""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd


# Production scope — first full Mon–Fri window (Cabecera "Creado el")
TARGET_WEEK: Dict[str, str] = {"start": "02/02/2026", "end": "06/02/2026"}
HACKATON_SHEET_CABECERA = "Cabecera Transporte"
HACKATON_SHEET_DETALLE = "Detalle entrega"
ENV_HACKATON_SESSION = "INTERHACK_HACKATON_XLSX"

# Same tariffs as Block 2 routing (see block2_maps_routing)
FUEL_EUR_PER_KM = 0.12
OP_EUR_PER_MIN = 0.35


def _norm_hdr(col: Any) -> str:
    return "".join(str(col).strip().lower().split())


def _cabecera_creado_col(cabecera_df: pd.DataFrame) -> str:
    for c in cabecera_df.columns:
        if "creado" in _norm_hdr(c):
            return str(c)
    raise ValueError("Cabecera: no encuentro una columna de fecha tipo 'Creado el'")


def _cabecera_entrega_col(df: pd.DataFrame) -> str:
    """Columna Entrega en cabecera o detalle."""
    for c in df.columns:
        if _norm_hdr(c) == "entrega":
            return str(c)
    for c in df.columns:
        if "entrega" in _norm_hdr(c):
            return str(c)
    raise ValueError("No encuentro columna 'Entrega'")


def _entrega_series_norm(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.split(".").str[0]


def detalle_pairs_from_filtered_df(detalle_df: pd.DataFrame) -> List[Tuple[str, str]]:
    """(Ruta, FECHA_dd/mm/yyyy) únicos ordenados cronológicamente."""
    if detalle_df.empty or "Ruta" not in detalle_df.columns or "FECHA" not in detalle_df.columns:
        return []
    df = detalle_df.copy()
    df["_fs"] = df["FECHA"].map(normalize_fecha_str)
    df = df.dropna(subset=["_fs"])
    df["Ruta"] = df["Ruta"].astype(str).str.strip()
    pairs = df[["Ruta", "_fs"]].drop_duplicates()
    out = [(str(r).strip(), str(f)) for r, f in pairs.itertuples(index=False)]
    out.sort(key=lambda x: (datetime.strptime(x[1], "%d/%m/%Y"), x[0]))
    return out


def _partition_by_transport(
    cabecera_df: pd.DataFrame,
    detalle_df: pd.DataFrame,
) -> Dict[str, Any]:
    return {"pairs": detalle_pairs_from_filtered_df(detalle_df)}


def partition_routes(
    cabecera_df: pd.DataFrame,
    detalle_df: pd.DataFrame,
    date_start: Optional[str] = None,
    date_end: Optional[str] = None,
    *,
    ruta_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """
    If date_start and date_end are provided, filters cabecera to that 'Creado el' span,
    restricts detalle to Entrega IDs still present (join), then restricts cabecera to
    matched entregas. Applied before pairing — rows outside the window never reach optimise().
    Optionally filter to one Ruta (detalle FECHA semantics unchanged).
    """
    cabecera_df = cabecera_df.copy()
    detalle_df = detalle_df.copy()

    if date_start and date_end:
        c_created = _cabecera_creado_col(cabecera_df)
        c_ent = _cabecera_entrega_col(cabecera_df)
        ts = pd.to_datetime(cabecera_df[c_created], dayfirst=True, errors="coerce").dt.normalize()
        ds = pd.to_datetime(str(date_start), dayfirst=True).normalize()
        de = pd.to_datetime(str(date_end), dayfirst=True).normalize()
        mask = ts.notna() & (ts >= ds) & (ts <= de)
        cabecera_df = cabecera_df.loc[mask].reset_index(drop=True)
        eids = pd.Series(_entrega_series_norm(cabecera_df[c_ent])).unique()
        d_ent_col = _cabecera_entrega_col(detalle_df)
        det_norm = _entrega_series_norm(detalle_df[d_ent_col])
        detalle_df = detalle_df.loc[det_norm.isin(eids)].reset_index(drop=True)
        det_ids = pd.Series(_entrega_series_norm(detalle_df[d_ent_col])).unique()
        cab_norm = _entrega_series_norm(cabecera_df[c_ent])
        cabecera_df = cabecera_df.loc[cab_norm.isin(det_ids)].reset_index(drop=True)

    if ruta_filter:
        rf = str(ruta_filter).strip()
        detalle_df = detalle_df.loc[detalle_df["Ruta"].astype(str).str.strip() == rf].reset_index(drop=True)
        d_ent_col = _cabecera_entrega_col(detalle_df)
        det_ids = pd.Series(_entrega_series_norm(detalle_df[d_ent_col])).unique()
        c_ent = _cabecera_entrega_col(cabecera_df)
        cab_norm = _entrega_series_norm(cabecera_df[c_ent])
        cabecera_df = cabecera_df.loc[cab_norm.isin(det_ids)].reset_index(drop=True)

    part = _partition_by_transport(cabecera_df, detalle_df)
    part["cabecera_df"] = cabecera_df
    part["detalle_df"] = detalle_df
    return part


def write_filtered_hackaton_workbook(
    source_xlsx: str,
    cabecera_df: pd.DataFrame,
    detalle_df: pd.DataFrame,
    dest_path: str,
) -> None:
    """Copia todas las hojas del libro base y sustituye cabecera + detalle ya filtrados."""
    xl = pd.read_excel(source_xlsx, sheet_name=None)
    xl[HACKATON_SHEET_CABECERA] = cabecera_df
    xl[HACKATON_SHEET_DETALLE] = detalle_df
    writer = pd.ExcelWriter(dest_path, engine="openpyxl")
    try:
        for name, sdf in xl.items():
            sdf.to_excel(writer, sheet_name=name, index=False)
    finally:
        writer.close()


def apply_partition_env_and_resolve_hackaton(
    source_abs: str,
    out_root: str,
    partition: Dict[str, Any],
    *,
    write_name: str = "hackaton_partition.xlsx",
) -> str:
    """
    Persist filtered workbook next to outputs and pin INTERHACK_HACKATON_XLSX.
    Returns absolute path used for Block 1 + count_comandas + Block 3.
    """
    dest = os.path.join(out_root, write_name)
    write_filtered_hackaton_workbook(source_abs, partition["cabecera_df"], partition["detalle_df"], dest)
    session = os.path.abspath(dest)
    os.environ[ENV_HACKATON_SESSION] = session
    return session


def clear_partition_hackaton_env() -> None:
    os.environ.pop(ENV_HACKATON_SESSION, None)


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


def block2_baseline_rand_maps_totals(block2: Dict[str, Any]) -> Dict[str, Any]:
    """
    Aggregate baseline (= Block 1 random stop order projected onto optimized served stops),
    valued with the Distance Matrix legs + same EUR/km and EUR/op-min as clusters.
    Per-cluster summaries come from optimize_cluster_route (baseline_rand_maps_*).
    """
    km = fu = op = total = 0.0
    for v in block2.values():
        if not isinstance(v, dict):
            continue
        sm = v.get("route_summary") or {}
        km += float(sm.get("baseline_rand_maps_distance_km") or 0.0)
        fu += float(sm.get("baseline_rand_maps_fuel_cost") or 0.0)
        op += float(sm.get("baseline_rand_maps_operational_cost") or 0.0)
        total += float(sm.get("baseline_rand_maps_total_eur") or 0.0)
    return {
        "total_rand_maps_distance_km": round(km, 3),
        "total_rand_maps_fuel_eur": round(fu, 4),
        "total_rand_maps_operational_eur": round(op, 4),
        "baseline_maps_total_eur": round(total, 4),
    }


def classify_route_value_segment(stop_count: int) -> str:
    """Operational bucket for reporting (<8 stops flagged separately from headline savings)."""
    if stop_count >= 15:
        return "high_value"
    if stop_count >= 8:
        return "medium_value"
    return "low_value"


PRIMARY_SAVINGS_MIN_STOPS = 8
HIGH_VALUE_ROUTE_MIN_STOPS = 15


def resolve_pipeline_baseline_eur(block1: Dict[str, Any], block2: Dict[str, Any]) -> Dict[str, Any]:
    """
    Primary baseline for savings: Distance Matrix (same legs as clusters), random visitation
    constrained to stops actually served. Includes legacy Haversine figures from Block 1 as reference_only.
    """
    legacy = baseline_eur_from_block1_rand(block1)
    if block2:
        m = block2_baseline_rand_maps_totals(block2)
        primary = float(m["baseline_maps_total_eur"])
        return {
            "baseline_total_eur": primary,
            "baseline_rand_maps_distance_km": m["total_rand_maps_distance_km"],
            "baseline_method": "google_dm_rand_order_same_served_stops_clusterwise",
            "legacy_rand_dist_km": legacy["rand_dist_km"],
            "legacy_rand_time_min": legacy["rand_time_min"],
            "legacy_haversine_baseline_total_eur": legacy["baseline_total_eur"],
        }
    primary = legacy["baseline_total_eur"]
    return {
        "baseline_total_eur": primary,
        "baseline_rand_maps_distance_km": legacy["rand_dist_km"],
        "baseline_method": "block1_haversine_fallback_no_block2_data",
        "legacy_rand_dist_km": legacy["rand_dist_km"],
        "legacy_rand_time_min": legacy["rand_time_min"],
        "legacy_haversine_baseline_total_eur": primary,
    }


def baseline_eur_from_block1_rand(block1: Dict[str, Any]) -> Dict[str, float]:
    """
    Legacy Haversine estimate from Block 1 random permutation (straight-line kilometres).
    For fair savings vs optimised Block 2, prefer resolve_pipeline_baseline_eur(...) instead.
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
