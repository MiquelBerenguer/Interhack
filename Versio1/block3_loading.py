# -*- coding: utf-8 -*-
"""
Block 3 - Physical loading plan and warehouse pick sequence from Block 2 route.

Pure data transformation: no API calls. File paths are always passed in (Block3Paths).
"""
from __future__ import annotations

import json
import logging
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

# --- Configurable constants -------------------------------------------------
PALLET_VOLUME_L = 2030.0  # usable volume per pallet slot (L)
PALLET_MAX_WEIGHT_KG = 800.0

STACKING_RULES: Dict[str, Dict[str, Any]] = {
    "BARRIL": {
        "can_go_on_top_of": [],
        "can_support": ["CAJA", "OTRO"],
        "max_stack_height": 1,
        "max_weight_on_top_kg": 50.0,
    },
    "CAJA": {
        "can_go_on_top_of": ["BARRIL", "CAJA", "OTRO"],
        "can_support": ["CAJA", "BOTELLA", "OTRO"],
        "max_stack_height": 3,
        "max_weight_on_top_kg": 80.0,
    },
    "BOTELLA": {
        "can_go_on_top_of": ["BARRIL", "CAJA", "BOTELLA", "OTRO"],
        "can_support": ["BOTELLA", "OTRO"],
        "max_stack_height": 2,
        "max_weight_on_top_kg": 20.0,
    },
    "OTRO": {
        "can_go_on_top_of": ["CAJA", "BOTELLA", "OTRO"],
        "can_support": ["OTRO"],
        "max_stack_height": 4,
        "max_weight_on_top_kg": 30.0,
    },
}


@dataclass
class Block3Paths:
    """All input paths - never hardcode outside defaults in CLI."""

    block2_json: str
    hackaton_xlsx: str
    zm040_xlsx: str
    materiales_xlsx: str
    hackaton_sheet_detalle: str = "Detalle entrega"
    materiales_sheet: str = "Materiales zubic"


@dataclass
class LoadingItem:
    material: str
    description: str
    quantity: int
    uma: str
    weight_kg: float
    volume_l: float
    product_type: str
    stack_layer: int
    stacking_note: str


@dataclass
class LoadingPlanEntry:
    load_sequence: int
    parcela: int
    customer_name: str
    delivery_sequence: int
    items: List[LoadingItem]
    parcela_weight_kg: float
    parcela_volume_l: float
    parcela_volume_pct: float


@dataclass
class PickSequenceEntry:
    pick_order: int
    material: str
    description: str
    quantity: int
    uma: str
    warehouse_location: str
    load_to_parcela: int
    stack_layer: int
    customer_name: str
    delivery_sequence: int


# --- Column / string helpers -------------------------------------------------


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def _find_column(df: pd.DataFrame, *must_contain: str) -> Optional[str]:
    for c in df.columns:
        low = _strip_accents(str(c)).lower()
        if all(_strip_accents(m).lower() in low for m in must_contain):
            return str(c)
    return None


def _norm_entrega(val: Any) -> str:
    if pd.isna(val):
        return ""
    if isinstance(val, float) and val == int(val):
        return str(int(val))
    return str(val).strip()


def _norm_mat_uma(mat: str, uma: str) -> Tuple[str, str]:
    return str(mat).strip().upper(), str(uma).strip().upper()


def _dim_to_cm(value: float, unit_raw: Any) -> float:
    if pd.isna(unit_raw):
        u = "CM"
    else:
        u = _strip_accents(str(unit_raw).strip()).upper()
    if u in ("MM", "MILIMETRO", "MILIMETROS"):
        return float(value) / 10.0
    if u in ("M", "METRO", "METROS"):
        return float(value) * 100.0
    return float(value)


def classify_product_type(
    material: str,
    uma: str,
    description: str,
    volume_l_per_unit: Optional[float] = None,
) -> str:
    """
    Returns one of: BARRIL, CAJA, BOTELLA, OTRO
    """
    u = str(uma).strip().upper()
    m = str(material).strip().upper()
    desc = str(description).strip().upper()

    if u in ("BRL", "BAR") or m.startswith("ED3") or m.startswith("VO2"):
        return "BARRIL"
    if u == "CAJ":
        return "CAJA"
    if u in ("BOT", "UN"):
        if volume_l_per_unit is not None and volume_l_per_unit <= 2.0 + 1e-6:
            return "BOTELLA"
        if volume_l_per_unit is None and ("BOTELLA" in desc or "BOT" in desc):
            return "BOTELLA"
        if volume_l_per_unit is not None:
            return "OTRO"
        return "OTRO"
    return "OTRO"


