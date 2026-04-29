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