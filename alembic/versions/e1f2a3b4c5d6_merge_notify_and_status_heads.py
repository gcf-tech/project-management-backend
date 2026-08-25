"""merge heads: notify_email + card status

Une las dos ramas que salen del branchpoint a7b8c9d0e1f3:
  - b8c9d0e1f2a3 (users.notify_email)
  - d0e1f2a3b4c5 (deck_cards.status, vía c3ce7a92f57a "add portafolios")
Sin cambios de esquema; solo unifica el historial en un único head.

Revision ID: e1f2a3b4c5d6
Revises: b8c9d0e1f2a3, d0e1f2a3b4c5
Create Date: 2026-08-06 13:00:00.000000

"""

revision = 'e1f2a3b4c5d6'
down_revision = ('b8c9d0e1f2a3', 'd0e1f2a3b4c5')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