def _zm040_dims_from_row(row: pd.Series, zdf: pd.DataFrame) -> Tuple[float, float, float, float, float]:
    """Return long_cm, wide_cm, high_cm, volume_l, weight_kg from a ZM040 row."""
    cols = list(zdf.columns)

    def _dim_pair(name_key: str) -> Tuple[float, Any]:
        dc = _find_column(zdf, name_key) or (
            next((c for c in cols if str(c).lower().strip() == name_key.lower()), None)
        )
        if dc is None:
            return 0.0, "CM"
        idx = cols.index(dc)
        val = float(row[dc] or 0.0) if pd.notna(row.get(dc)) else 0.0
        unit_col = cols[idx + 1] if idx + 1 < len(cols) else None
        unit_raw = row[unit_col] if unit_col is not None else "CM"
        return val, unit_raw

    long_raw, unit_l = _dim_pair("longitud")
    ancho_raw, unit_a = _dim_pair("ancho")
    alt_raw, unit_h = _dim_pair("altura")
    long_cm = _dim_to_cm(long_raw, unit_l)
    wide_cm = _dim_to_cm(ancho_raw, unit_a)
    high_cm = _dim_to_cm(alt_raw, unit_h)
    vol_c = _find_column(zdf, "volumen") or next((c for c in cols if "volumen" in str(c).lower()), cols[13])
    peso_c = _find_column(zdf, "peso", "bruto") or next(
        (c for c in cols if "peso" in str(c).lower() and "bruto" in str(c).lower()), None
    )
    if peso_c is None:
        peso_c = next((c for c in cols if str(c).lower().strip() == "peso bruto"), cols[16])
    vol = float(row[vol_c] or 0.0) if vol_c and pd.notna(row.get(vol_c)) else 0.0
    peso = float(row[peso_c] or 0.0) if peso_c and pd.notna(row.get(peso_c)) else 0.0
    return long_cm, wide_cm, high_cm, vol, peso


def build_zm040_material_uma_index(zdf: pd.DataFrame) -> Dict[Tuple[str, str], pd.Series]:
    idx: Dict[Tuple[str, str], pd.Series] = {}
    mat_col = _find_column(zdf, "material") or "Material"
    uma_col = _find_column(zdf, "uma") or "UMA"
    for _, row in zdf.iterrows():
        m = str(row[mat_col]).strip().upper()
        u = str(row[uma_col]).strip().upper()
        if not m:
            continue
        idx[(m, u)] = row
    return idx


def _pal_row_for_material(zdf: pd.DataFrame, material: str) -> Optional[pd.Series]:
    mat_col = _find_column(zdf, "material") or "Material"
    uma_col = _find_column(zdf, "uma") or "UMA"
    m = str(material).strip().upper()
    sub = zdf[zdf[mat_col].astype(str).str.strip().str.upper() == m]
    pal = sub[sub[uma_col].astype(str).str.strip().str.upper() == "PAL"]
    if pal.empty:
        return None
    return pal.iloc[0]


