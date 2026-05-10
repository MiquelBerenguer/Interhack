"""
Dynamic truck capacity in UP:
- N pallet slots = N UP (one full pallet slot = 1 UP)
- First customer outbound uses their actual delivery UP (counts against n_slots - reserved_empty)
- Returns: 60% of delivered UP becomes empties on board; net +40% free space per stop
- Optional reserved empty UP at departure (policy per truck size)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

RETURN_RATE = 0.60
NET_FREE_RATE = 1.0 - RETURN_RATE  # 0.40


TRUCK_SLOTS = {"small": 3, "standard": 6, "large": 8}


def slots_for_truck_type(truck_type: str) -> int:
    t = str(truck_type).strip().lower()
    if t in TRUCK_SLOTS:
        return TRUCK_SLOTS[t]
    if t.isdigit():
        return int(t)
    return TRUCK_SLOTS["standard"]


def reserved_empty_up(truck_type: str, n_slots: int) -> float:
    """
    Practical rule: ~1 slot worth of empty space at departure (33% / 17% / 12.5%).
    Implemented as min(1.0, n_slots * fraction) so it never exceeds one full pallet slot.
    """
    t = str(truck_type).strip().lower()
    if t == "small" or n_slots <= 3:
        return min(1.0, n_slots * (1.0 / 3.0))
    if t == "large" or n_slots >= 8:
        return min(1.0, n_slots * 0.125)
    return min(1.0, n_slots * (1.0 / 6.0))


@dataclass
class LoadLegState:
    """After completing stop index `after_stop_idx` (0 = departure before any stop)."""

    cumulative_used_up: float
    available_free_up: float


@dataclass
class FeasibilityResult:
    ok: bool
    messages: List[str]
    peak_used_up: float
    legs: List[LoadLegState]


def departure_load_constraints(
    delivery_up: Sequence[float],
    first_idx: int,
    n_slots: float,
    reserved_empty: float,
    exclusive_first_slot: bool = True,
    exclusive_slot_capacity_up: float = 1.0,
) -> Tuple[bool, List[str]]:
    """
    At warehouse loading:
    - First customer (first_idx) outbound is their actual UP, bounded by net capacity
      (n_slots - reserved_empty); remaining stops share the leftover headroom.
    - Total outbound <= n_slots - reserved_empty
    - exclusive_slot_capacity_up is retained for API compatibility and is not used.
    """
    msgs: List[str] = []
    if not delivery_up:
        return True, msgs
    n = len(delivery_up)
    tot = sum(delivery_up)
    if tot > n_slots - reserved_empty + 1e-9:
        msgs.append(
            f"Carga total {tot:.3f} UP supera capacidad neta {n_slots - reserved_empty:.3f} UP "
            f"(N={int(n_slots)}, reserva vacio {reserved_empty:.3f})."
        )

    if exclusive_first_slot and 0 <= first_idx < n:
        d1 = delivery_up[first_idx]
        rest = sum(delivery_up[i] for i in range(n) if i != first_idx)
        max_first = float(n_slots) - float(reserved_empty)
        if d1 > max_first + 1e-9:
            msgs.append(
                f"Cliente primero (indice {first_idx}) tiene {d1:.3f} UP > capacidad neta "
                f"{max_first:.3f} UP (N={n_slots:.3f}, reserva vacio {reserved_empty:.3f})."
            )
        max_rest = max(0.0, float(n_slots) - float(reserved_empty) - d1)
        if rest > max_rest + 1e-9:
            msgs.append(
                f"Carga resto clientes {rest:.3f} UP > espacio tras primer cliente {max_rest:.3f} UP."
            )

    ok = not msgs
    return ok, msgs


def full_route_feasibility(
    route_order: Sequence[int],
    stop_delivery_up: Sequence[float],
    n_slots: int,
    truck_type: str = "standard",
    reserved_empty: Optional[float] = None,
    return_rate: float = RETURN_RATE,
    exclusive_first_slot: bool = True,
) -> FeasibilityResult:
    """
    route_order: permutation of stop indices; route_order[0] is first customer (slot 1).
    stop_delivery_up: delivery UP per stop index in original stops array.
    """
    msgs: List[str] = []
    if reserved_empty is None:
        reserved_empty = reserved_empty_up(truck_type, n_slots)

    du = [float(stop_delivery_up[i]) for i in route_order]

    ok_dep, m_dep = departure_load_constraints(
        du,
        first_idx=0,
        n_slots=float(n_slots),
        reserved_empty=reserved_empty,
        exclusive_first_slot=exclusive_first_slot,
    )
    msgs.extend(m_dep)

    total_out = sum(du)
    legs: List[LoadLegState] = []
    running = total_out
    for d in du:
        running = running - (1.0 - return_rate) * float(d)
        legs.append(
            LoadLegState(
                cumulative_used_up=running,
                available_free_up=max(0.0, float(n_slots) - running),
            )
        )

    # Simultaneous outbound + empties peaks at departure for this linear returns model.
    peak = total_out
    if peak > float(n_slots) + 1e-6:
        msgs.append(f"Pico ocupacion salida {peak:.3f} UP > {n_slots} plazas.")

    return FeasibilityResult(
        ok=ok_dep and peak <= float(n_slots) + 1e-6,
        messages=msgs,
        peak_used_up=peak,
        legs=legs,
    )
