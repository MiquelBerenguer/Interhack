"""
Generate the 3 DDI documents as PDFs:
1. Hoja de Ruta  - route summary with client values
2. Hoja de Carga - truck load manifest by warehouse location  
3. Albarán       - delivery note per client
"""
from __future__ import annotations

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
from reportlab.pdfbase import pdfmetrics

import json
import os
from typing import Tuple

from damm_engine import estimate_unit_price_eur

# Paths configurable for pipeline (env or init_result)
RESULT_JSON = os.environ.get("RESULT_JSON", "result.json")
OUT = os.environ.get("PDF_OUT_DIR", ".")


def init_result(result_path: str | None = None, out_dir: str | None = None) -> dict:
    """Load Block 1 `result.json`-shaped dict and set module globals for PDF builders."""
    global res, RESULT_JSON, OUT, N_PARCELS, stops, opt_route, parcels, parcel_products
    global depot, ruta, fecha, carga_num, vehiculo, repartidor, rep_num
    path = result_path or os.environ.get("RESULT_JSON", "result.json")
    out = out_dir if out_dir is not None else os.environ.get("PDF_OUT_DIR", ".")
    RESULT_JSON = path
    OUT = out
    os.makedirs(OUT, exist_ok=True)
    with open(path, encoding="utf-8") as f:
        res = json.load(f)
    N_PARCELS = int(res.get("n_parcels", 6))
    stops = res["stops"]
    opt_route = res["opt_route"]
    parcels = res["parcels"]
    parcel_products = res["parcel_products"]
    depot = res["depot"]
    ruta = res["ruta"]
    fecha = res["fecha"] if res.get("fecha") else "02/03/2026"
    carga_num = os.environ.get("DDI_CARGA_NUM", "11764300")
    vehiculo = os.environ.get("DDI_VEHICULO", "7524KXX")
    repartidor = os.environ.get("DDI_REPARTIDOR", "FRAN ROMERO")
    rep_num = os.environ.get("DDI_REP_NUM", "850004")
    return res


res = {}
N_PARCELS = 6
stops = []
opt_route = []
parcels = {}
parcel_products = {}
depot = {}
ruta = ""
fecha = ""
carga_num = "11764300"
vehiculo = "7524KXX"
repartidor = "FRAN ROMERO"
rep_num = "850004"

W, H = A4
DAMM_RED  = colors.HexColor('#C8102E')
DAMM_GOLD = colors.HexColor('#F2A623')
DARK      = colors.HexColor('#1a1a1a')
LIGHT     = colors.HexColor('#f5f5f5')
MID       = colors.HexColor('#e0e0e0')

styles = getSampleStyleSheet()
title_style = ParagraphStyle('title', fontSize=16, fontName='Helvetica-Bold',
    textColor=DAMM_RED, spaceAfter=4)
sub_style = ParagraphStyle('sub', fontSize=8, fontName='Helvetica',
    textColor=colors.HexColor('#666666'), spaceAfter=2)
header_style = ParagraphStyle('hdr', fontSize=10, fontName='Helvetica-Bold',
    textColor=colors.white)
body_style = ParagraphStyle('body', fontSize=8, fontName='Helvetica', leading=11)
small_style = ParagraphStyle('small', fontSize=7, fontName='Helvetica',
    textColor=colors.HexColor('#555555'))

def header_table(doc_title, subtitle=''):
    data = [[
        Paragraph(f'<b>Distri. de Begudes Movi SL</b><br/>DDI - {depot["name"]}<br/>DDIDGP', 
                  ParagraphStyle('co', fontSize=8, fontName='Helvetica', leading=10)),
        Paragraph(f'<b style="font-size:18">{doc_title}</b><br/><font size=8 color="grey">{subtitle}</font>', 
                  ParagraphStyle('title2', fontSize=18, fontName='Helvetica-Bold',
                  textColor=DAMM_RED, alignment=TA_CENTER)),
        Paragraph(f'<b>Nº Carga:</b> {carga_num}<br/><b>Fecha:</b> {fecha}<br/>'
                  f'<b>Vehículo:</b> {vehiculo}<br/><b>Ruta:</b> {ruta}',
                  ParagraphStyle('meta', fontSize=8, fontName='Helvetica', 
                  leading=11, alignment=TA_RIGHT)),
    ]]
    t = Table(data, colWidths=[5*cm, 9*cm, 5*cm])
    t.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('LINEBELOW', (0,0), (-1,0), 1.5, DAMM_RED),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
    ]))
    return t