def get_item_dimensions(
    material: str,
    uma: str,
    quantity: int,
    zm040_index: Dict[Tuple[str, str], pd.Series],
    zm040_df: pd.DataFrame,
    pal_fallback_warned: Optional[set] = None,
) -> Dict[str, float]:
    """
    Physical dimensions for `quantity` units of material+uma.
    MM -> CM; volume in L/DM3 treated as liters.
    Zero dims -> PAL / Contador fallback with warning.
    """
    if pal_fallback_warned is None:
        pal_fallback_warned = set()
    m, u = _norm_mat_uma(material, uma)
    q = max(0, int(quantity))
    zero_dims = {
        "length_cm": 0.0,
        "width_cm": 0.0,
        "height_cm": 0.0,
        "volume_l": 0.0,
        "weight_kg": 0.0,
        "total_volume_l": 0.0,
        "total_weight_kg": 0.0,
    }
    row = zm040_index.get((m, u))
    used_pal_fallback = False
    contador_target = 1.0

    if row is None:
        logger.warning("ZM040 missing Material+UMA: %s + %s", m, u)
        return zero_dims

    try:
        contador_col = _find_column(zm040_df, "contador") or "Contador"
        contador_target = float(row[contador_col] or 1.0)
        if contador_target <= 0:
            contador_target = 1.0
    except Exception:
        contador_target = 1.0

    long_cm, wide_cm, high_cm, vol_l, w_kg = _zm040_dims_from_row(row, zm040_df)
    dim_all_zero = long_cm <= 1e-9 and wide_cm <= 1e-9 and high_cm <= 1e-9

    if dim_all_zero and vol_l <= 1e-9:
        pal = _pal_row_for_material(zm040_df, m)
        if pal is not None:
            key = (m, u)
            if key not in pal_fallback_warned:
                logger.warning(
                    "ZM040 zero dimensions for %s+%s - using PAL row / Contador fallback",
                    m,
                    u,
                )
                pal_fallback_warned.add(key)
            used_pal_fallback = True
            plong, pwide, phigh, pvol, pw = _zm040_dims_from_row(pal, zm040_df)
            try:
                c_col = _find_column(zm040_df, "contador") or "Contador"
                pal_c = float(pal[c_col] or 1.0)
            except Exception:
                pal_c = 1.0
            if pal_c <= 0:
                pal_c = 1.0
            # Per sales-unit (this UMA) from full pallet
            if pvol > 0:
                vol_l = pvol / pal_c * contador_target
            elif plong > 0 and pwide > 0 and phigh > 0:
                vol_l = (plong * pwide * phigh) / 1000.0 / pal_c * contador_target
            if pw > 0:
                w_kg = pw / pal_c * contador_target
            if plong > 0:
                long_cm = plong / (pal_c ** (1 / 3))  # rough; prefer volume
            if pwide > 0:
                wide_cm = pwide / (pal_c ** (1 / 3))
            if phigh > 0:
                high_cm = phigh / (pal_c ** (1 / 3))
        else:
            logger.warning("ZM040 zero dimensions and no PAL row for material %s", m)

    if vol_l <= 0 and long_cm > 0 and wide_cm > 0 and high_cm > 0:
        vol_l = (long_cm * wide_cm * high_cm) / 1000.0

    per_vol = max(0.0, float(vol_l))
    per_w = max(0.0, float(w_kg))
    return {
        "length_cm": float(long_cm),
        "width_cm": float(wide_cm),
        "height_cm": float(high_cm),
        "volume_l": per_vol,
        "weight_kg": per_w,
        "total_volume_l": per_vol * q,
        "total_weight_kg": per_w * q,
        "_pal_fallback": 1.0 if used_pal_fallback else 0.0,
    }


def load_hackaton_detalle(paths: Block3Paths) -> pd.DataFrame:
    xl = pd.read_excel(paths.hackaton_xlsx, sheet_name=paths.hackaton_sheet_detalle)
    return xl


def load_materiales(paths: Block3Paths) -> pd.DataFrame:
    return pd.read_excel(paths.materiales_xlsx, sheet_name=paths.materiales_sheet)


def load_zm040(paths: Block3Paths) -> pd.DataFrame:
    return pd.read_excel(paths.zm040_xlsx)


def build_materiales_lookup(mat_df: pd.DataFrame) -> Tuple[Dict[str, str], Dict[str, str]]:
    """material -> ubic, material -> description"""
    mat_c = next((c for c in mat_df.columns if str(c).strip().lower() == "material"), None) or "Material"
    ubic_c = _find_column(mat_df, "ubic") or "Ubic."
    desc_c = None
    for c in mat_df.columns:
        low = _strip_accents(str(c)).lower()
        if "nmero" in low and "material" in low:
            desc_c = str(c)
            break
        if "material" in low and "nmero" in low.replace("-", "u"):
            desc_c = str(c)
            break
    if desc_c is None:
        desc_c = _find_column(mat_df, "nmero", "material") or mat_df.columns[1]

    ubic: Dict[str, str] = {}
    desc: Dict[str, str] = {}
    for _, row in mat_df.iterrows():
        mk = str(row[mat_c]).strip().upper()
        if not mk:
            continue
        ubic[mk] = str(row[ubic_c]).strip() if pd.notna(row.get(ubic_c)) else "UNKNOWN"
        desc[mk] = str(row[desc_c]).strip() if desc_c and pd.notna(row.get(desc_c)) else ""
    return ubic, desc


