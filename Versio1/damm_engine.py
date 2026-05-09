"""
DAMM SMART TRUCK V1 - Core Engine
Reads Hackaton.xlsx, runs optimisation, outputs route + load plan data
"""
import pandas as pd
import math
import json

# ── CONFIG ──────────────────────────────────────────────────────────────
XLSX_PATH   = 'Hackaton.xlsx'
ALPHA       = 0.65   # priority (valor € estimado) vs distancia en nn_route
R_RATE      = 0.60   # returnable ratio
PARCELS     = 6
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

def estimate_unit_price_eur(mat_code, unit, desc):
    """
    Precio unitario mayorista orientativo (€ sin IVA, redondeado) solo para rankear prioridad.
    No sustituye PVP ni tarifa real DAMM — heurística por UM + palabras clave en denominación/material.
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

# ── LOAD DATA ────────────────────────────────────────────────────────────
def load_data():
    xl = pd.read_excel(XLSX_PATH, sheet_name=None)
    deliveries = xl['Detalle entrega']
    materials  = xl['Materiales zubic']
    mat_loc = dict(zip(materials['Material'].astype(str), materials['Ubic.'].astype(str)))
    return deliveries, mat_loc

# ── PROCESS ONE ROUTE/DATE ───────────────────────────────────────────────
def process_route(deliveries, mat_loc, ruta, fecha=None):
    df = deliveries[deliveries['Ruta'] == ruta].copy()
    if fecha:
        df = df[df['FECHA'] == fecha]
    if df.empty:
        # Use most recent date
        latest = deliveries[deliveries['Ruta'] == ruta]['FECHA'].iloc[-1]
        df = deliveries[(deliveries['Ruta'] == ruta) & (deliveries['FECHA'] == latest)].copy()
        fecha = latest

    df['qty_num'] = df['Cantidad entrega'].apply(parse_qty)

    # Build per-stop aggregates
    stops = {}
    for _, row in df.iterrows():
        eid = str(row['Entrega'])
        mat = str(row['Material'])
        qty = row['qty_num']
        un  = str(row['Un.medida venta']).strip()

        if eid not in stops:
            cp_raw = str(row['CP']).strip().zfill(5)
            city   = str(row['Población']).strip()
            lat,lon = cp_to_coords(cp_raw, city)
            stops[eid] = {
                'id': eid,
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
                'delivery_value_eur': 0.0,
                'items': [],
                'ret_items': [],
            }

        item_entry = {
            'mat': mat,
            'desc': str(row['Denominación']).strip(),
            'qty': qty,
            'unit': un,
            'ubic': mat_loc.get(mat, 'ZCG'),
        }

        if is_returnable(mat):
            stops[eid]['ret_items'].append(item_entry)
            if un == 'CAJ': stops[eid]['ret_caj'] += qty
            elif un == 'BRL': stops[eid]['ret_brl'] += qty
        else:
            stops[eid]['items'].append(item_entry)
            unit_eur = estimate_unit_price_eur(mat, un, item_entry['desc'])
            stops[eid]['delivery_value_eur'] += qty * unit_eur
            if un == 'CAJ': stops[eid]['delivery_caj'] += qty
            elif un == 'BRL': stops[eid]['delivery_brl'] += qty
            else: stops[eid]['delivery_un'] += qty

    stop_list = list(stops.values())
    return stop_list, fecha

# ── PRIORITY SCORING ─────────────────────────────────────────────────────
def score_priority(stops):
    values = [max(0.0, float(s.get('delivery_value_eur', 0))) for s in stops]
    max_v = max(values) if max(values) > 0 else 1

    ret_ratios = []
    for s in stops:
        total_d = s['delivery_caj'] + s['delivery_brl']*5
        total_r = s['ret_caj'] + s['ret_brl']*5
        ret_ratios.append(min(total_r/total_d, 1.0) if total_d > 0 else 0.0)

    scores = [0.7*(v/max_v) + 0.3*r for v,r in zip(values, ret_ratios)]
    return scores

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
def build_load_plan(route, stops):
    # Load order = reverse of route (last client loaded first = deepest in truck)
    load_order = list(reversed(route))
    vols = [stops[i]['delivery_caj'] + stops[i]['delivery_brl']*5 for i in load_order]
    total = sum(vols) if sum(vols) > 0 else 1
    per_parcel = total / (PARCELS - 1)  # P1 reserved for returnables

    parcels = {i: [] for i in range(1, PARCELS+1)}
    cur_p = PARCELS  # start filling from P6 (deepest)
    cur_vol = 0

    for client_i, vol in zip(load_order, vols):
        parcels[cur_p].append(client_i)
        cur_vol += vol
        if cur_vol >= per_parcel and cur_p > 2:
            cur_p -= 1
            cur_vol = 0

    # P1 always returnables
    parcels[1] = ['RETURNABLES']

    # Build flat product list per parcel with warehouse locations
    parcel_products = {}
    for p_num, client_idxs in parcels.items():
        products = []
        if p_num == 1:
            # Collect all returnable items across all stops
            for i in route:
                for item in stops[i]['ret_items']:
                    item['client'] = stops[i]['name']
                    products.append(item)
        else:
            for ci in client_idxs:
                if isinstance(ci, int):
                    for item in stops[ci]['items']:
                        item['client'] = stops[ci]['name']
                        products.append(item)
        parcel_products[p_num] = products

    return parcels, parcel_products

# ── MAIN OPTIMISE FUNCTION ────────────────────────────────────────────────
def optimise(ruta, fecha=None):
    deliveries, mat_loc = load_data()
    stops, fecha_used = process_route(deliveries, mat_loc, ruta, fecha)

    if not stops:
        return None

    depot = DEPOTS.get(ruta, DEPOTS['DEFAULT'])
    depot_coords = (depot['lat'], depot['lon'])

    priority = score_priority(stops)
    density  = density_scores(stops)

    import random
    random.seed(42)
    rand_route = list(range(len(stops)))
    random.shuffle(rand_route)
    rand_dist = route_dist(rand_route, stops, depot_coords)

    nn = nn_route(stops, depot_coords, priority, ALPHA)
    opt_route, opt_dist = two_opt(nn, stops, depot_coords)

    improvement = (rand_dist - opt_dist) / rand_dist * 100 if rand_dist > 0 else 0
    opt_time  = opt_dist/AVG_SPEED*60  + len(stops)*UNLOAD_MIN
    rand_time = rand_dist/AVG_SPEED*60 + len(stops)*UNLOAD_MIN

    parcels, parcel_products = build_load_plan(opt_route, stops)

    return {
        'ruta': ruta,
        'fecha': fecha_used,
        'depot': depot,
        'stops': stops,
        'priority': [round(p,3) for p in priority],
        'density': [round(d,3) for d in density],
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

