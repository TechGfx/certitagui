"""
Script para crear usuarios en la base de datos
Ejecuta: python crear_usuario.py
"""

import os

from app import app
from models import User, db


def crear_usuario():
    with app.app_context():
        # Asegurar que la carpeta instance existe
        os.makedirs("instance", exist_ok=True)

        # Crear las tablas si no existen
        db.create_all()
        print("✓ Base de datos inicializada")

        # Solicitar datos
        print("\n=== CREAR NUEVO USUARIO ===")
        username = input("Usuario: ")
        password = input("Contraseña: ")

        print("\nRoles disponibles:")
        print("1. operador (puede generar certificados)")
        print("2. admin (puede generar certificados y gestionar usuarios)")
        role_option = input("Selecciona rol (1 o 2): ")

        role = "admin" if role_option == "2" else "operador"

        # Verificar si el usuario ya existe
        if User.query.filter_by(username=username).first():
            print(f"\n❌ El usuario '{username}' ya existe")
            return

        # Crear usuario
        user = User(username=username, role=role)
        user.set_password(password)

        db.session.add(user)
        db.session.commit()

        print(f"\n✓ Usuario '{username}' creado exitosamente")
        print(f"  Rol: {role}")


def listar_usuarios():
    with app.app_context():
        users = User.query.all()

        if not users:
            print("\n❌ No hay usuarios registrados")
            return

        print("\n=== USUARIOS REGISTRADOS ===")
        for user in users:
            print(f"• {user.username} - Rol: {user.role}")


def menu():
    print("\n" + "=" * 40)
    print("  GESTIÓN DE USUARIOS")
    print("=" * 40)
    print("1. Crear usuario")
    print("2. Listar usuarios")
    print("3. Salir")
    print("=" * 40)

    opcion = input("\nSelecciona una opción: ")

    if opcion == "1":
        crear_usuario()
        menu()
    elif opcion == "2":
        listar_usuarios()
        menu()
    elif opcion == "3":
        print("\n¡Hasta luego!")
    else:
        print("\n❌ Opción inválida")
        menu()


if __name__ == "__main__":
    menu()
