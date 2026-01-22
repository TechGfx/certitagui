import os
import random
from datetime import datetime
from ftplib import FTP
from io import BytesIO
from pathlib import Path

import qrcode
from flask import Flask, render_template, request, send_file
from jinja2 import Environment, FileSystemLoader
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, NumberObject
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

app = Flask(__name__)

env = Environment(loader=FileSystemLoader("templates"), autoescape=False)

Path("build").mkdir(exist_ok=True)


os.makedirs("generados", exist_ok=True)


def dividir_tipo_transporte(texto, palabras_linea1=2):
    """Divide el tipo de transporte en dos líneas - Página 1"""
    if not texto:
        return {"tipodetransporte_1": "", "tipodetransporte_2": ""}

    palabras = texto.split()

    if len(palabras) <= palabras_linea1:
        return {"tipodetransporte_1": texto, "tipodetransporte_2": ""}

    return {
        "tipodetransporte_1": " ".join(palabras[:palabras_linea1]),
        "tipodetransporte_2": " ".join(palabras[palabras_linea1:]),
    }


def cm_to_points(cm):
    """Convierte centímetros a puntos (1 cm = 28.3465 puntos)"""
    return cm * 28.3465


def generar_numero_acta(fecha_inspeccion, placa):
    """
    Genera número de acta en formato YYYYMMDDPLACA
    Ejemplo: 20260120PRY576
    """
    try:
        fecha_obj = datetime.strptime(fecha_inspeccion, "%Y-%m-%d")
        fecha_formateada = fecha_obj.strftime("%Y%m%d")
        return f"{fecha_formateada}{placa}"
    except:
        return f"{placa}"


def generar_numero_inspeccion(placa):
    """
    Genera número de inspección con 5 dígitos aleatorios + PLACA
    Ejemplo: 16560PRY576
    """
    aleatorio = random.randint(10000, 99999)
    return f"{aleatorio}{placa}"


def convertir_fecha_formato_acta(fecha_inspeccion):
    """
    Convierte fecha de YYYY-MM-DD a DD/MM/YYYY
    Ejemplo: 2026-01-20 → 20/01/2026
    """
    try:
        fecha_obj = datetime.strptime(fecha_inspeccion, "%Y-%m-%d")
        return fecha_obj.strftime("%d/%m/%Y")
    except:
        return fecha_inspeccion


def convertir_fecha_formato_firma(fecha_inspeccion):
    """
    Convierte fecha para el formato de firma en página 4
    Retorna: {'dia': '20', 'mes': 'ENERO', 'anio': '2026'}
    """
    try:
        fecha_obj = datetime.strptime(fecha_inspeccion, "%Y-%m-%d")

        meses = {
            1: "ENERO",
            2: "FEBRERO",
            3: "MARZO",
            4: "ABRIL",
            5: "MAYO",
            6: "JUNIO",
            7: "JULIO",
            8: "AGOSTO",
            9: "SEPTIEMBRE",
            10: "OCTUBRE",
            11: "NOVIEMBRE",
            12: "DICIEMBRE",
        }

        return {
            "dia": str(fecha_obj.day),
            "mes": meses[fecha_obj.month],
            "anio": str(fecha_obj.year),
        }
    except:
        return {"dia": "", "mes": "", "anio": ""}


def generar_link_certificado(placa, tipo_certificado):
    """
    Genera el link de verificación del certificado

    Args:
        placa: Placa del vehículo (ej: PRY576)
        tipo_certificado: nuevo, remo, remo2, remo3, remo4, remo5

    Returns:
        URL completa del certificado
    """
    base_url = "https://itaguigov-com.us.stackstaging.com/index"

    if tipo_certificado == "nuevo":
        return f"{base_url}{placa}.html"
    else:
        return f"{base_url}{placa}{tipo_certificado}.html"


def generar_qr_code(link):
    """
    Genera un código QR a partir del link

    Args:
        link: URL completa del certificado

    Returns:
        PIL Image del código QR
    """
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(link)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    return img