def meta_row():
    data = [[
        f'Repartidor: {repartidor}  ({rep_num})',
        f'Nº Viaje: 01',
        f'Fecha envío: {fecha}',
        f'Ruta: {ruta}',
    ]]
    t = Table(data, colWidths=[5*cm, 3*cm, 4*cm, 7*cm])
    t.setStyle(TableStyle([
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#f0f0f0')),
        ('TEXTCOLOR', (0,0), (-1,-1), colors.HexColor('#444444')),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
    ]))
    return t

# ════════════════════════════════════════════════════════════════
# DOC 1: HOJA DE RUTA
# ════════════════════════════════════════════════════════════════
def make_hoja_ruta():
    path = os.path.join(OUT, "Hoja_Ruta.pdf")
    doc = SimpleDocTemplate(path, pagesize=A4,
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm)
    story = []

    story.append(header_table('Hoja de Ruta', f'Ruta optimizada · {len(opt_route)} paradas'))
    story.append(Spacer(1, 3*mm))
    story.append(meta_row())
    story.append(Spacer(1, 4*mm))

    # Summary metrics
    metrics_data = [[
        Paragraph(f'<b>Distancia optimizada</b><br/><font size=14 color="#C8102E"><b>{res["opt_dist"]} km</b></font><br/>'
                  f'<font size=7 color="grey">vs {res["rand_dist"]} km sin optimizar</font>', body_style),
        Paragraph(f'<b>Tiempo estimado</b><br/><font size=14 color="#C8102E"><b>{res["opt_time"]} min</b></font><br/>'
                  f'<font size=7 color="grey">Ahorro: {res["time_saved"]} min</font>', body_style),
        Paragraph(f'<b>Mejora distancia</b><br/><font size=14 color="#1D9E75"><b>{res["improvement_pct"]}%</b></font><br/>'
                  f'<font size=7 color="grey">vs ruta no optimizada</font>', body_style),
        Paragraph(f'<b>Paradas</b><br/><font size=14 color="#1a1a1a"><b>{len(opt_route)}</b></font><br/>'
                  f'<font size=7 color="grey">clientes en ruta</font>', body_style),
    ]]
    mt = Table(metrics_data, colWidths=[4.7*cm]*4)
    mt.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 0.5, MID),
        ('INNERGRID', (0,0), (-1,-1), 0.5, MID),
        ('BACKGROUND', (0,0), (-1,-1), LIGHT),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(mt)
    story.append(Spacer(1, 5*mm))

    # Route table
    story.append(Paragraph('Orden de visitas optimizado', 
        ParagraphStyle('sec', fontSize=9, fontName='Helvetica-Bold', 
        textColor=DAMM_RED, spaceAfter=3)))

    route_data = [['#', 'Cliente', 'Dirección', 'Ciudad', 'Entrega (UP)', 'Retorno vacíos (UP)', 'Prioridad']]
    total_del = total_ret = 0
    for rank, idx in enumerate(opt_route):
        s = stops[idx]
        p = res['priority'][idx]
        tag = '●●●' if p > 0.6 else ('●●○' if p > 0.3 else '●○○')
        del_qty = float(s.get('delivery_up', s['delivery_caj'] + s['delivery_brl'] * 5))
        ret_qty = float(s.get('return_up', res.get('r_rate', 0.6) * del_qty))
        total_del += del_qty
        total_ret += ret_qty
        route_data.append([
            str(rank+1),
            Paragraph(f'<b>{s["name"]}</b>', body_style),
            Paragraph(s['address'].split(',')[0], small_style),
            Paragraph(s['city'], small_style),
            f'{del_qty:.3f}',
            f'{ret_qty:.3f}',
            tag,
        ])
    route_data.append(['', Paragraph('<b>TOTAL</b>', body_style), '', '',
                       Paragraph(f'<b>{total_del:.3f}</b>', body_style),
                       Paragraph(f'<b>{total_ret:.3f}</b>', body_style), ''])

    rt = Table(route_data, colWidths=[0.8*cm, 4.5*cm, 4.5*cm, 3*cm, 2*cm, 2*cm, 1.7*cm])
    n = len(route_data)
    rt.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), DAMM_RED),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 8),
        ('FONTSIZE', (0,1), (-1,-1), 7.5),
        ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
        ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor('#fff3e0')),
        ('ROWBACKGROUNDS', (0,1), (-1,-2), [colors.white, LIGHT]),
        ('GRID', (0,0), (-1,-1), 0.3, MID),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('ALIGN', (4,0), (6,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(rt)
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph(
        f'Algoritmo: Nearest-Neighbour TSP + 2-opt  |  α={res["alpha"]} (sectores_transporte/distancia)  '
        f'· cap. UP/sector {float(res.get("transport_capacity_vol", 0)):.2f} (ZM040)  '
        f'· camión {res.get("truck_type", "?")} {res.get("n_parcels", "?")} parcelas  |  '
        f'Generado automáticamente por Damm Smart Truck V1',
        ParagraphStyle('foot', fontSize=7, textColor=colors.HexColor('#aaaaaa'), alignment=TA_CENTER)))

    doc.build(story)
    return path

# ════════════════════════════════════════════════════════════════
# DOC 2: HOJA DE CARGA
# ════════════════════════════════════════════════════════════════
def make_hoja_carga():
    path = os.path.join(OUT, "Hoja_Carga.pdf")
    doc = SimpleDocTemplate(path, pagesize=A4,
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm)
    story = []

    story.append(header_table('Hoja de Carga', 'Plan de carga del camión por parcela'))
    story.append(Spacer(1, 3*mm))
    story.append(meta_row())
    story.append(Spacer(1, 4*mm))

    # Truck diagram as table
    story.append(Paragraph(f'Distribución del camión ({N_PARCELS} parcelas)',
        ParagraphStyle('sec', fontSize=9, fontName='Helvetica-Bold',
        textColor=DAMM_RED, spaceAfter=3)))

    truck_row = [Paragraph('<b>CAB</b>', ParagraphStyle('c', fontSize=7, fontName='Helvetica-Bold',
                    textColor=colors.HexColor('#888888'), alignment=TA_CENTER))]
    for p in range(N_PARCELS, 0, -1):
        if p == 1:
            label = 'P1\nC1+RET'
            tc = colors.HexColor('#1D9E75')
        else:
            clients = [stops[c]['name'].split()[0] for c in parcels.get(str(p),[]) if isinstance(c,int)]
            label = f'P{p}\n' + ' / '.join(clients[:2]) + (f' +{len(clients)-2}' if len(clients)>2 else '')
            tc = colors.HexColor('#F2A623')
        truck_row.append(Paragraph(f'<b>{label}</b>',
            ParagraphStyle('tp', fontSize=7, fontName='Helvetica-Bold',
            textColor=tc, alignment=TA_CENTER)))
    truck_row.append(Paragraph('<b>PUERTA\n🚪</b>', ParagraphStyle('d', fontSize=7,
        fontName='Helvetica-Bold', textColor=DAMM_RED, alignment=TA_CENTER)))

    parcel_w = min(2.4 * cm, (19 * cm) / max(N_PARCELS, 1))
    tt = Table([truck_row], colWidths=[1.2*cm] + [parcel_w]*N_PARCELS + [1.4*cm])
    bg_colors = [colors.HexColor('#eeeeee')]
    for _ in range(N_PARCELS - 1):
        bg_colors.append(colors.HexColor('#fff8e1'))
    bg_colors.append(colors.HexColor('#e8f5e9'))
    bg_colors.append(colors.HexColor('#ffebee'))
    style_cmds = [
        ('GRID', (0,0), (-1,-1), 0.5, MID),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 10),
        ('BOTTOMPADDING', (0,0), (-1,-1), 10),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
    ]
    for i, bg in enumerate(bg_colors):
        style_cmds.append(('BACKGROUND', (i,0), (i,0), bg))
    tt.setStyle(TableStyle(style_cmds))
    story.append(tt)

    story.append(Spacer(1, 2*mm))
    story.append(Paragraph('← Cargado último (primeros clientes)      Cargado primero (últimos clientes) →',
        ParagraphStyle('arr', fontSize=7, textColor=colors.HexColor('#999999'), alignment=TA_CENTER)))
    story.append(Spacer(1, 5*mm))

    # Products per parcel
    for p_num in range(N_PARCELS, 0, -1):
        p_str = str(p_num)
        products = parcel_products.get(p_str, [])
        client_idxs = parcels.get(p_str, [])

        if p_num == 1:
            section_title = f'PARCELA P1 — Cliente 1 (slot exclusivo) + retornables ruta'
            bg_h = colors.HexColor('#e8f5e9')
            tc_h = colors.HexColor('#1D9E75')
        else:
            clients_in_p = [stops[c]['name'] for c in client_idxs if isinstance(c,int)]
            section_title = f'PARCELA P{p_num} — {" / ".join(clients_in_p[:3])}'
            bg_h = DAMM_RED
            tc_h = colors.white

        story.append(Paragraph(section_title,
            ParagraphStyle('ph', fontSize=8.5, fontName='Helvetica-Bold',
            textColor=tc_h, backColor=bg_h, spaceAfter=1,
            leftIndent=4, rightIndent=4, spaceBefore=6,
            borderPad=4)))

        if products:
            prod_data = [['Ubic.', 'Código', 'Descripción', 'Cant.', 'Unidad', 'Cliente']]
            for item in products:
                prod_data.append([
                    str(item.get('ubic','')),
                    str(item.get('mat','')),
                    Paragraph(str(item.get('desc',''))[:50], small_style),
                    str(int(item.get('qty',0))),
                    str(item.get('unit','')),
                    Paragraph(str(item.get('client',''))[:25], small_style),
                ])
            pt = Table(prod_data, colWidths=[1.5*cm, 2*cm, 6*cm, 1*cm, 1.2*cm, 3.3*cm])
            pt.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#f5f5f5')),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('FONTSIZE', (0,0), (-1,-1), 7),
                ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#fafafa')]),
                ('GRID', (0,0), (-1,-1), 0.2, MID),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('TOPPADDING', (0,0), (-1,-1), 2),
                ('BOTTOMPADDING', (0,0), (-1,-1), 2),
                ('LEFTPADDING', (0,0), (-1,-1), 3),
            ]))
            story.append(pt)
        else:
            story.append(Paragraph('(sin productos asignados)', small_style))

    story.append(Spacer(1, 4*mm))
    total_items = sum(len(parcel_products.get(str(p),[])) for p in range(1, N_PARCELS + 1))
    total_up = sum(float(s.get('delivery_up', 0)) for s in stops)
    story.append(Paragraph(
        f'Total líneas en carga: {total_items}  |  '
        f'Entrega total: {total_up:.3f} UP (ZM040)  |  '
        f'Modelo vacíos: {int(res.get("r_rate", 0.6)*100)}% de la entrega en UP',
        ParagraphStyle('foot', fontSize=7, textColor=colors.HexColor('#aaaaaa'), alignment=TA_CENTER)))

    doc.build(story)
    return path

