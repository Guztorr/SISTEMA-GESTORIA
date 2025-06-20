from flask import Flask, request, send_file, render_template, redirect, url_for, session, Response
from pypdf import PdfReader, PdfWriter, PageObject
from reportlab.pdfgen import canvas
from reportlab.graphics.barcode import code128
from reportlab.lib.units import cm
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from PIL import Image
import qrcode
import io
import zipfile
import os
import random
import sys

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "supersecretkey")

USERNAME = os.environ.get("APP_USER", "admin")
PASSWORD = os.environ.get("APP_PASSWORD", "1234")

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('username') == USERNAME and request.form.get('password') == PASSWORD:
            session['authenticated'] = True
            return redirect(url_for('index'))
        return render_template("login.html", error="Credenciales incorrectas")
    return render_template("login.html")

@app.route('/logout', methods=['POST'])
def logout():
    session.pop('authenticated', None)
    return redirect(url_for('login'))

@app.before_request
def require_login():
    if request.endpoint not in ('login', 'static') and not session.get('authenticated'):
        return redirect(url_for('login'))

def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

@app.route('/')
def index():
    return render_template("index.html")

def convert_to_pageobject(page):
    if isinstance(page, dict):
        return PageObject(page.pdf, page.indirect_reference)
    return page

def detectar_estado(texto):
    estados = [
        "AGUASCALIENTES", "BAJA CALIFORNIA", "BAJA CALIFORNIA SUR", "CAMPECHE", "CHIAPAS", "CHIHUAHUA",
        "CIUDAD DE MEXICO", "COAHUILA", "COLIMA", "DURANGO", "MEXICO", "GUANAJUATO",
        "GUERRERO", "HIDALGO", "JALISCO", "MICHOACAN", "MORELOS", "NAYARIT", "NUEVO LEON",
        "OAXACA", "PUEBLA", "QUERETARO", "QUINTANA ROO", "SAN LUIS POTOSI", "SINALOA",
        "SONORA", "TABASCO", "TAMAULIPAS", "TLAXCALA", "VERACRUZ", "YUCATAN", "ZACATECAS"
    ]

    texto_region = texto.extract_text() if hasattr(texto, 'extract_text') else str(texto)
    texto_region = texto_region.upper()

    for estado in estados:
        if f"CODIGO CIVIL DEL ESTADO DE {estado}" in texto_region:
            return estado
        if f"DEL ESTADO DE {estado}" in texto_region:
            return estado

    for estado in estados:
        if estado in texto_region:
            return estado

    return None

def detectar_tipo_documento(texto):
    texto = texto.lower()
    if any(p in texto for p in ['defunción', 'falleció', 'muerto']):
        return 'defuncion'
    return 'nacimiento'

def extraer_curp(texto):
    import re
    import unicodedata

    # Limpieza del texto
    texto = texto.replace("\n", "").replace("\r", "").replace("\t", "").replace(" ", "").upper()
    texto = unicodedata.normalize("NFD", texto)
    texto = texto.encode("ascii", "ignore").decode("utf-8")

    print(f"[DEPURAR] Texto limpio para CURP: {texto[:300]}")

    # Regex relajada: busca secuencias CURP-like sin validar a fondo estructura oficial
    match = re.search(r'[A-Z]{4}\d{6}[A-Z0-9]{8}', texto)
    if match:
        curp_detectada = match.group(0)
        print(f"[DEPURAR] CURP flexible detectada (no validada): {curp_detectada}")
        return curp_detectada

    print("[DEPURAR] CURP no detectada.")
    return None


def generar_qr_con_texto(curp, mediabox):
    from reportlab.lib.utils import ImageReader

    qr_img = qrcode.make(curp)
    buffer = io.BytesIO()
    qr_img.save(buffer, format="PNG")
    buffer.seek(0)
    img = ImageReader(buffer)

    # Tamaño del QR
    qr_size = 3.2 * cm

    # 🔁 Posición en esquina superior izquierda (ajustable según necesidades)
    x = 1.0 * cm
    y = mediabox.height - qr_size - 0.7 * cm  # margen desde arriba

    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=(mediabox.width, mediabox.height))
    c.drawImage(img, x, y, width=qr_size, height=qr_size, mask='auto')

    # Texto centrado debajo del QR
    c.setFont("Helvetica", 9)
    c.drawCentredString(x + qr_size / 2, y - 12, curp)
    c.save()
    packet.seek(0)

    qr_pdf = PdfReader(packet)
    return qr_pdf.pages[0]

