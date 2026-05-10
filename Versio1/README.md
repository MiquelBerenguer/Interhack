# INTERHACK — Damm smart routing (Versio1)

Prototype stack for the hackathon challenge: cluster deliveries by transporter zone, optimise stop order and truck capacity in **pallet units (UP)** from **ZM040**, optionally refine legs with **Google Distance Matrix**, build a **physical loading plan**, and export **PDFs** plus a **savings comparativa**.

Python **`#` comments** (and most module docstrings) are in **English** for maintainers. **`run_full_pipeline.py`** uses **Spanish** for CLI help and `print` output (DAMM / stakeholder demos). Some **PDF labels** may use Spanish business wording.

---

## Quick start

1. **Python 3.10+** recommended.
2. Copy **`.env.example`** to **`.env`** and set at least **`GOOGLE_MAPS_API_KEY`** if you want real road distances in Block 2 (otherwise Haversine is used).
3. Put **`Hackaton.xlsx`** next to the scripts (or pass `--hackaton`). Default paths also look for **`../Hackaton/ZM040.XLSX`** and **`../Hackaton/Horarios Entrega.XLSX`** when present.
4. From this directory:

```bash
cd Versio1
python run_full_pipeline.py
```

Single route/day uses **`E2E_RUTA`** and **`E2E_FECHA`** (defaults in code / env). Process every day in the sheet:

```bash
python run_full_pipeline.py --all-dates
python run_full_pipeline.py --all-dates --ruta-filter DR0027
```

Outputs default to **`pipeline_out/`** (override with **`PIPELINE_OUT`**).

---

## Pipeline stages

| Stage | Role |
|--------|------|
| **Block 1** | `optimise()` in **`damm_engine.py`**: reads Excel, builds stops, sectors, NN + 2-opt route, dynamic truck feasibility, JSON-shaped result. |
| **Block 2** | **`block2_maps_routing.py`**: per-cluster routes using Distance Matrix (or fallback), optional LLM hints on deadlocks. |
| **Block 3** | **`block3_loading.py`**: loading plan + warehouse pick sequence from Block 2 JSON + dimensions from ZM040/Hackaton. |
| **Docs** | **`generate_docs.py`**: route / load / delivery-note PDFs from Block 1 JSON. **`generate_comparativa_pdf.py`**: executive savings PDF from pipeline aggregate. |

Convenience entrypoints:

- **`run_e2e_block2.py`** — Block 1 → Block 2 only.
- **`run_full_pipeline.py`** — full run, aggregate JSON, optional Block 3 on the **largest sample day**, sample PDFs, comparativa.
- **`run_block3.py`** — Block 3 alone from a **`block2_result.json`** (or env paths).

---

## Environment variables

See **`.env.example`**. Common variables:

| Variable | Purpose |
|----------|---------|
| `GOOGLE_MAPS_API_KEY` | Distance Matrix for Block 2. |
| `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` | Optional LLM for Block 2 deadlock handling. |
| `E2E_RUTA`, `E2E_FECHA` | Default route and date for single-day runs. |
| `PIPELINE_OUT` | Output directory for the full pipeline. |
| `ZM040_XLSX` | Override ZM040 path (Block 3 / pipeline sample). |
| `BLOCK2_JSON`, `HACKATON_XLSX`, `MATERIALES_XLSX`, `BLOCK3_OUT` | **`run_block3.py`** paths. |

---

## Python module map (what is “useful”?)

**Core (needed for optimisation and routing)**

- **`damm_engine.py`** — Block 1 engine, data load, `optimise()`, `run_block2()` wrapper.
- **`priority_cluster.py`** — Zona transporter clustering, truck escalation, unassigned detection.
- **`dynamic_truck.py`** — UP-based capacity and feasibility along the route.
- **`horarios_windows.py`** — Delivery time windows from Horarios sheet.
- **`zm040_up.py`** — UP per sales unit from ZM040 PAL rows.
- **`cabecera_transporte.py`** — Cabecera sheet: reassignment hints, current transport numbers.
- **`block2_maps_routing.py`** — Block 2 routing and API integration.

**Pipeline / aggregation**

- **`run_full_pipeline.py`**, **`run_e2e_block2.py`**, **`pipeline_utils.py`** — CLI and helpers (pairs from Hackaton, baseline vs optimised EUR, etc.).

**Block 3**

- **`block3_loading.py`**, **`run_block3.py`**.

**PDF generation (optional if you only need JSON)**

- **`generate_docs.py`**, **`generate_comparativa_pdf.py`** — require **ReportLab** (and existing JSON inputs).

**Optional / standalone**

- **`index.html`** — Standalone front-end demo; **not** imported by the Python pipeline. Keep or drop depending on whether you still use that UI.

**Data**

- **`Hackaton.xlsx`** — Primary input (expected in this folder for default runs).
- **`../Hackaton/ZM040.XLSX`**, **`../Hackaton/Horarios Entrega.XLSX`** — Used when found (paths also searched under `Versio1`).

**Config / secrets**

- **`.env`** — Local secrets (gitignored). **`.env.example`** — template only.

---

## Dependencies

Install as needed (no root `requirements.txt` yet), for example:

```bash
pip install pandas openpyxl python-dotenv reportlab
```

Block 2 may use **`urllib`** (stdlib) and optional HTTP clients for LLM providers — see imports in **`block2_maps_routing.py`**.

---

## Generated artifacts

The **`.gitignore`** in this folder ignores typical outputs: **`pipeline_out/`**, **`__pycache__/`**, `result.json`, `block2_result.json`, etc. Regenerate with the scripts above; do not commit large PDFs or aggregates unless you intend to version them.
