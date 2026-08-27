"""
Migración one-off: crea las tablas del historial de conversación del asistente.

QUÉ HACE
    1. CREATE TABLE workspace_assistant_threads   (un hilo de conversación)
    2. CREATE TABLE workspace_assistant_messages  (un turno, con su hora)
    Cada paso solo se ejecuta si la tabla todavía no existe.

POR QUÉ ESTE SCRIPT Y NO `alembic upgrade`
    El contenedor de despliegue no trae alembic ni cliente de MySQL, y la BD vive
    en la red privada del proveedor. El único punto desde el que se alcanza es la
    terminal del panel de despliegue, que sí tiene intérprete de Python. La
    revisión de Alembic equivalente (a3c1d5e7f9b2) existe y es idempotente: si se
    corre después, se encuentra las tablas hechas y solo avanza la versión.

QUÉ **NO** HACE, Y ES DELIBERADO
    No toca `workspace_assistant_log`. Ese rastro es auditoría de solo escritura
    y no es el historial: mezclar los dos convertiría un registro que nadie puede
    editar en una lista que la persona puede borrar.
    No rellena nada hacia atrás. Las conversaciones anteriores a estas tablas
    vivían en memoria del navegador y ya no existen; inventarles filas sería
    fabricar un historial que nunca ocurrió.

IDEMPOTENTE
    Se puede correr las veces que haga falta. Cada CREATE se salta si la tabla ya
    está (se consulta information_schema.TABLES). No hay UPDATE sobre datos
    existentes, así que no existe el riesgo de aplicar dos veces.

USO
    Desde la raíz del repo, con el venv del backend:
        python scripts/migrate_hilos_asistente.py             # dry-run
        python scripts/migrate_hilos_asistente.py --commit    # escribe

    La conexión sale del .env del repo (DB_HOST, DB_PORT, DB_USER, DB_PASSWORD,
    DB_NAME); las variables de entorno del proceso tienen prioridad sobre él.

AVISO SOBRE EL DDL
    En MySQL un CREATE TABLE hace commit implícito: no se deshace con un
    rollback. Por eso el dry-run no lo ejecuta, en vez de ejecutarlo y revertirlo.
    Para deshacerlo hay que soltar las tablas a mano, y en este orden:
        DROP TABLE workspace_assistant_messages;
        DROP TABLE workspace_assistant_threads;
"""

import os
import sys
import argparse

import pymysql

# La consola de Windows (cp1252) no codifica emojis; forzamos UTF-8 en la salida.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TABLA_HILOS = "workspace_assistant_threads"
TABLA_MENSAJES = "workspace_assistant_messages"

_ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")


def load_env(path):
    """Lee un .env simple (KEY=VALUE) sin dependencias externas."""
    vals = {}
    if not os.path.exists(path):
        return vals
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


_ENV = load_env(_ENV_PATH)


def env(k, default=None):
    return os.getenv(k) or _ENV.get(k) or default


MYSQL_CONFIG = dict(
    host=env("DB_HOST", "localhost"),
    port=int(env("DB_PORT", "3306")),
    user=env("DB_USER", "root"),
    password=env("DB_PASSWORD", ""),
    database=env("DB_NAME", ""),
    charset="utf8mb4",
)

# El DDL va aquí y no en el flujo para que se pueda leer de un vistazo qué se
# crea exactamente. utf8mb4 explícito: en el contenido hay emojis dictados.
DDL_HILOS = f"""
CREATE TABLE {TABLA_HILOS} (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    usuario_id   INT NOT NULL,
    titulo       VARCHAR(120) NOT NULL,
    created_at   DATETIME NOT NULL,
    updated_at   DATETIME NOT NULL,
    CONSTRAINT fk_ws_asst_hilo_usuario FOREIGN KEY (usuario_id)
        REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_ws_asst_hilo_usuario (usuario_id, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

DDL_MENSAJES = f"""
CREATE TABLE {TABLA_MENSAJES} (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    hilo_id      INT NOT NULL,
    rol          ENUM('usuario','asistente') NOT NULL,
    contenido    TEXT NOT NULL,
    origen       ENUM('voz','texto') NOT NULL DEFAULT 'texto',
    created_at   DATETIME NOT NULL,
    CONSTRAINT fk_ws_asst_msg_hilo FOREIGN KEY (hilo_id)
        REFERENCES {TABLA_HILOS}(id) ON DELETE CASCADE,
    INDEX idx_ws_asst_msg_hilo (hilo_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def tabla_existe(cur, tabla):
    """information_schema en vez de un SHOW TABLES parseado a mano: devuelve un
    contador y no hay que interpretar la forma de la salida."""
    cur.execute(
        """
        SELECT COUNT(*) FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
        """,
        (MYSQL_CONFIG["database"], tabla),
    )
    return cur.fetchone()[0] > 0


def crear_si_falta(cur, tabla, ddl, seco):
    """Crea la tabla si no está. Devuelve 1 si la creó (o la habría creado)."""
    if tabla_existe(cur, tabla):
        print(f"   ya existe: {tabla}")
        return 0
    if seco:
        print(f"   [dry-run] crearía {tabla}")
        return 1
    cur.execute(ddl)
    print(f"   ✅ creada {tabla}")
    return 1


def main():
    ap = argparse.ArgumentParser(
        description="Crea las tablas del historial de conversación del asistente.",
    )
    ap.add_argument("--commit", action="store_true",
                    help="Escribe en MySQL (por defecto: dry-run)")
    args = ap.parse_args()
    seco = not args.commit

    # ===== VALIDACIONES DE ENTRADA =====
    if not MYSQL_CONFIG["database"]:
        print("❌ Falta DB_NAME (ni en el entorno ni en el .env). No se conecta a ciegas.")
        return 1

    # ===== CAMINO PRINCIPAL =====
    print("🧪 DRY-RUN (no escribe)\n" if seco else "🚀 COMMIT (escribe en MySQL)\n")
    print(f"📊 {MYSQL_CONFIG['user']}@{MYSQL_CONFIG['host']}:{MYSQL_CONFIG['port']}"
          f"/{MYSQL_CONFIG['database']}\n")

    conn = pymysql.connect(**MYSQL_CONFIG)
    creadas = 0
    try:
        with conn.cursor() as cur:
            if not tabla_existe(cur, "users"):
                print("❌ No existe la tabla `users`. Esta no es la BD del backend.")
                return 1
            print("1) Hilos")
            creadas += crear_si_falta(cur, TABLA_HILOS, DDL_HILOS, seco)
            print("2) Mensajes")
            # El orden importa: la FK de mensajes apunta a hilos.
            if seco and not tabla_existe(cur, TABLA_HILOS):
                print(f"   [dry-run] crearía {TABLA_MENSAJES} (después de los hilos)")
                creadas += 1
            else:
                creadas += crear_si_falta(cur, TABLA_MENSAJES, DDL_MENSAJES, seco)
        conn.commit()
    finally:
        conn.close()

    print(f"\n{'Se crearían' if seco else 'Creadas'}: {creadas} tabla(s)")
    if seco:
        print("Vuelve a correrlo con --commit para aplicarlo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