def _albaran_line_prices(item: dict) -> Tuple[float, float]:
    """Unit and line total EUR for PDF; uses JSON fields or same heuristic as Block 1."""
    pu = item.get("unit_price_eur")
    lt = item.get("line_total_eur")
    if pu is not None and lt is not None:
        return float(pu), float(lt)
    u = float(estimate_unit_price_eur(str(item["mat"]), str(item["unit"]), str(item["desc"])))
    q = float(item.get("qty") or 0)
    return u, round(q * u, 2)


def _fmt_eur_es(x: float) -> str:
    return f"{x:.2f}".replace(".", ",") + " €"


# ════════════════════════════════════════════════════════════════
# DOC 3: ALBARANES (one per client)
# ════════════════════════════════════════════════════════════════
def make_albaranes():
    path = os.path.join(OUT, "Albaranes.pdf")
    doc = SimpleDocTemplate(path, pagesize=A4,
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm)
    story = []

    for rank, idx in enumerate(opt_route):
        s = stops[idx]
        alb_num = f'ALB-{ruta}-{rank+1:03d}'

        # Header
        header_data = [[
            Paragraph('<b>Distri. de Begudes Movi SL</b><br/>DDI - Mollet del Vallès<br/>ddimollet@ddidistribucion.com',
                      ParagraphStyle('co2', fontSize=8, fontName='Helvetica', leading=11)),
            Paragraph('<b>ALBARÁN-FACTURA</b>',
                      ParagraphStyle('aTitle', fontSize=16, fontName='Helvetica-Bold',
                      textColor=DAMM_RED, alignment=TA_CENTER)),
            Paragraph(f'<b>Nº Albarán:</b> {alb_num}<br/>'
                      f'<b>Fecha:</b> {fecha}<br/>'
                      f'<b>Carga:</b> {carga_num}<br/>'
                      f'<b>Ruta:</b> {ruta} · Parada {rank+1}',
                      ParagraphStyle('meta2', fontSize=8, fontName='Helvetica',
                      leading=11, alignment=TA_RIGHT)),
        ]]
        ht = Table(header_data, colWidths=[5*cm, 9*cm, 5*cm])
        ht.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('LINEBELOW', (0,0), (-1,0), 1.5, DAMM_RED),
            ('TOPPADDING', (0,0), (-1,-1), 5),
            ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ]))
        story.append(ht)
        story.append(Spacer(1, 3*mm))

        # Client info
        client_data = [[
            Paragraph(f'<b>Cliente:</b><br/><b>{s["name"]}</b><br/>'
                      f'{s["address"]}<br/>'
                      f'{s["city"]}',
                      ParagraphStyle('cl', fontSize=9, fontName='Helvetica', leading=13)),
            Paragraph(f'<b>Dirección entrega:</b><br/>{s["address"]}<br/>{s["city"]}',
                      ParagraphStyle('del', fontSize=9, fontName='Helvetica', leading=13)),
            Paragraph(f'<b>Repartidor:</b> {repartidor}<br/>'
                      f'<b>Vehículo:</b> {vehiculo}<br/>'
                      f'<b>Viaje:</b> 01',
                      ParagraphStyle('drv', fontSize=8, fontName='Helvetica', leading=12)),
        ]]
        ct = Table(client_data, colWidths=[6*cm, 6*cm, 5*cm])
        ct.setStyle(TableStyle([
            ('BOX', (0,0), (-1,-1), 0.5, MID),
            ('INNERGRID', (0,0), (-1,-1), 0.5, MID),
            ('BACKGROUND', (0,0), (-1,-1), LIGHT),
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('TOPPADDING', (0,0), (-1,-1), 6),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ('LEFTPADDING', (0,0), (-1,-1), 6),
        ]))
        story.append(ct)
        story.append(Spacer(1, 3*mm))

        # Products table (unit + line EUR; heuristic same as engine priority value)
        if s['items']:
            prod_data = [
                ['Producto', 'Descripción', 'UM', 'Cant.', 'P. unit. (€)', 'Importe (€)', 'Ubic.'],
            ]
            sum_lines = 0.0
            for item in s['items']:
                pu, lt = _albaran_line_prices(item)
                sum_lines += lt
                prod_data.append([
                    str(item['mat']),
                    Paragraph(str(item['desc'])[:48], small_style),
                    str(item['unit']),
                    str(int(item['qty'])),
                    _fmt_eur_es(pu),
                    _fmt_eur_es(lt),
                    str(item.get('ubic', '')),
                ])
            prod_data.append([
                '', '', '', '', Paragraph('<b>Total</b>', small_style),
                Paragraph(f'<b>{_fmt_eur_es(sum_lines)}</b>', small_style),
                '',
            ])
            pt = Table(
                prod_data,
                colWidths=[1.9 * cm, 6.0 * cm, 1.2 * cm, 1.1 * cm, 1.55 * cm, 1.55 * cm, 1.9 * cm],
            )
            last_row = len(prod_data) - 1
            pt.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), DAMM_RED),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 7.5),
                ('ROWBACKGROUNDS', (0, 1), (0, last_row - 1), [colors.white, LIGHT]),
                ('BACKGROUND', (0, last_row), (-1, last_row), colors.HexColor('#fce4ec')),
                ('GRID', (0, 0), (-1, -1), 0.3, MID),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('TOPPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                ('LEFTPADDING', (0, 0), (-1, -1), 4),
                ('ALIGN', (3, 0), (3, last_row - 1), 'CENTER'),
                ('ALIGN', (4, 1), (5, last_row - 1), 'RIGHT'),
                ('ALIGN', (4, last_row), (5, last_row), 'RIGHT'),
            ]))
            story.append(Paragraph('Productos a entregar',
                ParagraphStyle('sec2', fontSize=8, fontName='Helvetica-Bold',
                textColor=DAMM_RED, spaceAfter=2)))
            story.append(pt)
            story.append(Paragraph(
                '<i>Precios orientativos mayoristas sin IVA (misma heurística que valor de prioridad en el motor).</i>',
                ParagraphStyle('albnote', fontSize=6, fontName='Helvetica', textColor=colors.HexColor('#666666'),
                               spaceBefore=1, spaceAfter=0),
            ))
            story.append(Spacer(1, 2*mm))

        # Returnables
        if s['ret_items']:
            ret_data = [['Código', 'Descripción', 'UM', 'Cant.']]
            for item in s['ret_items']:
                ret_data.append([str(item['mat']),
                    Paragraph(str(item['desc'])[:55], small_style),
                    str(item['unit']), str(int(item['qty']))])
            rtt = Table(ret_data, colWidths=[2.2*cm, 10*cm, 1.5*cm, 2.3*cm])
            rtt.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1D9E75')),
                ('TEXTCOLOR', (0,0), (-1,0), colors.white),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('FONTSIZE', (0,0), (-1,-1), 7.5),
                ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#e8f5e9')]),
                ('GRID', (0,0), (-1,-1), 0.3, MID),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('TOPPADDING', (0,0), (-1,-1), 3),
                ('BOTTOMPADDING', (0,0), (-1,-1), 3),
                ('LEFTPADDING', (0,0), (-1,-1), 4),
            ]))
            story.append(Paragraph('Retornos a recoger',
                ParagraphStyle('sec3', fontSize=8, fontName='Helvetica-Bold',
                textColor=colors.HexColor('#1D9E75'), spaceAfter=2)))
            story.append(rtt)
            story.append(Spacer(1, 2*mm))

        # Signature line
        sig_data = [[
            Paragraph('Firma y sello del cliente:<br/><br/><br/>____________________________',
                      ParagraphStyle('sig', fontSize=8, fontName='Helvetica')),
            Paragraph(f'Nombre y DNI:<br/><br/><br/>____________________________',
                      ParagraphStyle('sig2', fontSize=8, fontName='Helvetica')),
            Paragraph(
                      f'<b>Entrega (UP):</b> {float(s.get("delivery_up", 0)):.3f}<br/>'
                      f'<b>Retorno vacíos (UP, ~60%):</b> {float(s.get("return_up", 0)):.3f}<br/><br/>'
                      f'<font size=7 color="grey">Parcela: P{next((p for p, cl in parcels.items() if isinstance(cl, list) and idx in cl), "—")}</font>',
                      ParagraphStyle('totals', fontSize=9, fontName='Helvetica',
                      leading=14, alignment=TA_RIGHT)),
        ]]
        sigt = Table(sig_data, colWidths=[6*cm, 6*cm, 5*cm])
        sigt.setStyle(TableStyle([
            ('BOX', (0,0), (-1,-1), 0.5, MID),
            ('INNERGRID', (0,0), (-1,-1), 0.5, MID),
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('TOPPADDING', (0,0), (-1,-1), 6),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ('LEFTPADDING', (0,0), (-1,-1), 6),
        ]))
        story.append(sigt)

        # Page break between clients (except last)
        if rank < len(opt_route) - 1:
            from reportlab.platypus import PageBreak
            story.append(PageBreak())

    doc.build(story)
    return path


def generate_three_pdfs(result_path: str | None = None, out_dir: str | None = None) -> dict:
    """Load Block 1 JSON and write Hoja_Ruta, Hoja_Carga, Albaranes PDFs into ``out_dir``."""
    init_result(result_path, out_dir)
    return {
        "hoja_ruta": make_hoja_ruta(),
        "hoja_carga": make_hoja_carga(),
        "albaranes": make_albaranes(),
    }


if __name__ == '__main__':
    init_result()
    print("Generating Hoja de Ruta...")
    p1 = make_hoja_ruta()
    print(f"  → {p1}")
    print("Generating Hoja de Carga...")
    p2 = make_hoja_carga()
    print(f"  → {p2}")
    print("Generating Albaranes...")
    p3 = make_albaranes()
    print(f"  → {p3}")
    print("All documents done.")
