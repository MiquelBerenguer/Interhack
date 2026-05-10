# -*- coding: utf-8 -*-
"""Executive comparison PDF: orders vs plan, savings, annualization, priority mix."""
from __future__ import annotations

import os
from typing import Any, Dict, List

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from pipeline_utils import PRIMARY_SAVINGS_MIN_STOPS

DAMM_RED = colors.HexColor("#C8102E")
LIGHT = colors.HexColor("#f5f5f5")
MID = colors.HexColor("#e0e0e0")


def generate_comparativa_pdf(aggregate: Dict[str, Any], out_path: str) -> str:
    """
    aggregate expects keys: totals, interval, priority_breakdown_eur, weights, runs (list).
    """
    parent = os.path.dirname(os.path.abspath(out_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    doc = SimpleDocTemplate(
        out_path,
        pagesize=A4,
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
    )
    h1 = ParagraphStyle("h1", fontSize=16, fontName="Helvetica-Bold", textColor=DAMM_RED, spaceAfter=6)
    body = ParagraphStyle("body", fontSize=9, fontName="Helvetica", leading=12)
    small = ParagraphStyle("small", fontSize=7, fontName="Helvetica", textColor=colors.HexColor("#555555"))

    story: List[Any] = []
    story.append(Paragraph("Comparativa: comandas CSV vs rutas generadas", h1))
    story.append(
        Paragraph(
            "Resumen agregado (Block 1 + Block 2). La linea base principal usa los mismos tramos Distance Matrix por "
            "cluster que el optimizado, con el orden aleatorio global del Block 1 reordenado dentro de cada cluster "
            "sobre las paradas efectivamente visitadas — misma geometria rodada que el optimizado, distinto orden "
            "de secuenciacion."
            "<br/><br/><i>Los valores tipo Haversine (km rectos Block 1) quedan como referencia en el JSON pero no "
            "se usan para la comparativa de coste EUR.</i>",
            body,
        )
    )
    story.append(Spacer(1, 4))

    tot = aggregate.get("totals") or {}
    inter = aggregate.get("interval") or {}
    pb = (
        aggregate.get("priority_breakdown_eur_primary_ge8_metric")
        or aggregate.get("priority_breakdown_eur")
        or {}
    )
    wts = aggregate.get("weights") or {}

    kpi = [
        ["Metrica", "Valor"],
        ["Lineas comanda (CSV)", str(tot.get("total_comandas_rows", 0))],
        ["Paradas servidas (plan)", str(tot.get("total_stops_served", 0))],
        ["Ejecuciones OK", str(tot.get("runs_ok", 0))],
        ["Ejecuciones sin datos / error", str(tot.get("runs_failed", 0))],
        [
            "Coste baseline EUR (Maps, orden aleatorio B1 sobre paradas servidas)",
            f"{float(tot.get('total_baseline_eur_maps_dm') or tot.get('total_baseline_eur', 0)):.2f}",
        ],
        ["Coste optimizado Block 2 (EUR)", f"{float(tot.get('total_optimized_eur', 0)):.2f}"],
        [
            "Ahorro metrica principal (>={} paradas, EUR)".format(PRIMARY_SAVINGS_MIN_STOPS),
            f"{float(tot.get('primary_savings_metric_ge8_stops_eur') or tot.get('total_savings_eur', 0)):.2f}",
        ],
        [
            "Ahorro rutas cortas (<8 paradas, EUR)",
            f"{float(tot.get('savings_eur_under_8_stops_routes_only', 0)):.2f}",
        ],
        [
            "Suma todos los EUR ahorro (diag.)",
            f"{float(tot.get('total_savings_eur_all_runs') or tot.get('total_savings_eur', 0)):.2f}",
        ],
        ["Primera fecha en lote", str(inter.get("fecha_min", "-"))],
        ["Ultima fecha en lote", str(inter.get("fecha_max", "-"))],
        ["Dias cubiertos (calendario)", str(inter.get("span_days", 0))],
        ["Meses equivalentes (~30.44 d)", f"{float(inter.get('months_span', 0)):.2f}"],
        [
            "Proyeccion anual GE8 metrica (EUR)",
            f"{float(inter.get('annual_savings_projection_eur_primary_ge8_stops') or inter.get('annual_savings_projection_eur', 0)):.2f}  (~30.44 d/mes sobre intervalo GE8)",
        ],
    ]
    t0 = Table(kpi, colWidths=[9 * cm, 8 * cm])
    t0.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), DAMM_RED),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.3, MID),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]
        )
    )
    story.append(t0)
    story.append(Spacer(1, 6))

    story.append(Paragraph("Desglose ilustrativo del ahorro por peso de prioridad", h1))
    story.append(
        Paragraph(
            "Desglose referido al ahorro metrica principal (>= {} paradas). Cada factor recibe una fraccion "
            "segun PRIORITY_WEIGHTS (mezcla del modelo, no efecto causal aislado).".format(PRIMARY_SAVINGS_MIN_STOPS),
            small,
        )
    )
    story.append(Spacer(1, 2))
    rows = [["Factor", "Peso", "EUR atribuidos"]]
    for k in sorted(pb.keys()):
        wt = float(wts.get(k, 0))
        rows.append([k, f"{wt:.2f}", f"{float(pb.get(k, 0)):.2f}"])
    t1 = Table(rows, colWidths=[6 * cm, 3 * cm, 4 * cm])
    t1.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#333333")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.25, MID),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
            ]
        )
    )
    story.append(t1)
    story.append(Spacer(1, 6))

    story.append(Paragraph("Detalle por ruta y dia (max 35 filas)", h1))
    runs: List[Dict[str, Any]] = list(aggregate.get("runs") or [])
    head = [["Ruta", "Fecha", "Seg.", "Comandas", "Paradas", "BaseEUR", "Opt.EUR", "Ahorro", "Estado"]]
    max_rows = 35
    for r in runs[:max_rows]:
        head.append(
            [
                str(r.get("ruta", ""))[:12],
                str(r.get("fecha", ""))[:12],
                str(r.get("route_value_segment", ""))[:8],
                str(r.get("comandas_rows", "")),
                str(r.get("stops_count", "")),
                f"{float(r.get('baseline_total_eur', 0)):.1f}",
                f"{float(r.get('optimized_total_eur', 0)):.1f}",
                f"{float(r.get('savings_eur', 0)):.1f}",
                str(r.get("status", ""))[:14],
            ]
        )
    if len(runs) > max_rows:
        head.append([f"... +{len(runs) - max_rows} mas en JSON", "", "", "", "", "", "", "", ""])

    t2 = Table(
        head,
        colWidths=[1.6 * cm, 1.8 * cm, 1 * cm, 1 * cm, 0.95 * cm, 1.6 * cm, 1.55 * cm, 1.4 * cm, 1.95 * cm],
    )
    t2.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), DAMM_RED),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("GRID", (0, 0), (-1, -1), 0.2, MID),
            ]
        )
    )
    story.append(t2)
    story.append(Spacer(1, 4))
    smp = aggregate.get("sample") or {}
    story.append(
        Paragraph(
            f"Muestra PDFs DDI: ruta {smp.get('ruta', '')} fecha {smp.get('fecha', '')} - {smp.get('reason', '')}",
            small,
        )
    )

    doc.build(story)
    return out_path
