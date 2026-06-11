"""Add actor_id and driver_id to memory_nodes.

Revision ID: 019_add_actor_driver_id
Revises: 018_add_content_hash
Create Date: 2026-06-11
"""

import sqlalchemy as sa

from alembic import op

revision = "019_add_actor_driver_id"
down_revision = "018_add_content_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memory_nodes",
        sa.Column("actor_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "memory_nodes",
        sa.Column("driver_id", sa.String(255), nullable=True),
    )
    op.create_index("ix_memory_nodes_actor_id", "memory_nodes", ["actor_id"])
    op.create_index("ix_memory_nodes_driver_id", "memory_nodes", ["driver_id"])

    op.execute(
        "UPDATE memory_nodes SET actor_id = owner_id, driver_id = owner_id "
        "WHERE actor_id IS NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_memory_nodes_driver_id", "memory_nodes")
    op.drop_index("ix_memory_nodes_actor_id", "memory_nodes")
    op.drop_column("memory_nodes", "driver_id")
    op.drop_column("memory_nodes", "actor_id")
