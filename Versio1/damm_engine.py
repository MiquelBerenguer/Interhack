"""
DAMM SMART TRUCK V1 - Core Engine
Reads Hackaton.xlsx, runs optimisation, outputs route + load plan data
"""
import json
import math
import os

import pandas as pd

from dynamic_truck import (
    full_route_feasibility,
    reserved_empty_up,
    slots_for_truck_type,
)
from cabecera_transporte import (
    collect_entrega_ids_for_route,
    infer_current_transport_numbers,
    load_cabecera_sheet,
    normalize_cabecera_df,
    reassignment_hints_for_stops,
)
from horarios_windows import attach_stop_windows, load_horarios_dataframe
from priority_cluster import (
    PRIORITY_WEIGHTS,
    escalate_truck_slots,
    find_unassigned,
    route_with_zona_clusters,
)
from zm040_up import aggregate_stop_up, build_units_per_pallet_map, load_zm040

# ── CONFIG ──────────────────────────────────────────────────────────────
XLSX_PATH   = 'Hackaton.xlsx'
# run_full_pipeline can point Block 1 to a filtered workbook (same schema)
HACKATON_XLSX_ENV = "INTERHACK_HACKATON_XLSX"


def hackaton_xlsx_read_path() -> str:
    p = os.environ.get(HACKATON_XLSX_ENV, "").strip()
    return p if p else XLSX_PATH
ZM040_PATH  = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'Hackaton', 'ZM040.XLSX')
ALPHA       = 0.65   # priority (transport sector / in-stop value) vs distance in nn_route
# Truck sizes: small (3) | standard (6) | large (8 pallet slots = UP)
TRUCK_TYPE  = 'standard'
# ── Sector = delivery area per transporter (territorial heat-map) ──
# Capacity per sector in UP (ZM040); None -> total route UP / TRANSPORT_AUTO_SECTOR_COUNT.
TRANSPORT_CAPACITY_UNITS = None
TRANSPORT_AUTO_SECTOR_COUNT = 4
# If not None, a sector may not exceed this pair-to-pair diameter (km) while growing;
# keeps zones more compact when capacity still allows distant stops.
SECTOR_MAX_DIAMETER_KM = None

R_RATE      = 0.60   # return empties ~60% of delivery UP (dynamic truck model)
AVG_SPEED   = 35     # km/h urban
UNLOAD_MIN  = 8      # minutes per stop

# Known depots per route prefix (lat/lon)
DEPOTS = {
    'DR0027': {'name': 'DDI Mollet del Vallès', 'lat': 41.5430, 'lon': 2.2140},
    'DR0001': {'name': 'DDI Mollet del Vallès', 'lat': 41.5430, 'lon': 2.2140},
    'DR0006': {'name': 'DDI Mollet del Vallès', 'lat': 41.5430, 'lon': 2.2140},
    'DEFAULT':{'name': 'DDI Mollet del Vallès', 'lat': 41.5430, 'lon': 2.2140},
}

# Approximate coords by postal code (Catalonia)
CP_COORDS = {
    '08100': (41.5430, 2.2140), '08101': (41.5430, 2.2140),
    '08170': (41.5540, 2.2680), '08160': (41.5600, 2.1900),
    '08105': (41.5350, 2.2450), '08191': (41.5660, 2.2570),
    '08403': (41.6070, 2.2890), '08401': (41.6070, 2.2890),
    '08410': (41.5900, 2.2700), '08500': (41.9302, 2.2540),
    '08503': (41.9501, 2.2673), '08506': (41.9611, 2.2810),
    '08510': (41.9912, 2.3175), '08552': (41.8970, 2.2430),
    '08560': (42.0030, 2.2880), '08580': (42.0480, 2.2200),
    '08550': (41.9800, 2.3400), '08520': (41.8740, 2.2800),
    '08530': (41.9100, 2.1900), '08480': (41.7500, 2.1600),
    '08460': (41.7670, 2.4650), '08401': (41.6090, 2.2890),
    '08402': (41.6090, 2.2890), '08226': (41.5618, 2.0083),
    '08025': (41.4060, 2.1840), '08120': (41.5433, 2.2281),
    '17412': (41.7690, 2.7250),
}

