import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.api.v1 import auth, tasks, metrics, teams, weekly, config_router, calendar, reports, commercial, assessment, deck, workspace, workspace_assistant, portafolios
from app.services.assistant_reminders import bucle_recordatorios
from app.services.deck_push import bucle_push_deck
from app.services.talk_push import bucle_talk_push


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[INFO] Activity Tracker API started")
    # Scheduler de recordatorios del asistente. Va aquí y no en el Express del
    # workspace, cuyo estado es volátil y se pierde en cada reinicio.
    tarea_recordatorios = asyncio.create_task(bucle_recordatorios())
    # Barrido de push nativo 'due_soon' del Deck (para que llegue con la app cerrada).
    tarea_deck_push = asyncio.create_task(bucle_push_deck())
    # Poller de push nativo de mensajes de Talk (con la app cerrada).
    tarea_talk_push = asyncio.create_task(bucle_talk_push())
    try:
        yield
    finally:
        # Sin cancelar, un reload deja los bucles huérfanos golpeando la BD.
        for t in (tarea_recordatorios, tarea_deck_push, tarea_talk_push):
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
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
        "http://localhost:5174",  # commercial-dashboard dev
        "http://localhost:5175",  # self-assessment dev
        "http://localhost:5176",  # deck dev
        "https://commercial-dash.gcf.group", # commercial-dashboard prod
        "https://self-assessment.gcf.group", # self-assessment prod
        "https://deck.gcf.group", # deck prod
        "http://localhost:3000",  # workspace (habbo) dev — servido por su Express
        "https://workspace.gcf.group",  # workspace prod (definir dominio real)
        "https://portfolios.gcf.group",  # portafolio prod (definir dominio real)
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Calendar payloads can grow large with many events; gzip cuts wire size
# substantially. 1 KB threshold avoids overhead on tiny responses.
app.add_middleware(GZipMiddleware, minimum_size=1024)

# Routers
app.include_router(auth.router,    prefix="/auth")
app.include_router(tasks.router,   prefix="/api/proyectos")
app.include_router(metrics.router, prefix="/api/dashboard")
app.include_router(teams.router,   prefix="/api")
app.include_router(weekly.router,       prefix="/api/weekly")
app.include_router(calendar.router,     prefix="/api/calendar")
app.include_router(config_router.router, prefix="/config")
app.include_router(reports.router,      prefix="/api/v1")
app.include_router(commercial.router,   prefix="/api/commercial")
app.include_router(assessment.router,   prefix="/api/assessment")
app.include_router(deck.router,         prefix="/api/decks")  # /api/deck is a legacy NC-proxy in teams.py
app.include_router(workspace.router,    prefix="/api/workspace")
# No se solapa con /api/workspace: aquel no define rutas "assistant/…"
app.include_router(workspace_assistant.router, prefix="/api/workspace/assistant")
app.include_router(portafolios.router,   prefix="/api/portafolios")

@app.get("/health")
async def root_health():
    return {"status": "ok"}