def build_enriched_route(
    block2_result: dict,
    hackaton_df: pd.DataFrame,
    transporter_id: str,
    truck_slots: int,
) -> List[dict]:
    """
    Join final_route stops with Hackaton lines on customer_id == Entrega.
    """
    entrega_col = _find_column(hackaton_df, "entrega") or "Entrega"
    mat_col = _find_column(hackaton_df, "material") or "Material"
    qty_col = _find_column(hackaton_df, "cantidad") or "Cantidad entrega"
    uma_col = _find_column(hackaton_df, "medida", "venta") or "Un.medida venta"

    cluster = block2_result.get(transporter_id)
    if not cluster:
        logger.warning("No cluster %s in block2_result", transporter_id)
        return []

    final_route = cluster.get("final_route") or []
    out: List[dict] = []

    den_col = _find_column(hackaton_df, "denomin")
    if den_col is None:
        den_col = next((c for c in hackaton_df.columns if "denomin" in str(c).lower()), None)

    for seq, stop in enumerate(final_route):
        cust = _norm_entrega(stop.get("customer_id"))
        sub = hackaton_df[hackaton_df[entrega_col].apply(_norm_entrega) == cust]
        items: List[dict] = []
        if sub.empty:
            logger.warning("Hackaton: no rows for Entrega/customer_id=%s", cust)
        else:
            for _, r in sub.iterrows():
                try:
                    q = int(float(r[qty_col]))
                except (TypeError, ValueError):
                    q = 0
                desc_v = ""
                if den_col is not None and pd.notna(r.get(den_col)):
                    desc_v = str(r[den_col]).strip()
                items.append(
                    {
                        "material": str(r[mat_col]).strip(),
                        "uma": str(r[uma_col]).strip() if pd.notna(r.get(uma_col)) else "",
                        "quantity": q,
                        "description": desc_v,
                    }
                )

        out.append(
            {
                "stop_index": int(stop.get("stop_index", -1)),
                "customer_id": cust,
                "customer_name": str(stop.get("customer_name", "")),
                "delivery_sequence": seq + 1,
                "arrival_time": str(stop.get("arrival_time", "")),
                "departure_time": str(stop.get("departure_time", "")),
                "walking_group_id": stop.get("walking_group_id"),
                "items": items,
                "truck_slots": int(truck_slots),
            }
        )
    return out


def _assign_stack_layers(
    lines: List[Tuple[dict, Dict[str, float], str]],
) -> List[LoadingItem]:
    """
    lines: list of (raw_item, dims dict from get_item_dimensions, product_type)
    Layer bottom->top: BARRIL, CAJA (heavy first), BOTELLA, OTRO
    """
    barril: List[Tuple[dict, Dict[str, float], str]] = []
    caja: List[Tuple[dict, Dict[str, float], str]] = []
    bot: List[Tuple[dict, Dict[str, float], str]] = []
    otro: List[Tuple[dict, Dict[str, float], str]] = []
    for tup in lines:
        t = tup[2]
        if t == "BARRIL":
            barril.append(tup)
        elif t == "CAJA":
            caja.append(tup)
        elif t == "BOTELLA":
            bot.append(tup)
        else:
            otro.append(tup)

    caja.sort(key=lambda x: -x[1].get("total_weight_kg", 0.0))

    ordered: List[Tuple[dict, Dict[str, float], str]] = []
    if barril:
        ordered.extend(barril)
        ordered.extend(caja)
        ordered.extend(bot)
        ordered.extend(otro)
        base_layer = 1
    else:
        ordered.extend(caja)
        ordered.extend(bot)
        ordered.extend(otro)
        base_layer = 1

    loading_items: List[LoadingItem] = []
    layer = base_layer
    prev_type: Optional[str] = None
    for raw, dims, ptype in ordered:
        note = ""
        if ptype == "BARRIL":
            note = "floor only - barril"
        elif prev_type and ptype in STACKING_RULES.get(prev_type, {}).get("can_go_on_top_of", []):
            note = f"on top of {prev_type}"
        loading_items.append(
            LoadingItem(
                material=str(raw["material"]),
                description=str(raw.get("description", "")),
                quantity=int(raw["quantity"]),
                uma=str(raw["uma"]),
                weight_kg=float(dims["total_weight_kg"]),
                volume_l=float(dims["total_volume_l"]),
                product_type=ptype,
                stack_layer=layer,
                stacking_note=note,
            )
        )
        prev_type = ptype
        # Simple layer increment per row (stacking height enforced in validate)
        layer += 1

    return loading_items


