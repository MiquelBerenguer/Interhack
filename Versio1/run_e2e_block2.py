# -*- coding: utf-8 -*-
"""
End-to-end: Block 1 (optimise) -> Block 2 (run_block2).
Run from this folder so Hackaton.xlsx and .env are found:

  cd Versio1
  python run_e2e_block2.py

For the full pipeline (optional all dates, PDFs, comparativa, Block 3 sample):
  python run_full_pipeline.py
  python run_full_pipeline.py --all-dates [--ruta-filter DR0027]

Put GOOGLE_MAPS_API_KEY (and optionally ANTHROPIC_API_KEY or GEMINI_API_KEY) in .env.
"""
from __future__ import annotations

import json
import os

# Ensure cwd is Versio1 (Excel + .env)
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from block2_maps_routing import load_dotenv
from damm_engine import optimise, run_block2


def main() -> None:
    load_dotenv()
    ruta = os.environ.get("E2E_RUTA", "DR0027")
    fecha = os.environ.get("E2E_FECHA", "02/03/2026")

    print(f"Block 1: optimise({ruta!r}, {fecha!r}) ...")
    b1 = optimise(ruta, fecha)
    if not b1:
        raise SystemExit("Block 1 returned None (no data for route/date?)")

    print(f"  stops: {len(b1.get('stops', []))}, clusters: {list((b1.get('clusters_zona_transp') or {}).keys())}")

    maps = (os.environ.get("GOOGLE_MAPS_API_KEY") or "").strip()
    if not maps:
        print("  WARNING: GOOGLE_MAPS_API_KEY empty - Block 2 will use haversine fallback.")

    print("Block 2: run_block2 ...")
    b2 = run_block2(b1, maps_api_key=maps)

    out_path = os.path.join(os.getcwd(), "block2_result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(b2, f, indent=2, default=str)
    print(f"Wrote {out_path}")
    for zona, z in b2.items():
        sm = z.get("route_summary") or {}
        print(
            f"  [{zona}] departure={z.get('departure_time_actual')} "
            f"stops={sm.get('total_stops')} km={sm.get('total_distance_km')} "
            f"fuel_EUR={sm.get('total_fuel_cost')}"
        )


if __name__ == "__main__":
    main()
