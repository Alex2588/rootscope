"""Declarative base shared by every ORM model.

Alembic's env.py imports Base.metadata as its autogenerate target — every model must be
imported somewhere reachable from there (see app/db/models/__init__.py) or autogenerate
silently won't see it.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
