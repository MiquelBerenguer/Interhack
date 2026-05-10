"""

Block 2 - Real-world routing per ZonaTransp cluster using Google Distance Matrix API.



Constraints: Distance Matrix only (no Directions/Places/Geocoding). Depot coordinates

are fixed and cached at import time from DEPOT (authoritative lat/lon).



API keys (optional via environment if arguments are empty):

  GOOGLE_MAPS_API_KEY  - Distance Matrix API

  ANTHROPIC_API_KEY    - Claude for deadlock suggestions (preferred if set)

  GEMINI_API_KEY       - Gemini for deadlock if Anthropic is not set

"""

from __future__ import annotations



import json

import logging

import os

import urllib.error

import urllib.parse

import urllib.request

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple



from dynamic_truck import reserved_empty_up

from horarios_windows import ROUTE_START_ABS_MIN

from priority_cluster import (

    PRIORITY_WEIGHTS,

    Delivery,

    can_add_to_departure_tour,

    compute_priority_score,

    is_eligible,

    marginal_load_capacity_up,

    normalize,

    pick_next_delivery,

    haversine_km,

)



logger = logging.getLogger(__name__)



_BLOCK2_DIR = os.path.dirname(os.path.abspath(__file__))





def load_dotenv(path: Optional[str] = None) -> Optional[str]:

    """

    Load KEY=value pairs from `.env` into os.environ (never overwrites existing vars).

    Searches `path`, then `Versio1/.env`, then cwd `.env`. Skips empty values.

    Returns the path loaded, or None.

    """

    candidates: List[str] = []

    if path:

        candidates.append(path)

    candidates.extend(

        [

            os.path.join(_BLOCK2_DIR, ".env"),

            os.path.join(os.getcwd(), ".env"),

        ]

    )

    for p in candidates:

        if not p or not os.path.isfile(p):

            continue

        try:

            with open(p, encoding="utf-8") as f:

                for raw in f:

                    line = raw.split("#", 1)[0].strip()

                    if not line or "=" not in line:

                        continue

                    key, _, val = line.partition("=")

                    key = key.strip()

                    val = val.strip().strip('"').strip("'")

                    if not key or not val:

                        continue

                    if key not in os.environ:

                        os.environ[key] = val

            logger.info("Loaded .env from %s", p)

            return p

        except OSError as e:

            logger.warning("Could not read .env %s: %s", p, e)

    return None





# --- Configurable constants (Block 2) ---

FUEL_COST_PER_KM = 0.12

TRUCK_OPCOST_PER_MIN = 0.35

UNLOADING_TIME_MIN = 8.0

EARLIEST_DEPARTURE_MIN = 360  # 06:00 from midnight



WALKING_RADIUS_METERS = 150.0

WALKING_SPEED_M_PER_MIN = 80.0  # ~4.8 km/h



HAVERSINE_FALLBACK_SPEED_KMH = 35.0

DM_BATCH_SIZE = 10



# Anthropic Messages API (Claude) - deadlock helper only

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"

ANTHROPIC_VERSION = "2023-06-01"

CLAUDE_DEADLOCK_MODEL = "claude-3-5-haiku-20241022"

GEMINI_DEADLOCK_MODEL = "gemini-2.0-flash"



DEPOT = {

    "name": "Estrella Damm Mollet - Magatzem",

    "address": "Carrer Moli de Can Bassa, Mollet del Valles, Barcelona",

    "lat": 41.5363,

    "lon": 2.2131,

}



_DEPOT_COORDS_CACHE: Optional[Tuple[float, float]] = None





def get_depot_coordinates() -> Tuple[float, float]:

    """

    Returns authoritative depot WGS84 coordinates from DEPOT.

    Policy: Distance Matrix API only - no Geocoding calls; lat/lon are pre-validated.

    """

    global _DEPOT_COORDS_CACHE

    if _DEPOT_COORDS_CACHE is None:

        _DEPOT_COORDS_CACHE = (float(DEPOT["lat"]), float(DEPOT["lon"]))

        logger.info("Depot coordinates cached: %s", _DEPOT_COORDS_CACHE)

    return _DEPOT_COORDS_CACHE





def _haversine_edge_mins_km(

    a: Tuple[float, float], b: Tuple[float, float]

) -> Tuple[float, float]:

    km = haversine_km(a, b)

    mins = km / max(HAVERSINE_FALLBACK_SPEED_KMH, 1e-6) * 60.0

    return mins, km





