import math

import pytest
from fastapi import HTTPException

from src.schemas.cluster import AudienceMemberInput, ClusterRunInput, HouseholdStudyInput, PlanGenerateInput
from src.services import cluster_algorithm, clusters
from src.utils.serializers import checksum

BASE = dict(source_filename='roster.csv', p0=.08, p1=.12, cost_per_contact=.12, budget=3000, icc=.15, simulations=200)


def make_study(db, name, **overrides):
    return clusters.create_study(db, HouseholdStudyInput(name=name, **(BASE | overrides)), db_actor())


def db_actor():
    class Actor:
        email = 'admin@demo.local'
    return Actor()


def fill(db, study_id, layout):
    """layout: [(household, stratum, size), ...] -> rows added through the service."""
    n = 0
    for household, stratum, size in layout:
        for _ in range(size):
            n += 1
            clusters.add_member(db, study_id, AudienceMemberInput(
                source_key=f'{household}-{n}', household_key=household, stratum=stratum), db_actor())


def plan(db, study_id, **params):
    return clusters.generate_plan(db, study_id, PlanGenerateInput(**params), db_actor())


def fresh_db():
    from src.config.database import Base, Session, engine
    Base.metadata.drop_all(engine); Base.metadata.create_all(engine)
    return Session()


def test_same_household_never_split_and_stratum_balance():
    with fresh_db() as db:
        s = make_study(db, '均衡')
        layout = [(f'H{i}', '高活跃' if i < 8 else '低活跃', [1, 2, 3, 4][i % 4]) for i in range(16)]
        fill(db, s['id'], layout)
        result = plan(db, s['id'])
        assert result['feasible'] is True
        seen = {}
        for item in result['allocations']:
            assert seen.setdefault(item['household_key'], item['group']) == item['group']
            assert item['size'] == layout[int(item['household_key'][1:])][2]
        by_stratum = {}
        for item in result['allocations']:
            bucket = by_stratum.setdefault(item['stratum'], {'A': 0, 'B': 0, 'max': 0})
            bucket[item['group']] += item['size']
            bucket['max'] = max(bucket['max'], item['size'])
        for name, bucket in by_stratum.items():
            assert abs(bucket['A'] - bucket['B']) <= bucket['max'], name


def test_single_household_stratum_is_infeasible_without_splitting():
    with fresh_db() as db:
        s = make_study(db, '孤单层')
        fill(db, s['id'], [('A1', '主层', 2), ('A2', '主层', 2), ('B1', '边层', 3)])
        result = plan(db, s['id'])
        assert result['feasible'] is False
        assert '边层' in result['reason']
        with pytest.raises(HTTPException) as exc:
            clusters.publish_plan(db, result['id'], db_actor())
        assert exc.value.status_code == 409
        # 主层仍按整户分流，边层整户原样不动，不被拆开凑数。
        assert {x['household_key'] for x in result['allocations']} == {'A1', 'A2'}


def test_household_conflict_across_plans():
    with fresh_db() as db:
        first = make_study(db, '先发布')
        second = make_study(db, '后发布')
        layout = [(f'H{i}', '层A', 1 + i % 3) for i in range(12)]
        fill(db, first['id'], layout)
        fill(db, second['id'], layout)
        one = plan(db, first['id'])
        clusters.publish_plan(db, one['id'], db_actor())
        two = plan(db, second['id'])
        assert two['feasible'] is False
        assert two['summary']['eligible_households'] == 0
        assert '占用' in two['reason']
        with pytest.raises(HTTPException) as exc:
            clusters.publish_plan(db, two['id'], db_actor())
        assert exc.value.status_code == 409


def test_republish_is_idempotent_and_does_not_double_claim():
    with fresh_db() as db:
        s = make_study(db, '重试发布')
        fill(db, s['id'], [(f'H{i}', '层A', 1 + i % 2) for i in range(10)])
        p = plan(db, s['id'])
        first = clusters.publish_plan(db, p['id'], db_actor())
        second = clusters.publish_plan(db, p['id'], db_actor())
        assert first['already_published'] is False
        assert second['already_published'] is True
        assert second['claimed_households'] == first['claimed_households']
        assert len(clusters.claims(db)) == first['claimed_households']