def assign_parcelas(
    enriched_route: List[dict],
    truck_slots: int,
    zm040_index: Dict[Tuple[str, str], pd.Series],
    zm040_df: pd.DataFrame,
    materiales_desc: Dict[str, str],
    materiales_ubic: Dict[str, str],
) -> Tuple[List[LoadingPlanEntry], List[str], Set[Tuple[str, str]]]:
    """
    LIFO: delivery_sequence 1 -> lowest parcela (front); last delivery -> high parcela (back).
    Last physical parcela slot reserved for empty returns.
    """
    warnings: List[str] = []
    reserved_tail = 1
    max_cargo_parcela = max(1, int(truck_slots) - reserved_tail)
    seq_order: List[int] = []
    _seen_ds: set = set()
    for stop in enriched_route:
        ds = int(stop["delivery_sequence"])
        if ds not in _seen_ds:
            _seen_ds.add(ds)
            seq_order.append(ds)

    # Recompute payloads per stop in one pass
    stop_chunks: Dict[int, List[List[LoadingItem]]] = {ds: [] for ds in seq_order}
    stop_meta: Dict[int, str] = {}
    pal_warn2: set = set()

    for stop in enriched_route:
        ds = int(stop["delivery_sequence"])
        cname = str(stop["customer_name"])
        stop_meta[ds] = cname
        raw_lines = []
        for it in stop["items"]:
            dims = get_item_dimensions(
                it["material"], it["uma"], int(it["quantity"]), zm040_index, zm040_df, pal_warn2
            )
            desc = it.get("description") or materiales_desc.get(str(it["material"]).strip().upper(), "")
            vol_pu = dims["volume_l"] / max(1, int(it["quantity"])) if it["quantity"] else dims["volume_l"]
            ptype = classify_product_type(it["material"], it["uma"], desc, vol_pu)
            raw_lines.append((it, dims, ptype))

        if not raw_lines:
            continue
        cur = []
        cur_v = 0.0
        cur_w = 0.0
        for tup in raw_lines:
            _, d, _ = tup
            line_v = d["total_volume_l"]
            line_w = d["total_weight_kg"]
            if cur and (cur_v + line_v > PALLET_VOLUME_L + 1e-6 or cur_w + line_w > PALLET_MAX_WEIGHT_KG + 1e-6):
                if line_v > PALLET_VOLUME_L + 1e-6 or line_w > PALLET_MAX_WEIGHT_KG + 1e-6:
                    warnings.append(
                        f"Line {tup[0].get('material')}+{tup[0].get('uma')} exceeds single-parcela "
                        f"volume/weight; keeping on one parcela with warning (seq={ds})."
                    )
                stop_chunks[ds].append(_assign_stack_layers(cur))
                cur = [tup]
                cur_v, cur_w = line_v, line_w
            else:
                cur.append(tup)
                cur_v += line_v
                cur_w += line_w
        if cur:
            stop_chunks[ds].append(_assign_stack_layers(cur))

    # Assign parcela numbers: increasing with delivery_sequence; multiple chunks use consecutive parcelas
    parcela_entries: List[LoadingPlanEntry] = []
    next_parcela = 1
    for ds in seq_order:
        cname = stop_meta.get(ds, "")
        chunks_li = stop_chunks.get(ds) or [[]]
        for items in chunks_li:
            if not items:
                continue
            if next_parcela > max_cargo_parcela:
                warnings.append(
                    f"Parcela overflow: need parcela {next_parcela} but max cargo parcela is {max_cargo_parcela} "
                    f"(delivery_sequence={ds})."
                )
            pv = sum(i.volume_l for i in items)
            pw = sum(i.weight_kg for i in items)
            pct = min(100.0, 100.0 * pv / PALLET_VOLUME_L) if PALLET_VOLUME_L > 0 else 0.0
            parcela_entries.append(
                LoadingPlanEntry(
                    load_sequence=0,  # filled after sort
                    parcela=next_parcela,
                    customer_name=cname,
                    delivery_sequence=ds,
                    items=items,
                    parcela_weight_kg=pw,
                    parcela_volume_l=pv,
                    parcela_volume_pct=round(pct, 2),
                )
            )
            next_parcela += 1

    # load_sequence: highest parcela (back) loaded first -> load_sequence 1 = max parcela
    max_p = max((e.parcela for e in parcela_entries), default=0)
    for e in parcela_entries:
        e.load_sequence = max_p - e.parcela + 1

    return parcela_entries, warnings, pal_warn2