def build_distance_matrix(

    stops: list,

    depot: dict,

    api_key: str,

    cache: Optional[Dict[Tuple[int, int], Dict[str, float]]] = None,

) -> Dict[Tuple[int, int], Dict[str, float]]:

    """

    Depot is index 0. Stops are indices 1..N matching `stops` order.

    Returns dict: {(i, j): {"duration_min": float, "distance_km": float}}

    Batches 10x10; caches within `cache` dict if provided.

    Falls back to haversine on failure - logs warning, never raises.

    """

    if cache is None:

        cache = {}



    depot_ll = get_depot_coordinates()

    nodes: List[Tuple[float, float]] = [depot_ll] + [

        (float(s["lat"]), float(s["lon"])) for s in stops

    ]

    n = len(nodes)



    def fill_haversine(i: int, j: int) -> None:

        if i == j:

            cache[(i, j)] = {"duration_min": 0.0, "distance_km": 0.0}

            return

        mins, km = _haversine_edge_mins_km(nodes[i], nodes[j])

        cache[(i, j)] = {"duration_min": mins, "distance_km": km}



    missing: List[Tuple[int, int]] = []

    for i in range(n):

        for j in range(n):

            if (i, j) not in cache:

                missing.append((i, j))



    if not api_key or not api_key.strip():

        logger.warning("No Maps API key - filling matrix with haversine estimates.")

        for i, j in missing:

            fill_haversine(i, j)

        return cache



    batches: List[Tuple[List[int], List[int]]] = []

    oi = 0

    while oi < n:

        o_end = min(oi + DM_BATCH_SIZE, n)

        o_idxs = list(range(oi, o_end))

        dj = 0

        while dj < n:

            d_end = min(dj + DM_BATCH_SIZE, n)

            d_idxs = list(range(dj, d_end))

            batches.append((o_idxs, d_idxs))

            dj = d_end

        oi = o_end



    for o_idxs, d_idxs in batches:

        origins = [_fmt_latlng(nodes[i]) for i in o_idxs]

        dests = [_fmt_latlng(nodes[j]) for j in d_idxs]

        ok, payload = _distance_matrix_request(origins, dests, api_key.strip())

        if not ok:

            logger.warning("Distance Matrix batch failed (%s); using haversine for batch.", payload)

            for i in o_idxs:

                for j in d_idxs:

                    if (i, j) not in cache:

                        fill_haversine(i, j)

            continue

        try:

            rows = payload.get("rows", [])

        except Exception:

            rows = []

        for ri, i in enumerate(o_idxs):

            elems = rows[ri].get("elements", []) if ri < len(rows) else []

            for ci, j in enumerate(d_idxs):

                el = elems[ci] if ci < len(elems) else {}

                st = el.get("status", "UNKNOWN")

                if st != "OK":

                    logger.warning("DM element (%s,%s) status=%s; haversine fallback.", i, j, st)

                    fill_haversine(i, j)

                    continue

                dur_s = el.get("duration", {}).get("value")

                dist_m = el.get("distance", {}).get("value")

                if dur_s is None or dist_m is None:

                    fill_haversine(i, j)

                    continue

                cache[(i, j)] = {

                    "duration_min": float(dur_s) / 60.0,

                    "distance_km": float(dist_m) / 1000.0,

                }



    for i in range(n):

        for j in range(n):

            if (i, j) not in cache:

                fill_haversine(i, j)



    return cache


def _route_positions(rand_route_perm: Sequence[int]) -> Dict[int, int]:
    """Global stop index → position along Block 1 random permutation."""
    return {int(g): pos for pos, g in enumerate(rand_route_perm)}


def cluster_maps_baseline_tour_cost_dm(
    route_local: List[int],
    matrix: Dict[Tuple[int, int], Dict[str, float]],
) -> Dict[str, float]:
    """
    Cost of serving ``route_local`` (local indices) in order using DM edges + the same fuel/op
    multipliers as the optimized tally (Fuel + operational per leg incl. unloading per stop).

    Mirrors the aggregation loop in optimize_cluster_route (final_route tally + return edge).
    """
    if not route_local:
        return {
            "total_km": 0.0,
            "total_fuel_cost": 0.0,
            "total_operational_cost": 0.0,
            "baseline_rand_maps_total_eur": 0.0,
            "baseline_service_and_drive_min": 0.0,
        }
    total_dist = 0.0
    total_fuel = 0.0
    total_op = 0.0
    service_drive_min = 0.0

    loc0 = route_local[0]
    edge = matrix[(0, loc0 + 1)]
    travel = float(edge["duration_min"])
    dist_km = float(edge["distance_km"])
    total_dist += dist_km
    total_fuel += dist_km * FUEL_COST_PER_KM
    total_op += (travel + UNLOADING_TIME_MIN) * TRUCK_OPCOST_PER_MIN
    service_drive_min += travel + UNLOADING_TIME_MIN

    for seq in range(1, len(route_local)):
        prev = route_local[seq - 1]
        loc = route_local[seq]
        edge = matrix[(prev + 1, loc + 1)]
        travel = float(edge["duration_min"])
        dist_km = float(edge["distance_km"])
        total_dist += dist_km
        total_fuel += dist_km * FUEL_COST_PER_KM
        total_op += (travel + UNLOADING_TIME_MIN) * TRUCK_OPCOST_PER_MIN
        service_drive_min += travel + UNLOADING_TIME_MIN

    last = route_local[-1]
    edge_ret = matrix[(last + 1, 0)]
    dist_ret = float(edge_ret["distance_km"])
    travel_ret = float(edge_ret["duration_min"])
    total_dist += dist_ret
    total_fuel += dist_ret * FUEL_COST_PER_KM
    total_op += travel_ret * TRUCK_OPCOST_PER_MIN
    service_drive_min += travel_ret

    teur = round(total_fuel + total_op, 4)
    return {
        "total_km": round(total_dist, 3),
        "total_fuel_cost": round(total_fuel, 4),
        "total_operational_cost": round(total_op, 4),
        "baseline_rand_maps_total_eur": teur,
        "baseline_service_and_drive_min": round(service_drive_min, 2),
    }


def _fmt_latlng(ll: Tuple[float, float]) -> str:

    return f"{ll[0]:.6f},{ll[1]:.6f}"