def test_roster_update_invalidates_draft_but_freezes_published():
    with fresh_db() as db:
        s = make_study(db, '版本交错')
        fill(db, s['id'], [(f'H{i}', '层A', 1 + i % 3) for i in range(12)])
        p = plan(db, s['id'])
        clusters.publish_plan(db, p['id'], db_actor())
        assert clusters.get_plan(db, p['id'])['status'] == 'published'
        draft = plan(db, s['id'])
        assert draft['feasible'] is False  # 已发布的家庭被占用，无法再排一份
        fill(db, s['id'], [('NEW', '层A', 2)])
        after = clusters.get_study(db, s['id'])
        statuses = {x['version']: x['status'] for x in after['plans']}
        assert statuses[p['version']] == 'published'
        assert 'stale' in statuses.values()
        stale_versions = [v for v, st in statuses.items() if st == 'stale']
        assert stale_versions
        stale = clusters.get_plan(db, stale_versions[0])
        with pytest.raises(HTTPException) as exc:
            clusters.publish_plan(db, stale['id'], db_actor())
        assert exc.value.status_code == 409
        # 已发布方案仍可排队，冻结原名单版本。
        run = clusters.create_run(db, ClusterRunInput(plan_id=p['id'], name='锁定研究'), db_actor())
        assert run['snapshot']['roster_version'] == p['roster_version']
        assert run['snapshot']['roster_checksum'] == p['snapshot']['roster_checksum']
        assert checksum(run['snapshot']) == run['checksum']


def test_design_effect_monotone_and_cluster_power_close_to_target():
    n_ind = 3213
    assert cluster_algorithm.design_effect(1, .2) == 1.0
    assert cluster_algorithm.design_effect(4, .2) > cluster_algorithm.design_effect(2, .2)
    assert cluster_algorithm.design_effect(2, 0) == 1.0
    sizes = [2] * math.ceil(n_ind * cluster_algorithm.design_effect(2, .05) / 2)
    power, se, note = cluster_algorithm.simulate_cluster_power(
        .08, .1, __import__('numpy').array(sizes), __import__('numpy').array(sizes), .05, .05, 3000, 42)
    assert note == ''
    assert .74 <= power <= .86
    assert 0 < se < .02


def test_budget_truncates_on_household_boundary():
    allocations = [{'household_key': f'H{i}', 'stratum': '层A', 'size': 2, 'group': 'A' if i % 2 == 0 else 'B'} for i in range(20)]
    params = dict(p0=.08, p1=.1, cost_per_contact=1.0, budget=41.0, alpha=.05, power=.8,
                  simulations=200, icc=.1, seed=42)
    result = cluster_algorithm.analyze(allocations, params)
    assert result['budget']['affordable_contacts'] == 41
    assert result['budget']['funded_a_members'] % 2 == 0
    assert result['budget']['funded_b_members'] % 2 == 0
    assert result['budget']['funded_a_members'] <= 20
    assert result['budget']['funded_b_members'] <= 20
    assert result['feasible'] is True


def test_budget_never_splits_an_unequal_household():
    allocations = []
    for i in range(6):
        group = 'A' if i < 3 else 'B'
        allocations.append({'household_key': f'H{i}', 'stratum': '层A', 'size': 3, 'group': group})
    params = dict(p0=.08, p1=.1, cost_per_contact=1.0, budget=10.0, alpha=.05, power=.8,
                  simulations=200, icc=.1, seed=42)
    result = cluster_algorithm.analyze(allocations, params)
    # 每组可负担 5 次触达，但每户 3 人；只能整户取 3 人，不能取 5 人拆户。
    assert result['budget']['affordable_contacts'] == 10
    assert result['budget']['funded_a_members'] == 3
    assert result['budget']['funded_b_members'] == 3
    assert result['budget']['planned_contacts'] == 18
    assert result['power']['budget'] == 0.0 or result['power']['budget'] < result['power']['planned']


def test_duplicate_claim_is_rejected_by_database_constraint():
    import pytest as _pytest
    from sqlalchemy.exc import IntegrityError
    from src.config.database import Session
    from src.models.cluster import HouseholdClaim
    with fresh_db() as db:
        db.add(HouseholdClaim(household_key='H1', stratum='层A', group_name='A',
                              study_id=1, plan_id=1, created_at='t'))
        db.commit()
        db.add(HouseholdClaim(household_key='H1', stratum='层A', group_name='B',
                              study_id=2, plan_id=2, created_at='t'))
        with _pytest.raises(IntegrityError):
            db.commit()


def test_member_must_not_straddle_strata():
    with fresh_db() as db:
        s = make_study(db, '跨层')
        fill(db, s['id'], [('H1', '高活跃', 2)])
        with pytest.raises(HTTPException) as exc:
            clusters.add_member(db, s['id'], AudienceMemberInput(source_key='X', household_key='H1', stratum='低活跃'), db_actor())
        assert exc.value.status_code == 422