def cp_to_coords(cp, city=''):
    cp_str = str(cp).strip().zfill(5)
    if cp_str in CP_COORDS:
        return CP_COORDS[cp_str]
    # Jitter by city hash for uniqueness
    h = abs(hash(city)) % 1000
    base = CP_COORDS.get(cp_str[:3]+'00', (41.60, 2.20))
    return (base[0] + (h%50-25)*0.001, base[1] + (h//50-10)*0.001)

def haversine(a, b):
    R = 6371
    lat1,lon1 = math.radians(a[0]),math.radians(a[1])
    lat2,lon2 = math.radians(b[0]),math.radians(b[1])
    x = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return R*2*math.asin(math.sqrt(max(0,x)))

def is_returnable(mat_code):
    m = str(mat_code).strip()
    return m.startswith('3ENV') or m.startswith('CJ') or m in ['BRL30V','BRL20V','BRL50V','TB8V']

def parse_qty(v):
    try:
        return float(str(v).replace('.','').replace(',','.'))
    except:
        return 0.0

def _norm_text(s):
    return ' '.join(str(s).strip().lower().split())

def row_zona_transp(row) -> str:
    """ZonaTransp column(s); duplicate headers become ZonaTransp, ZonaTransp.1 in pandas."""
    for c in row.index:
        key = str(c).lower().replace(" ", "").replace("_", "")
        if "zonatransp" in key:
            v = row.get(c)
            if v is not None and str(v).strip() and str(v).strip().lower() not in ("nan", "none"):
                return str(v).strip()
    return "SIN_ZONA"


def business_key(row):
    """
    One business / delivery point: same name, postal code and street -> one stop.
    Several different Entrega ids with the same key merge into a single stop.
    """
    return '|'.join((
        _norm_text(row['Nombre 1']),
        str(row['CP']).strip().zfill(5),
        _norm_text(row['Calle']),
    ))

def estimate_unit_price_eur(mat_code, unit, desc):
    """
    Rough wholesale unit price (EUR ex VAT, rounded) only to rank delivery priority.
    Does not replace list price or real DAMM tariffs — heuristic from UM + keywords in description/material.
    """
    m_raw = str(mat_code).strip()
    u = str(unit).strip().upper()
    d = str(desc).strip().lower()
    m = m_raw.lower()

    if u == 'BRL':
        if '50' in m or '50l' in d or '50 l' in d:
            return 128.0
        if '30' in m or '30l' in d or '30 l' in d:
            return 112.0
        return 96.0

    if u == 'CAJ':
        px = 24.0
        if any(k in d for k in ('especial', 'ipa', 'double', 'cerveza negra',
                                'imperial', 'barrel', 'barrica', 'weiss',
                                'trigo', 'weizen', 'gourmet', 'edición')):
            px = 34.0
        elif any(k in d for k in ('radler', 'shandy', '0,0', 'sin alcohol')):
            px = 26.0
        elif any(k in d for k in ('agua', 'aquabona', 'fontvell', 'fon ', 'miner')):
            px = 16.0
        elif any(k in d for k in ('refresco', 'gaseosa', 'litro', 'tónica', 'tonica',
                                  'limonada', 'kombucha')):
            px = 20.5
        elif any(k in d for k in ('vermút', 'vermut', 'sangría')):
            px = 28.0
        elif any(k in d for k in ('estrella', 'damm', 'voll', 'ak ', 'turia',
                                  'malandar', 'bock')) or any(k in m for k in ('est', 'damm', 'voll')):
            px = 26.0
        if any(k in d for k in ('pack ', 'premium', 'lata')):
            px = max(px, 27.5)
        return px

    if u == 'UN':
        return 1.15

    return 20.0

def _resolved_zm040_path():
    base = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        ZM040_PATH,
        os.path.join(base, 'ZM040.XLSX'),
        os.path.join(os.getcwd(), 'Hackaton', 'ZM040.XLSX'),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


# ── LOAD DATA ────────────────────────────────────────────────────────────
def load_data():
    xl = pd.read_excel(hackaton_xlsx_read_path(), sheet_name=None)
    deliveries = xl['Detalle entrega']
    materials  = xl['Materiales zubic']
    mat_loc = dict(zip(materials['Material'].astype(str), materials['Ubic.'].astype(str)))
    zm040 = _resolved_zm040_path()
    units_per_pallet = {}
    if zm040:
        zdf = load_zm040(zm040)
        units_per_pallet = build_units_per_pallet_map(zdf)
    cabecera = load_cabecera_sheet(xl)
    return deliveries, mat_loc, units_per_pallet, cabecera

# ── PROCESS ONE ROUTE/DATE ───────────────────────────────────────────────
def process_route(deliveries, mat_loc, ruta, fecha=None, units_per_pallet=None):
    df = deliveries[deliveries['Ruta'] == ruta].copy()
    if fecha:
        df = df[df['FECHA'] == fecha]
    if df.empty:
        # Use most recent date
        latest = deliveries[deliveries['Ruta'] == ruta]['FECHA'].iloc[-1]
        df = deliveries[(deliveries['Ruta'] == ruta) & (deliveries['FECHA'] == latest)].copy()
        fecha = latest

    df['qty_num'] = df['Cantidad entrega'].apply(parse_qty)
    if units_per_pallet is None:
        units_per_pallet = {}

    # Build per-stop aggregates (one stop per business: name + postal code + street).
    stops = {}
    for _, row in df.iterrows():
        eid = str(row['Entrega'])
        bid = business_key(row)
        mat = str(row['Material'])
        qty = row['qty_num']
        un  = str(row['Un.medida venta']).strip()

        if bid not in stops:
            cp_raw = str(row['CP']).strip().zfill(5)
            city   = str(row['Población']).strip()
            lat,lon = cp_to_coords(cp_raw, city)
            stops[bid] = {
                'id': eid,
                'entrega_ids': [eid],
                'name': str(row['Nombre 1']).strip(),
                'address': f"{str(row['Calle']).strip()}, {cp_raw} {city}",
                'city': city,
                'cp': cp_raw,
                'lat': lat,
                'lon': lon,
                'delivery_caj': 0,
                'delivery_brl': 0,
                'delivery_un':  0,
                'ret_caj': 0,
                'ret_brl': 0,
                'delivery_up': 0.0,
                'return_up': 0.0,
                'delivery_value_eur': 0.0,
                'items': [],
                'ret_items': [],
                'zona_vals': [],
            }
        else:
            if eid not in stops[bid]['entrega_ids']:
                stops[bid]['entrega_ids'].append(eid)

        zt = row_zona_transp(row)
        stops[bid]['zona_vals'].append(zt)

        item_entry = {
            'mat': mat,
            'desc': str(row['Denominación']).strip(),
            'qty': qty,
            'unit': un,
            'ubic': mat_loc.get(mat, 'ZCG'),
        }

        if is_returnable(mat):
            stops[bid]['ret_items'].append(item_entry)
            if un == 'CAJ': stops[bid]['ret_caj'] += qty
            elif un == 'BRL': stops[bid]['ret_brl'] += qty
        else:
            unit_eur = estimate_unit_price_eur(mat, un, item_entry['desc'])
            item_entry['unit_price_eur'] = round(float(unit_eur), 4)
            item_entry['line_total_eur'] = round(float(qty) * float(unit_eur), 2)
            stops[bid]['items'].append(item_entry)
            stops[bid]['delivery_value_eur'] += qty * unit_eur
            if un == 'CAJ': stops[bid]['delivery_caj'] += qty
            elif un == 'BRL': stops[bid]['delivery_brl'] += qty
            else: stops[bid]['delivery_un'] += qty

    stop_list = list(stops.values())
    for s in stop_list:
        s['entrega_ids'].sort()
        s['delivery_up'] = aggregate_stop_up(s['items'], units_per_pallet)
        # Returns in UP (same ZM040 mapping); dynamic model uses 60% x delivery_up as empties on board
        s['return_up'] = R_RATE * float(s['delivery_up'])
        vals = s.pop('zona_vals', [])
        if vals:
            s['zona_transp'] = max(set(vals), key=vals.count)
        else:
            s['zona_transp'] = 'SIN_ZONA'
    return stop_list, fecha

# ── PRIORITY SCORING ─────────────────────────────────────────────────────
def score_priority(stops):
    """
    Only outbound order value (estimated EUR). Returnables are empties and do not weigh here:
    they are not subtracted from value nor mixed with a ratio against same-day delivery.
    """
    values = [max(0.0, float(s.get('delivery_value_eur', 0))) for s in stops]
    max_v = max(values) if max(values) > 0 else 1
    return [v / max_v for v in values]

def stop_delivery_up(s):
    """Outbound demand in UP (ZM040: 1/PAL per sales unit from PAL row)."""
    return float(s.get('delivery_up', 0.0))


def pairwise_max_distance_km(coords, idx_list):
    if len(idx_list) < 2:
        return 0.0
    m = 0.0
    for ia in range(len(idx_list)):
        for ib in range(ia + 1, len(idx_list)):
            d = haversine(coords[idx_list[ia]], coords[idx_list[ib]])
            if d > m:
                m = d
    return m


def centroid_geo(coords, idx_list):
    lats = [coords[i][0] for i in idx_list]
    lons = [coords[i][1] for i in idx_list]
    return (sum(lats) / len(lats), sum(lons) / len(lons))


def resolved_transport_capacity(stops):
    if TRANSPORT_CAPACITY_UNITS is not None:
        return max(float(TRANSPORT_CAPACITY_UNITS), 1e-6)
    total = sum(stop_delivery_up(s) for s in stops)
    if total <= 0:
        return max(1.0, len(stops) / max(TRANSPORT_AUTO_SECTOR_COUNT, 1))
    return max(total / max(TRANSPORT_AUTO_SECTOR_COUNT, 1), 1e-6)


def build_transport_sectors(stops, density, capacity_units):
    """
    Sectors = territory assignable to a transporter: cumulative demand <= capacity,
    growth by nearest customer to the current block, optional diameter cap.
    Cover radius (radius_cover_km) is derived later from the set of stops.
    """
    n = len(stops)
    coords = [(s['lat'], s['lon']) for s in stops]
    demand = [stop_delivery_up(s) for s in stops]
    assigned = set()
    sectors = []

    while len(assigned) < n:
        rem = [i for i in range(n) if i not in assigned]

        def pick_seed(rem_list):
            if not rem_list:
                return None
            heavy_local = [i for i in rem_list if demand[i] > capacity_units + 1e-9]
            pool = heavy_local if heavy_local else rem_list
            return max(pool, key=lambda i: density[i] * 1e9 + demand[i])

        seed = pick_seed(rem)
        memb = [seed]
        assigned.add(seed)
        load = demand[seed]

        while True:
            best_j, best_near = None, math.inf
            for j in range(n):
                if j in assigned:
                    continue
                if load + demand[j] > capacity_units + 1e-9:
                    continue
                d_near = min(haversine(coords[j], coords[k]) for k in memb)
                tent = memb + [j]
                diam = pairwise_max_distance_km(coords, tent)
                if SECTOR_MAX_DIAMETER_KM is not None and diam > SECTOR_MAX_DIAMETER_KM + 1e-9:
                    continue
                if d_near < best_near:
                    best_near = d_near
                    best_j = j
            if best_j is None:
                break
            memb.append(best_j)
            assigned.add(best_j)
            load += demand[best_j]
        sectors.append(sorted(memb))
    return sectors


def sort_sectors_for_priority_display(sectors, density):
    """Sort sectors by average heat-map intensity (density)."""
    scored = [(sum(density[i] for i in s) / len(s), sorted(s.copy())) for s in sectors]
    scored.sort(reverse=True, key=lambda t: t[0])
    return [t[1] for t in scored]


def transport_sectors_geo_meta(stops, sectors_ordered_lists, coords, dens, capacity_units):
    """Radii and capacity usage per sector (for UI / heat maps)."""
    meta = []
    for sid, memb in enumerate(sectors_ordered_lists):
        heat = sum(dens[i] for i in memb) / len(memb)
        vol = sum(stop_delivery_up(stops[i]) for i in memb)
        c = centroid_geo(coords, memb)
        cov = max(haversine(c, coords[i]) for i in memb)
        dia = pairwise_max_distance_km(coords, memb)
        meta.append({
            'sector_id': sid,
            'heat_avg': round(heat, 4),
            'demand_vol': round(vol, 4),
            'demand_up': round(vol, 4),
            'capacity_vol': round(capacity_units, 4),
            'capacity_usage_pct': round(100.0 * vol / capacity_units, 1) if capacity_units else None,
            'radius_cover_km': round(cov, 3),
            'diameter_span_km': round(dia, 3),
            'centroid_lat': round(c[0], 5),
            'centroid_lon': round(c[1], 5),
            'n_stops': len(memb),
            'stop_indices': memb[:],
            'overload_sector': vol > capacity_units + 1e-6,
        })
    return meta


def priority_within_transport_sectors(stops, sectors_ordered_lists, dens):
    """
    Across sectors: display order by density (already reflected in sectors_ordered_lists).
    Within a sector: relative priority from order value EUR only.
    """
    n = len(stops)
    if n == 0:
        return [], []

    values = [max(0.0, float(s.get('delivery_value_eur', 0))) for s in stops]
    BAND = 1000
    priority_raw = [0.0] * n
    stop_sector_rank = [0] * n

    clusters_scored = []
    for idxs in sectors_ordered_lists:
        heat_avg = sum(dens[i] for i in idxs) / len(idxs)
        clusters_scored.append({'stop_indices': idxs[:], 'heat_avg': heat_avg})

    for sector_rank, cl in enumerate(clusters_scored):
        idxs = cl['stop_indices']
        mx = max((values[i] for i in idxs), default=0.0)
        if mx <= 0:
            mx = 1.0
        vn = {i: values[i] / mx for i in idxs}
        band_base = (len(clusters_scored) - 1 - sector_rank) * BAND
        for i in idxs:
            priority_raw[i] = band_base + vn[i] * (BAND - 1)
            stop_sector_rank[i] = sector_rank

    mxp = max(priority_raw) if priority_raw else 1.0
    if mxp <= 0:
        mxp = 1.0
    priority_final = [p / mxp for p in priority_raw]
    return priority_final, stop_sector_rank

# ── DENSITY HEATMAP ──────────────────────────────────────────────────────
def density_scores(stops, k=4):
    coords = [(s['lat'],s['lon']) for s in stops]
    result = []
    for i,c in enumerate(coords):
        dists = sorted([haversine(c,coords[j]) for j in range(len(coords)) if j!=i])
        avg = sum(dists[:k])/k if len(dists)>=k else sum(dists)/len(dists) if dists else 1
        result.append(avg)
    mx = max(result) if result and max(result) > 0 else 1
    return [1-(d/mx) for d in result]

# ── ROUTING ──────────────────────────────────────────────────────────────
def nn_route(stops, depot_coords, priority, alpha):
    unvisited = list(range(len(stops)))
    route = []
    current = depot_coords
    while unvisited:
        best, best_score = None, -1
        for i in unvisited:
            d = haversine(current, (stops[i]['lat'], stops[i]['lon']))
            if d < 0.01: d = 0.01
            score = alpha*priority[i] + (1-alpha)*(1/d)
            if score > best_score:
                best_score, best = score, i
        route.append(best)
        current = (stops[best]['lat'], stops[best]['lon'])
        unvisited.remove(best)
    return route

def route_dist(route, stops, depot):
    coords = [(s['lat'],s['lon']) for s in stops]
    total = haversine(depot, coords[route[0]])
    for i in range(len(route)-1):
        total += haversine(coords[route[i]], coords[route[i+1]])
    total += haversine(coords[route[-1]], depot)
    return total

def two_opt(route, stops, depot):
    best = route[:]
    best_d = route_dist(best, stops, depot)
    improved = True
    while improved:
        improved = False
        for i in range(1, len(route)-1):
            for j in range(i+1, len(route)):
                new = best[:i] + best[i:j+1][::-1] + best[j+1:]
                nd = route_dist(new, stops, depot)
                if nd < best_d - 0.01:
                    best, best_d, improved = new, nd, True
    return best, best_d

# ── LOAD PLAN ────────────────────────────────────────────────────────────
def build_load_plan(route, stops, n_parcels=None):
    """
    P1 = first visited customer; P2..Pn share remaining slots by UP.
    P1 products: first customer outbound + all returnables on the route (manifest).
    """
    if n_parcels is None:
        n_parcels = slots_for_truck_type(TRUCK_TYPE)
    if not route:
        return {}, {}

    first = route[0]
    load_order = list(reversed(route))
    vols = [stops[i]['delivery_up'] for i in load_order]
    total = sum(vols) if sum(vols) > 0 else 1
    shared_slots = max(1, n_parcels - 1)
    per_parcel = total / shared_slots

    parcels = {i: [] for i in range(1, n_parcels + 1)}
    parcels[1] = [first]

    cur_p = n_parcels
    cur_vol = 0.0
    for client_i in load_order:
        if client_i == first:
            continue
        parcels[cur_p].append(client_i)
        cur_vol += stops[client_i]['delivery_up']
        if cur_vol >= per_parcel and cur_p > 2:
            cur_p -= 1
            cur_vol = 0.0

    parcel_products = {}
    for p_num, client_idxs in parcels.items():
        products = []
        if p_num == 1:
            for item in stops[first]['items']:
                row = dict(item)
                row['client'] = stops[first]['name']
                products.append(row)
            for i in route:
                for item in stops[i]['ret_items']:
                    row = dict(item)
                    row['client'] = stops[i]['name']
                    products.append(row)
        else:
            for ci in client_idxs:
                if isinstance(ci, int):
                    for item in stops[ci]['items']:
                        row = dict(item)
                        row['client'] = stops[ci]['name']
                        products.append(row)
        parcel_products[p_num] = products

    return parcels, parcel_products

# ── MAIN OPTIMISE FUNCTION ────────────────────────────────────────────────
def optimise(ruta, fecha=None):
    deliveries, mat_loc, units_per_pallet, cabecera_df = load_data()
    stops, fecha_used = process_route(deliveries, mat_loc, ruta, fecha, units_per_pallet)

    if not stops:
        return None

    horarios_df = load_horarios_dataframe()
    attach_stop_windows(stops, horarios_df, str(fecha_used))

    depot = DEPOTS.get(ruta, DEPOTS['DEFAULT'])
    depot_coords = (depot['lat'], depot['lon'])

    density = density_scores(stops)
    priority_value = score_priority(stops)
    cap_vol = resolved_transport_capacity(stops)
    sector_partition = build_transport_sectors(stops, density, cap_vol)
    sectors_ordered = sort_sectors_for_priority_display(sector_partition, density)
    coords = [(s['lat'], s['lon']) for s in stops]
    transport_sectors = transport_sectors_geo_meta(stops, sectors_ordered, coords, density, cap_vol)
    priority, stop_sector_rank = priority_within_transport_sectors(stops, sectors_ordered, density)

    import random

    rng = random.Random(42)
    rand_route = list(range(len(stops)))
    rng.shuffle(rand_route)
    rand_dist = route_dist(rand_route, stops, depot_coords)

    nn = nn_route(stops, depot_coords, priority, ALPHA)

    truck_chain = ("small", "standard", "large")
    try_types = escalate_truck_slots(TRUCK_TYPE, truck_chain)
    opt_route: list = []
    selected_truck = TRUCK_TYPE
    n_slots = slots_for_truck_type(TRUCK_TYPE)
    cluster_warnings: list = []
    clusters_by_zona: dict = {}
    unserviceable: list = []
    needs_reassignment: list = []

    best_partial: list = []
    best_partial_meta: tuple = (TRUCK_TYPE, slots_for_truck_type(TRUCK_TYPE), -1)
    best_clusters: dict = {}

    for tt in try_types:
        ns = float(slots_for_truck_type(tt))
        res_e = reserved_empty_up(tt, int(ns))
        cand, clusters_by_zona, cluster_warnings, _ = route_with_zona_clusters(
            stops,
            depot_coords,
            ns,
            res_e,
            AVG_SPEED,
            UNLOAD_MIN,
            weights=PRIORITY_WEIGHTS,
        )
        un = find_unassigned(stops, cand)
        delivery_up_vec = [float(s["delivery_up"]) for s in stops]
        cap_feas = full_route_feasibility(cand, delivery_up_vec, int(ns), truck_type=tt)
        served = len(cand)
        if served > best_partial_meta[2]:
            best_partial = cand[:]
            best_partial_meta = (tt, int(ns), served)
            best_clusters = {k: v[:] for k, v in clusters_by_zona.items()}
        if len(un) == 0 and cap_feas.ok:
            opt_route = cand
            selected_truck = tt
            n_slots = int(ns)
            break
    else:
        if best_partial:
            opt_route = best_partial
            selected_truck, n_slots, _ = best_partial_meta
            clusters_by_zona = best_clusters

    if not opt_route:
        opt_route = nn[:]
        selected_truck = TRUCK_TYPE
        n_slots = slots_for_truck_type(TRUCK_TYPE)

    unassigned_final = find_unassigned(stops, opt_route)
    for ui in unassigned_final:
        unserviceable.append(
            {
                "stop_index": ui,
                "name": stops[ui].get("name", ""),
                "reason": "no_fit_window_capacity_or_zone",
            }
        )
        needs_reassignment.append(ui)

    try:
        route_day = pd.to_datetime(str(fecha_used), dayfirst=True).normalize()
    except Exception:
        route_day = pd.Timestamp.now().normalize()
    cab_norm = normalize_cabecera_df(cabecera_df) if cabecera_df is not None and not cabecera_df.empty else pd.DataFrame()
    entregas_ruta = collect_entrega_ids_for_route(deliveries, ruta, str(fecha_used))
    current_nt = (
        infer_current_transport_numbers(cab_norm, entregas_ruta, route_day)
        if not cab_norm.empty
        else []
    )
    reassignment_hints = reassignment_hints_for_stops(
        stops, unassigned_final, cab_norm, route_day, current_nt
    )

    opt_route, opt_dist = two_opt(opt_route, stops, depot_coords)

    improvement = (rand_dist - opt_dist) / rand_dist * 100 if rand_dist > 0 else 0
    opt_time  = opt_dist/AVG_SPEED*60  + len(stops)*UNLOAD_MIN
    rand_time = rand_dist/AVG_SPEED*60 + len(stops)*UNLOAD_MIN

    parcels, parcel_products = build_load_plan(opt_route, stops, n_parcels=n_slots)

    delivery_up_vec = [float(s['delivery_up']) for s in stops]
    cap_feas = full_route_feasibility(
        opt_route,
        delivery_up_vec,
        n_slots,
        truck_type=selected_truck,
    )

    return {
        'ruta': ruta,
        'fecha': fecha_used,
        'depot': depot,
        'stops': stops,
        'capacity_model': 'UP_ZM040',
        'truck_type': selected_truck,
        'truck_type_requested': TRUCK_TYPE,
        'n_parcels': n_slots,
        'priority_weights': PRIORITY_WEIGHTS,
        'clusters_zona_transp': {k: v[:] for k, v in clusters_by_zona.items()},
        'cluster_tight_window_warnings': cluster_warnings,
        'unserviceable_stops': unserviceable,
        'needs_reassignment_indices': needs_reassignment,
        'cabecera_n_transporte_inferred': current_nt,
        'cabecera_reassignment_hints': reassignment_hints,
        'reserved_empty_up': round(reserved_empty_up(selected_truck, n_slots), 4),
        'total_delivery_up': round(sum(delivery_up_vec), 4),
        'capacity_feasible': cap_feas.ok,
        'capacity_warnings': cap_feas.messages,
        'peak_used_up': round(cap_feas.peak_used_up, 4),
        'priority': [round(p,3) for p in priority],
        'priority_value': [round(p,3) for p in priority_value],
        'density': [round(d,3) for d in density],
        'transport_capacity_vol': round(cap_vol, 4),
        'transport_auto_sector_count': TRANSPORT_AUTO_SECTOR_COUNT,
        'transport_sectors': transport_sectors,
        'stop_sector_rank': stop_sector_rank,
        'stop_heatmap_group_rank': stop_sector_rank,
        'opt_route': opt_route,
        'rand_route': rand_route,
        'opt_dist': round(opt_dist,1),
        'rand_dist': round(rand_dist,1),
        'opt_time': round(opt_time),
        'rand_time': round(rand_time),
        'improvement_pct': round(improvement,1),
        'time_saved': round(rand_time - opt_time),
        'parcels': {str(k): v for k,v in parcels.items()},
        'parcel_products': {str(k): v for k,v in parcel_products.items()},
        'alpha': ALPHA,
        'r_rate': R_RATE,
    }


def run_block2(
    block1_output: dict,
    maps_api_key: str,
    llm_api_key: str | None = None,
    gemini_api_key: str | None = None,
    weights: dict | None = None,
) -> dict:
    """
    Block 2: per-cluster routes via Google Distance Matrix API.
    Maps: pass `maps_api_key` or set GOOGLE_MAPS_API_KEY.
    Deadlock LLM: pass `llm_api_key` (Claude / Anthropic) or ANTHROPIC_API_KEY; or pass
    `gemini_api_key` / set GEMINI_API_KEY if you use Gemini instead.
    """
    from block2_maps_routing import optimize_all_routes

    return optimize_all_routes(
        block1_output,
        maps_api_key,
        anthropic_api_key=llm_api_key,
        gemini_api_key=gemini_api_key,
        weights=weights,
    )


if __name__ == '__main__':
    result = optimise('DR0027', '02/03/2026')
    print(f"Route: {result['ruta']} | Date: {result['fecha']}")
    print(f"Stops: {len(result['stops'])}")
    print(f"Distance: {result['rand_dist']} → {result['opt_dist']} km ({result['improvement_pct']}% saved)")
    print(f"Time: {result['rand_time']} → {result['opt_time']} min ({result['time_saved']} min saved)")
    print("Parcels:")
    for p,clients in result['parcels'].items():
        names = [result['stops'][c]['name'] if isinstance(c,int) else c for c in clients[:3]]
        print(f"  P{p}: {names}")
    with open('result.json','w') as f:
        json.dump(result, f, indent=2, default=str)
    print('Saved result.json')

