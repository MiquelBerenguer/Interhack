"""
Block 1: ZonaTransp clusters + weighted priority routing + tight-window cluster check.
Constants for fuel/operational cost until replaced by real tariffs.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

# Economic constants (tune / replace with real data).
FUEL_EUR_PER_KM = 0.42
OP_EUR_PER_MIN = 0.35

PRIORITY_WEIGHTS: Dict[str, float] = {
    "revenue": 0.30,
    "distance": 0.20,
    "fuel_cost": 0.15,
    "travel_time": 0.10,
    "operational_cost": 0.10,
    "route_efficiency": 0.10,
    "time_window_urgency": 0.05,
}

TIGHT_WINDOW_MAX_MIN = 120
MAX_DETOUR_KM = 50.0
URGENCY_MAX_WINDOW_MIN = 480


def validate_weights(weights: Dict[str, float]) -> None:
    total = sum(weights.values())
    if any(v < 0 for v in weights.values()):
        raise ValueError("All weights must be >= 0")
    if not (0.99 <= total <= 1.01):
        raise ValueError(f"Weights must sum to 1.0 , currently {total:.3f}")


def normalize_weights(weights: Dict[str, float]) -> Dict[str, float]:
    total = sum(weights.values())
    return {k: v / total for k, v in weights.items()}


def haversine_km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    R = 6371.0
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    x = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return R * 2 * math.asin(math.sqrt(max(0.0, x)))


def normalize(values: List[float]) -> List[float]:
    if not values:
        return []
    min_v, max_v = min(values), max(values)
    if max_v == min_v:
        return [1.0] * len(values)
    return [(v - min_v) / (max_v - min_v) for v in values]


def time_window_urgency(window_close: int, current_time: int) -> float:
    time_remaining = window_close - current_time
    if time_remaining <= 0:
        return 0.0
    return 1.0 - min(time_remaining / float(URGENCY_MAX_WINDOW_MIN), 1.0)


def route_efficiency(
    current_pos: Tuple[float, float],
    candidate_pos: Tuple[float, float],
    remaining_stops: List[Tuple[float, float]],
) -> float:
    if not remaining_stops:
        return 1.0
    n = len(remaining_stops)
    cx = sum(p[0] for p in remaining_stops) / n
    cy = sum(p[1] for p in remaining_stops) / n
    centroid = (cx, cy)
    direct = haversine_km(current_pos, centroid)
    if direct < 1e-6:
        direct = 1e-6
    via = haversine_km(current_pos, candidate_pos) + haversine_km(candidate_pos, centroid)
    detour = via - direct
    return max(0.0, 1.0 - detour / MAX_DETOUR_KM)


@dataclass
class Delivery:
    stop_idx: int
    revenue: float
    distance_km: float
    fuel_cost: float
    travel_time_min: float
    operational_cost: float
    window_open: int
    window_close: int
    load_up: float
    pos: Tuple[float, float]


def compute_priority_score(
    delivery: Delivery,
    norm: Dict[str, float],
    weights: Dict[str, float],
    current_time: int,
    current_pos: Tuple[float, float],
    remaining_positions: List[Tuple[float, float]],
) -> float:
    urgency = time_window_urgency(delivery.window_close, current_time)
    efficiency = route_efficiency(current_pos, delivery.pos, remaining_positions)
    return (
        weights["revenue"] * norm["revenue"]
        - weights["distance"] * norm["distance"]
        - weights["fuel_cost"] * norm["fuel_cost"]
        - weights["travel_time"] * norm["travel_time"]
        - weights["operational_cost"] * norm["operational_cost"]
        + weights["route_efficiency"] * efficiency
        + weights["time_window_urgency"] * urgency
    )


def is_eligible(
    delivery: Delivery,
    current_time: int,
    truck_capacity_remaining: float,
) -> bool:
    if current_time > delivery.window_close:
        return False
    if current_time + delivery.travel_time_min > delivery.window_close:
        return False
    if delivery.load_up > truck_capacity_remaining + 1e-9:
        return False
    return True


def can_add_to_departure_tour(
    chosen_so_far: List[int],
    candidate_idx: int,
    load_up_fn: Callable[[int], float],
    n_slots: float,
    reserved_empty: float,
) -> bool:
    """
    Departure tour: total outbound UP (first customer uses actual load_up, not a fixed 1 UP)
    must fit in net truck space (n_slots - reserved_empty).
    """
    up = load_up_fn
    r = reserved_empty
    cap = float(n_slots) - float(r)
    total = sum(up(i) for i in chosen_so_far) + up(candidate_idx)
    return total <= cap + 1e-9


def marginal_load_capacity_up(
    route_so_far: List[int],
    stops: Sequence[dict],
    n_slots: float,
    reserved_empty: float,
) -> float:
    """
    Headroom in UP for one more departure outbound after `route_so_far`, net of reserved_empty.
    First stop consumes its real delivery_up against the same total cap (no 1 UP ceiling).
    """
    cap = float(n_slots) - float(reserved_empty)
    if not route_so_far:
        return max(0.0, cap)
    total = sum(float(stops[i].get("delivery_up", 0.0)) for i in route_so_far)
    if total > cap + 1e-9:
        return -1.0
    return cap - total


def pick_next_delivery(
    current_pos: Tuple[float, float],
    current_time: int,
    candidates: List[Delivery],
    truck_capacity_remaining: float,
    weights: Dict[str, float],
) -> Optional[Delivery]:
    eligible = [d for d in candidates if is_eligible(d, current_time, truck_capacity_remaining)]
    if not eligible:
        return None

    norm_revenue = normalize([d.revenue for d in eligible])
    norm_distance = normalize([d.distance_km for d in eligible])
    norm_fuel = normalize([d.fuel_cost for d in eligible])
    norm_time = normalize([d.travel_time_min for d in eligible])
    norm_opcost = normalize([d.operational_cost for d in eligible])
    remaining_positions = [d.pos for d in eligible]

    scored: List[Tuple[float, Delivery]] = []
    for i, delivery in enumerate(eligible):
        norm = {
            "revenue": norm_revenue[i],
            "distance": norm_distance[i],
            "fuel_cost": norm_fuel[i],
            "travel_time": norm_time[i],
            "operational_cost": norm_opcost[i],
        }
        score = compute_priority_score(
            delivery,
            norm,
            weights,
            current_time,
            current_pos,
            remaining_positions,
        )
        scored.append((score, delivery))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def _entry_stop_for_cluster(cluster: List[int], depot: Tuple[float, float], pos_fn: Callable[[int], Tuple[float, float]]) -> int:
    return min(cluster, key=lambda i: haversine_km(depot, pos_fn(i)))


def travel_depot_to_cluster_min(
    cluster: List[int],
    depot: Tuple[float, float],
    pos_fn: Callable[[int], Tuple[float, float]],
    avg_speed_kmh: float,
) -> float:
    if not cluster:
        return 0.0
    entry = _entry_stop_for_cluster(cluster, depot, pos_fn)
    km = haversine_km(depot, pos_fn(entry))
    return km / max(avg_speed_kmh, 1e-6) * 60.0


def min_internal_to_client_min(
    cluster: List[int],
    client_idx: int,
    depot: Tuple[float, float],
    pos_fn: Callable[[int], Tuple[float, float]],
    avg_speed_kmh: float,
    unload_min: float,
) -> float:
    entry = _entry_stop_for_cluster(cluster, depot, pos_fn)
    km = haversine_km(pos_fn(entry), pos_fn(client_idx))
    return km / max(avg_speed_kmh, 1e-6) * 60.0 + unload_min


def is_tight_window(window_open: int, window_close: int) -> bool:
    return (window_close - window_open) < TIGHT_WINDOW_MAX_MIN


def cluster_tight_window_feasible(
    cluster: List[int],
    stops_meta: Sequence[dict],
    depot: Tuple[float, float],
    travel_to_cluster_min: float,
    avg_speed_kmh: float,
    unload_min: float,
) -> Tuple[bool, List[str]]:
    """
    For each client with window width < 120 min (minutes from departure):
    travel_to_cluster + min_internal(entry -> client) must be <= window_close.
    """
    msgs: List[str] = []

    def pos_fn(i: int) -> Tuple[float, float]:
        s = stops_meta[i]
        return (float(s["lat"]), float(s["lon"]))

    arrival_cluster = float(travel_to_cluster_min)
    for c in cluster:
        wo = int(stops_meta[c].get("window_open_min", 0))
        wc = int(stops_meta[c].get("window_close_min", 24 * 60))
        if not is_tight_window(wo, wc):
            continue
        t_int = min_internal_to_client_min(cluster, c, depot, pos_fn, avg_speed_kmh, unload_min)
        eta = arrival_cluster + t_int
        if eta > wc + 1e-6:
            msgs.append(
                f"Tight window: stop {c} ({stops_meta[c].get('name','')}) "
                f"eta {eta:.0f} min > close {wc} (cluster entry travel {travel_to_cluster_min:.0f} min)."
            )
    return (len(msgs) == 0, msgs)


def order_clusters_greedy_nn(
    clusters: Dict[str, List[int]],
    stops: Sequence[dict],
    depot: Tuple[float, float],
) -> List[str]:
    """Visit order of zona keys by NN on zone centroids from depot."""
    if not clusters:
        return []
    zonas = list(clusters.keys())

    def centroid(z: str) -> Tuple[float, float]:
        idxs = clusters[z]
        lat = sum(float(stops[i]["lat"]) for i in idxs) / len(idxs)
        lon = sum(float(stops[i]["lon"]) for i in idxs) / len(idxs)
        return (lat, lon)

    remaining = set(zonas)
    order: List[str] = []
    pos = depot
    while remaining:
        best_z, best_d = None, float("inf")
        for z in remaining:
            c = centroid(z)
            d = haversine_km(pos, c)
            if d < best_d:
                best_d, best_z = d, z
        assert best_z is not None
        order.append(best_z)
        remaining.remove(best_z)
        pos = centroid(best_z)
    return order


def build_deliveries_for_candidates(
    cand_indices: List[int],
    stops: Sequence[dict],
    current_pos: Tuple[float, float],
    avg_speed_kmh: float,
    unload_min: float,
) -> List[Delivery]:
    out: List[Delivery] = []
    for i in cand_indices:
        s = stops[i]
        pos = (float(s["lat"]), float(s["lon"]))
        d_km = haversine_km(current_pos, pos)
        t_travel = d_km / max(avg_speed_kmh, 1e-6) * 60.0
        t_total = t_travel + unload_min
        fuel = d_km * FUEL_EUR_PER_KM
        op = t_total * OP_EUR_PER_MIN
        out.append(
            Delivery(
                stop_idx=i,
                revenue=float(s.get("delivery_value_eur", 0.0)),
                distance_km=d_km,
                fuel_cost=fuel,
                travel_time_min=t_total,
                operational_cost=op,
                window_open=int(s.get("window_open_min", 0)),
                window_close=int(s.get("window_close_min", 24 * 60)),
                load_up=float(s.get("delivery_up", 0.0)),
                pos=pos,
            )
        )
    return out


def route_with_zona_clusters(
    stops: List[dict],
    depot_coords: Tuple[float, float],
    n_slots: float,
    reserved_empty: float,
    avg_speed_kmh: float,
    unload_min: float,
    weights: Optional[Dict[str, float]] = None,
) -> Tuple[List[int], Dict[str, List[int]], List[str], List[str]]:
    validate_weights(weights or PRIORITY_WEIGHTS)
    w = dict(weights or PRIORITY_WEIGHTS)
    clusters: Dict[str, List[int]] = {}
    for i, s in enumerate(stops):
        z = str(s.get("zona_transp", "SIN_ZONA")).strip() or "SIN_ZONA"
        clusters.setdefault(z, []).append(i)

    warnings: List[str] = []
    for z, idxs in list(clusters.items()):
        ttc = travel_depot_to_cluster_min(idxs, depot_coords, lambda i: (float(stops[i]["lat"]), float(stops[i]["lon"])), avg_speed_kmh)
        ok, msgs = cluster_tight_window_feasible(
            idxs, stops, depot_coords, ttc, avg_speed_kmh, unload_min
        )
        if not ok:
            warnings.extend([f"[{z}] {m}" for m in msgs])

    zone_order = order_clusters_greedy_nn(clusters, stops, depot_coords)
    route: List[int] = []
    current_pos = depot_coords
    current_time = 0.0

    load_up_fn = lambda idx: float(stops[idx]["delivery_up"])

    for z in zone_order:
        pool = [i for i in clusters[z] if i not in route]
        while pool:
            deliveries = build_deliveries_for_candidates(pool, stops, current_pos, avg_speed_kmh, unload_min)
            chosen_prefix = route[:]
            cand_deliveries = [
                d
                for d in deliveries
                if can_add_to_departure_tour(chosen_prefix, d.stop_idx, load_up_fn, n_slots, reserved_empty)
            ]
            if not cand_deliveries:
                break
            eligible_deliveries: List[Delivery] = []
            for d in cand_deliveries:
                rem_cap = marginal_load_capacity_up(route, stops, n_slots, reserved_empty)
                if rem_cap < 0:
                    break
                if is_eligible(d, int(current_time), rem_cap):
                    eligible_deliveries.append(d)
            if not eligible_deliveries:
                break
            norm_revenue = normalize([d.revenue for d in eligible_deliveries])
            norm_distance = normalize([d.distance_km for d in eligible_deliveries])
            norm_fuel = normalize([d.fuel_cost for d in eligible_deliveries])
            norm_time = normalize([d.travel_time_min for d in eligible_deliveries])
            norm_opcost = normalize([d.operational_cost for d in eligible_deliveries])
            remaining_positions = [d.pos for d in eligible_deliveries]
            scored: List[Tuple[float, Delivery]] = []
            for i, delivery in enumerate(eligible_deliveries):
                norm = {
                    "revenue": norm_revenue[i],
                    "distance": norm_distance[i],
                    "fuel_cost": norm_fuel[i],
                    "travel_time": norm_time[i],
                    "operational_cost": norm_opcost[i],
                }
                sc = compute_priority_score(
                    delivery,
                    norm,
                    w,
                    int(current_time),
                    current_pos,
                    remaining_positions,
                )
                scored.append((sc, delivery))
            scored.sort(key=lambda x: x[0], reverse=True)
            pick = scored[0][1]
            route.append(pick.stop_idx)
            current_time += pick.travel_time_min
            current_pos = pick.pos
            pool.remove(pick.stop_idx)

    return route, clusters, warnings, []


def find_unassigned(stops: List[dict], route: List[int]) -> List[int]:
    sset = set(route)
    return [i for i in range(len(stops)) if i not in sset]


def escalate_truck_slots(
    preferred_type: str,
    truck_chain: Sequence[str],
) -> List[str]:
    """3 -> 6 -> 8 preference order starting from preferred_type index."""
    types = list(truck_chain)
    if preferred_type in types:
        i = types.index(preferred_type)
        return types[i:] + [t for t in types if t not in types[i:]]
    return list(types)


# validate at import
validate_weights(PRIORITY_WEIGHTS)
