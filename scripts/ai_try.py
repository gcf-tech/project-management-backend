"""Prueba manual del planificador IA sin pasar por la API ni la autenticación.
NO escribe nada en la base de datos: solo lee el contexto y llama al modelo.

Uso (desde la raíz del backend, con el venv activo y el .env configurado):

    python -m scripts.ai_try --boards
        Lista los tableros (id, título, equipo, nº de tarjetas, nº de proyectos
        con subtareas) para elegir uno.

    python -m scripts.ai_try --board 3 --context
        Muestra el contexto que recibiría la IA para el tablero 3 (miembros con
        historial, proyectos anteriores, columnas) tal como se renderiza en el prompt.

    python -m scripts.ai_try --board 3 --text "Descripción del proyecto..."
        Genera una propuesta con contexto completo y la imprime.

    python -m scripts.ai_try --board 3 --text "..." --no-context
        Igual, pero en modo sin contexto (experimento de control).

    python -m scripts.ai_try --board 3 --text "..." --json salida.json
        Además guarda la propuesta y el contexto en un JSON (para el documento).
"""
import argparse
import asyncio
import json
import sys

from sqlalchemy import func

from app.db.database import SessionLocal
from app.db.models import DeckBoard, DeckCard, Team
from app.services.ai_context import build_board_context, summarize_context_for_prompt
from app.services import ai_planner
from app.services.ai_svc import AIError
from app.core import config


def list_boards(db):
    rows = (db.query(DeckBoard, Team.name)
            .outerjoin(Team, Team.id == DeckBoard.team_id)
            .order_by(DeckBoard.id).all())
    print(f"{'id':>4}  {'tarjetas':>8}  {'proyectos':>9}  equipo / tablero")
    for board, team_name in rows:
        n_cards = db.query(func.count(DeckCard.id)).filter(
            DeckCard.board_id == board.id, DeckCard.archived.is_(False)).scalar()
        n_parents = (db.query(func.count(func.distinct(DeckCard.parent_card_id)))
                     .filter(DeckCard.board_id == board.id, DeckCard.parent_card_id.isnot(None)).scalar())
        print(f"{board.id:>4}  {n_cards:>8}  {n_parents:>9}  {team_name or '-'} / {board.title}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--boards", action="store_true", help="listar tableros")
    ap.add_argument("--board", type=int, help="id del tablero")
    ap.add_argument("--context", action="store_true", help="mostrar el contexto renderizado")
    ap.add_argument("--text", help="descripción del proyecto a planificar")
    ap.add_argument("--no-context", action="store_true", help="modo sin historial (control)")
    ap.add_argument("--json", help="guardar propuesta + contexto en este archivo")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        if args.boards or not args.board:
            list_boards(db)
            if not args.board:
                return
        board = db.query(DeckBoard).filter(DeckBoard.id == args.board).first()
        if not board:
            print(f"Tablero {args.board} no existe"); sys.exit(1)

        ctx = build_board_context(db, board, include_history=not args.no_context)
        print(f"\n== Tablero {board.id}: {board.title} | miembros en contexto: {len(ctx['members'])}"
              f" | proyectos históricos: {len(ctx['history_projects'])} | modo: {ctx['context_mode']}")
        if args.context or not args.text:
            print("\n----- CONTEXTO RENDERIZADO PARA EL PROMPT -----")
            print(summarize_context_for_prompt(ctx))
            print("-----------------------------------------------")
        if not args.text:
            return

        if not config.AI_ENABLED:
            print("OPENAI_API_KEY no configurada en .env"); sys.exit(1)
        print(f"\nLlamando a {config.AI_PROVIDER}/{config.AI_MODEL} ...")
        try:
            gen = await ai_planner.generate_plan(ctx, args.text)
        except AIError as e:
            print(f"ERROR IA: {e}"); sys.exit(2)

        p = gen["proposal"]
        name_of = {m["id"]: m["name"] for m in ctx["members"]}
        print(f"\n== PROPUESTA ({gen['model']}, tokens {gen['usage']['prompt_tokens']}+{gen['usage']['completion_tokens']})")
        print(f"Proyecto: {p['project']['title']}\n  {p['project']['description']}\n")
        for t in p["tasks"]:
            who = name_of.get(t["suggested_assignee_id"], "SIN ASIGNAR")
            print(f"[{t['id']}] {t['title']}  →  {who}  | {t['estimated_hours']} h | confianza {t['confidence']}"
                  + (f" | ref proyecto #{t['reference_project_id']}" if t.get("reference_project_id") else ""))
            if t.get("description"):
                print(f"      {t['description']}")
            if t.get("assignee_reasoning"):
                print(f"      ↳ {t['assignee_reasoning']}")
            if t.get("depends_on"):
                print(f"      depende de: {', '.join(t['depends_on'])}")
        if p.get("assumptions"):
            print("\nSupuestos:"); [print("  -", a) for a in p["assumptions"]]
        if p.get("risks"):
            print("Riesgos:"); [print("  -", r) for r in p["risks"]]
        if gen["warnings"]:
            print("\nAjustes de la validación programática:"); [print("  !", w) for w in gen["warnings"]]

        if args.json:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump({"board_id": board.id, "input_text": args.text, "context": ctx,
                           "proposal": p, "warnings": gen["warnings"], "model": gen["model"],
                           "usage": gen["usage"]}, f, ensure_ascii=False, indent=2)
            print(f"\nGuardado en {args.json}")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