def generar_folio_pdf(mediabox):
    folio_num = ''.join(str(random.randint(0, 9)) for _ in range(8))
    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=(mediabox.width, mediabox.height))
    margin_x = 0 * cm
    margin_y = 2.0 * cm
    block_width = 6 * cm
    y_start = mediabox.height - margin_y
    c.setFont("Helvetica-Bold", 14)
    c.setFillColorRGB(1, 0, 0)
    text_folio = "FOLIO"
    text_folio_width = c.stringWidth(text_folio, "Helvetica-Bold", 14)
    folio_x = margin_x + (block_width - text_folio_width) / 2
    c.drawString(folio_x, y_start, text_folio)
    c.setFont("Helvetica", 16)
    c.setFillColorRGB(0, 0, 0)
    folio_num_width = c.stringWidth(folio_num, "Helvetica", 16)
    folio_num_x = margin_x + (block_width - folio_num_width) / 2
    c.drawString(folio_num_x, y_start - 18, folio_num)
    barcode = code128.Code128(folio_num, barHeight=25, barWidth=0.6)
    barcode_x = margin_x + (block_width - barcode.width) / 2
    barcode_y = y_start - 18 - 30
    barcode.drawOn(c, barcode_x, barcode_y)
    c.save()
    packet.seek(0)
    overlay_pdf = PdfReader(packet)
    folio_page = overlay_pdf.pages[0]
    folio_page.mediabox = mediabox
    return folio_page

@app.route('/merge_pdfs', methods=['POST'])
def merge_pdfs():
    original_files = request.files.getlist('original_pdfs')
    agregar_reverso = request.form.get('reverso', 'no').lower() == 'si'
    agregar_folio = request.form.get('folio', 'no').lower() == 'si'
    if len(original_files) > 20:
        return "No puedes subir más de 20 archivos.", 400
    processed_files = []
    mensajes = []
    for original_file in original_files:
        original_pdf_reader = PdfReader(original_file)
        writer = PdfWriter()
        if not original_pdf_reader.pages:
            continue
        first_page = convert_to_pageobject(original_pdf_reader.pages[0])
        texto_pagina = first_page.extract_text() or ""
        tipo_doc = detectar_tipo_documento(texto_pagina)
        estado_detectado = detectar_estado(first_page) if agregar_reverso else None
        curp = extraer_curp(texto_pagina)
        marco_file = 'pdfs/MARCO DEFUNCION ORIGINAL.pdf' if tipo_doc == 'defuncion' else 'pdfs/MARCO NACIMIENTO ORIGINAL.pdf'
        with open(resource_path(marco_file), 'rb') as f:
            base_pdf_bytes = f.read()
        base_overlay = PdfReader(io.BytesIO(base_pdf_bytes)).pages[0]
        base_overlay.mediabox = first_page.mediabox
        base_copy = PageObject.create_blank_page(
            width=first_page.mediabox.width,
            height=first_page.mediabox.height
        )
        base_copy.merge_page(base_overlay)
        base_copy.merge_page(first_page)
        if agregar_folio:
            folio_overlay = generar_folio_pdf(base_copy.mediabox)
            base_copy.merge_page(folio_overlay)
            mensajes.append(f"{original_file.filename}: Folio generado")
        writer.add_page(base_copy)
        for i in range(1, len(original_pdf_reader.pages)):
            writer.add_page(original_pdf_reader.pages[i])
        if estado_detectado:
            reverso_path = resource_path(f'pdfs/reversos/{estado_detectado}.pdf')
            if os.path.exists(reverso_path):
                with open(reverso_path, 'rb') as f:
                    reverso_reader = PdfReader(io.BytesIO(f.read()))
                reverso_page = reverso_reader.pages[0]
                reverso_page.mediabox = base_copy.mediabox
                if curp:
                    qr_overlay = generar_qr_con_texto(curp, base_copy.mediabox)
                    reverso_page.merge_page(qr_overlay)
                    mensajes.append(f"{original_file.filename}: QR con CURP agregado")
                else:
                    mensajes.append(f"{original_file.filename}: CURP no detectada, no se agregó QR")
                writer.add_page(reverso_page)
                mensajes.append(f"{original_file.filename}: Reverso agregado ({estado_detectado})")
            else:
                mensajes.append(f"{original_file.filename}: Estado detectado pero reverso no encontrado")
        elif agregar_reverso:
            mensajes.append(f"{original_file.filename}: No se detectó estado para reverso")
        output_pdf = io.BytesIO()
        writer.write(output_pdf)
        output_pdf.seek(0)
        processed_files.append({
            "filename": f"Act_{original_file.filename}",
            "content": output_pdf
        })
    if not processed_files:
        return "No se procesó ningún archivo válido.", 400
    for m in mensajes:
        print(m)
    if len(processed_files) == 1:
        return send_file(
            processed_files[0]["content"],
            mimetype='application/pdf',
            as_attachment=True,
            download_name=processed_files[0]["filename"]
        )
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w') as zipf:
        for file in processed_files:
            file["content"].seek(0)
            zipf.writestr(file["filename"], file["content"].read())
    zip_buffer.seek(0)
    return send_file(
        zip_buffer,
        mimetype='application/zip',
        as_attachment=True,
        download_name='pdf_combinados.zip'
    )

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
