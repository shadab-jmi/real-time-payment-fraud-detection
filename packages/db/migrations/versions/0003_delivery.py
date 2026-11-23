from alembic import op

revision = "0003_delivery"
down_revision = "0002_evidence"


def upgrade():
    op.execute("CREATE INDEX outbox_pending_account ON outbox(topic,key) WHERE published_at IS NULL")
    op.execute("CREATE INDEX unresolved_transaction ON quarantine(transaction_id) WHERE resolved_at IS NULL")


def downgrade():
    op.execute("DROP INDEX unresolved_transaction")
    op.execute("DROP INDEX outbox_pending_account")
