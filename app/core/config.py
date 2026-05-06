import base64
import os

NC_URL = os.getenv("NC_URL", "https://portaltest.gcf.group")
OAUTH_CLIENT_ID = os.getenv("NC_OAUTH_CLIENT_ID", "")
OAUTH_CLIENT_SECRET = os.getenv("NC_OAUTH_CLIENT_SECRET", "")

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "activity_tracker")

DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

BUSINESS_TIMEZONE  = "America/New_York"
BUSINESS_HOUR_START = 8
BUSINESS_HOUR_END   = 17

# ── CalDAV / Calendar integration ────────────────────────────────────────────
# Base CalDAV URL for the user's principal. `{nc_user_id}` is interpolated.
# Nextcloud convention: /remote.php/dav/calendars/<user>/
CALDAV_USER_URL_TEMPLATE = os.getenv(
    "CALDAV_USER_URL_TEMPLATE",
    f"{NC_URL}/remote.php/dav/calendars/{{nc_user_id}}/",
)
# How to authenticate against CalDAV. See docs/calendar-auth-strategy.md.
#   - "bearer": pass the user's OAuth2 access_token in Authorization header.
#               Requires Nextcloud `allow_oauth2_in_caldav = true`.
#   - "app_password": Basic Auth with a per-user App Password (encrypted column).
CALDAV_AUTH_MODE = os.getenv("CALDAV_AUTH_MODE", "bearer").lower()
# AES-256-GCM master key for encrypting App Passwords at rest.
# Must be a base64-encoded 32-byte value, e.g.: openssl rand -base64 32
# Required (and validated below) when CALDAV_AUTH_MODE=app_password.
CALDAV_ENCRYPTION_KEY = os.getenv("CALDAV_ENCRYPTION_KEY", "")
# Decoded key bytes populated below; b"" in bearer mode (never used).
CALDAV_ENCRYPTION_KEY_BYTES: bytes = b""

if CALDAV_AUTH_MODE == "app_password":
    if not CALDAV_ENCRYPTION_KEY:
        raise SystemExit(
            "[config] CALDAV_ENCRYPTION_KEY must be set when CALDAV_AUTH_MODE=app_password. "
            "Generate with: openssl rand -base64 32"
        )
    try:
        CALDAV_ENCRYPTION_KEY_BYTES = base64.b64decode(CALDAV_ENCRYPTION_KEY)
        if len(CALDAV_ENCRYPTION_KEY_BYTES) != 32:
            raise ValueError(
                f"expected 32 bytes after base64-decode, got {len(CALDAV_ENCRYPTION_KEY_BYTES)}"
            )
    except Exception as _key_exc:
        raise SystemExit(f"[config] CALDAV_ENCRYPTION_KEY invalid: {_key_exc}") from _key_exc

# Maximum range a single /api/calendar/events call can request (days).
CALDAV_MAX_RANGE_DAYS = int(os.getenv("CALDAV_MAX_RANGE_DAYS", "200"))
# Hard timeout per CalDAV HTTP call in seconds.
CALDAV_TIMEOUT_S = float(os.getenv("CALDAV_TIMEOUT_S", "10.0"))

# ── Calendar cache TTLs (seconds) per view ──────────────────────────────────
CACHE_TTL_DAY      = int(os.getenv("CACHE_TTL_DAY",       "300"))
CACHE_TTL_WEEK     = int(os.getenv("CACHE_TTL_WEEK",      "300"))
CACHE_TTL_MONTH    = int(os.getenv("CACHE_TTL_MONTH",     "600"))
CACHE_TTL_QUARTER  = int(os.getenv("CACHE_TTL_QUARTER",   "900"))
CACHE_TTL_SEMESTER = int(os.getenv("CACHE_TTL_SEMESTER", "1800"))
# Below `STALE_THRESHOLD * TTL` ⇒ fresh (return immediately, no refresh).
# Between `STALE_THRESHOLD * TTL` and `TTL` ⇒ stale (return + refresh async).
# Above `TTL` ⇒ miss (block on fetch).
CACHE_STALE_THRESHOLD = float(os.getenv("CACHE_STALE_THRESHOLD", "0.7"))

# Redis connection (optional). When set, the calendar cache uses Redis;
# otherwise it falls back to a per-process in-memory TTL cache.
REDIS_URL = os.getenv("REDIS_URL", "")