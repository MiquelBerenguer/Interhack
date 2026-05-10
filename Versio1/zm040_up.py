"""
Pallet units (UP) from ZM040: UP per sales unit = 1 / (PAL row Contador)
for that Material. The PAL Contador is how many sales-base units fit on one pallet.
"""
from __future__ import annotations

import os
from typing import Dict

import pandas as pd

# Fallback when Material or PAL row missing (order-of-magnitude CAJ/BRL)
DEFAULT_UP_PER_CAJ = 1.0 / 70.0
DEFAULT_UP_PER_BRL = 1.0 / 15.0
DEFAULT_UP_PER_OTHER = 1.0 / 60.0


def _norm_uma(u: str) -> str:
    return str(u).strip().upper()


def load_zm040(xlsx_path: str) -> pd.DataFrame:
    if not os.path.isfile(xlsx_path):
        raise FileNotFoundError(f"ZM040 not found: {xlsx_path}")
    return pd.read_excel(xlsx_path)


def build_units_per_pallet_map(df: pd.DataFrame) -> Dict[str, float]:
    """
    For each Material, Contador on the PAL row = units of the primary sales packaging
    per full pallet (e.g. CAJ per pallet, or BRL per pallet for keg-only SKUs).
    """
    dfp = df[df["UMA"].astype(str).str.upper() == "PAL"].copy()
    out: Dict[str, float] = {}
    for _, row in dfp.iterrows():
        mat = str(row["Material"]).strip()
        try:
            c = float(row["Contador"])
        except (TypeError, ValueError):
            continue
        if c <= 0:
            continue
        if mat not in out:
            out[mat] = c
    return out


def up_per_sales_unit(
    material: str,
    uma: str,
    units_per_pallet: Dict[str, float],
) -> float:
    """
    UP for one unit sold in UMA (CAJ, BRL, PAL, UN, ...).
    Primary rule: 1 / units_per_pallet[material] from PAL row.
    If sales UMA is PAL, one PAL = 1 UP.
    """
    mat = str(material).strip()
    u = _norm_uma(uma)
    if u == "PAL":
        return 1.0
    cpp = units_per_pallet.get(mat)
    if cpp and cpp > 0:
        return 1.0 / cpp
    if u == "CAJ":
        return DEFAULT_UP_PER_CAJ
    if u == "BRL":
        return DEFAULT_UP_PER_BRL
    return DEFAULT_UP_PER_OTHER


def line_up(qty: float, material: str, uma: str, units_per_pallet: Dict[str, float]) -> float:
    if qty <= 0:
        return 0.0
    return float(qty) * up_per_sales_unit(material, uma, units_per_pallet)


def aggregate_stop_up(
    items: list,
    units_per_pallet: Dict[str, float],
) -> float:
    total = 0.0
    for it in items:
        total += line_up(
            float(it.get("qty", 0) or 0),
            str(it.get("mat", "")),
            str(it.get("unit", "")),
            units_per_pallet,
        )
    return total