def build_pick_sequence(plan_entries: List[LoadingPlanEntry], materiales_ubic: Dict[str, str]) -> List[PickSequenceEntry]:
    """Primary load_sequence asc; secondary Ubic asc; tertiary weight desc."""
    loc_unknown_warn = set()

    def ubic_of(it: LoadingItem) -> str:
        loc = materiales_ubic.get(it.material.strip().upper(), "UNKNOWN")
        if loc == "UNKNOWN" and it.material not in loc_unknown_warn:
            logger.warning("Materiales: no Ubic for material %s - UNKNOWN", it.material)
            loc_unknown_warn.add(it.material)
        return loc

    rows: List[Tuple[int, str, float, LoadingItem, LoadingPlanEntry]] = []
    for ent in plan_entries:
        for it in ent.items:
            rows.append(
                (
                    ent.load_sequence,
                    ubic_of(it),
                    -it.weight_kg,
                    it,
                    ent,
                )
            )
    rows.sort(key=lambda x: (x[0], x[1], x[2]))

    picks: List[PickSequenceEntry] = []
    for i, (_, loc, _, it, ent) in enumerate(rows, start=1):
        picks.append(
            PickSequenceEntry(
                pick_order=i,
                material=it.material,
                description=it.description,
                quantity=it.quantity,
                uma=it.uma,
                warehouse_location=loc,
                load_to_parcela=ent.parcela,
                stack_layer=it.stack_layer,
                customer_name=ent.customer_name,
                delivery_sequence=ent.delivery_sequence,
            )
        )
    return picks


def _count_types(entries: List[LoadingPlanEntry]) -> Tuple[int, int, int, int]:
    b = c = bo = o = 0
    for e in entries:
        for it in e.items:
            if it.product_type == "BARRIL":
                b += it.quantity
            elif it.product_type == "CAJA":
                c += it.quantity
            elif it.product_type == "BOTELLA":
                bo += it.quantity
            else:
                o += it.quantity
    return b, c, bo, o


def validate_loading_plan(
    plan_entries: List[LoadingPlanEntry],
    enriched_route: List[dict],
    stacking_rules: Dict[str, Dict[str, Any]],
    pal_fallback_keys: set,
) -> List[str]:
    msgs: List[str] = []
    total_items_in = 0
    for stop in enriched_route:
        for it in stop["items"]:
            total_items_in += int(it.get("quantity", 0))

    total_items_plan = sum(it.quantity for e in plan_entries for it in e.items)
    if total_items_in != total_items_plan:
        msgs.append(
            f"Item count mismatch: enriched route qty sum={total_items_in} vs plan qty sum={total_items_plan}"
        )

    for e in plan_entries:
        if e.parcela_weight_kg > PALLET_MAX_WEIGHT_KG + 1e-3:
            msgs.append(f"Parcela {e.parcela}: weight {e.parcela_weight_kg:.1f} kg > limit {PALLET_MAX_WEIGHT_KG}")
        if e.parcela_volume_l > PALLET_VOLUME_L + 1e-3:
            msgs.append(f"Parcela {e.parcela}: volume {e.parcela_volume_l:.1f} L > limit {PALLET_VOLUME_L}")
        for it in e.items:
            if it.product_type == "BARRIL" and it.stack_layer > 1:
                msgs.append(f"BARRIL on layer {it.stack_layer} (material {it.material}) - must be floor")
            mh = stacking_rules.get(it.product_type, {}).get("max_stack_height", 99)
            if it.stack_layer > mh + 5:  # loose check (we increment per line)
                msgs.append(f"Stack layer {it.stack_layer} may exceed max_stack_height for {it.product_type}")

    by_seq: Dict[int, List[int]] = {}
    for e in plan_entries:
        by_seq.setdefault(e.delivery_sequence, []).append(e.parcela)
    max_for_prev = 0
    for ds in sorted(by_seq):
        mn = min(by_seq[ds])
        mx = max(by_seq[ds])
        if mn <= max_for_prev:
            msgs.append(
                f"LIFO violation: delivery_sequence {ds} starts at parcela {mn} "
                f"but earlier deliveries used parcelas up to {max_for_prev}"
            )
        max_for_prev = max(max_for_prev, mx)

    for k in pal_fallback_keys:
        msgs.append(f"PAL fallback used for ZM040 key {k}")
    return msgs


