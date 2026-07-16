"""Migra un board del Deck de Nextcloud a un board de esta app.

Las columnas (stacks) de Nextcloud se convierten en las ETAPAS del board destino
(en el mismo orden). Cada card se importa en su etapa, con sus etiquetas
(labels→tags) y asignados (por usuario de Nextcloud).

Credenciales (contraseña de aplicación de Nextcloud, NO tu contraseña normal):
    export NC_MIGRATE_USER=tu_usuario_nc
    export NC_MIGRATE_APP_PASSWORD=xxxx-xxxxx-xxxxx        # Ajustes → Seguridad → Contraseña de aplicación

Uso:
    python -m scripts.migrate_nc_deck --source "MARKETING DECK" --target-team "Marketing" [--dry]
    python -m scripts.migrate_nc_deck --source "MARKETING DECK" --target-board 3 --keep-stages

Opciones:
    --source        Título del board en Nextcloud (obligatorio)
    --target-board  Id del board destino en la app  (o usa --target-team)
    --target-team   Nombre del equipo cuyo board es el destino
    --keep-stages   NO reemplaza las etapas: usa las existentes y mapea por nombre
                    (por defecto: las columnas de Nextcloud pasan a ser las etapas)
    --dry           Simula: no escribe nada, solo muestra lo que haría
"""
import os
import sys
import base64
import argparse

import httpx

from app.db.database import SessionLocal
from app.db import models as M
from app.core.config import NC_URL
from app.core.datetime_utils import utc_now

TAG_PALETTE = ["#F37022", "#1d2129", "#1f7a44", "#e0a11f", "#d64545", "#5a6473"]
STAGE_PALETTE = ["#8a93a3", "#5a6473", "#1c62b0", "#e0a11f", "#1f7a44", "#d64545", "#7a5ea6", "#F37022"]


def _nc_get(path, auth_header):
    r = httpx.get(f"{NC_URL}{path}", timeout=30.0, headers={
        "Authorization": auth_header, "OCS-APIREQUEST": "true", "Accept": "application/json",
    })
    r.raise_for_status()
    return r.json()


