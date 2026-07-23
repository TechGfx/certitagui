from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash
from datetime import datetime

db = SQLAlchemy()


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default="operador")  # operador, admin
    generation_audits = db.relationship(
        "GenerationAudit",
        back_populates="user",
        lazy=True,
        cascade="all, delete-orphan",
    )

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"<User {self.username}>"


class Party(db.Model):
    __tablename__ = "parties"

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(20), nullable=False, default="person")
    document_number = db.Column(db.String(50), unique=True, index=True)
    name = db.Column(db.String(255), nullable=False)
    phone = db.Column(db.String(50))
    email = db.Column(db.String(255))
    address = db.Column(db.String(255))
    city = db.Column(db.String(120))
    department = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    vehicles = db.relationship("VehicleProfile", back_populates="owner", lazy=True)
    certificates = db.relationship("CertificateRecord", back_populates="party", lazy=True)

    def __repr__(self):
        return f"<Party {self.name}>"


class VehicleProfile(db.Model):
    __tablename__ = "vehicle_profiles"

    id = db.Column(db.Integer, primary_key=True)
    plate = db.Column(db.String(20), unique=True, index=True, nullable=False)
    owner_id = db.Column(db.Integer, db.ForeignKey("parties.id"))
    marca = db.Column(db.String(120))
    modelo = db.Column(db.String(50))
    color = db.Column(db.String(80))
    capacidad = db.Column(db.String(120))
    tipo_transporte = db.Column(db.String(255))
    clase_vehiculo = db.Column(db.String(50))
    sistema_refrigeracion = db.Column(db.String(10))
    trailer_plate = db.Column(db.String(30))
    codigo_verificacion = db.Column(db.String(120))
    last_certificate_key = db.Column(db.String(120), index=True)
    last_certificate_type = db.Column(db.String(20))
    last_imported_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    owner = db.relationship("Party", back_populates="vehicles")
    certificates = db.relationship("CertificateRecord", back_populates="vehicle", lazy=True)

    def __repr__(self):
        return f"<VehicleProfile {self.plate}>"


class CertificateRecord(db.Model):
    __tablename__ = "certificate_records"

    id = db.Column(db.Integer, primary_key=True)
    certificate_key = db.Column(db.String(120), unique=True, index=True, nullable=False)
    plate = db.Column(db.String(20), index=True, nullable=False)
    certificate_type = db.Column(db.String(20), nullable=False, default="nuevo")
    pdf_filename = db.Column(db.String(255), unique=True, index=True)
    viewer_html_filename = db.Column(db.String(255), unique=True, index=True)
    index_html_filename = db.Column(db.String(255), unique=True, index=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicle_profiles.id"))
    party_id = db.Column(db.Integer, db.ForeignKey("parties.id"))
    inspection_date = db.Column(db.String(20))
    expiration_date = db.Column(db.String(20))
    acta_number = db.Column(db.String(50))
    inspection_number = db.Column(db.String(50))
    extracted_json = db.Column(db.Text)
    page_text = db.Column(db.Text)
    source_status = db.Column(db.String(20), nullable=False, default="pending")
    parse_notes = db.Column(db.Text)
    imported_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    vehicle = db.relationship("VehicleProfile", back_populates="certificates")
    party = db.relationship("Party", back_populates="certificates")

    def __repr__(self):
        return f"<CertificateRecord {self.certificate_key}>"


class GenerationAudit(db.Model):
    __tablename__ = "generation_audits"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    plate = db.Column(db.String(20), index=True)
    certificate_type = db.Column(db.String(20), nullable=False, default="nuevo")
    status = db.Column(db.String(20), nullable=False, default="success")
    message = db.Column(db.Text)
    pdf_filename = db.Column(db.String(255))
    index_url = db.Column(db.String(500))
    viewer_url = db.Column(db.String(500))
    remote_pdf_url = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    user = db.relationship("User", back_populates="generation_audits")

    def __repr__(self):
        return f"<GenerationAudit {self.plate} {self.certificate_type} {self.status}>"
