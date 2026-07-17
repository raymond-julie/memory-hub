"""Add reconciliation_decisions table for dreaming pipeline audit trail.

Records every create/update/skip decision made during extraction
reconciliation. Enables threshold tuning from data and provides the
rollback surface for extraction runs.

Part of #347 (stage contracts + reconciliation).

Revision ID: 025_add_reconciliation_decisions
Revises: 024_add_search_vector
Create Date: 2026-07-16
"""

from alembic import op

revision = "025_add_reconciliation_decisions"
down_revision = "024_add_search_vector"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE reconciliation_decisions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            extraction_run_id TEXT NOT NULL,
            candidate_content TEXT NOT NULL,
            candidate_stub TEXT NOT NULL,
            nearest_match_id UUID REFERENCES memory_nodes(id) ON DELETE SET NULL,
            similarity_score DOUBLE PRECISION,
            action TEXT NOT NULL,
            tiebreaker_verdict TEXT,
            content_type_match BOOLEAN,
            domain_match BOOLEAN,
            memory_id UUID REFERENCES memory_nodes(id) ON DELETE SET NULL,
            reason TEXT,
            owner_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            scope TEXT NOT NULL,
            scope_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_recon_decisions_run ON reconciliation_decisions(extraction_run_id)"
    )
    op.execute(
        "CREATE INDEX ix_recon_decisions_tenant ON reconciliation_decisions(tenant_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_recon_decisions_tenant")
    op.execute("DROP INDEX IF EXISTS ix_recon_decisions_run")
    op.execute("DROP TABLE IF EXISTS reconciliation_decisions")
