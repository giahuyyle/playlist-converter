from sqlalchemy import create_engine, pool

from alembic import context
from app.core.config import config
from app.core.database import Base
from app.models import entities  # noqa: F401


def offline():
    context.configure(
        url=config.database_url, target_metadata=Base.metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()


def online():
    engine = create_engine(config.database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=Base.metadata, compare_type=True
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    offline()
else:
    online()