def _distance_matrix_request(

    origins: List[str], destinations: List[str], api_key: str

) -> Tuple[bool, Any]:

    q = urllib.parse.urlencode(

        {

            "origins": "|".join(origins),

            "destinations": "|".join(destinations),

            "key": api_key,

            "units": "metric",

            "mode": "driving",

        },

        safe="|,",

    )

    url = f"https://maps.googleapis.com/maps/api/distancematrix/json?{q}"

    try:

        req = urllib.request.Request(url, headers={"User-Agent": "INTERHACK-Block2/1.0"})

        with urllib.request.urlopen(req, timeout=60) as resp:

            raw = resp.read().decode("utf-8")

        data = json.loads(raw)

    except urllib.error.HTTPError as e:

        return False, f"HTTP {e.code}"

    except Exception as e:

        return False, str(e)



    st = data.get("status")

    if st != "OK":

        return False, st

    return True, data





def enrich_deliveries_with_matrix(

    deliveries: List[Delivery],

    current_matrix_idx: int,

    distance_matrix: Dict[Tuple[int, int], Dict[str, float]],

    unload_min: float = UNLOADING_TIME_MIN,

) -> None:

    """In-place: real matrix leg costs (travel+unload for time and op cost)."""

    for d in deliveries:

        j = d.stop_idx + 1

        edge = distance_matrix.get((current_matrix_idx, j))

        if not edge:

            edge = {"duration_min": 0.0, "distance_km": 0.0}

        t_travel = float(edge["duration_min"])

        d_km = float(edge["distance_km"])

        d.distance_km = d_km

        d.fuel_cost = d_km * FUEL_COST_PER_KM

        t_total = t_travel + unload_min

        d.travel_time_min = t_total

        d.operational_cost = t_total * TRUCK_OPCOST_PER_MIN





def calculate_departure_time(first_stop: dict, travel_to_first_stop_min: float) -> int:

    """Minutes from midnight. Arrive at window open of first stop; never before EARLIEST_DEPARTURE_MIN."""

    open_abs = ROUTE_START_ABS_MIN + int(first_stop.get("window_open_min", 0))

    ideal = float(open_abs) - float(travel_to_first_stop_min)

    return int(max(ideal, float(EARLIEST_DEPARTURE_MIN)))





def _abs_window_bounds(stop: dict) -> Tuple[int, int]:

    o = ROUTE_START_ABS_MIN + int(stop.get("window_open_min", 0))

    c = ROUTE_START_ABS_MIN + int(stop.get("window_close_min", 24 * 60))

    return o, c





def select_first_stop_local(

    cluster_stops: Sequence[dict],

    distance_matrix: Dict[Tuple[int, int], Dict[str, float]],

) -> int:

    """Tightest window span, then highest revenue, then shortest depot travel."""

    best_i = 0

    best_key: Optional[Tuple[float, float, float]] = None

    for i in range(len(cluster_stops)):

        o_abs, c_abs = _abs_window_bounds(cluster_stops[i])

        span = max(0.0, float(c_abs - o_abs))

        rev = float(cluster_stops[i].get("delivery_value_eur", 0.0))

        travel = float(distance_matrix.get((0, i + 1), {}).get("duration_min", 0.0))

        key = (span, -rev, travel)

        if best_key is None or key < best_key:

            best_key = key

            best_i = i

    return best_i





def window_status(arrival_min: int, window_close_min: int) -> str:

    margin = int(window_close_min) - int(arrival_min)

    if margin < 0:

        return "missed"

    if margin < 30:

        return "tight"

    if margin < 60:

        return "at_risk"

    return "on_time"





def find_walkable_groups(

    stops: list, radius_m: float = WALKING_RADIUS_METERS

) -> List[List[int]]:

    """Union of pairs within radius_m (haversine). Indices are local 0..n-1."""

    n = len(stops)

    parent = list(range(n))



    def find(a: int) -> int:

        while parent[a] != a:

            parent[a] = parent[parent[a]]

            a = parent[a]

        return a



    def union(a: int, b: int) -> None:

        ra, rb = find(a), find(b)

        if ra != rb:

            parent[rb] = ra



    for i in range(n):

        for j in range(i + 1, n):

            a = (float(stops[i]["lat"]), float(stops[i]["lon"]))

            b = (float(stops[j]["lat"]), float(stops[j]["lon"]))

            if haversine_km(a, b) * 1000.0 <= radius_m + 1e-6:

                union(i, j)



    groups: Dict[int, List[int]] = {}

    for i in range(n):

        r = find(i)

        groups.setdefault(r, []).append(i)

    return [sorted(v) for v in groups.values()]





def walking_time_min_between(a: Tuple[float, float], b: Tuple[float, float]) -> float:

    m = haversine_km(a, b) * 1000.0

    return m / max(WALKING_SPEED_M_PER_MIN, 1e-6)





def walking_group_saving(

    group: List[int],

    stops: Sequence[dict],

    distance_matrix: Dict[Tuple[int, int], Dict[str, float]],

) -> float:

    if len(group) < 2:

        return 0.0

    g = sorted(group)

    drive_sum = 0.0

    walk_sum = 0.0

    for a, b in zip(g, g[1:]):

        ia, ib = a + 1, b + 1

        drive_sum += float(distance_matrix.get((ia, ib), {}).get("duration_min", 0.0))

        pa = (float(stops[a]["lat"]), float(stops[a]["lon"]))

        pb = (float(stops[b]["lat"]), float(stops[b]["lon"]))

        walk_sum += walking_time_min_between(pa, pb)

    return max(0.0, drive_sum - walk_sum)





def get_transporter_for_zona(zona: str, block1_output: dict) -> Dict[str, str]:

    return {"id": str(zona), "name": f"Transporter {zona}"}





