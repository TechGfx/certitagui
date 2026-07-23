import os
import random
import re
import tempfile
from datetime import datetime
from ftplib import FTP
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

import qrcode
from flask import Flask, jsonify, render_template, request, send_file, send_from_directory, url_for
from flask_login import (
    LoginManager,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, NumberObject
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from auth import auth
from models import GenerationAudit, Party, User, VehicleProfile, db

load_dotenv()
print("DATABASE_URL =", os.getenv("DATABASE_URL"))
app = Flask(__name__)

# === CONFIGURACIÓN DE LA BASE DE DATOS Y SEGURIDAD ===
# === CONFIGURACIÓN DE LA BASE DE DATOS Y SEGURIDAD ===

# SECRET_KEY
app.secret_key = os.environ.get("SECRET_KEY", "dev-key-cambiar-en-produccion")

# DATABASE_URL (ahora para MySQL)
database_url = os.environ.get("DATABASE_URL")

if not database_url:
    raise RuntimeError("DATABASE_URL no está configurada")

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

# Configurar login manager
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Debes iniciar sesión para acceder a esta página"
login_manager.init_app(app)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


app.register_blueprint(auth)

with app.app_context():
    try:
        db.create_all()
    except Exception as exc:
        print("WARN: no se pudieron crear tablas automáticamente:", exc)

env = Environment(loader=FileSystemLoader("templates"), autoescape=False)

Path("build").mkdir(exist_ok=True)

os.makedirs("generados", exist_ok=True)
os.makedirs("instance", exist_ok=True)

CERTIFICATE_SUFFIXES = ["remo5", "remo4", "remo3", "remo2", "remo", ""]


def normalizar_placa(placa):
    return (placa or "").strip().upper().replace(" ", "")


def guardar_perfil_autocompletado(datos):
    placa = normalizar_placa(datos.get("placa", ""))
    if not placa:
        return

    nit = (datos.get("nit", "") or "").strip()
    persona = (datos.get("persona", "") or "").strip()

    owner = None
    if nit:
        owner = Party.query.filter_by(document_number=nit).first()
        if owner is None:
            owner = Party(document_number=nit, name=persona or nit, kind="company")
            db.session.add(owner)
        else:
            if persona:
                owner.name = persona
    elif persona:
        owner = Party.query.filter_by(name=persona).first()
        if owner is None:
            owner = Party(name=persona, kind="person")
            db.session.add(owner)

    if owner is not None:
        owner.phone = (datos.get("telefono", "") or "").strip() or owner.phone
        owner.email = (datos.get("correo_electronico", "") or "").strip() or owner.email
        owner.address = (
            (datos.get("direccion_notificacion", "") or "").strip() or owner.address
        )
        owner.city = (datos.get("ciudad", "") or "").strip() or owner.city
        owner.department = (
            (datos.get("departamento", "") or "").strip() or owner.department
        )

    vehicle = VehicleProfile.query.filter_by(plate=placa).first()
    if vehicle is None:
        vehicle = VehicleProfile(plate=placa)
        db.session.add(vehicle)

    if owner is not None:
        vehicle.owner = owner

    vehicle.marca = (datos.get("marca", "") or "").strip() or vehicle.marca
    vehicle.modelo = (datos.get("modelo", "") or "").strip() or vehicle.modelo
    vehicle.color = (datos.get("color", "") or "").strip() or vehicle.color
    vehicle.capacidad = (datos.get("capacidad", "") or "").strip() or vehicle.capacidad
    vehicle.tipo_transporte = (
        (datos.get("tipo_transporte", "") or "").strip() or vehicle.tipo_transporte
    )
    vehicle.clase_vehiculo = (
        (datos.get("clase_vehiculo", "") or "").strip() or vehicle.clase_vehiculo
    )
    vehicle.sistema_refrigeracion = (
        (datos.get("sistema_refrigeracion", "") or "").strip()
        or vehicle.sistema_refrigeracion
    )
    vehicle.codigo_verificacion = (
        (datos.get("codigo_verificacion", "") or "").strip()
        or vehicle.codigo_verificacion
    )
    vehicle.last_certificate_type = (
        (datos.get("tipo_certificado", "") or "").strip() or vehicle.last_certificate_type
    )


def serializar_autocompletado(vehicle):
    owner = vehicle.owner
    return {
        "placa": vehicle.plate,
        "marca": vehicle.marca or "",
        "modelo": vehicle.modelo or "",
        "color": vehicle.color or "",
        "capacidad": vehicle.capacidad or "",
        "tipo_transporte": vehicle.tipo_transporte or "",
        "codigo_verificacion": vehicle.codigo_verificacion or "",
        "clase_vehiculo": vehicle.clase_vehiculo or "",
        "sistema_refrigeracion": vehicle.sistema_refrigeracion or "",
        "tipo_certificado": vehicle.last_certificate_type or "nuevo",
        "persona": owner.name if owner else "",
        "nit": owner.document_number if owner else "",
        "telefono": owner.phone if owner else "",
        "correo_electronico": owner.email if owner else "",
        "direccion_notificacion": owner.address if owner else "",
        "ciudad": owner.city if owner else "",
        "departamento": owner.department if owner else "",
    }


def _normalize_text(value):
    return re.sub(r"\s+", " ", (value or "")).strip()


def _normalize_key(value):
    return re.sub(r"[^a-z0-9]+", "", _normalize_text(value).lower())


def _extract_form_fields(reader):
    fields = {}
    raw_fields = reader.get_fields() or {}
    for field_name, field_data in raw_fields.items():
        if not field_data:
            continue
        field_value = field_data.get("/V")
        if field_value is None:
            continue
        fields[str(field_name)] = _normalize_text(str(field_value))
    return fields


def _extract_value_from_lines(page_text, aliases):
    lines = [_normalize_text(line) for line in page_text.splitlines() if _normalize_text(line)]
    regexes = [re.compile(rf"{re.escape(alias)}\s*:?(.*)$", re.I) for alias in aliases]

    def looks_like_label(line):
        normalized_line = _normalize_key(line)
        return any(_normalize_key(alias) in normalized_line for alias in aliases)

    for idx, line in enumerate(lines):
        for regex in regexes:
            match = regex.search(line)
            if not match:
                continue

            inline_value = _normalize_text(match.group(1))
            if inline_value:
                return inline_value

            for next_line in lines[idx + 1 : idx + 4]:
                if next_line and not looks_like_label(next_line):
                    return next_line
    return ""


def _extract_semantic_value(form_fields, page_texts, aliases):
    alias_keys = [_normalize_key(alias) for alias in aliases]

    for field_name, field_value in form_fields.items():
        normalized_name = _normalize_key(field_name)
        if any(alias in normalized_name for alias in alias_keys):
            if field_value:
                return field_value

    for page_text in page_texts:
        value = _extract_value_from_lines(page_text, aliases)
        if value:
            return value

    return ""


def _parse_autocomplete_pdf(pdf_path):
    reader = PdfReader(pdf_path)
    form_fields = _extract_form_fields(reader)
    page_texts = [(page.extract_text() or "") for page in reader.pages]

    return {
        "placa": _extract_semantic_value(form_fields, page_texts, ["Placa", "Placa del Vehículo"]),
        "marca": _extract_semantic_value(form_fields, page_texts, ["Marca"]),
        "modelo": _extract_semantic_value(form_fields, page_texts, ["Modelo"]),
        "color": _extract_semantic_value(form_fields, page_texts, ["Color"]),
        "capacidad": _extract_semantic_value(form_fields, page_texts, ["Capacidad"]),
        "persona": _extract_semantic_value(
            form_fields,
            page_texts,
            ["Persona", "Nombre del propietario", "Nombre del Propietario"],
        ),
        "nit": _extract_semantic_value(form_fields, page_texts, ["Nit", "NIT", "Número de documento"]),
        "codigo_verificacion": _extract_semantic_value(
            form_fields,
            page_texts,
            ["Código de verificación", "Código de Verificación"],
        ),
        "tipo_transporte": _extract_semantic_value(
            form_fields,
            page_texts,
            ["Tipo de transporte", "Tipo de alimento transportado", "Tipo de Alimento Transportado"],
        ),
        "ciudad": _extract_semantic_value(form_fields, page_texts, ["Ciudad", "Ciudad (Municipio)", "Municipio"]),
        "departamento": _extract_semantic_value(form_fields, page_texts, ["Departamento"]),
        "direccion_notificacion": _extract_semantic_value(
            form_fields,
            page_texts,
            ["Dirección de notificación", "Dirección de Notificación"],
        ),
        "telefono": _extract_semantic_value(form_fields, page_texts, ["Teléfonos", "Telefono", "Teléfono"]),
        "correo_electronico": _extract_semantic_value(
            form_fields,
            page_texts,
            ["Correo electrónico del propietario", "Correo Electrónico del Propietario", "Correo Electrónico"],
        ),
        "sistema_refrigeracion": _extract_semantic_value(
            form_fields,
            page_texts,
            ["Sistema de refrigeración", "Sistema de Refrigeración"],
        ),
        "clase_vehiculo": _extract_semantic_value(
            form_fields,
            page_texts,
            ["Clase del vehículo", "Clase del Vehículo"],
        ),
    }


def _list_remote_files(ftp, remote_path):
    current_dir = ftp.pwd()
    try:
        ftp.cwd(remote_path)
        return ftp.nlst()
    finally:
        ftp.cwd(current_dir)


def buscar_autocompletado_en_ftp(placa_norm):
    from ftp_config import FTP_BASE, FTP_HOST, FTP_PASS, FTP_USER, FTP_VISOR

    base_publica = "https://itaguigov-com.us.stackstaging.com"
    carpeta_visor = quote("___ Busqueda de certificados electronicos __._files")

    ftp = FTP(FTP_HOST, timeout=45)
    ftp.login(user=FTP_USER, passwd=FTP_PASS)

    try:
        root_files = set(_list_remote_files(ftp, FTP_BASE))
        visor_files = set(_list_remote_files(ftp, FTP_VISOR))

        selected = None
        for suffix in CERTIFICATE_SUFFIXES:
            key = f"{placa_norm}{suffix}"
            index_file = f"index{key}.html"
            viewer_file = f"{key}.html"
            pdf_file = f"{key}.pdf"

            if index_file in root_files and pdf_file in visor_files:
                selected = (key, suffix or "nuevo", index_file, viewer_file, pdf_file)
                break

        if selected is None:
            for suffix in CERTIFICATE_SUFFIXES:
                key = f"{placa_norm}{suffix}"
                pdf_file = f"{key}.pdf"
                if pdf_file in visor_files:
                    selected = (
                        key,
                        suffix or "nuevo",
                        f"index{key}.html",
                        f"{key}.html",
                        pdf_file,
                    )
                    break

        if selected is None:
            return None, None

        cert_key, cert_type, index_file, viewer_file, pdf_file = selected
        remote_pdf_path = f"{FTP_VISOR.rstrip('/')}/{pdf_file}"

        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file:
                temp_path = temp_file.name
                ftp.retrbinary(f"RETR {remote_pdf_path}", temp_file.write)

            parsed = _parse_autocomplete_pdf(temp_path)
            parsed["placa"] = parsed.get("placa") or placa_norm
            parsed["tipo_certificado"] = cert_type

            payload = {
                "data": parsed,
                "index_url": f"{base_publica}/{index_file}",
                "viewer_url": f"{base_publica}/{carpeta_visor}/{quote(viewer_file)}",
                "remote_pdf_url": f"{base_publica}/{carpeta_visor}/{quote(pdf_file)}",
                "certificate_key": cert_key,
            }
            return payload, None
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)
    except Exception as exc:
        return None, str(exc)
    finally:
        ftp.quit()


