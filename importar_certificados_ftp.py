"""Importa certificados históricos desde el FTP priorizando el PDF.

El HTML se usa solo para resolver la ruta exacta del certificado por nombre
de archivo. La extracción principal sale del PDF: primero campos de formulario,
luego texto embebido si el PDF viene aplanado.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from ftplib import FTP
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv
from flask import Flask
from pypdf import PdfReader

from ftp_config import FTP_BASE, FTP_HOST, FTP_PASS, FTP_PORT, FTP_USER, FTP_VISOR
from models import CertificateRecord, Party, VehicleProfile, db


SUFFIXES = ["remo5", "remo4", "remo3", "remo2", "remo"]


@dataclass
class RemoteCertificate:
    certificate_key: str
    plate: str
    certificate_type: str
    pdf_filename: str
    viewer_html_filename: str
    index_html_filename: str


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_text(value).lower())


def build_remote_certificate(filename: str) -> RemoteCertificate | None:
    base_name = Path(filename).stem

    if base_name.startswith("index"):
        base_name = base_name[5:]

    certificate_type = "nuevo"
    plate = base_name

    for suffix in SUFFIXES:
        if base_name.endswith(suffix):
            certificate_type = suffix
            plate = base_name[: -len(suffix)]
            break

    plate = plate.strip()
    if not plate:
        return None

    return RemoteCertificate(
        certificate_key=base_name,
        plate=plate,
        certificate_type=certificate_type,
        pdf_filename=f"{base_name}.pdf",
        viewer_html_filename=f"{base_name}.html",
        index_html_filename=f"index{base_name}.html",
    )


def ftp_client() -> FTP:
    ftp = FTP()
    ftp.connect(FTP_HOST, FTP_PORT, timeout=45)
    ftp.login(user=FTP_USER, passwd=FTP_PASS)
    return ftp


def list_remote_files(ftp: FTP, remote_path: str) -> list[str]:
    current_dir = ftp.pwd()
    try:
        ftp.cwd(remote_path)
        return ftp.nlst()
    finally:
        ftp.cwd(current_dir)


def download_remote_file(ftp: FTP, remote_path: str, destination: Path) -> None:
    with destination.open("wb") as handle:
        ftp.retrbinary(f"RETR {remote_path}", handle.write)


def extract_form_fields(reader: PdfReader) -> dict[str, str]:
    fields: dict[str, str] = {}
    raw_fields = reader.get_fields() or {}

    for field_name, field_data in raw_fields.items():
        if not field_data:
            continue
        field_value = field_data.get("/V")
        if field_value is None:
            continue
        fields[str(field_name)] = normalize_text(str(field_value))

    return fields


def extract_pdf_pages(reader: PdfReader) -> list[str]:
    pages: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        pages.append(text)
    return pages


def extract_from_label_lines(page_text: str, aliases: Iterable[str]) -> str:
    lines = [normalize_text(line) for line in page_text.splitlines() if normalize_text(line)]

    compiled_aliases = [re.compile(rf"{re.escape(alias)}\s*:?(.*)$", re.I) for alias in aliases]

    def looks_like_label(line: str) -> bool:
        normalized_line = normalize_key(line)
        return any(normalize_key(alias) in normalized_line for alias in aliases)

    for index, line in enumerate(lines):
        for pattern in compiled_aliases:
            match = pattern.search(line)
            if not match:
                continue

            inline_value = normalize_text(match.group(1))
            if inline_value:
                return inline_value

            for next_line in lines[index + 1 : index + 4]:
                if next_line and not looks_like_label(next_line):
                    return next_line

    return ""


def extract_semantic_value(
    form_fields: dict[str, str],
    page_texts: list[str],
    aliases: Iterable[str],
) -> str:
    alias_keys = [normalize_key(alias) for alias in aliases]

    for field_name, field_value in form_fields.items():
        normalized_name = normalize_key(field_name)
        if any(alias in normalized_name for alias in alias_keys):
            if field_value:
                return field_value

    for page_text in page_texts:
        extracted = extract_from_label_lines(page_text, aliases)
        if extracted:
            return extracted

    return ""


def detect_party_kind(name: str, document_number: str) -> str:
    normalized_name = normalize_text(name).upper()
    if any(token in normalized_name for token in ["S.A.S.", "SAS", "LTDA", "S.A.", "E.U.", "SA "]):
        return "company"
    if document_number and len(re.sub(r"\D", "", document_number)) >= 8:
        return "company"
    return "person"


def parse_pdf(pdf_path: Path) -> dict:
    reader = PdfReader(str(pdf_path))
    form_fields = extract_form_fields(reader)
    page_texts = extract_pdf_pages(reader)
    full_text = "\n\n".join(page_texts)

    data = {
        "placa": extract_semantic_value(form_fields, page_texts, ["Placa", "Placa del Vehículo"]),
        "marca": extract_semantic_value(form_fields, page_texts, ["Marca"]),
        "modelo": extract_semantic_value(form_fields, page_texts, ["Modelo"]),
        "color": extract_semantic_value(form_fields, page_texts, ["Color"]),
        "capacidad": extract_semantic_value(form_fields, page_texts, ["Capacidad"]),
        "persona": extract_semantic_value(
            form_fields,
            page_texts,
            ["Persona", "Nombre del propietario", "Nombre del Propietario"],
        ),
        "nit": extract_semantic_value(form_fields, page_texts, ["Nit", "NIT", "Número de documento"]),
        "codigo_verificacion": extract_semantic_value(
            form_fields,
            page_texts,
            ["Código de verificación", "Código de Verificación"],
        ),
        "tipo_transporte": extract_semantic_value(
            form_fields,
            page_texts,
            ["Tipo de transporte", "Tipo de alimento transportado", "Tipo de Alimento Transportado"],
        ),
        "fecha_inspeccion": extract_semantic_value(form_fields, page_texts, ["Fecha de Inspección", "Fecha"]),
        "fecha_vencimiento": extract_semantic_value(form_fields, page_texts, ["Fecha de Vencimiento"]),
        "ciudad": extract_semantic_value(form_fields, page_texts, ["Ciudad", "Ciudad (Municipio)", "Municipio"]),
        "departamento": extract_semantic_value(form_fields, page_texts, ["Departamento"]),
        "direccion_notificacion": extract_semantic_value(
            form_fields,
            page_texts,
            ["Dirección de notificación", "Dirección de Notificación"],
        ),
        "telefono": extract_semantic_value(form_fields, page_texts, ["Teléfonos", "Telefono", "Teléfono"]),
        "correo_electronico": extract_semantic_value(
            form_fields,
            page_texts,
            ["Correo electrónico del propietario", "Correo Electrónico del Propietario", "Correo Electrónico"],
        ),
        "fecha_ultima_inspeccion": extract_semantic_value(
            form_fields,
            page_texts,
            ["Fecha última inspección", "Fecha ultima inspeccion"],
        ),
        "sistema_refrigeracion": extract_semantic_value(
            form_fields,
            page_texts,
            ["Sistema de refrigeración", "Sistema de Refrigeración"],
        ),
        "clase_vehiculo": extract_semantic_value(
            form_fields,
            page_texts,
            ["Clase del vehículo", "Clase del Vehículo"],
        ),
        "clase_otro_especifique": extract_semantic_value(
            form_fields,
            page_texts,
            ["Cual?", "Cuál?", "Otro"],
        ),
        "acta_number": extract_semantic_value(form_fields, page_texts, ["Acta N°", "Acta No", "Numero de acta"]),
        "inspection_number": extract_semantic_value(
            form_fields,
            page_texts,
            ["Número de inspección", "Numero de inspeccion"],
        ),
        "raw_form_fields": form_fields,
        "page_texts": page_texts,
        "full_text": full_text,
    }

    return data


def upsert_party(data: dict) -> Party | None:
    document_number = normalize_text(data.get("nit", ""))
    name = normalize_text(data.get("persona", ""))

    if not name and not document_number:
        return None

    party = None
    if document_number:
        party = Party.query.filter_by(document_number=document_number).first()

    if party is None:
        if not name:
            name = document_number
        party = Party(document_number=document_number or None, name=name, kind=detect_party_kind(name, document_number))
        db.session.add(party)
    else:
        if name and party.name != name:
            party.name = name
        party.kind = detect_party_kind(name or party.name, document_number)

    party.phone = normalize_text(data.get("telefono", "")) or party.phone
    party.email = normalize_text(data.get("correo_electronico", "")) or party.email
    party.address = normalize_text(data.get("direccion_notificacion", "")) or party.address
    party.city = normalize_text(data.get("ciudad", "")) or party.city
    party.department = normalize_text(data.get("departamento", "")) or party.department
    return party


def upsert_vehicle(certificate_key: str, certificate_type: str, data: dict, party: Party | None) -> VehicleProfile:
    plate = normalize_text(data.get("placa", ""))
    vehicle = VehicleProfile.query.filter_by(plate=plate).first()

    if vehicle is None:
        vehicle = VehicleProfile(plate=plate)
        db.session.add(vehicle)

    if party is not None:
        vehicle.owner = party

    vehicle.marca = normalize_text(data.get("marca", "")) or vehicle.marca
    vehicle.modelo = normalize_text(data.get("modelo", "")) or vehicle.modelo
    vehicle.color = normalize_text(data.get("color", "")) or vehicle.color
    vehicle.capacidad = normalize_text(data.get("capacidad", "")) or vehicle.capacidad
    vehicle.tipo_transporte = normalize_text(data.get("tipo_transporte", "")) or vehicle.tipo_transporte
    vehicle.clase_vehiculo = normalize_text(data.get("clase_vehiculo", "")) or vehicle.clase_vehiculo
    vehicle.sistema_refrigeracion = normalize_text(data.get("sistema_refrigeracion", "")) or vehicle.sistema_refrigeracion
    vehicle.codigo_verificacion = normalize_text(data.get("codigo_verificacion", "")) or vehicle.codigo_verificacion
    vehicle.last_certificate_key = certificate_key
    vehicle.last_certificate_type = certificate_type
    vehicle.last_imported_at = datetime.utcnow()
    return vehicle


def upsert_certificate_record(remote: RemoteCertificate, data: dict, party: Party | None, vehicle: VehicleProfile) -> CertificateRecord:
    record = CertificateRecord.query.filter_by(certificate_key=remote.certificate_key).first()
    if record is None:
        record = CertificateRecord(certificate_key=remote.certificate_key)
        db.session.add(record)

    record.plate = remote.plate
    record.certificate_type = remote.certificate_type
    record.pdf_filename = remote.pdf_filename
    record.viewer_html_filename = remote.viewer_html_filename
    record.index_html_filename = remote.index_html_filename
    record.vehicle = vehicle
    record.party = party
    record.inspection_date = normalize_text(data.get("fecha_inspeccion", "")) or record.inspection_date
    record.expiration_date = normalize_text(data.get("fecha_vencimiento", "")) or record.expiration_date
    record.acta_number = normalize_text(data.get("acta_number", "")) or record.acta_number
    record.inspection_number = normalize_text(data.get("inspection_number", "")) or record.inspection_number
    record.extracted_json = json.dumps(
        {
            "raw_form_fields": data.get("raw_form_fields", {}),
            "page_texts": data.get("page_texts", []),
            "full_text": data.get("full_text", ""),
            "parsed": {k: v for k, v in data.items() if k not in {"raw_form_fields", "page_texts", "full_text"}},
        },
        ensure_ascii=False,
        indent=2,
    )
    record.page_text = data.get("full_text", "")
    record.source_status = "imported"
    record.parse_notes = None
    record.imported_at = datetime.utcnow()
    return record


def import_one_certificate(ftp: FTP, remote: RemoteCertificate, temp_dir: Path, dry_run: bool = False) -> str:
    pdf_remote_path = f"{FTP_VISOR.rstrip('/')}/{remote.pdf_filename}"
    index_remote_path = f"{FTP_BASE.rstrip('/')}/{remote.index_html_filename}" if FTP_BASE != "/" else f"/{remote.index_html_filename}"
    viewer_remote_path = f"{FTP_VISOR.rstrip('/')}/{remote.viewer_html_filename}"

    pdf_path = temp_dir / remote.pdf_filename
    download_remote_file(ftp, pdf_remote_path, pdf_path)

    parsed = parse_pdf(pdf_path)
    parsed["placa"] = parsed.get("placa") or remote.plate

    party = upsert_party(parsed)
    vehicle = upsert_vehicle(remote.certificate_key, remote.certificate_type, parsed, party)
    record = upsert_certificate_record(remote, parsed, party, vehicle)

    if dry_run:
        db.session.rollback()
    else:
        db.session.commit()

    exists_notes = []
    try:
        remote_index_listing = set(list_remote_files(ftp, FTP_BASE))
        remote_viewer_listing = set(list_remote_files(ftp, FTP_VISOR))
        if remote.index_html_filename not in remote_index_listing:
            exists_notes.append("index faltante")
        if remote.viewer_html_filename not in remote_viewer_listing:
            exists_notes.append("visor faltante")
    except Exception:
        exists_notes.append("no se pudo validar rutas HTML")

    if exists_notes:
        record.parse_notes = "; ".join(exists_notes)
        if not dry_run:
            db.session.commit()

    return remote.certificate_key


def discover_certificates(ftp: FTP) -> list[RemoteCertificate]:
    visor_files = set(name for name in list_remote_files(ftp, FTP_VISOR) if name.lower().endswith(".pdf"))
    root_files = set(name for name in list_remote_files(ftp, FTP_BASE))

    certificates: list[RemoteCertificate] = []
    for filename in sorted(visor_files):
        remote = build_remote_certificate(filename)
        if remote is None:
            continue

        if remote.index_html_filename not in root_files:
            continue

        if remote.viewer_html_filename not in visor_files and remote.viewer_html_filename not in root_files:
            # El HTML de visor ayuda a ubicar el certificado; si no existe, igual
            # puede importarse el PDF, pero dejamos registro del hueco para revisión.
            pass

        certificates.append(remote)

    return certificates


def create_local_app() -> Flask:
    load_dotenv()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL no está configurada")

    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    return app


def run_import(limit: int | None = None, dry_run: bool = False) -> None:
    app = create_local_app()

    with app.app_context():
        db.create_all()

        ftp = ftp_client()
        try:
            certificates = discover_certificates(ftp)
            if limit is not None:
                certificates = certificates[:limit]

            if not certificates:
                print("No se encontraron certificados PDF para importar.")
                return

            with tempfile.TemporaryDirectory() as temp_dir_name:
                temp_dir = Path(temp_dir_name)
                for remote in certificates:
                    print(f"Importando {remote.certificate_key} ({remote.certificate_type})...")
                    imported_key = import_one_certificate(ftp, remote, temp_dir, dry_run=dry_run)
                    print(f"  ✓ {imported_key}")

            if dry_run:
                print("\nDry-run completado: no se guardaron cambios.")
            else:
                print("\nImportación completada.")
        finally:
            ftp.quit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Importa certificados históricos desde FTP priorizando el PDF.")
    parser.add_argument("--limit", type=int, default=None, help="Importar solo N certificados")
    parser.add_argument("--dry-run", action="store_true", help="No guardar cambios en la base de datos")
    args = parser.parse_args()

    run_import(limit=args.limit, dry_run=args.dry_run)


if __name__ == "__main__":
    main()