def resolve_cluster_deadlock(

    transporter_name: str,

    current_state: dict,

    weights: dict,

    anthropic_api_key: Optional[str],

    gemini_api_key: Optional[str] = None,

) -> str:

    log_path = "block2_deadlock_log.jsonl"

    line = json.dumps(

        {"transporter": transporter_name, "state": current_state, "weights": weights},

        default=str,

    )

    try:

        with open(log_path, "a", encoding="utf-8") as f:

            f.write(line + "\n")

    except OSError as e:

        logger.error("Could not write deadlock log: %s", e)



    akey = (anthropic_api_key or "").strip()

    gkey = (gemini_api_key or "").strip()

    if not akey and not gkey:

        return (

            f"[{transporter_name}] Deadlock logged to {log_path}. "

            "Review remaining windows, consider customer window extension, "

            "or flag a stop for reassignment to another route."

        )



    if akey:

        suggestion = _llm_deadlock_suggestion_anthropic(

            transporter_name, current_state, weights, akey

        )

    else:

        suggestion = _llm_deadlock_suggestion_gemini(

            transporter_name, current_state, weights, gkey

        )

    return suggestion or (

        f"[{transporter_name}] LLM returned empty; see {log_path} for structured state."

    )





def _llm_deadlock_suggestion_anthropic(

    transporter_name: str, current_state: dict, weights: dict, anthropic_api_key: str

) -> str:

    """Anthropic Claude Messages API via urllib."""

    system = (

        "You advise a logistics operator. Never output autonomous routing decisions. "

        "Plain language: which stop is closest to still being reachable, "

        "whether to call the customer for a window extension, "

        "which stop to flag for reassignment. Max 120 words."

    )

    user_text = json.dumps(

        {"transporter": transporter_name, "state": current_state, "weights": weights},

        default=str,

    )[:12000]

    body = {

        "model": CLAUDE_DEADLOCK_MODEL,

        "max_tokens": 512,

        "system": system,

        "messages": [{"role": "user", "content": user_text}],

    }

    data = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(

        ANTHROPIC_MESSAGES_URL,

        data=data,

        headers={

            "Content-Type": "application/json",

            "x-api-key": anthropic_api_key.strip(),

            "anthropic-version": ANTHROPIC_VERSION,

            "User-Agent": "INTERHACK-Block2/1.0",

        },

        method="POST",

    )

    try:

        with urllib.request.urlopen(req, timeout=60) as resp:

            out = json.loads(resp.read().decode("utf-8"))

    except Exception as e:

        logger.warning("Claude deadlock suggestion failed: %s", e)

        return ""

    blocks = out.get("content") or []

    for block in blocks:

        if isinstance(block, dict) and block.get("type") == "text":

            return str(block.get("text", "")).strip()

    return ""





def _llm_deadlock_suggestion_gemini(

    transporter_name: str, current_state: dict, weights: dict, gemini_api_key: str

) -> str:

    """Google Gemini generateContent (REST). Key from AI Studio; urllib only."""

    system = (

        "You advise a logistics operator. Never output autonomous routing decisions. "

        "Plain language: which stop is closest to still being reachable, "

        "whether to call the customer for a window extension, "

        "which stop to flag for reassignment. Max 120 words."

    )

    user_text = json.dumps(

        {"transporter": transporter_name, "state": current_state, "weights": weights},

        default=str,

    )[:12000]

    combined = f"{system}\n\nData (JSON):\n{user_text}"

    body = {

        "contents": [{"role": "user", "parts": [{"text": combined}]}],

        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 512},

    }

    q = urllib.parse.urlencode({"key": gemini_api_key.strip()})

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_DEADLOCK_MODEL}:generateContent?{q}"

    data = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(

        url,

        data=data,

        headers={"Content-Type": "application/json", "User-Agent": "INTERHACK-Block2/1.0"},

        method="POST",

    )

    try:

        with urllib.request.urlopen(req, timeout=60) as resp:

            out = json.loads(resp.read().decode("utf-8"))

    except Exception as e:

        logger.warning("Gemini deadlock suggestion failed: %s", e)

        return ""

    cands = out.get("candidates") or []

    if not cands:

        return ""

    parts = (cands[0].get("content") or {}).get("parts") or []

    if not parts:

        return ""

    return str(parts[0].get("text", "")).strip()





def _min_to_hhmm(m: float) -> str:

    x = int(round(m)) % (24 * 60)

    if x < 0:

        x += 24 * 60

    return f"{x // 60:02d}:{x % 60:02d}"





def _clone_stop(s: dict) -> dict:

    return json.loads(json.dumps(s, default=str))





def _adjust_windows_for_departure(stops: List[dict], departure_abs: int) -> None:

    for s in stops:

        o_abs, c_abs = _abs_window_bounds(s)

        s["window_open_min"] = int(o_abs - departure_abs)

        s["window_close_min"] = int(c_abs - departure_abs)





# Routing diagnosis (prints only for this cluster; does not affect optimisation)

ROUTING_DIAG_TRANSPORTER_ID = "DD13100050"





def _routing_diag_enabled(transporter_id: str) -> bool:

    return transporter_id == ROUTING_DIAG_TRANSPORTER_ID





def _diag_wall_minutes(departure_abs: int, elapsed_since_departure_min: int) -> int:

    return int(departure_abs) + int(elapsed_since_departure_min)





def _diag_position_label(depot: dict, stops_work: Sequence[dict], route_local: List[int]) -> str:

    if not route_local:

        return f"depot ({depot.get('name', 'depot')})"

    last = route_local[-1]

    return str(stops_work[last].get("name", f"stop[{last}]"))





