"""
Inicializa la base de datos con las tablas y un usuario admin
"""

import os

from app import app
from models import User, db


def init_db():
    with app.app_context():
        print("Creando tablas en la base de datos...")
        db.create_all()
        print("✓ Tablas creadas")

        # Verificar si ya existe algún usuarioo
        if User.query.first() is None:
            # Crear usuario admin por defecto
            admin_username = os.environ.get("ADMIN_USER", "lauvasco518")
            admin_password = os.environ.get("ADMIN_PASSWORD", "0518lau")

            admin = User(username=admin_username, role="admin")
            admin.set_password(admin_password)

            db.session.add(admin)
            db.session.commit()

            print(f"✓ Usuario admin creado")
            print(f"  Usuario: {admin_username}")
            print(f"  Contraseña: {admin_password}")
            print("  ⚠️  CAMBIA LA CONTRASEÑA DESPUÉS DEL PRIMER LOGIN")
        else:
            print("✓ Base de datos ya tiene usuarios")


if __name__ == "__main__":
    init_db()
