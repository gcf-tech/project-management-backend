from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import auth, tasks, metrics, teams, weekly


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[INFO] Activity Tracker API started")
    yield
    print("[INFO] Activity Tracker API shutting down")


app = FastAPI(
    title="Activity Tracker API",
    version="4.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://portal.gcf.group",
        "https://portaltest.gcf.group",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth.router,    prefix="/auth")
app.include_router(tasks.router,   prefix="/api/proyectos")
app.include_router(metrics.router, prefix="/api/dashboard")
app.include_router(teams.router,   prefix="/api")
app.include_router(weekly.router,  prefix="/api/weekly")


@app.get("/health")
async def root_health():
    return {"status": "ok"}