def _diag_is_eligible_failure_reasons(

    d: Delivery,

    current_time: int,

    truck_capacity_remaining: float,

) -> List[str]:

    """Messages aligned with is_eligible() in priority_cluster (plus zero-width window)."""

    reasons: List[str] = []

    if int(d.window_open) >= int(d.window_close):

        reasons.append("not available  -  closed that day")

    if int(current_time) > int(d.window_close):

        reasons.append("window already closed  -  current_time > window_close_min")

    if int(current_time) + float(d.travel_time_min) > int(d.window_close):

        reasons.append("cannot arrive in time  -  current_time + travel_time_min > window_close_min")

    if float(d.load_up) > float(truck_capacity_remaining) + 1e-9:

        reasons.append("load exceeds capacity  -  load_up > truck_capacity_remaining")

    return reasons





def _compute_pick_priority_score(

    pick: Delivery,

    eligible: List[Delivery],

    current_pos: Tuple[float, float],

    current_time: int,

    weights: Dict[str, float],

) -> float:

    """Same scoring kernel as pick_next_delivery for the chosen candidate."""

    norm_revenue = normalize([d.revenue for d in eligible])

    norm_distance = normalize([d.distance_km for d in eligible])

    norm_fuel = normalize([d.fuel_cost for d in eligible])

    norm_time = normalize([d.travel_time_min for d in eligible])

    norm_opcost = normalize([d.operational_cost for d in eligible])

    remaining_positions = [d.pos for d in eligible]

    try:

        idx = next(i for i, d in enumerate(eligible) if d.stop_idx == pick.stop_idx)

    except StopIteration:

        return 0.0

    norm = {

        "revenue": norm_revenue[idx],

        "distance": norm_distance[idx],

        "fuel_cost": norm_fuel[idx],

        "travel_time": norm_time[idx],

        "operational_cost": norm_opcost[idx],

    }

    return float(

        compute_priority_score(

            pick, norm, weights, current_time, current_pos, remaining_positions

        )

    )