def dividir_tipo_transporte(texto, palabras_linea1=3):
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

        # Usar placa_archivos para nombres de archivos
        placa_display = datos.get("placa", "")  # Placa completa para mostrar
        placa_archivos = datos.get("placa_archivos", placa_display)  # Para archivos

        # Dividir tipo de transporte (Página 1: 2 palabras)
        tipo_dividido = dividir_tipo_transporte(
            datos.get("tipo_transporte", ""), palabras_linea1=3
        )

        # Generar campos automáticos
        placa = datos.get("placa", "")
        fecha_inspeccion = datos.get("fecha_inspeccion", "")
        tipo_certificado = datos.get("tipo_certificado", "nuevo")

        #################################
        #################################

        print("\n===== DEBUG CERTIFICADO =====")
        print("TIPO:", tipo_certificado)
        print("PLACA:", placa)
        print("PLACA_ARCHIVOS:", placa_archivos)

        #################################
        #################################

        numero_acta = generar_numero_acta(fecha_inspeccion, placa)
        numero_inspeccion = generar_numero_inspeccion(placa)
        fecha_acta = convertir_fecha_formato_acta(fecha_inspeccion)
        fecha_firma = convertir_fecha_formato_firma(fecha_inspeccion)
        link_certificado = generar_link_certificado(placa, tipo_certificado)

        #################################
        #################################

        print("LINK:", link_certificado)
        print("=============================\n")

        #################################
        #################################

        # Generar código QR
        qr_image = generar_qr_code(link_certificado)

        # Guardar QR temporalmente
        qr_buffer = BytesIO()
        qr_image.save(qr_buffer, format="PNG")
        qr_buffer.seek(0)

        # === DETERMINAR QUÉ CHECKBOXES MARCAR ===
        sistema_refrigeracion = datos.get("sistema_refrigeracion", "NO")
        clase_vehiculo = datos.get("clase_vehiculo", "CAMION")

        # Sistema de refrigeración
        refri_si = "X" if sistema_refrigeracion == "SI" else ""
        refri_no = "X" if sistema_refrigeracion == "NO" else ""

        # Clase de vehículo
        check_camioneta = "X" if clase_vehiculo == "CAMIONETA" else ""
        check_camion = "X" if clase_vehiculo == "CAMION" else ""
        check_moto = "X" if clase_vehiculo == "MOTO" else ""
        check_otro = "X" if clase_vehiculo == "OTRO" else ""

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
            "tipo_transporte": str(datos.get("tipo_transporte", "")),
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
            # === CHECKBOXES CON X ===
            "sistema_refrigeracion_si_check": refri_si,
            "sistema_refrigeracion_no_check": refri_no,
            "clase_camioneta_check": check_camioneta,
            "clase_camion_check": check_camion,
            "clase_moto_check": check_moto,
            "clase_otro_check": check_otro,
        }

        # PÁGINA 4 - Fecha de firma
        datos_pagina4 = {
            "fecha_firma_dia": fecha_firma["dia"],
            "fecha_firma_mes": fecha_firma["mes"],
            "fecha_firma_anio": fecha_firma["anio"],
        }

        # === GENERAR NOMBRE DE ARCHIVO CON PLACA DEL TRAILER ===
        placa_limpia = placa_archivos.replace(" ", "_")

        if tipo_certificado != "nuevo":
            nombre_archivo = f"{placa_limpia}{tipo_certificado}.pdf"
        else:
            nombre_archivo = f"{placa_limpia}.pdf"

        # Actualizar cada página por separado
        if len(writer.pages) >= 1:
            writer.update_page_form_field_values(writer.pages[0], datos_pagina1)

        if len(writer.pages) >= 2:
            writer.update_page_form_field_values(writer.pages[1], datos_pagina2)

        # Página 3 no tiene campos (estática)

        if len(writer.pages) >= 4:
            writer.update_page_form_field_values(writer.pages[3], datos_pagina4)

        # === YA NO MANEJAMOS RADIO BUTTONS MANUALMENTE ===
        # (Eliminada toda la sección anterior de radio buttons)

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

        ruta_salida = os.path.join("generados", nombre_archivo)

        # Guardar el PDF final
        with open(ruta_salida, "wb") as output_file:
            final_writer.write(output_file)

        # Limpiar archivos temporales
        os.remove(temp_path)
        os.remove(qr_overlay_path)

        # Publicar en web
        ok, publicacion = publicar_certificado_web(datos, ruta_salida)

        if not ok:
            return None, f"Error FTP: {publicacion}", None

        return ruta_salida, None, publicacion

    except Exception as e:
        return None, str(e), None


