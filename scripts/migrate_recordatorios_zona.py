"""
Migración one-off: añade `zona_horaria` a `workspace_assistant_reminders`.

QUÉ HACE
    1. ALTER TABLE ... ADD COLUMN zona_horaria VARCHAR(64) NOT NULL
       DEFAULT 'America/Bogota'   (solo si la columna todavía no existe)
    2. Saneo: rellena con 'America/Bogota' las filas que hayan quedado con la
       columna en NULL o en cadena vacía.

QUÉ **NO** HACE, Y ES DELIBERADO
    No toca `vence_en`. Ni una operación aritmética, ni una conversión de zona.
    Las filas existentes YA están en UTC: `ensure_aware_utc()` está en el POST
    desde el commit que creó estas tablas, y esa función rechaza con 422 cualquier
    datetime sin offset, así que nunca pudo entrar una hora local sin convertir.
    Convertirlas "de Bogotá a UTC" las correría +5 horas y rompería todos los
    recordatorios pendientes. Lo que les falta es la ZONA, no la conversión.

    Tampoco toca el scheduler ni sus índices. La columna nueva es un metadato: no
    entra en el barrido, que compara UTC contra UTC.

IDEMPOTENTE
    Se puede correr las veces que haga falta. El paso 1 se salta si la columna ya
    existe (se consulta information_schema.COLUMNS) y el paso 2 solo escribe sobre
    filas sin valor. Como no hay aritmética sobre ninguna fecha, no existe el
    riesgo clásico de "convertir dos veces".

USO
    Desde la raíz del repo, con el venv del backend:
        python scripts/migrate_recordatorios_zona.py             # dry-run
        python scripts/migrate_recordatorios_zona.py --commit    # escribe

    La conexión sale del .env del repo (DB_HOST, DB_PORT, DB_USER, DB_PASSWORD,
    DB_NAME); las variables de entorno del proceso tienen prioridad sobre él, que
    es lo que hace falta para correrlo apuntando a Railway.

AVISO SOBRE EL DDL
    En MySQL un ALTER TABLE hace commit implícito: no se puede deshacer con un
    rollback. Por eso el dry-run no lo ejecuta, en vez de ejecutarlo y revertirlo.
    Para deshacerlo hay que soltar la columna a mano:
        ALTER TABLE workspace_assistant_reminders DROP COLUMN zona_horaria;
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

TABLA = "workspace_assistant_reminders"
COLUMNA = "zona_horaria"
ZONA_POR_DEFECTO = "America/Bogota"

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
    # Mismo charset que la app (app/core/config.py): sin él pymysql no garantiza
    # UTF-8 y un nombre de zona no lo necesita, pero el resto de la tabla sí.
    charset="utf8mb4",
)


def columna_existe(cur):
    """information_schema en vez de un SHOW COLUMNS parseado a mano: devuelve un
    contador y no hay que interpretar la forma de la salida."""
    cur.execute(
        """
        SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s
        """,
        (MYSQL_CONFIG["database"], TABLA, COLUMNA),
    )
    return cur.fetchone()[0] > 0


def tabla_existe(cur):
    cur.execute(
        """
        SELECT COUNT(*) FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
        """,
        (MYSQL_CONFIG["database"], TABLA),
    )
    return cur.fetchone()[0] > 0


def main():
    ap = argparse.ArgumentParser(
        description="Añade zona_horaria a workspace_assistant_reminders. No toca vence_en.",
    )
    ap.add_argument("--commit", action="store_true",
                    help="Escribe en MySQL (por defecto: dry-run)")
    args = ap.parse_args()
    DRY = not args.commit

    if not MYSQL_CONFIG["database"]:
        print("❌ Falta DB_NAME (ni en el entorno ni en el .env). No se conecta a ciegas.")
        return 1

    print("🧪 DRY-RUN (no escribe)\n" if DRY else "🚀 COMMIT (escribe en MySQL)\n")
    print(f"📊 {MYSQL_CONFIG['user']}@{MYSQL_CONFIG['host']}:{MYSQL_CONFIG['port']}"
          f"/{MYSQL_CONFIG['database']}\n")

    conn = pymysql.connect(**MYSQL_CONFIG)
    cur = conn.cursor()
    stats = {"columna_creada": 0, "filas_saneadas": 0, "filas_totales": 0}

    try:
        if not tabla_existe(cur):
            print(f"❌ La tabla {TABLA} no existe en esta base. ¿Base equivocada?")
            return 1

        cur.execute(f"SELECT COUNT(*) FROM {TABLA}")
        stats["filas_totales"] = cur.fetchone()[0]
        print(f"   filas en {TABLA}: {stats['filas_totales']}")

        # ---- Paso 1: la columna ----
        ya_estaba = columna_existe(cur)
        if ya_estaba:
            print(f"   ✅ La columna {COLUMNA} ya existe: no se vuelve a añadir.")
        else:
            ddl = (
                f"ALTER TABLE {TABLA} "
                f"ADD COLUMN {COLUMNA} VARCHAR(64) NOT NULL "
                f"DEFAULT '{ZONA_POR_DEFECTO}'"
            )
            print(f"   ➕ Falta la columna. DDL:\n      {ddl}")
            # MySQL rellena las filas existentes con el DEFAULT al añadir la
            # columna, así que no hace falta un UPDATE detrás para el caso normal.
            if not DRY:
                cur.execute(ddl)
                stats["columna_creada"] = 1
                print(f"      ✅ columna añadida ({stats['filas_totales']} filas "
                      f"heredan '{ZONA_POR_DEFECTO}')")

        # ---- Paso 2: saneo ----
        # Solo tiene sentido si la columna existe de verdad. En un dry-run que
        # todavía no la ha creado no hay nada que contar, y consultarla sería un
        # error de SQL: se dice y se sigue.
        if ya_estaba or not DRY:
            cur.execute(
                f"SELECT COUNT(*) FROM {TABLA} WHERE {COLUMNA} IS NULL OR {COLUMNA} = ''"
            )
            pendientes = cur.fetchone()[0]
            if pendientes:
                print(f"   🧹 {pendientes} fila(s) sin zona → '{ZONA_POR_DEFECTO}'")
                if not DRY:
                    cur.execute(
                        f"UPDATE {TABLA} SET {COLUMNA} = %s "
                        f"WHERE {COLUMNA} IS NULL OR {COLUMNA} = ''",
                        (ZONA_POR_DEFECTO,),
                    )
                    stats["filas_saneadas"] = cur.rowcount
            else:
                print("   ✅ Ninguna fila sin zona.")
        else:
            print(f"   ⏭  Saneo no evaluado: la columna aún no existe en esta base.")

        # ---- Comprobación de que vence_en sigue intacto ----
        # No es decorativo: es la afirmación central de esta migración, y conviene
        # que quede en la salida por si alguien la revisa después.
        cur.execute(f"SELECT MIN(vence_en), MAX(vence_en) FROM {TABLA}")
        lo, hi = cur.fetchone()
        print(f"\n   🔒 vence_en NO se toca. Rango actual (UTC): {lo} … {hi}")

        if not DRY:
            conn.commit()
    finally:
        cur.close()
        conn.close()

    print("\n📋 Resumen:")
    for k, v in stats.items():
        print(f"   {k:16} {v}")
    print("\n" + ("🧪 Dry-run: nada se escribió. Repite con --commit."
                  if DRY else "✅ Migración aplicada."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