def optimize_cluster_route(

    cluster_stops: List[dict],

    transporter_id: str,

    transporter_name: str,

    truck_type: str,

    truck_slots: int,

    depot: dict,

    maps_api_key: str,

    anthropic_api_key: Optional[str] = None,

    gemini_api_key: Optional[str] = None,

    weights: Optional[dict] = None,

    global_stop_indices: Optional[List[int]] = None,

    block1_rand_route: Optional[Sequence[int]] = None,

) -> dict:

    get_depot_coordinates()

    w = dict(weights or PRIORITY_WEIGHTS)

    n_loc = len(cluster_stops)

    if n_loc == 0:

        return _empty_cluster_result(

            transporter_id, transporter_name, truck_type, truck_slots, depot

        )



    diag = _routing_diag_enabled(transporter_id)

    step = 0



    dm_cache: Dict[Tuple[int, int], Dict[str, float]] = {}

    matrix = build_distance_matrix(cluster_stops, depot, maps_api_key, cache=dm_cache)



    first_local = select_first_stop_local(cluster_stops, matrix)

    travel0 = float(matrix.get((0, first_local + 1), {}).get("duration_min", 0.0))

    departure_abs = calculate_departure_time(cluster_stops[first_local], travel0)



    stops_work = [_clone_stop(s) for s in cluster_stops]

    _adjust_windows_for_departure(stops_work, departure_abs)



    reserved = reserved_empty_up(truck_type, int(truck_slots))

    load_up_fn: Callable[[int], float] = lambda idx: float(stops_work[idx]["delivery_up"])



    route_local: List[int] = [first_local]

    pool = [i for i in range(n_loc) if i != first_local]



    current_time = int(round(travel0))

    wo0 = int(stops_work[first_local]["window_open_min"])

    if current_time < wo0:

        current_time = wo0

    current_time += int(round(UNLOADING_TIME_MIN))



    current_pos = (float(stops_work[first_local]["lat"]), float(stops_work[first_local]["lon"]))

    current_matrix_idx = first_local + 1



    deadlock_suggestions: List[str] = []

    priority_by_stop: Dict[int, float] = {}



    max_rev = max((float(s.get("delivery_value_eur", 0.0)) for s in cluster_stops), default=1.0)

    priority_by_stop[first_local] = round(

        float(cluster_stops[first_local].get("delivery_value_eur", 0.0)) / max(max_rev, 1e-6), 4

    )



    if diag:

        total_up_cluster = sum(float(s.get("delivery_up", 0.0)) for s in cluster_stops)

        first_up = float(cluster_stops[first_local].get("delivery_up", 0.0))

        net_dep_cap = float(truck_slots) - float(reserved)

        usable_after_first = net_dep_cap - first_up

        fl_name = str(cluster_stops[first_local].get("name", ""))

        print(f"[BLOCK2 DIAG {transporter_id}] ========== pre-route snapshot (before while) ==========")

        print(

            f"  departure_actual_wall = {_min_to_hhmm(float(departure_abs))} "

            f"({departure_abs} min from midnight)"

        )

        print(

            f"  first_stop_driving_departure_calc: local_idx={first_local} name={fl_name!r} "

            f"travel_depot_to_first_min={travel0:.2f}"

        )

        print(f"  sum(load_up) all stops in cluster = {total_up_cluster:.4f} UP")

        print(f"  first_stop.load_up = {first_up:.4f} UP")

        print(

            f"  net_departure_cap (truck_slots - reserved_empty) = {net_dep_cap:.4f} UP | "

            f"usable_after_first = {usable_after_first:.4f} UP"

        )

        print(f"  reserved_empty_up = {reserved:.4f}")

        w_after_first = _diag_wall_minutes(departure_abs, current_time)

        print(

            f"  route_clock_after_first_service = {current_time} min since cluster departure | "

            f"wall {_min_to_hhmm(float(w_after_first))} ({w_after_first} min from midnight)"

        )

        print(f"  position_after_first_service = {_diag_position_label(depot, stops_work, route_local)}")



    while pool:

        step += 1

        if diag:

            wmin = _diag_wall_minutes(departure_abs, current_time)

            pos_lab = _diag_position_label(depot, stops_work, route_local)

            print(f"[BLOCK2 DIAG {transporter_id}] ---------- step {step} ----------")

            print(

                f"  current_time = {current_time} min since cluster departure | "

                f"wall {_min_to_hhmm(float(wmin))} ({wmin} min from midnight)"

            )

            print(f"  current_position (last served) = {pos_lab}")



        deliveries = _build_deliveries_stub(pool, stops_work)

        enrich_deliveries_with_matrix(deliveries, current_matrix_idx, matrix)

        cand_deliveries = [

            d

            for d in deliveries

            if can_add_to_departure_tour(route_local, d.stop_idx, load_up_fn, float(truck_slots), reserved)

        ]

        if diag:

            rem0 = marginal_load_capacity_up(route_local, stops_work, float(truck_slots), reserved)

            print(f"  marginal_load_capacity_remaining = {rem0:.4f} UP")

            if not cand_deliveries:

                print("  can_add_to_departure_tour removed ALL pool candidates. Pool local indices:", pool)

                for pidx in pool:

                    pnm = str(stops_work[pidx].get("name", pidx))

                    ok_ca = can_add_to_departure_tour(

                        route_local, pidx, load_up_fn, float(truck_slots), reserved

                    )

                    print(f"    pool[{pidx}] {pnm!r} can_add_to_departure_tour={ok_ca}")

            for d in cand_deliveries:

                rem_cap = marginal_load_capacity_up(

                    route_local, stops_work, float(truck_slots), reserved

                )

                if rem_cap < 0:

                    print(

                        f"  candidate local_idx={d.stop_idx} "

                        f"{stops_work[d.stop_idx].get('name', '')!r} | skip is_eligible: rem_cap<0 ({rem_cap:.4f})"

                    )

                    break

                if not is_eligible(d, int(current_time), rem_cap):

                    nm = str(stops_work[d.stop_idx].get("name", d.stop_idx))

                    for reason in _diag_is_eligible_failure_reasons(

                        d, int(current_time), rem_cap

                    ):

                        print(

                            f"  candidate local_idx={d.stop_idx} {nm!r} | NOT is_eligible: {reason}"

                        )



        if not cand_deliveries:

            break

        eligible: List[Delivery] = []

        for d in cand_deliveries:

            rem_cap = marginal_load_capacity_up(route_local, stops_work, float(truck_slots), reserved)

            if rem_cap < 0:

                break

            if is_eligible(d, int(current_time), rem_cap):

                eligible.append(d)

        if not eligible:

            if diag:

                print(f"  eligible list empty after is_eligible (cand_deliveries had {len(cand_deliveries)})")

            st = {

                "current_pos": current_pos,

                "current_time": current_time,

                "current_matrix_idx": current_matrix_idx,

                "pool": pool,

                "route_local": route_local[:],

            }

            msg = resolve_cluster_deadlock(

                transporter_name, st, w, anthropic_api_key, gemini_api_key

            )

            deadlock_suggestions.append(msg)

            logger.error("Cluster deadlock for %s: %s", transporter_name, msg)

            break



        rem = marginal_load_capacity_up(route_local, stops_work, float(truck_slots), reserved)

        pick = pick_next_delivery(current_pos, current_time, eligible, rem, w)

        if pick is None:

            if diag:

                print(

                    f"  pick_next_delivery returned None "

                    f"(eligible count={len(eligible)}, marginal_rem={rem:.4f})"

                )

            st = {

                "current_pos": current_pos,

                "current_time": current_time,

                "eligible": [d.stop_idx for d in eligible],

                "route_local": route_local[:],

            }

            msg = resolve_cluster_deadlock(

                transporter_name, st, w, anthropic_api_key, gemini_api_key

            )

            deadlock_suggestions.append(msg)

            logger.error("pick_next_delivery returned None for %s", transporter_name)

            break



        priority_by_stop[pick.stop_idx] = round(

            _compute_pick_priority_score(pick, eligible, current_pos, int(current_time), w), 4

        )



        route_local.append(pick.stop_idx)

        pool.remove(pick.stop_idx)

        travel_only = float(pick.travel_time_min) - UNLOADING_TIME_MIN

        current_time += int(round(travel_only))

        wo = int(stops_work[pick.stop_idx]["window_open_min"])

        if current_time < wo:

            current_time = wo

        current_time += int(round(UNLOADING_TIME_MIN))

        current_pos = (float(stops_work[pick.stop_idx]["lat"]), float(stops_work[pick.stop_idx]["lon"]))

        current_matrix_idx = pick.stop_idx + 1



    unassigned_local = [i for i in range(n_loc) if i not in route_local]

    unserviceable = []

    needs_reassignment_indices: List[int] = []

    for li in unassigned_local:

        gidx = global_stop_indices[li] if global_stop_indices is not None else li

        unserviceable.append(

            {

                "stop_index": gidx,

                "name": cluster_stops[li].get("name", ""),

                "reason": "block2_deadlock_or_capacity_or_window",

            }

        )

        needs_reassignment_indices.append(gidx)



    baseline_maps_summary: Dict[str, float] = {

        "total_km": 0.0,

        "total_fuel_cost": 0.0,

        "total_operational_cost": 0.0,

        "baseline_rand_maps_total_eur": 0.0,

    }

    br_loc: List[int] = []

    baseline_visit_order_global: List[int] = []

    optimized_visit_order_global: List[int] = []

    if route_local and global_stop_indices is not None and block1_rand_route is not None:

        try:

            pos = _route_positions(block1_rand_route)

            rank_miss = len(block1_rand_route) + 9999

            br_loc = sorted(

                route_local,

                key=lambda li: float(pos.get(int(global_stop_indices[li]), rank_miss)),

            )

            baseline_maps_summary = cluster_maps_baseline_tour_cost_dm(br_loc, matrix)

        except Exception as exc:

            logger.warning("baseline rand Maps tour failed for %s: %s", transporter_name, exc)



    if route_local and global_stop_indices is not None:

        optimized_visit_order_global = [int(global_stop_indices[i]) for i in route_local]

        if br_loc:

            baseline_visit_order_global = [int(global_stop_indices[i]) for i in br_loc]

        else:

            baseline_visit_order_global = optimized_visit_order_global[:]



    walk_groups = find_walkable_groups(cluster_stops, WALKING_RADIUS_METERS)

    walk_id_map = _assign_walking_group_ids(route_local, walk_groups)



    final_route: List[dict] = []

    t = float(departure_abs)

    leg_from_depot = True

    total_dist = 0.0

    total_fuel = 0.0

    total_op = 0.0

    total_rev = 0.0



    for seq, loc in enumerate(route_local):

        if leg_from_depot:

            edge = matrix[(0, loc + 1)]

            travel = float(edge["duration_min"])

            dist_km = float(edge["distance_km"])

            leg_from_depot = False

        else:

            p = route_local[seq - 1]

            edge = matrix[(p + 1, loc + 1)]

            travel = float(edge["duration_min"])

            dist_km = float(edge["distance_km"])

        total_dist += dist_km

        total_fuel += dist_km * FUEL_COST_PER_KM

        total_op += (travel + UNLOADING_TIME_MIN) * TRUCK_OPCOST_PER_MIN



        t += travel

        o_abs, c_abs = _abs_window_bounds(cluster_stops[loc])

        if t < float(o_abs):

            t = float(o_abs)

        arrival_abs = int(round(t))

        ws = window_status(arrival_abs, c_abs)

        t += UNLOADING_TIME_MIN

        departure_abs_stop = t



        gidx = global_stop_indices[loc] if global_stop_indices is not None else loc

        sc = float(cluster_stops[loc].get("delivery_value_eur", 0.0))

        total_rev += sc



        final_route.append(

            {

                "stop_index": gidx,

                "customer_id": str(cluster_stops[loc].get("id", "")),

                "customer_name": str(cluster_stops[loc].get("name", "")),

                "arrival_time": _min_to_hhmm(arrival_abs),

                "departure_time": _min_to_hhmm(departure_abs_stop),

                "travel_time_min": round(travel, 2),

                "distance_km": round(dist_km, 3),

                "fuel_cost": round(dist_km * FUEL_COST_PER_KM, 4),

                "operational_cost": round((travel + UNLOADING_TIME_MIN) * TRUCK_OPCOST_PER_MIN, 4),

                "priority_score":         priority_by_stop.get(loc, round(sc / max(max_rev, 1e-6), 4)),

                "walking_group_id": walk_id_map.get(loc),

                "window_open": _min_to_hhmm(o_abs),

                "window_close": _min_to_hhmm(c_abs),

                "window_status": ws,

                "lat": float(cluster_stops[loc].get("lat", 0.0)),

                "lon": float(cluster_stops[loc].get("lon", 0.0)),

                "delivery_caj": float(cluster_stops[loc].get("delivery_caj") or 0.0),

                "delivery_brl": float(cluster_stops[loc].get("delivery_brl") or 0.0),

                "return_caj": float(cluster_stops[loc].get("ret_caj") or 0.0),

                "return_brl": float(cluster_stops[loc].get("ret_brl") or 0.0),

            }

        )



    if route_local:

        last = route_local[-1]

        edge_ret = matrix[(last + 1, 0)]

        dist_ret = float(edge_ret["distance_km"])

        travel_ret = float(edge_ret["duration_min"])

        total_dist += dist_ret

        total_fuel += dist_ret * FUEL_COST_PER_KM

        total_op += travel_ret * TRUCK_OPCOST_PER_MIN

        t += travel_ret



    time_saved = 0.0

    for wg in walk_groups:

        if len(wg) > 1:

            time_saved += walking_group_saving(wg, cluster_stops, matrix)



    route_summary: Dict[str, Any] = {

        "total_stops": len(route_local),

        "total_distance_km": round(total_dist, 3),

        "total_duration_min": round(t - float(departure_abs), 2),

        "total_fuel_cost": round(total_fuel, 4),

        "total_operational_cost": round(total_op, 4),

        "total_revenue": round(total_rev, 2),

        "walking_groups_count": len([g for g in walk_groups if len(g) > 1]),

        "estimated_time_saved_min": round(time_saved, 2),

    }

    route_summary.update(

        {

            "baseline_rand_maps_distance_km": baseline_maps_summary.get("total_km", 0.0),

            "baseline_rand_maps_fuel_cost": baseline_maps_summary.get("total_fuel_cost", 0.0),

            "baseline_rand_maps_operational_cost": baseline_maps_summary.get("total_operational_cost", 0.0),

            "baseline_rand_maps_total_eur": baseline_maps_summary.get("baseline_rand_maps_total_eur", 0.0),

            "baseline_rand_maps_drive_service_min": baseline_maps_summary.get("baseline_service_and_drive_min", 0.0),

        }

    )



    return {

        "transporter_id": transporter_id,

        "transporter_name": transporter_name,

        "truck_type": truck_type,

        "truck_slots": int(truck_slots),

        "depot": depot,

        "departure_time_actual": _min_to_hhmm(departure_abs),

        "final_route": final_route,

        "route_summary": route_summary,

        "unserviceable_stops": unserviceable,

        "needs_reassignment_indices": needs_reassignment_indices,

        "deadlock_suggestions": deadlock_suggestions,

        "baseline_visit_order_global": baseline_visit_order_global,

        "optimized_visit_order_global": optimized_visit_order_global,

    }





