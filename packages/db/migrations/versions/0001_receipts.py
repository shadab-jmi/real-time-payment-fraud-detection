from pathlib import Path

from alembic import op

revision = "0001_receipts"
down_revision = None


def upgrade():
    sql = Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql, execution_options={"no_parameters": True})


def downgrade():
    raise RuntimeError("Restore a backup to revert durable receipt storage.")
