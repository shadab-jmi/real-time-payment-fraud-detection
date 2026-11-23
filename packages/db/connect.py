from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

# libpq reads PGHOST, PGDATABASE, PGUSER and PGPASSWORD from the environment.
engine = create_engine("postgresql+psycopg://", connect_args={"connect_timeout": 10})


def upgrade(connection, revision="head"):
    # Serialize even the first creation of the Alembic revision table.
    connection.exec_driver_sql("SELECT pg_advisory_xact_lock(715301)")
    config = Config("alembic.ini")
    config.attributes["connection"] = connection
    command.upgrade(config, revision)
    return connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