def _assign_walking_group_ids(

    route_local: List[int], walk_groups: List[List[int]]

) -> Dict[int, Optional[int]]:

    m: Dict[int, int] = {}

    gid = 0

    for g in walk_groups:

        if len(g) < 2:

            continue

        gid += 1

        for idx in g:

            m[idx] = gid

    return {loc: m.get(loc) for loc in route_local}





def _empty_cluster_result(

    transporter_id: str,

    transporter_name: str,

    truck_type: str,

    truck_slots: int,

    depot: dict,

) -> dict:

    return {

        "transporter_id": transporter_id,

        "transporter_name": transporter_name,

        "truck_type": truck_type,

        "truck_slots": int(truck_slots),

        "depot": depot,

        "departure_time_actual": _min_to_hhmm(EARLIEST_DEPARTURE_MIN),

        "final_route": [],

        "route_summary": {

            "total_stops": 0,

            "total_distance_km": 0.0,

            "total_duration_min": 0.0,

            "total_fuel_cost": 0.0,

            "total_operational_cost": 0.0,

            "total_revenue": 0.0,

            "walking_groups_count": 0,

            "estimated_time_saved_min": 0.0,

            "baseline_rand_maps_distance_km": 0.0,

            "baseline_rand_maps_fuel_cost": 0.0,

            "baseline_rand_maps_operational_cost": 0.0,

            "baseline_rand_maps_total_eur": 0.0,

            "baseline_rand_maps_drive_service_min": 0.0,

        },

        "unserviceable_stops": [],

        "needs_reassignment_indices": [],

        "deadlock_suggestions": [],

        "baseline_visit_order_global": [],

        "optimized_visit_order_global": [],

    }