def _parse_iso(v):
    if not v:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _assigned_uids(card):
    out = []
    for au in (card.get("assignedUsers") or []):
        uid = (au.get("participant") or {}).get("uid") if isinstance(au.get("participant"), dict) else None
        uid = uid or au.get("uid")
        if uid:
            out.append(uid)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--target-board", type=int)
    ap.add_argument("--target-team")
    ap.add_argument("--keep-stages", action="store_true")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    user = os.getenv("NC_MIGRATE_USER")
    pw = os.getenv("NC_MIGRATE_APP_PASSWORD")
    if not user or not pw:
        sys.exit("Faltan NC_MIGRATE_USER / NC_MIGRATE_APP_PASSWORD en el entorno.")
    auth = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()

    # ── Nextcloud: encontrar el board y traer stacks + cards ──
    boards = _nc_get("/index.php/apps/deck/api/v1.0/boards", auth)
    src = next((b for b in boards if b.get("title", "").strip().lower() == args.source.strip().lower()), None)
    if not src:
        titles = ", ".join(b.get("title", "?") for b in boards)
        sys.exit(f"No encontré el board de Nextcloud '{args.source}'. Disponibles: {titles}")
    stacks = _nc_get(f"/index.php/apps/deck/api/v1.0/boards/{src['id']}/stacks", auth)
    stacks = sorted(stacks, key=lambda s: s.get("order", 0))
    print(f"[NC] Board '{src['title']}' (id {src['id']}) · {len(stacks)} columnas")

    db = SessionLocal()
    try:
        # ── Board destino ──
        board = None
        if args.target_board:
            board = db.query(M.DeckBoard).filter(M.DeckBoard.id == args.target_board).first()
        elif args.target_team:
            team = db.query(M.Team).filter(M.Team.name == args.target_team).first()
            if team:
                board = db.query(M.DeckBoard).filter(M.DeckBoard.team_id == team.id).first()
        if not board:
            sys.exit("No encontré el board destino (usa --target-board <id> o --target-team <nombre>).")
        print(f"[APP] Board destino: '{board.title}' (id {board.id}, equipo {board.team_id})")

        actor = (db.query(M.User).filter(M.User.nc_user_id == user).first()
                 or db.query(M.User).filter(M.User.deck_role == "admin").first()
                 or db.query(M.User).first())
        by_uid = {u.nc_user_id: u for u in db.query(M.User).all() if u.nc_user_id}

        existing = db.query(M.DeckColumn).filter(M.DeckColumn.board_id == board.id).order_by(M.DeckColumn.position).all()

        # ── Etapas ──
        if args.keep_stages:
            col_by_name = {c.title.strip().lower(): c for c in existing}
            fallback = existing[0] if existing else None
            print(f"[APP] Conservando etapas actuales ({len(existing)}). Cards sin match → '{fallback.title if fallback else '—'}'")
        else:
            # Las columnas de Nextcloud pasan a ser las etapas (en orden).
            col_by_name = {}
            for pos, st in enumerate(stacks):
                name = (st.get("title") or f"Etapa {pos+1}")[:100]
                col = next((c for c in existing if c.title.strip().lower() == name.strip().lower()), None)
                if col:
                    col.position = pos
                else:
                    col = M.DeckColumn(board_id=board.id, title=name, color=STAGE_PALETTE[pos % len(STAGE_PALETTE)],
                                       position=pos, is_default=False, default_minutes=60,
                                       created_at=utc_now(), updated_at=utc_now())
                    if not args.dry:
                        db.add(col); db.flush()
                col_by_name[name.strip().lower()] = col
            # Etapas viejas que no están en Nextcloud: borrar si están vacías, si no dejarlas al final
            keep_names = {(st.get("title") or "").strip().lower() for st in stacks}
            for pos, c in enumerate(existing):
                if c.title.strip().lower() in keep_names:
                    continue
                has_cards = db.query(M.DeckCard).filter(M.DeckCard.column_id == c.id).first() is not None
                if has_cards:
                    c.position = len(stacks) + pos
                    print(f"[APP] Etapa '{c.title}' no está en NC pero tiene cards → se conserva al final")
                elif not args.dry:
                    db.delete(c)
            fallback = None
            print(f"[APP] Etapas = columnas de Nextcloud: {[st.get('title') for st in stacks]}")

        # ── Tags existentes del board ──
        tags = {t.name.strip().lower(): t for t in db.query(M.DeckTag).filter(M.DeckTag.board_id == board.id).all()}

        created = 0
        skipped = 0
        for st in stacks:
            stack_name = (st.get("title") or "").strip().lower()
            col = col_by_name.get(stack_name) or fallback
            if not col:
                print(f"[skip] Columna '{st.get('title')}' sin etapa destino")
                continue
            for card in (st.get("cards") or []):
                if card.get("archived"):
                    skipped += 1
                    continue
                title = (card.get("title") or "").strip()
                if not title:
                    skipped += 1
                    continue
                nc_uids = _assigned_uids(card)
                labels = [l.get("title", "") for l in (card.get("labels") or []) if l.get("title")]
                print(f"  + [{st.get('title')}] {title[:60]}"
                      f"{'  asig:' + ','.join(nc_uids) if nc_uids else ''}"
                      f"{'  tags:' + ','.join(labels) if labels else ''}")
                if args.dry:
                    created += 1
                    continue

                dc = M.DeckCard(
                    board_id=board.id, column_id=col.id, owner_team_id=board.team_id,
                    title=title[:255], description=(card.get("description") or None),
                    due_date=_parse_iso(card.get("duedate")),
                    position=_next_pos(db, col.id),
                    created_by=actor.id if actor else None, created_at=utc_now(), updated_at=utc_now(),
                )
                if card.get("done"):
                    dc.completed_at = _parse_iso(card.get("done")) or utc_now()
                db.add(dc); db.flush()

                db.add(M.DeckCardTeam(card_id=dc.id, team_id=board.team_id, is_owner=True,
                                      shared_by=actor.id if actor else None, created_at=utc_now()))
                follower_ids = set()
                if actor:
                    follower_ids.add(actor.id)
                for uid in nc_uids:
                    u = by_uid.get(uid)
                    if u:
                        db.add(M.DeckCardAssignee(card_id=dc.id, user_id=u.id,
                                                  assigned_by=actor.id if actor else None, created_at=utc_now()))
                        follower_ids.add(u.id)
                for fid in follower_ids:
                    db.add(M.DeckCardFollower(card_id=dc.id, user_id=fid, created_at=utc_now()))
                for name in labels:
                    tag = tags.get(name.strip().lower())
                    if not tag:
                        tag = M.DeckTag(board_id=board.id, name=name[:60],
                                        color=TAG_PALETTE[len(tags) % len(TAG_PALETTE)], created_at=utc_now())
                        db.add(tag); db.flush()
                        tags[name.strip().lower()] = tag
                    db.add(M.DeckCardTag(card_id=dc.id, tag_id=tag.id, created_at=utc_now()))
                created += 1

        if args.dry:
            db.rollback()
            print(f"\n[DRY] Se crearían {created} cards · {skipped} omitidas. (nada persistido)")
        else:
            db.commit()
            print(f"\n[OK] {created} cards importadas · {skipped} omitidas.")
    finally:
        db.close()


def _next_pos(db, column_id):
    from sqlalchemy import func
    n = db.query(func.coalesce(func.max(M.DeckCard.position), -1)).filter(M.DeckCard.column_id == column_id).scalar()
    return (n or -1) + 1


if __name__ == "__main__":
    main()
