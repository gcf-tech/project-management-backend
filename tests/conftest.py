import sys
from unittest.mock import patch

# Patch DATABASE_URL before any app module is imported so the engine
# created in app/db/database.py uses SQLite in-memory instead of MySQL.
_patcher = patch.dict(
    "os.environ",
    {"DATABASE_URL": "sqlite:///:memory:"},
)
_patcher.start()

# Also patch the config value directly in case it was already read.
import app.core.config as _cfg
_cfg.DATABASE_URL = "sqlite:///:memory:"

import app.db.database as _db
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_db.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
_db.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_db.engine)