def generar_certificado(datos):
    """Genera el certificado PDF con los datos proporcionados"""
    try:
        # Leer la plantilla
        reader = PdfReader("plantilla/pny_prueba.pdf")
        writer = PdfWriter()

        # Agregar todas las páginas
        writer.append(reader)

        # Dividir tipo de transporte (Página 1: 2 palabras)
        tipo_dividido = dividir_tipo_transporte(
            datos.get("tipo_transporte", ""), palabras_linea1=2
        )

        # Generar campos automáticos
        placa = datos.get("placa", "")
        fecha_inspeccion = datos.get("fecha_inspeccion", "")
        tipo_certificado = datos.get("tipo_certificado", "nuevo")

        numero_acta = generar_numero_acta(fecha_inspeccion, placa)
        numero_inspeccion = generar_numero_inspeccion(placa)
        fecha_acta = convertir_fecha_formato_acta(fecha_inspeccion)
        fecha_firma = convertir_fecha_formato_firma(fecha_inspeccion)
        link_certificado = generar_link_certificado(placa, tipo_certificado)

        # Generar código QR
        qr_image = generar_qr_code(link_certificado)

        # Guardar QR temporalmente
        qr_buffer = BytesIO()
        qr_image.save(qr_buffer, format="PNG")
        qr_buffer.seek(0)

        # PÁGINA 1 - Datos del formulario
        datos_pagina1 = {
            "placa": str(datos.get("placa", "")),
            "marca": str(datos.get("marca", "")),
            "modelo": str(datos.get("modelo", "")),
            "color": str(datos.get("color", "")),
            "capacidad": str(datos.get("capacidad", "")),
            "persona": str(datos.get("persona", "")),
            "nit": str(datos.get("nit", "")),
            "codigo_verificacion": str(datos.get("codigo_verificacion", "")),
            "fecha_inspeccion": str(datos.get("fecha_inspeccion", "")),
            "fecha_inspeccion2": str(datos.get("fecha_inspeccion", "")),
            "fecha_vencimiento": str(datos.get("fecha_vencimiento", "")),
            "tipodetransporte_1": str(tipo_dividido["tipodetransporte_1"]),
            "tipodetransporte_2": str(tipo_dividido["tipodetransporte_2"]),
            "link_certificado": link_certificado,
        }

        # PÁGINA 2 - Datos del acta
        datos_pagina2 = {
            # Campos duplicados de página 1 (con _2)
            "placa_2": str(datos.get("placa", "")),
            "marca_2": str(datos.get("marca", "")),
            "modelo_2": str(datos.get("modelo", "")),
            "color_2": str(datos.get("color", "")),
            "persona_2": str(datos.get("persona", "")),
            "nit_2": str(datos.get("nit", "")),
            # Tipo de alimento completo (sin split)
            "tipodealimento": str(datos.get("tipo_transporte", "")),
            # Campos específicos de página 2
            "ciudad": str(datos.get("ciudad", "")),
            "direccion_notificacion": str(datos.get("direccion_notificacion", "")),
            "departamento": str(datos.get("departamento", "")),
            "telefonos": str(datos.get("telefono", "")),
            "correo_electronico": str(datos.get("correo_electronico", "")),
            "fecha_ultima_inspeccion": str(datos.get("fecha_ultima_inspeccion", "")),
            "numero_acta": numero_acta,
            "numero_inspeccion": numero_inspeccion,
            "fecha_acta": fecha_acta,
            # Campo "Otro" especifique
            "clase_otro_especifique": str(datos.get("clase_otro_especifique", "")),
        }

        # PÁGINA 4 - Fecha de firma
        datos_pagina4 = {
            "fecha_firma_dia": fecha_firma["dia"],
            "fecha_firma_mes": fecha_firma["mes"],
            "fecha_firma_anio": fecha_firma["anio"],
        }

        # Actualizar cada página por separado
        if len(writer.pages) >= 1:
            writer.update_page_form_field_values(writer.pages[0], datos_pagina1)

        if len(writer.pages) >= 2:
            writer.update_page_form_field_values(writer.pages[1], datos_pagina2)

        # Página 3 no tiene campos (estática)

        if len(writer.pages) >= 4:
            writer.update_page_form_field_values(writer.pages[3], datos_pagina4)

        # === MANEJAR RADIO BUTTONS MANUALMENTE ===
        sistema_refrigeracion = datos.get("sistema_refrigeracion", "NO")
        clase_vehiculo = datos.get("clase_vehiculo", "CAMION")

        # Iterar sobre las anotaciones de la página 2
        if len(writer.pages) >= 2:
            page2 = writer.pages[1]
            if "/Annots" in page2:
                for annot in page2["/Annots"]:
                    annot_obj = annot.get_object()

                    # Verificar si es un botón de opción
                    if annot_obj.get("/FT") == "/Btn":
                        nombre = annot_obj.get("/T", "")

                        # Sistema de refrigeración
                        if "sistema_refrigeracion" in str(nombre).lower():
                            if (
                                "si" in str(nombre).lower()
                                and sistema_refrigeracion == "SI"
                            ):
                                annot_obj.update({"/AS": "/Yes"})
                            elif (
                                "no" in str(nombre).lower()
                                and sistema_refrigeracion == "NO"
                            ):
                                annot_obj.update({"/AS": "/Yes"})
                            else:
                                annot_obj.update({"/AS": "/Off"})

                        # Clase de vehículo
                        if "clase" in str(nombre).lower():
                            nombre_lower = str(nombre).lower()

                            if (
                                "camioneta" in nombre_lower
                                and clase_vehiculo == "CAMIONETA"
                            ):
                                annot_obj.update({"/AS": "/Yes"})
                            elif (
                                "camion" in nombre_lower
                                and "camioneta" not in nombre_lower
                                and clase_vehiculo == "CAMION"
                            ):
                                annot_obj.update({"/AS": "/Yes"})
                            elif "moto" in nombre_lower and clase_vehiculo == "MOTO":
                                annot_obj.update({"/AS": "/Yes"})
                            elif "otro" in nombre_lower and clase_vehiculo == "OTRO":
                                annot_obj.update({"/AS": "/Yes"})
                            else:
                                annot_obj.update({"/AS": "/Off"})

        # === INSERTAR CÓDIGO QR EN LA PÁGINA 1 ===
        # Primero guardamos el PDF con los campos rellenados
        temp_path = os.path.join("generados", "temp_sin_qr.pdf")
        with open(temp_path, "wb") as temp_file:
            writer.write(temp_file)

        # Ahora usamos ReportLab para agregar el QR
        # Crear overlay con el QR
        qr_overlay_path = os.path.join("generados", "qr_overlay.pdf")
        c = canvas.Canvas(qr_overlay_path)

        # --- Posición y tamaño del QR en CM ---
        pos_x_cm = 16.60
        pos_y_cm = 14.75
        width_cm = 3.04
        height_cm = 3.04
        # ------------------------------------

        # Conversión a puntos
        x = cm_to_points(pos_x_cm)
        y = cm_to_points(pos_y_cm)
        width = cm_to_points(width_cm)
        height = cm_to_points(height_cm)

        c.drawImage(ImageReader(qr_buffer), x, y, width, height)
        c.save()

        # Combinar el PDF con campos y el overlay del QR
        final_writer = PdfWriter()

        with open(qr_overlay_path, "rb") as qr_file, open(temp_path, "rb") as temp_file:
            qr_reader = PdfReader(qr_file)
            temp_reader = PdfReader(temp_file)

            # Página 1: Combinar con el QR
            page1 = temp_reader.pages[0]
            qr_page = qr_reader.pages[0]
            page1.merge_page(qr_page)
            final_writer.add_page(page1)

            # Resto de páginas sin cambios
            for i in range(1, len(temp_reader.pages)):
                final_writer.add_page(temp_reader.pages[i])

        # Generar nombre de archivo único
        placa_limpia = placa.replace(" ", "_")
        nombre_archivo = f"{placa_limpia}.pdf"
        ruta_salida = os.path.join("generados", nombre_archivo)

        # Guardar el PDF final
        with open(ruta_salida, "wb") as output_file:
            final_writer.write(output_file)
            # =========================
            # Publicar HTML + PDF en la web
            # =========================
            publicar_certificado_web(datos_pagina1, ruta_salida)

            # Limpiar archivos temporales
        os.remove(temp_path)
        os.remove(qr_overlay_path)

        return ruta_salida, None

    except Exception as e:
        return None, str(e)


