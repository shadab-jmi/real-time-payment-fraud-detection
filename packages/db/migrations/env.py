from alembic import context

# The CLI owns the connection and transaction, including the migration lock.
connection = context.config.attributes["connection"]
context.configure(connection=connection)
with context.begin_transaction():
    context.run_migrations()