def process_cluster_block3(
    cluster: dict,
    hackaton_df: pd.DataFrame,
    zm040_df: pd.DataFrame,
    zm040_index: Dict[Tuple[str, str], pd.Series],
    materiales_ubic: Dict[str, str],
    materiales_desc: Dict[str, str],
    transporter_id: str,
) -> dict:
    truck_slots = int(cluster.get("truck_slots", 6))
    enriched = build_enriched_route(
        {transporter_id: cluster},
        hackaton_df,
        transporter_id,
        truck_slots,
    )
    plan_entries, assign_warn, pal_warn2 = assign_parcelas(
        enriched,
        truck_slots,
        zm040_index,
        zm040_df,
        materiales_desc,
        materiales_ubic,
    )
    for w in assign_warn:
        logger.warning("%s", w)

    picks = build_pick_sequence(plan_entries, materiales_ubic)
    pal_fb = {f"{a}+{b}" for a, b in pal_warn2}

    val_msgs = validate_loading_plan(plan_entries, enriched, STACKING_RULES, pal_fb)
    warnings = assign_warn + val_msgs

    tw = sum(e.parcela_weight_kg for e in plan_entries)
    tv = sum(e.parcela_volume_l for e in plan_entries)
    heaviest_p = max(plan_entries, key=lambda e: e.parcela_weight_kg).parcela if plan_entries else 0
    max_pct = max((e.parcela_volume_pct for e in plan_entries), default=0.0)
    b, c, bo, o = _count_types(plan_entries)

    def ser_item(it: LoadingItem) -> dict:
        d = asdict(it)
        return d

    def ser_entry(e: LoadingPlanEntry) -> dict:
        d = asdict(e)
        d["items"] = [ser_item(i) for i in e.items]
        return d

    return {
        "transporter_id": cluster.get("transporter_id", transporter_id),
        "transporter_name": cluster.get("transporter_name", ""),
        "truck_type": cluster.get("truck_type", ""),
        "truck_slots": truck_slots,
        "loading_plan": [ser_entry(e) for e in plan_entries],
        "warehouse_pick_sequence": [asdict(p) for p in picks],
        "plan_summary": {
            "total_parcelas_used": len(plan_entries),
            "total_weight_kg": round(tw, 2),
            "total_volume_l": round(tv, 2),
            "heaviest_parcela": heaviest_p,
            "most_loaded_parcela_pct": round(max_pct, 2),
            "barril_count": b,
            "caja_count": c,
            "botella_count": bo,
            "otro_count": o,
        },
        "warnings": warnings,
    }


def run_block3(paths: Block3Paths) -> dict:
    """
    Load all sources and return final output dict keyed by transporter_id.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with open(paths.block2_json, "r", encoding="utf-8") as f:
        block2 = json.load(f)

    hackaton_df = load_hackaton_detalle(paths)
    zm040_df = load_zm040(paths)
    mat_df = load_materiales(paths)
    zm040_index = build_zm040_material_uma_index(zm040_df)
    materiales_ubic, materiales_desc = build_materiales_lookup(mat_df)

    out: dict = {}
    for tid, cluster in block2.items():
        if not isinstance(cluster, dict):
            continue
        if "final_route" not in cluster:
            continue
        out[str(tid)] = process_cluster_block3(
            cluster,
            hackaton_df,
            zm040_df,
            zm040_index,
            materiales_ubic,
            materiales_desc,
            str(tid),
        )
    return out


def loading_item_to_dict(it: LoadingItem) -> dict:
    return asdict(it)