def publicar_certificado_web(datos_pagina1, ruta_pdf):
    placa = datos_pagina1["placa"]

    # =========================
    # Render HTML
    # =========================
    tpl_index = env.get_template("index_certificado.html")
    html_index = tpl_index.render(**datos_pagina1)

    index_path = f"build/index{placa}.html"
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html_index)

    tpl_visor = env.get_template("visor_pdf.html")
    html_visor = tpl_visor.render(**datos_pagina1)

    visor_path = f"build/{placa}.html"
    with open(visor_path, "w", encoding="utf-8") as f:
        f.write(html_visor)

    # =========================
    # FTP CLÁSICO (PUERTO 21)
    # =========================
    from ftp_config import FTP_BASE, FTP_HOST, FTP_PASS, FTP_USER, FTP_VISOR

    ftp = FTP(FTP_HOST, timeout=30)
    ftp.login(user=FTP_USER, passwd=FTP_PASS)

    # Subir index principal
    with open(index_path, "rb") as f:
        ftp.storbinary(f"STOR {FTP_BASE}/index{placa}.html", f)

    # Subir visor
    with open(visor_path, "rb") as f:
        ftp.storbinary(f"STOR {FTP_VISOR}/{placa}.html", f)

    # Subir PDF
    with open(ruta_pdf, "rb") as f:
        ftp.storbinary(f"STOR {FTP_VISOR}/{placa}.pdf", f)

    ftp.quit()


