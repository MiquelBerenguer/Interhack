"""
Cabecera Transporte sheet: Entrega -> N transporte, Repartidor, Destinatario.
Used for reassignment hints (other routes same day), not a full multi-route optimiser.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

import pandas as pd


def _norm_col(c: str) -> str:
    return "".join(str(c).strip().lower().split())


def _find_col(df: pd.DataFrame, *substrings: str) -> Optional[str]:
    for col in df.columns:
        n = _norm_col(str(col))
        for sub in substrings:
            s = sub.replace(" ", "").lower()
            if s in n:
                return col
    return None


def _parse_excel_date(val) -> Optional[pd.Timestamp]:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        return pd.to_datetime(val, dayfirst=True).normalize()
    except Exception:
        return None


def normalize_cabecera_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    c_ent = _find_col(out, "entrega")
    c_tr = _find_col(out, "transporte", "transporte.")
    if c_tr is None:
        c_tr = _find_col(out, "n transporte")
    c_rep = _find_col(out, "repartidor")
    c_fecha = _find_col(out, "creado")
    dest_cols = [
        c
        for c in out.columns
        if "destinatario" in _norm_col(str(c)) and "mcia" in _norm_col(str(c))
    ]
    dest_cols.sort(key=lambda c: list(out.columns).index(c))
    c_dest_id = dest_cols[0] if dest_cols else None
    c_dest_name = dest_cols[1] if len(dest_cols) > 1 else None
    if c_dest_name is None and c_dest_id:
        cols = list(out.columns)
        i = cols.index(c_dest_id)
        if i + 1 < len(cols):
            c_dest_name = cols[i + 1]

    rename = {}
    if c_ent:
        rename[c_ent] = "_entrega"
    if c_tr:
        rename[c_tr] = "_n_transporte"
    if c_rep:
        rename[c_rep] = "_repartidor"
    if c_fecha:
        rename[c_fecha] = "_creado_el"
    if c_dest_id:
        rename[c_dest_id] = "_dest_id"
    if c_dest_name:
        rename[c_dest_name] = "_dest_name"
    return out.rename(columns=rename)


def entrega_to_row_dict(norm: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    if "_entrega" not in norm.columns:
        return {}
    by_e: Dict[str, Dict[str, Any]] = {}
    for _, row in norm.iterrows():
        e = str(row.get("_entrega", "")).strip().split(".")[0]
        if not e or e.lower() == "nan":
            continue
        if e not in by_e:
            by_e[e] = row.to_dict()
    return by_e


def transports_on_date(norm: pd.DataFrame, day: pd.Timestamp) -> Set[str]:
    if "_creado_el" not in norm.columns or "_n_transporte" not in norm.columns:
        return set()
    day = day.normalize()
    nums: Set[str] = set()
    for _, row in norm.iterrows():
        d = _parse_excel_date(row.get("_creado_el"))
        if d is None or d.normalize() != day:
            continue
        t = str(row.get("_n_transporte", "")).strip()
        if t and t.lower() != "nan":
            nums.add(t)
    return nums


def infer_current_transport_numbers(
    norm: pd.DataFrame,
    entrega_ids: List[str],
    route_day: pd.Timestamp,
) -> List[str]:
    route_day = route_day.normalize()
    e_set = {str(x).strip().split(".")[0] for x in entrega_ids}
    hits: List[str] = []
    for _, row in norm.iterrows():
        d = _parse_excel_date(row.get("_creado_el"))
        if d is None or d.normalize() != route_day:
            continue
        e = str(row.get("_entrega", "")).strip().split(".")[0]
        if e in e_set:
            t = str(row.get("_n_transporte", "")).strip()
            if t:
                hits.append(t)
    if not hits:
        return []
    return [max(set(hits), key=hits.count)]


def alternate_transports_same_day(
    norm: pd.DataFrame,
    route_day: pd.Timestamp,
    exclude: Set[str],
    limit: int = 30,
) -> List[str]:
    all_n = transports_on_date(norm, route_day)
    alts = sorted(all_n - exclude, key=lambda x: (len(str(x)), str(x)))
    return alts[:limit]


def reassignment_hints_for_stops(
    stops: List[dict],
    stop_indices: List[int],
    norm: pd.DataFrame,
    route_day: pd.Timestamp,
    current_transport_nums: List[str],
) -> List[Dict[str, Any]]:
    if norm is None or norm.empty or "_entrega" not in norm.columns:
        return [
            {
                "stop_index": idx,
                "name": stops[idx].get("name", ""),
                "entrega_ids": stops[idx].get("entrega_ids", []),
                "alternate_n_transporte_same_day": [],
                "note": "Cabecera Transporte sheet missing or columns not matched.",
            }
            for idx in stop_indices
        ]
    route_day = route_day.normalize()
    exclude = {str(x).strip() for x in current_transport_nums if str(x).strip()}
    alts_pool = alternate_transports_same_day(norm, route_day, exclude)
    by_e = entrega_to_row_dict(norm)
    hints: List[Dict[str, Any]] = []
    for idx in stop_indices:
        s = stops[idx]
        eids = [str(x).strip().split(".")[0] for x in s.get("entrega_ids", [])]
        assigned = []
        for e in eids:
            r = by_e.get(e)
            if r and r.get("_n_transporte"):
                assigned.append(str(r["_n_transporte"]).strip())
        modal_assigned = max(set(assigned), key=assigned.count) if assigned else None
        dest_name = None
        if eids:
            r0 = by_e.get(eids[0])
            if r0:
                dest_name = r0.get("_dest_name")
        hints.append(
            {
                "stop_index": idx,
                "name": s.get("name", ""),
                "entrega_ids": eids,
                "destinatario_cabecera": dest_name,
                "n_transporte_cabecera": modal_assigned,
                "alternate_n_transporte_same_day": alts_pool,
                "note": "Planner: validate capacity and time windows on alternate N transporte before moving.",
            }
        )
    return hints


def collect_entrega_ids_for_route(deliveries: pd.DataFrame, ruta: str, fecha: Optional[str]) -> List[str]:
    df = deliveries[deliveries["Ruta"] == ruta].copy()
    if fecha:
        df = df[df["FECHA"] == fecha]
    if df.empty:
        latest = deliveries[deliveries["Ruta"] == ruta]["FECHA"].iloc[-1]
        df = deliveries[(deliveries["Ruta"] == ruta) & (deliveries["FECHA"] == latest)].copy()
    out: List[str] = []
    for v in df["Entrega"].astype(str).unique():
        v = v.strip().split(".")[0]
        if v and v.lower() != "nan":
            out.append(v)
    return out


def load_cabecera_sheet(xl: Dict[str, pd.DataFrame]) -> Optional[pd.DataFrame]:
    if "Cabecera Transporte" in xl:
        return xl["Cabecera Transporte"]
    for k in xl:
        lk = str(k).lower()
        if "cabecera" in lk and "transport" in lk:
            return xl[k]
    return None