def publicar_certificado_web(datos, ruta_pdf):
    try:
        placa = datos["placa"]
        tipo_certificado = datos.get("tipo_certificado", "nuevo")

        # =========================
        # Render HTML
        # =========================
        tpl_index = env.get_template("index_certificado.html")
        html_index = tpl_index.render(**datos)

        # indexPRY576.html / indexPRY576remo.html / indexPRY576remo2.html ...
        print("\n========== FTP ==========")
        print("Placa:", placa)
        print("Tipo:", tipo_certificado)

        sufijo = "" if tipo_certificado == "nuevo" else tipo_certificado

        print("Sufijo:", repr(sufijo))
        print("PDF remoto:", f"{placa}{sufijo}.pdf")
        print("HTML remoto:", f"{placa}{sufijo}.html")
        print("INDEX remoto:", f"index{placa}{sufijo}.html")
        print("=========================\n")
        index_path = f"build/index{placa}{sufijo}.html"

        with open(index_path, "w", encoding="utf-8") as f:
            f.write(html_index)

        tpl_visor = env.get_template("visor_pdf.html")
        html_visor = tpl_visor.render(**datos)

        visor_path = f"build/{placa}{sufijo}.html"
        with open(visor_path, "w", encoding="utf-8") as f:
            f.write(html_visor)

        # =========================
        # FTP
        # =========================
        from ftp_config import FTP_BASE, FTP_HOST, FTP_PASS, FTP_USER, FTP_VISOR

        ftp = FTP(FTP_HOST, timeout=30)
        ftp.login(user=FTP_USER, passwd=FTP_PASS)

        # Subir index
        with open(index_path, "rb") as f:
            ftp.storbinary(
                f"STOR {FTP_BASE}/index{placa}{sufijo}.html",
                f,
            )

        # Subir visor
        with open(visor_path, "rb") as f:
            ftp.storbinary(
                f"STOR {FTP_VISOR}/{placa}{sufijo}.html",
                f,
            )

        # Subir PDF
        with open(ruta_pdf, "rb") as f:
            ftp.storbinary(
                f"STOR {FTP_VISOR}/{placa}{sufijo}.pdf",
                f,
            )

        # Entrar al directorio donde quedó el PDF
        ftp.cwd(FTP_VISOR)

        archivos = ftp.nlst()

        print("\nARCHIVOS EN FTP:")
        for archivo in archivos:
            print(" -", archivo)

        if f"{placa}{sufijo}.pdf" in archivos:
            print("✅ El PDF existe en el FTP")
        else:
            print("❌ El PDF NO existe en el FTP")

        ftp.quit()

        base_publica = "https://itaguigov-com.us.stackstaging.com"
        carpeta_visor = quote("___ Busqueda de certificados electronicos __._files")

        pdf_filename = f"{placa}{sufijo}.pdf"
        viewer_filename = f"{placa}{sufijo}.html"
        index_filename = f"index{placa}{sufijo}.html"

        return True, {
            "pdf_filename": pdf_filename,
            "viewer_filename": viewer_filename,
            "index_filename": index_filename,
            "index_url": f"{base_publica}/{index_filename}",
            "viewer_url": f"{base_publica}/{carpeta_visor}/{quote(viewer_filename)}",
            "remote_pdf_url": f"{base_publica}/{carpeta_visor}/{quote(pdf_filename)}",
        }

    except Exception as e:
        return False, str(e)