def _build_deliveries_stub(pool: List[int], stops_work: Sequence[dict]) -> List[Delivery]:

    out: List[Delivery] = []

    for i in pool:

        s = stops_work[i]

        pos = (float(s["lat"]), float(s["lon"]))

        out.append(

            Delivery(

                stop_idx=i,

                revenue=float(s.get("delivery_value_eur", 0.0)),

                distance_km=0.0,

                fuel_cost=0.0,

                travel_time_min=0.0,

                operational_cost=0.0,

                window_open=int(s.get("window_open_min", 0)),

                window_close=int(s.get("window_close_min", 24 * 60)),

                load_up=float(s.get("delivery_up", 0.0)),

                pos=pos,

            )

        )

    return out





def optimize_all_routes(

    block1_output: dict,

    maps_api_key: str,

    anthropic_api_key: Optional[str] = None,

    gemini_api_key: Optional[str] = None,

    weights: Optional[dict] = None,

) -> dict:

    """

    One independent Distance Matrix optimization per ZonaTransp cluster.

    `block1_output` must be the dict returned by `damm_engine.optimise()`.



    If `maps_api_key` is empty, uses env `GOOGLE_MAPS_API_KEY` if set.

    Deadlock LLM: `anthropic_api_key` or env `ANTHROPIC_API_KEY` (Claude, preferred);

    else `gemini_api_key` or env `GEMINI_API_KEY` (Gemini).



    Loads `Versio1/.env` or cwd `.env` first (see `load_dotenv`).

    """

    load_dotenv()

    get_depot_coordinates()

    gmaps_key = (maps_api_key or os.environ.get("GOOGLE_MAPS_API_KEY") or "").strip()

    akey = (anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY") or "").strip() or None

    gkey = (gemini_api_key or os.environ.get("GEMINI_API_KEY") or "").strip() or None



    clusters: Dict[str, List[int]] = block1_output.get("clusters_zona_transp") or {}

    all_stops: List[dict] = block1_output.get("stops") or []

    truck_type = str(block1_output.get("truck_type", "standard"))

    truck_slots = int(block1_output.get("truck_slots", block1_output.get("n_parcels", 6)))



    results: Dict[str, Any] = {}

    rand_perm = block1_output.get("rand_route")

    if not rand_perm and all_stops:

        rand_perm = list(range(len(all_stops)))



    for zona, stop_indices in clusters.items():

        idxs = [int(i) for i in stop_indices]

        cluster_stops = [all_stops[i] for i in idxs]

        transporter = get_transporter_for_zona(zona, block1_output)

        results[zona] = optimize_cluster_route(

            cluster_stops=cluster_stops,

            transporter_id=transporter["id"],

            transporter_name=transporter["name"],

            truck_type=truck_type,

            truck_slots=truck_slots,

            depot=DEPOT,

            maps_api_key=gmaps_key,

            anthropic_api_key=akey,

            gemini_api_key=gkey,

            weights=weights,

            global_stop_indices=idxs,

            block1_rand_route=rand_perm,

        )

    return results





enrich_stops_with_real_costs = enrich_deliveries_with_matrix

