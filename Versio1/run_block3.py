# -*- coding: utf-8 -*-
"""
Run Block 3: build loading_plan + warehouse_pick_sequence from block2_result.json.

  cd Versio1
  python run_block3.py

Paths default to this folder + ../Hackaton/ZM040.XLSX. Override with flags or env:
  BLOCK2_JSON, HACKATON_XLSX, ZM040_XLSX, MATERIALES_XLSX, BLOCK3_OUT
"""
from __future__ import annotations

import argparse
import json
import os

from block3_loading import Block3Paths, run_block3


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="Block 3 loading plan from Block 2 JSON")
    ap.add_argument(
        "--block2",
        default=os.environ.get("BLOCK2_JSON", os.path.join(here, "block2_result.json")),
    )
    ap.add_argument(
        "--hackaton",
        default=os.environ.get("HACKATON_XLSX", os.path.join(here, "Hackaton.xlsx")),
    )
    ap.add_argument(
        "--zm040",
        default=os.environ.get("ZM040_XLSX", os.path.join(here, "..", "Hackaton", "ZM040.XLSX")),
    )
    ap.add_argument(
        "--materiales",
        default=os.environ.get("MATERIALES_XLSX", os.path.join(here, "Hackaton.xlsx")),
        help="Excel file containing sheet Materiales zubic (default: same as Hackaton)",
    )
    ap.add_argument(
        "--out",
        default=os.environ.get("BLOCK3_OUT", os.path.join(here, "block3_result.json")),
    )
    args = ap.parse_args()

    paths = Block3Paths(
        block2_json=os.path.abspath(args.block2),
        hackaton_xlsx=os.path.abspath(args.hackaton),
        zm040_xlsx=os.path.abspath(args.zm040),
        materiales_xlsx=os.path.abspath(args.materiales),
    )
    result = run_block3(paths)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"Wrote {os.path.abspath(args.out)}")
    for tid, z in result.items():
        sm = z.get("plan_summary") or {}
        print(
            f"  [{tid}] parcelas={sm.get('total_parcelas_used')} "
            f"weight_kg={sm.get('total_weight_kg')} warnings={len(z.get('warnings') or [])}"
        )


if __name__ == "__main__":
    main()