@app.route("/")
def index():
    """Página principal con el formulario"""
    return render_template("index.html")


@app.route("/generar", methods=["POST"])
def generar():
    """Procesa el formulario y genera el PDF"""
    datos = {
        # Tipo de certificado
        "tipo_certificado": request.form.get("tipo_certificado", "nuevo"),  # ← NUEVO
        # Página 1
        "placa": request.form.get("placa", ""),
        "marca": request.form.get("marca", ""),
        "modelo": request.form.get("modelo", ""),
        "color": request.form.get("color", ""),
        "capacidad": request.form.get("capacidad", ""),
        "persona": request.form.get("persona", ""),
        "nit": request.form.get("nit", ""),
        "codigo_verificacion": request.form.get("codigo_verificacion", ""),
        "tipo_transporte": request.form.get("tipo_transporte", ""),
        "fecha_inspeccion": request.form.get("fecha_inspeccion", ""),
        "fecha_vencimiento": request.form.get("fecha_vencimiento", ""),
        # Página 2
        "ciudad": request.form.get("ciudad", ""),
        "direccion_notificacion": request.form.get("direccion_notificacion", ""),
        "departamento": request.form.get("departamento", ""),
        "telefono": request.form.get("telefono", ""),
        "correo_electronico": request.form.get("correo_electronico", ""),
        "fecha_ultima_inspeccion": request.form.get("fecha_ultima_inspeccion", ""),
        "sistema_refrigeracion": request.form.get("sistema_refrigeracion", "NO"),
        "clase_vehiculo": request.form.get("clase_vehiculo", "CAMION"),
        "clase_otro_especifique": request.form.get("clase_otro_especifique", ""),
    }

    ruta_pdf, error = generar_certificado(datos)

    if error:
        return f"Error al generar el certificado: {error}", 500

    return send_file(
        ruta_pdf, as_attachment=True, download_name=os.path.basename(ruta_pdf)
    )


## **3. Agrega el campo en LibreOffice Draw:**

# En la **Página 1**, donde va el link de verificación (debajo del código QR):

# - **Campo de texto:** `link_certificado`

# ---

## **Cómo funciona:**

# 1. ✅ Usuario selecciona tipo de certificado al inicio
# 2. ✅ El preview del link se actualiza automáticamente al escribir la placa
# 3. ✅ Según el tipo seleccionado:
#   - **Nuevo:** `https://itaguigov-com.us.stackstaging.com/indexPRY576.html`
#   - **Primera Renovación:** `https://itaguigov-com.us.stackstaging.com/indexPRY576remo.html`
#   - **Segunda Renovación:** `https://itaguigov-com.us.stackstaging.com/indexPRY576remo2.html`
#   - ... hasta la quinta

## **Ejemplos de links generados:**

# Placa: PRY576 + Nuevo → indexPRY576.html
# Placa: PRY576 + Primera Renovación → indexPRY576remo.html
# Placa: TLK235 + Segunda Renovación → indexTLK235remo2.html
# Placa: ABC123 + Quinta Renovación → indexABC123remo5.html


if __name__ == "__main__":
    app.run(debug=True, port=5000)
