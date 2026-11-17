import os
import tempfile

import pytest

os.environ.setdefault('DATABASE_URL', 'sqlite:///' + os.path.join(tempfile.mkdtemp(prefix='cluster-tests-'), 'test.db'))
os.environ.setdefault('REDIS_URL', 'redis://localhost:6379/0')
os.environ.setdefault('JWT_SECRET', 'test-secret')

from src.config.database import Base, Session, engine  # noqa: E402
from src.models import user, study, observation, run, review, audit, cluster  # noqa: E402


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session() as session:
        yield session


class Actor:
    def __init__(self, email='admin@demo.local'):
        self.email = email


@pytest.fixture()
def admin():
    return Actor('admin@demo.local')