@app.route("/")
@login_required
def index():
    """Página principal con el formulario"""
    recientes = (
        GenerationAudit.query.filter_by(user_id=current_user.id)
        .order_by(GenerationAudit.created_at.desc())
        .limit(10)
        .all()
    )
    return render_template("index.html", user=current_user, recientes=recientes)


@app.route("/descargar/<path:filename>")
@login_required
def descargar_generado(filename):
    return send_from_directory("generados", filename, as_attachment=True)


@app.route("/api/autocompletar/placa/<placa>", methods=["GET"])
@login_required
def autocompletar_por_placa(placa):
    placa_norm = normalizar_placa(placa)
    if not placa_norm:
        return jsonify({"ok": False, "message": "Placa inválida"}), 400

    payload, error = buscar_autocompletado_en_ftp(placa_norm)
    if error:
        return jsonify({"ok": False, "message": f"Error consultando FTP: {error}"}), 500

    if payload is None:
        return jsonify(
            {
                "ok": False,
                "message": f"No se encontró certificado remoto para la placa {placa_norm}.",
            }
        )

    return jsonify(
        {
            "ok": True,
            "message": f"Datos cargados desde FTP para la placa {placa_norm}.",
            "source": "ftp",
            "data": payload["data"],
            "index_url": payload["index_url"],
            "viewer_url": payload["viewer_url"],
            "remote_pdf_url": payload["remote_pdf_url"],
            "certificate_key": payload["certificate_key"],
        }
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        # validación (luego BD)
        if email == "test@test.com" and password == "1234":
            return redirect("/dashboard")

        return render_template("login.html", error="Credenciales incorrectas")

    return render_template("login.html")


@app.route("/generar", methods=["POST"])
@login_required
def generar():
    """Procesa el formulario y genera el PDF"""

    # Capturar datos del trailer
    es_trailer = request.form.get("es_trailer") == "true"
    placa_vehiculo = request.form.get("placa", "")
    placa_trailer = request.form.get("placa_trailer", "")

    # Si es trailer, combinar placas
    if es_trailer and placa_trailer:
        placa_completa = f"{placa_vehiculo} TRAILER: {placa_trailer}"
        placa_archivos = placa_trailer  # Usar placa del trailer para archivos
    else:
        placa_completa = placa_vehiculo
        placa_archivos = placa_vehiculo

    print("====== FORM ======")
    print(request.form)
    print("tipo_certificado =", request.form.get("tipo_certificado"))
    print(
        "tipo_certificado_hidden =",
        request.form.get("tipo_certificado_hidden"),
    )
    print("==================")

    tipo_certificado = (
        request.form.get("tipo_certificado")
        or request.form.get("tipo_certificado_hidden")
        or "nuevo"
    )

    datos = {
        # Tipo de certificado
        "tipo_certificado": tipo_certificado,
        # Placas
        "placa": placa_completa,  # Para mostrar en el PDF
        "placa_archivos": placa_archivos,  # Para nombres de archivos
        "es_trailer": es_trailer,
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

    ruta_pdf, error, publicacion = generar_certificado(datos)

    if error:
        audit_error = GenerationAudit(
            user_id=current_user.id,
            plate=placa_archivos,
            certificate_type=tipo_certificado,
            status="error",
            message=error,
        )
        db.session.add(audit_error)
        db.session.commit()

        return jsonify({"ok": False, "message": f"Error al generar el certificado: {error}"}), 500

    pdf_filename = os.path.basename(ruta_pdf)
    descarga_url = url_for("descargar_generado", filename=pdf_filename)

    audit_ok = GenerationAudit(
        user_id=current_user.id,
        plate=placa_archivos,
        certificate_type=tipo_certificado,
        status="success",
        message="Certificado generado y publicado correctamente",
        pdf_filename=pdf_filename,
        index_url=publicacion.get("index_url") if publicacion else None,
        viewer_url=publicacion.get("viewer_url") if publicacion else None,
        remote_pdf_url=publicacion.get("remote_pdf_url") if publicacion else None,
    )
    db.session.add(audit_ok)

    try:
        guardar_perfil_autocompletado(datos)
    except Exception as profile_exc:
        print("WARN: no se pudo guardar perfil para autocompletado:", profile_exc)

    db.session.commit()

    return jsonify(
        {
            "ok": True,
            "message": "Certificado generado y publicado correctamente.",
            "download_url": descarga_url,
            "pdf_filename": pdf_filename,
            "index_url": publicacion.get("index_url") if publicacion else None,
            "viewer_url": publicacion.get("viewer_url") if publicacion else None,
            "remote_pdf_url": publicacion.get("remote_pdf_url") if publicacion else None,
            "generated_at": audit_ok.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "recent_item": {
                "plate": audit_ok.plate,
                "certificate_type": audit_ok.certificate_type,
                "status": audit_ok.status,
                "generated_at": audit_ok.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                "index_url": audit_ok.index_url,
                "viewer_url": audit_ok.viewer_url,
                "remote_pdf_url": audit_ok.remote_pdf_url,
            },
        }
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
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
