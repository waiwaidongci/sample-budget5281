import os

from fastapi.testclient import TestClient
import pytest

from src.config.database import Base, Session, engine
from src.models.user import User
from src.utils.security import hash_password


class FakeRedis:
    def incr(self, key): return 1
    def expire(self, key, ttl): return True
    def rpush(self, *args): return 1
    def blpop(self, *args, **kwargs): return None


@pytest.fixture()
def client(monkeypatch):
    import src.middlewares.rate_limit as rate_limit
    import src.services.clusters as clusters_service
    monkeypatch.setattr(rate_limit, 'redis', FakeRedis())
    monkeypatch.setattr(clusters_service, 'Redis', None, raising=False)
    Base.metadata.drop_all(engine); Base.metadata.create_all(engine)
    with Session() as db:
        db.add(User(email='admin@demo.local', role='admin', password_hash=hash_password('Research2026!')))
        db.add(User(email='viewer@demo.local', role='viewer', password_hash=hash_password('Research2026!')))
        db.commit()
    from src.main import app
    with TestClient(app) as test_client:
        yield test_client


def token(client, email):
    response = client.post('/auth/login', json={'email': email, 'password': 'Research2026!'})
    assert response.status_code == 200, response.text
    return {'Authorization': 'Bearer ' + response.json()['token']}


def create(client, headers, name='家庭研究', budget=3000):
    return client.post('/cluster-studies', headers=headers, json={
        'name': name, 'source_filename': 'roster.csv', 'description': 'api test',
        'p0': .08, 'p1': .12, 'cost_per_contact': .12, 'budget': budget,
        'icc': .15, 'simulations': 200,
    })


def roster(client, headers, study_id):
    members = [{'source_key': f'H{h}-{m}', 'household_key': f'H{h}', 'stratum': '层A' if h < 6 else '层B'}
               for h in range(12) for m in range(1 + h % 3)]
    return client.post(f'/cluster-studies/{study_id}/members/import', headers=headers, json={'members': members})


def test_http_flow_publish_retry_and_conflict(client):
    headers = token(client, 'admin@demo.local')
    first = create(client, headers, '甲研究').json()
    second = create(client, headers, '乙研究').json()
    assert roster(client, headers, first['id']).status_code == 200
    assert roster(client, headers, second['id']).status_code == 200

    generated = client.post(f"/cluster-studies/{first['id']}/plans", headers=headers, json={})
    assert generated.status_code == 200, generated.text
    plan = generated.json()
    assert plan['feasible'] is True

    published = client.post(f"/cluster-plans/{plan['id']}/publish", headers=headers)
    assert published.status_code == 200
    assert published.json()['already_published'] is False
    claims_after_first = len(client.get('/cluster-claims', headers=headers).json())

    retried = client.post(f"/cluster-plans/{plan['id']}/publish", headers=headers)
    assert retried.status_code == 200
    assert retried.json()['already_published'] is True
    assert len(client.get('/cluster-claims', headers=headers).json()) == claims_after_first

    rival = client.post(f"/cluster-studies/{second['id']}/plans", headers=headers, json={}).json()
    assert rival['feasible'] is False
    blocked = client.post(f"/cluster-plans/{rival['id']}/publish", headers=headers)
    assert blocked.status_code == 409

    run = client.post('/cluster-runs', headers=headers, json={'plan_id': plan['id'], 'name': '冻结核算'})
    assert run.status_code == 200
    assert run.json()['snapshot']['roster_version'] == plan['roster_version']
    assert run.json()['snapshot']['roster_checksum'] == plan['snapshot']['roster_checksum']


def test_roster_update_stales_draft_but_published_plan_keeps_basis(client):
    headers = token(client, 'admin@demo.local')
    study = create(client, headers, '交错研究').json()
    roster(client, headers, study['id'])
    draft = client.post(f"/cluster-studies/{study['id']}/plans", headers=headers, json={}).json()
    assert draft['feasible'] is True

    added = client.post(f"/cluster-studies/{study['id']}/members", headers=headers,
                        json={'source_key': 'NEW-1', 'household_key': 'NEW', 'stratum': '层A'})
    assert added.status_code == 200
    plans = client.get(f"/cluster-studies/{study['id']}/plans", headers=headers).json()
    assert any(x['status'] == 'stale' for x in plans)
    assert all(x['status'] != 'draft' or x['version'] > draft['version'] for x in plans)
    stale = next(x for x in plans if x['status'] == 'stale')
    assert stale['version'] == draft['version']
    refreshed = client.get(f"/cluster-studies/{study['id']}", headers=headers).json()
    assert refreshed['roster_version'] == draft['roster_version'] + 1


def test_viewer_cannot_write_and_anonymous_rejected(client):
    headers = token(client, 'admin@demo.local')
    viewer = token(client, 'viewer@demo.local')
    study = create(client, headers, '只读研究').json()
    assert client.get('/cluster-studies', headers={}).status_code == 401
    assert client.get('/cluster-studies', headers=viewer).status_code == 200
    assert create(client, viewer, '越权研究').status_code == 403
    assert client.post(f"/cluster-studies/{study['id']}/members", headers=viewer,
                       json={'source_key': 'A', 'household_key': 'H', 'stratum': '层A'}).status_code == 403


def test_invalid_probabilities_and_parameters_rejected(client):
    headers = token(client, 'admin@demo.local')
    same = {'name': '同概率', 'source_filename': 'r.csv', 'p0': .1, 'p1': .1,
            'cost_per_contact': .12, 'budget': 100, 'icc': .15, 'simulations': 200}
    assert client.post('/cluster-studies', headers=headers, json=same).status_code == 422
    out_of_range = same | {'name': '越界', 'p0': .08, 'p1': 1.2}
    assert client.post('/cluster-studies', headers=headers, json=out_of_range).status_code == 422
    payload = {'name': '坏ICC', 'source_filename': 'r.csv', 'p0': .08, 'p1': .12,
               'cost_per_contact': .12, 'budget': 100, 'icc': 1.2, 'simulations': 200}
    assert client.post('/cluster-studies', headers=headers, json=payload).status_code == 422
