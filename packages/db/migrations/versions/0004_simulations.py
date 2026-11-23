from alembic import op

revision = "0004_simulations"
down_revision = "0003_delivery"


def upgrade():
    op.execute("""CREATE TABLE simulations (
        id uuid PRIMARY KEY REFERENCES runs(id), owner text NOT NULL, peer text NOT NULL,
        scenario text NOT NULL, target integer NOT NULL, rate integer NOT NULL,
        status text NOT NULL, started timestamptz NOT NULL DEFAULT now(), ended timestamptz
    )""")
    op.execute("CREATE UNIQUE INDEX one_running_simulation ON simulations(status) WHERE status='Running'")
    op.execute("CREATE INDEX simulation_starts ON simulations(started)")
    op.execute("GRANT SELECT, INSERT, UPDATE ON simulations TO fraud_runtime")


def downgrade():
    op.execute("DROP TABLE simulations")
