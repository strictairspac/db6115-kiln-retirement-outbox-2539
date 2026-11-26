from src.models import audit, cases, retirement, reviews, runs, users  # noqa: F401  注册表
from src.models.base import metadata


def upgrade(connection):
    # Version 1 is additive and fully declarative.
    metadata.create_all(connection)
