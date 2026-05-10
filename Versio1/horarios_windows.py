"""Join Horarios Entrega.xlsx to stops: window_open_min / window_close_min from route departure."""
from __future__ import annotations

import os
import unicodedata
from typing import Optional

import pandas as pd


ROUTE_START_ABS_MIN = 6 * 60  # 06:00 departure default


def _norm_name(s: str) -> str:
    return " ".join(str(s).strip().lower().split())


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def _resolved_horarios_path() -> Optional[str]:
    base = os.path.dirname(os.path.abspath(__file__))
    for p in (
        os.path.join(base, "..", "Hackaton", "Horarios Entrega.XLSX"),
        os.path.join(base, "Horarios Entrega.XLSX"),
        os.path.join(os.getcwd(), "Hackaton", "Horarios Entrega.XLSX"),
    ):
        if os.path.isfile(p):
            return p
    return None


def _col(df: pd.DataFrame, *needles: str) -> Optional[str]:
    for c in df.columns:
        cs = _strip_accents(str(c).lower().replace(" ", "").replace("_", ""))
        for n in needles:
            if _strip_accents(n.lower().replace(" ", "")) in cs:
                return c
    return None


def _time_to_minutes_abs(val) -> Optional[int]:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if hasattr(val, "hour") and hasattr(val, "minute"):
        return int(val.hour) * 60 + int(val.minute)
    s = str(val).strip()
    if not s or s.lower() == "nan":
        return None
    parts = s.replace(".", ":").split(":")
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        return h * 60 + m
    except (ValueError, IndexError):
        return None


def _dia_matches(row_val, route_weekday: int) -> bool:
    """route_weekday: Monday=0. Horarios may use 1=Monday or Spanish text."""
    if row_val is None or (isinstance(row_val, float) and pd.isna(row_val)):
        return True
    if isinstance(row_val, (int, float)) and not pd.isna(row_val):
        d = int(row_val)
        if 1 <= d <= 7:
            return (d - 1) % 7 == route_weekday
    s = _strip_accents(str(row_val).strip().lower())
    days = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")
    if s in days:
        return days.index(s) == route_weekday
    return True


def load_horarios_dataframe() -> Optional[pd.DataFrame]:
    p = _resolved_horarios_path()
    if not p:
        return None
    return pd.read_excel(p)


def attach_stop_windows(
    stops: list,
    horarios: Optional[pd.DataFrame],
    fecha_str: str,
    route_start_abs_min: int = ROUTE_START_ABS_MIN,
) -> None:
    """Mutates stops: window_open_min, window_close_min (minutes from departure)."""
    try:
        fecha_dt = pd.to_datetime(fecha_str, dayfirst=True)
    except Exception:
        fecha_dt = pd.Timestamp.now()
    route_wd = int(fecha_dt.weekday())

    default_open = 0
    default_close = 32 * 60

    if horarios is None or horarios.empty:
        for s in stops:
            s["window_open_min"] = default_open
            s["window_close_min"] = default_close
        return

    col_nombre = _col(horarios, "nombre1", "nombre 1")
    col_dia = _col(horarios, "semana", "diasemana", "dia")
    col_hi = _col(horarios, "horarioinicia", "inicia")
    col_hf = _col(horarios, "horariotermina", "termina")

    if not col_nombre or not col_hi or not col_hf:
        for s in stops:
            s["window_open_min"] = default_open
            s["window_close_min"] = default_close
        return

    by_name: dict[str, tuple[int, int]] = {}
    for _, row in horarios.iterrows():
        if col_dia is not None and not _dia_matches(row.get(col_dia), route_wd):
            continue
        nm = _norm_name(row.get(col_nombre, ""))
        if not nm:
            continue
        oa = _time_to_minutes_abs(row.get(col_hi))
        ca = _time_to_minutes_abs(row.get(col_hf))
        if oa is None or ca is None:
            continue
        open_rel = max(0, oa - route_start_abs_min)
        close_rel = ca - route_start_abs_min
        if close_rel < open_rel:
            close_rel += 24 * 60
        if nm not in by_name:
            by_name[nm] = (open_rel, close_rel)
        else:
            o0, c0 = by_name[nm]
            by_name[nm] = (min(o0, open_rel), max(c0, close_rel))

    for s in stops:
        nm = _norm_name(s.get("name", ""))
        if nm in by_name:
            o, c = by_name[nm]
            s["window_open_min"] = int(o)
            s["window_close_min"] = int(c)
        else:
            s["window_open_min"] = default_open
            s["window_close_min"] = default_close
