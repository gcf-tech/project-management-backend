"""Limpia el artefacto de migración: el operador (p.ej. jflorez) quedó como
follower de TODA card importada. Quita sus follows en las cards que él figura
como creador (created_by) y donde NO es asignado real. No toca follows legítimos
(cards donde está asignado, ni cards que no creó él).

Uso:
    PYTHONPATH=. python scripts/cleanup_migration_followers.py --user jflorez --dry
    PYTHONPATH=. python scripts/cleanup_migration_followers.py --user jflorez        # aplica
"""
import argparse
import sys

from app.db.database import SessionLocal
from app.db import models as M


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True, help="nc_user_id del operador de la migración")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        u = db.query(M.User).filter(M.User.nc_user_id == args.user).first()
        if not u:
            sys.exit(f"No encontré al usuario nc_user_id={args.user}")

        # Cards que "creó" (created_by) y donde NO es asignado.
        assigned_ids = {r[0] for r in db.query(M.DeckCardAssignee.card_id)
                        .filter(M.DeckCardAssignee.user_id == u.id).all()}
        created_ids = {r[0] for r in db.query(M.DeckCard.id)
                       .filter(M.DeckCard.created_by == u.id).all()}
        target_cards = created_ids - assigned_ids

        rows = db.query(M.DeckCardFollower).filter(
            M.DeckCardFollower.user_id == u.id,
            M.DeckCardFollower.card_id.in_(target_cards or [0]),
        ).all()

        # Desglose por board (para revisar).
        from collections import Counter
        by_board = Counter()
        for r in rows:
            c = db.query(M.DeckCard.board_id).filter(M.DeckCard.id == r.card_id).scalar()
            by_board[c] += 1
        print(f"{u.display_name} (id={u.id})")
        print(f"  cards que creó={len(created_ids)} · donde está asignado={len(assigned_ids)}")
        print(f"  follows a quitar={len(rows)}")
        for bid, n in by_board.items():
            bt = db.query(M.DeckBoard.title).filter(M.DeckBoard.id == bid).scalar()
            print(f"    board {bid} ({bt}): -{n}")

        if args.dry:
            print("[DRY] nada borrado.")
            return
        for r in rows:
            db.delete(r)
        db.commit()
        print(f"[OK] {len(rows)} follows eliminados.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
