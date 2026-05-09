"""
Generate the 3 DDI documents as PDFs:
1. Hoja de Ruta  - route summary with client values
2. Hoja de Carga - truck load manifest by warehouse location  
3. Albarán       - delivery note per client
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
from reportlab.pdfbase import pdfmetrics
import json, os

with open('result.json') as f:
    res = json.load(f)

stops     = res['stops']
opt_route = res['opt_route']
parcels   = res['parcels']
parcel_products = res['parcel_products']
depot     = res['depot']
ruta      = res['ruta']
fecha     = res['fecha'] if res['fecha'] else '02/03/2026'
carga_num = '11764300'
vehiculo  = '7524KXX'
repartidor= 'FRAN ROMERO'
rep_num   = '850004'

OUT = '.'
os.makedirs(OUT, exist_ok=True)

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
    path = f'{OUT}/Hoja_Ruta.pdf'
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

    route_data = [['#', 'Cliente', 'Dirección', 'Ciudad', 'Entrega (caj)', 'Ret. (caj)', 'Prioridad']]
    total_del = total_ret = 0
    for rank, idx in enumerate(opt_route):
        s = stops[idx]
        p = res['priority'][idx]
        tag = '●●●' if p > 0.6 else ('●●○' if p > 0.3 else '●○○')
        del_qty = int(s['delivery_caj'] + s['delivery_brl']*5)
        ret_qty = int(s['ret_caj'] + s['ret_brl']*5)
        total_del += del_qty
        total_ret += ret_qty
        route_data.append([
            str(rank+1),
            Paragraph(f'<b>{s["name"]}</b>', body_style),
            Paragraph(s['address'].split(',')[0], small_style),
            Paragraph(s['city'], small_style),
            str(del_qty),
            str(ret_qty),
            tag,
        ])
    route_data.append(['', Paragraph('<b>TOTAL</b>', body_style), '', '',
                       Paragraph(f'<b>{total_del}</b>', body_style),
                       Paragraph(f'<b>{total_ret}</b>', body_style), ''])

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
        f'Algoritmo: Nearest-Neighbour TSP + 2-opt  |  α={res["alpha"]} (valor € estimado/distancia)  |  '
        f'Generado automáticamente por Damm Smart Truck V1',
        ParagraphStyle('foot', fontSize=7, textColor=colors.HexColor('#aaaaaa'), alignment=TA_CENTER)))

    doc.build(story)
    return path

# ════════════════════════════════════════════════════════════════
# DOC 2: HOJA DE CARGA
# ════════════════════════════════════════════════════════════════
def make_hoja_carga():
    path = f'{OUT}/Hoja_Carga.pdf'
    doc = SimpleDocTemplate(path, pagesize=A4,
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm)
    story = []

    story.append(header_table('Hoja de Carga', 'Plan de carga del camión por parcela'))
    story.append(Spacer(1, 3*mm))
    story.append(meta_row())
    story.append(Spacer(1, 4*mm))

    # Truck diagram as table
    story.append(Paragraph('Distribución del camión (6 parcelas)',
        ParagraphStyle('sec', fontSize=9, fontName='Helvetica-Bold',
        textColor=DAMM_RED, spaceAfter=3)))

    truck_row = [Paragraph('<b>CAB</b>', ParagraphStyle('c', fontSize=7, fontName='Helvetica-Bold',
                    textColor=colors.HexColor('#888888'), alignment=TA_CENTER))]
    for p in range(6, 0, -1):
        if p == 1:
            label = 'P1\nRETORNOS'
            bg = colors.HexColor('#e8f5e9')
            tc = colors.HexColor('#1D9E75')
        else:
            clients = [stops[c]['name'].split()[0] for c in parcels.get(str(p),[]) if isinstance(c,int)]
            label = f'P{p}\n' + ' / '.join(clients[:2]) + (f' +{len(clients)-2}' if len(clients)>2 else '')
            bg = colors.HexColor('#fff8e1') if p > 3 else colors.HexColor('#fff3e0')
            tc = colors.HexColor('#F2A623')
        truck_row.append(Paragraph(f'<b>{label}</b>',
            ParagraphStyle('tp', fontSize=7, fontName='Helvetica-Bold',
            textColor=tc, alignment=TA_CENTER)))
    truck_row.append(Paragraph('<b>PUERTA\n🚪</b>', ParagraphStyle('d', fontSize=7,
        fontName='Helvetica-Bold', textColor=DAMM_RED, alignment=TA_CENTER)))

    tt = Table([truck_row], colWidths=[1.2*cm] + [2.4*cm]*6 + [1.4*cm])
    bg_colors = [colors.HexColor('#eeeeee')] + [colors.HexColor('#fff8e1')]*3 + \
                [colors.HexColor('#fff3e0')]*2 + [colors.HexColor('#e8f5e9')] + \
                [colors.HexColor('#ffebee')]
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
    for p_num in range(6, 0, -1):
        p_str = str(p_num)
        products = parcel_products.get(p_str, [])
        client_idxs = parcels.get(p_str, [])

        if p_num == 1:
            section_title = f'PARCELA P1 — ZONA RETORNOS (recogida durante la ruta)'
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
            for item in products[:30]:
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
    total_items = sum(len(parcel_products.get(str(p),[])) for p in range(1,7))
    story.append(Paragraph(
        f'Total productos en carga: {total_items}  |  '
        f'Entrega: {sum(s["delivery_caj"] for s in stops):.0f} cajas  |  '
        f'Retornos esperados: {sum(s["ret_caj"] for s in stops):.0f} cajas (tasa {int(res["r_rate"]*100)}%)',
        ParagraphStyle('foot', fontSize=7, textColor=colors.HexColor('#aaaaaa'), alignment=TA_CENTER)))

    doc.build(story)
    return path

# ════════════════════════════════════════════════════════════════
# DOC 3: ALBARANES (one per client)
# ════════════════════════════════════════════════════════════════
def make_albaranes():
    path = f'{OUT}/Albaranes.pdf'
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

        # Products table
        if s['items']:
            prod_data = [['Producto', 'Descripción', 'UM', 'Cant.', 'Ubic.']]
            for item in s['items']:
                prod_data.append([
                    str(item['mat']),
                    Paragraph(str(item['desc'])[:55], small_style),
                    str(item['unit']),
                    str(int(item['qty'])),
                    str(item.get('ubic','')),
                ])
            pt = Table(prod_data, colWidths=[2.2*cm, 8.5*cm, 1.5*cm, 1.5*cm, 2.3*cm])
            pt.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), DAMM_RED),
                ('TEXTCOLOR', (0,0), (-1,0), colors.white),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('FONTSIZE', (0,0), (-1,-1), 7.5),
                ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, LIGHT]),
                ('GRID', (0,0), (-1,-1), 0.3, MID),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('TOPPADDING', (0,0), (-1,-1), 3),
                ('BOTTOMPADDING', (0,0), (-1,-1), 3),
                ('LEFTPADDING', (0,0), (-1,-1), 4),
                ('ALIGN', (3,0), (3,-1), 'CENTER'),
            ]))
            story.append(Paragraph('Productos a entregar',
                ParagraphStyle('sec2', fontSize=8, fontName='Helvetica-Bold',
                textColor=DAMM_RED, spaceAfter=2)))
            story.append(pt)
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
            Paragraph(f'<b>Total bultos entrega:</b> {int(s["delivery_caj"])} caj<br/>'
                      f'<b>Total bultos retorno:</b> {int(s["ret_caj"])} caj<br/><br/>'
                      f'<font size=7 color="grey">Parcela: P{next((p for p,cl in parcels.items() if isinstance(cl,list) and idx in cl), "—")}</font>',
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

if __name__ == '__main__':
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
