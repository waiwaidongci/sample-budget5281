import math

from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError

from src.models.cluster import (
    HouseholdStudy, AudienceMember, AllocationPlan, HouseholdClaim, ClusterRun,
)
from src.services import cluster_algorithm
from src.services.audits import log
from src.utils.serializers import record, now, checksum

MAX_MEMBERS = 5000


def _positive_number(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise HTTPException(422, label + '必须是数值')
    if not math.isfinite(number) or number <= 0 or number > 1e9:
        raise HTTPException(422, label + '必须是有限正数')
    return number


def _probability(value, label):
    number = _positive_number(value, label)
    if not 0 < number < 1:
        raise HTTPException(422, label + '须在0和1之间')
    return number


def _validate_parameters(params):
    try:
        alpha = float(params.get('alpha', .05))
        power = float(params.get('power', .8))
        simulations = int(params.get('simulations', 10000))
        icc = float(params.get('icc', .15))
        seed = int(params.get('seed', 42))
    except (TypeError, ValueError):
        raise HTTPException(422, '分析参数必须是数值')
    if not (.001 <= alpha <= .2):
        raise HTTPException(422, '显著性 α 须在0.001到0.2之间')
    if not (.5 <= power <= .99):
        raise HTTPException(422, '目标功效须在0.5到0.99之间')
    if not (200 <= simulations <= 20000):
        raise HTTPException(422, '模拟次数须在200到20000之间')
    if not (0 <= icc < 1):
        raise HTTPException(422, '户内相关系数须在0到1之间')
    if seed < 0 or seed > 2 ** 31 - 1:
        raise HTTPException(422, '随机种子超出范围')
    return {'alpha': alpha, 'power': power, 'simulations': simulations, 'icc': icc, 'seed': seed}


def create_study(db, data, user):
    p0 = _probability(data.p0, '基线点击率')
    p1 = _probability(data.p1, '候选点击率')
    if p0 == p1:
        raise HTTPException(422, '基线与候选点击率必须不同')
    cost = _positive_number(data.cost_per_contact, '单次触达成本')
    budget = _positive_number(data.budget, '总预算')
    params = _validate_parameters(data.model_dump())
    if db.scalar(select(HouseholdStudy).where(HouseholdStudy.name == data.name)):
        raise HTTPException(409, '成簇研究名称已存在')
    study = HouseholdStudy(
        name=data.name, source_filename=data.source_filename, description=data.description,
        p0=p0, p1=p1, cost_per_contact=cost, budget=budget,
        alpha=params['alpha'], power=params['power'], simulations=params['simulations'],
        icc=params['icc'], seed=params['seed'], roster_version=0, created_at=now(),
    )
    db.add(study); db.flush(); log(db, user, 'cluster_study.create', f'{study.id} {study.name}'); db.commit()
    return record(study)


def _study_or_404(db, id):
    study = db.get(HouseholdStudy, id)
    if not study: raise HTTPException(404, '成簇研究不存在')
    return study


def _lock_study(db, id):
    study = db.scalar(select(HouseholdStudy).where(HouseholdStudy.id == id).with_for_update())
    if not study: raise HTTPException(404, '成簇研究不存在')
    return study


def list_studies(db):
    out = []
    for study in db.scalars(select(HouseholdStudy).order_by(HouseholdStudy.id)):
        item = record(study)
        item['member_count'] = db.scalar(select(func.count()).select_from(AudienceMember).where(AudienceMember.study_id == study.id))
        item['household_count'] = db.scalar(select(func.count(func.distinct(AudienceMember.household_key))).where(AudienceMember.study_id == study.id))
        item['published_plans'] = db.scalar(select(func.count()).select_from(AllocationPlan).where(AllocationPlan.study_id == study.id, AllocationPlan.status == 'published'))
        out.append(item)
    return out


def get_study(db, id):
    study = _study_or_404(db, id)
    result = record(study)
    result['member_count'] = db.scalar(select(func.count()).select_from(AudienceMember).where(AudienceMember.study_id == id))
    result['household_count'] = db.scalar(select(func.count(func.distinct(AudienceMember.household_key))).where(AudienceMember.study_id == id))
    result['plans'] = list_plans(db, id)
    return result


def members(db, id):
    _study_or_404(db, id)
    return [record(x) for x in db.scalars(select(AudienceMember).where(AudienceMember.study_id == id).order_by(AudienceMember.household_key, AudienceMember.source_key))]


def _clean_member(raw, seen_keys, household_strata):
    source_key = str(raw.get('source_key', '')).strip()
    household_key = str(raw.get('household_key', '')).strip()
    stratum = str(raw.get('stratum', '')).strip()
    if not source_key or len(source_key) > 100: raise HTTPException(422, '源记录编号须为1到100个字符')
    if not household_key or len(household_key) > 100: raise HTTPException(422, '家庭编号须为1到100个字符')
    if not stratum or len(stratum) > 80: raise HTTPException(422, '分层归属须为1到80个字符')
    if source_key in seen_keys: raise HTTPException(409, '源记录编号重复：' + source_key)
    other = household_strata.get(household_key)
    if other is not None and other != stratum:
        raise HTTPException(422, f'家庭 {household_key} 同时属于分层 {other} 与 {stratum}，同户不可跨层')
    seen_keys.add(source_key)
    household_strata[household_key] = stratum
    return {'source_key': source_key, 'household_key': household_key, 'stratum': stratum}


def _existing_index(db, id, exclude_id=None):
    keys, strata = set(), {}
    for row in db.scalars(select(AudienceMember).where(AudienceMember.study_id == id)):
        if exclude_id is not None and row.id == exclude_id: continue
        keys.add(row.source_key); strata[row.household_key] = row.stratum
    return keys, strata


def _after_roster_change(db, study, user, action, detail):
    """名单变更：递增版本、使未发布方案失效并重算；已发布方案保持原依据。"""
    study.roster_version += 1
    stale = list(db.scalars(select(AllocationPlan).where(
        AllocationPlan.study_id == study.id, AllocationPlan.status == 'draft').order_by(AllocationPlan.version)))
    latest = stale[-1] if stale else None
    for plan in stale: plan.status = 'stale'
    if latest:
        _build_plan(db, study, latest.parameters, latest.seed, status='draft')
    log(db, user, action, detail); db.commit()


def add_member(db, id, data, user):
    study = _lock_study(db, id)
    count = db.scalar(select(func.count()).select_from(AudienceMember).where(AudienceMember.study_id == id))
    if count >= MAX_MEMBERS: raise HTTPException(422, f'单个研究最多{MAX_MEMBERS}位受众')
    keys, strata = _existing_index(db, id)
    clean = _clean_member(data.model_dump(), keys, strata)
    db.add(AudienceMember(study_id=id, created_at=now(), **clean))
    _after_roster_change(db, study, user, 'cluster_member.add', f'{id}/{clean["source_key"]}')
    return record(db.scalar(select(AudienceMember).where(AudienceMember.study_id == id, AudienceMember.source_key == clean['source_key'])))


def import_members(db, id, data, user):
    study = _lock_study(db, id)
    existing = db.scalar(select(func.count()).select_from(AudienceMember).where(AudienceMember.study_id == id))
    if existing + len(data.members) > MAX_MEMBERS:
        raise HTTPException(422, f'单个研究最多{MAX_MEMBERS}位受众')
    keys, strata = _existing_index(db, id)
    cleaned = [_clean_member(x.model_dump(), keys, strata) for x in data.members]
    if not cleaned: raise HTTPException(422, '至少导入一位受众')
    stamp = now()
    for item in cleaned:
        db.add(AudienceMember(study_id=id, created_at=stamp, **item))
    _after_roster_change(db, study, user, 'cluster_member.import', f'{id} imported {len(cleaned)}')
    return {'imported': len(cleaned), 'roster_version': study.roster_version}


def update_member(db, id, member_id, data, user):
    study = _lock_study(db, id)
    row = db.get(AudienceMember, member_id)
    if not row or row.study_id != id: raise HTTPException(404, '受众记录不存在')
    keys, strata = _existing_index(db, id, exclude_id=member_id)
    clean = _clean_member(data.model_dump(), keys, strata)
    row.source_key = clean['source_key']; row.household_key = clean['household_key']; row.stratum = clean['stratum']
    _after_roster_change(db, study, user, 'cluster_member.update', f'{id}/{clean["source_key"]}')
    return record(row)


def delete_member(db, id, member_id, user):
    study = _lock_study(db, id)
    row = db.get(AudienceMember, member_id)
    if not row or row.study_id != id: raise HTTPException(404, '受众记录不存在')
    key = row.source_key
    db.delete(row)
    _after_roster_change(db, study, user, 'cluster_member.delete', f'{id}/{key}')
    return {'deleted': True, 'roster_version': study.roster_version}


def _roster(db, study_id):
    return [record(x) for x in db.scalars(select(AudienceMember).where(AudienceMember.study_id == study_id).order_by(AudienceMember.household_key, AudienceMember.source_key))]


def _roster_checksum(rows):
    return checksum([[x['source_key'], x['household_key'], x['stratum']] for x in rows])


def _allocate(households, seed):
    """分层整户 LPT 分流。同户同组；层内两组人数差不超过该层最大户规模。"""
    import numpy as np
    rng = np.random.default_rng(seed)
    allocations, reasons = [], []
    by_stratum = {}
    for household, info in households.items():
        by_stratum.setdefault(info['stratum'], []).append((household, info['size']))
    for stratum in sorted(by_stratum):
        bucket = sorted(by_stratum[stratum])
        if len(bucket) < 2:
            reasons.append(f'分层「{stratum}」仅有 {len(bucket)} 个可分流家庭；整户随机化至少需2户，不拆户，方案不可行')
            continue
        order = []
        for size in sorted({x[1] for x in bucket}, reverse=True):
            group = [x for x in bucket if x[1] == size]
            rng.shuffle(group)
            order.extend(group)
        a_members = b_members = a_households = b_households = 0
        for household, size in order:
            go_a = (a_members, a_households) <= (b_members, b_households)
            if go_a:
                a_members += size; a_households += 1; group = 'A'
            else:
                b_members += size; b_households += 1; group = 'B'
            allocations.append({'household_key': household, 'stratum': stratum, 'size': size, 'group': group})
    return allocations, reasons


def _build_plan(db, study, parameters, seed, status='draft'):
    version = (db.scalar(select(func.max(AllocationPlan.version)).where(AllocationPlan.study_id == study.id)) or 0) + 1
    roster = _roster(db, study.id)
    claimed = set(db.scalars(select(HouseholdClaim.household_key)))
    households = {}
    for row in roster:
        if row['household_key'] in claimed: continue
        households.setdefault(row['household_key'], {'stratum': row['stratum'], 'size': 0})['size'] += 1
    allocations, reasons = _allocate(households, seed)
    eligible_strata = {info['stratum'] for info in households.values()}
    for name in sorted({row['stratum'] for row in roster}):
        if name not in eligible_strata:
            reasons.append(f'分层「{name}」的家庭均已被已发布方案占用，本方案无法整户分流')
    analysis = cluster_algorithm.analyze(allocations, parameters) if allocations else cluster_algorithm.empty_analysis(parameters)
    balance = analysis['strata']
    if any(row['a_households'] == 0 or row['b_households'] == 0 for row in balance):
        for row in balance:
            if row['a_households'] == 0 or row['b_households'] == 0:
                text = f'分层「{row["stratum"]}」两组未能同时有家庭'
                if text not in reasons: reasons.append(text)
    feasible = bool(allocations) and analysis['feasible'] and not reasons
    snapshot = {
        'roster_version': study.roster_version,
        'roster_checksum': _roster_checksum(roster),
        'parameters': parameters,
        'members': [
            {'source_key': row['source_key'], 'household_key': row['household_key'],
             'stratum': row['stratum'], 'group': next(
                 (a['group'] for a in allocations if a['household_key'] == row['household_key']), None)}
            for row in roster if row['household_key'] in households
        ],
        'allocations': allocations,
    }
    plan = AllocationPlan(
        study_id=study.id, version=version, status=status,
        roster_version=study.roster_version,
        seed=int(parameters.get('seed', study.seed)),
        parameters=parameters, snapshot=snapshot, allocations=allocations,
        summary={'analysis': analysis, 'balance': balance, 'reasons': reasons,
                 'claimed_excluded': len(claimed), 'claimed_excluded_keys': sorted(claimed),
                 'eligible_households': len(households)},
        checksum=checksum(snapshot), feasible=feasible,
        reason='；'.join(reasons),
        created_at=now(),
    )
    db.add(plan); db.flush()
    return plan


def generate_plan(db, study_id, data, user):
    study = _lock_study(db, study_id)
    if not db.scalar(select(func.count()).select_from(AudienceMember).where(AudienceMember.study_id == study_id)):
        raise HTTPException(422, '先录入受众、家庭与分层，再生成方案')
    base = {'p0': study.p0, 'p1': study.p1, 'cost_per_contact': study.cost_per_contact,
            'budget': study.budget, 'alpha': study.alpha, 'power': study.power,
            'simulations': study.simulations, 'icc': study.icc, 'seed': study.seed}
    overrides = {k: v for k, v in data.model_dump().items() if v is not None}
    params = base | _validate_parameters(base | overrides)
    cached = _cached_plan(db, study, params)
    if cached is not None:
        return get_plan(db, cached.id)
    for plan in db.scalars(select(AllocationPlan).where(
            AllocationPlan.study_id == study_id, AllocationPlan.status == 'draft')):
        plan.status = 'superseded'
    plan = _build_plan(db, study, params, params['seed'], status='draft')
    log(db, user, 'cluster_plan.generate', f'{study_id}/v{plan.version}')
    db.commit()
    return get_plan(db, plan.id)


def list_plans(db, study_id):
    _study_or_404(db, study_id)
    plans = db.scalars(select(AllocationPlan).where(AllocationPlan.study_id == study_id).order_by(AllocationPlan.version.desc()))
    # 列表只带索引字段；完整名单、分组与冻结内容由 GET /cluster-plans/{id} 提供。
    keys = ('id', 'study_id', 'version', 'status', 'roster_version', 'seed', 'checksum',
            'feasible', 'reason', 'created_at', 'published_at', 'published_by')
    return [{k: getattr(plan, k) for k in keys} for plan in plans]


def _cached_plan(db, study, parameters):
    """名单未变时复用未发布方案：重试生成/发布命中同一冻结依据，不重复占用家庭。

    在 Python 中比较参数，避免依赖 JSON 列在 PostgreSQL 上的相等运算符。
    """
    drafts = list(db.scalars(select(AllocationPlan).where(
        AllocationPlan.study_id == study.id,
        AllocationPlan.status == 'draft',
        AllocationPlan.roster_version == study.roster_version,
    ).order_by(AllocationPlan.version.desc())))
    for plan in drafts:
        if plan.parameters == parameters:
            return plan
    return None


def get_plan(db, id):
    plan = db.get(AllocationPlan, id)
    if not plan: raise HTTPException(404, '分流方案不存在')
    result = record(plan)
    result['claims'] = [record(x) for x in db.scalars(select(HouseholdClaim).where(HouseholdClaim.plan_id == id).order_by(HouseholdClaim.household_key))]
    result['publishable'] = plan.status == 'draft' and bool(plan.feasible)
    return result


def publish_plan(db, id, user):
    found = db.get(AllocationPlan, id)
    if not found: raise HTTPException(404, '分流方案不存在')
    # 统一加锁顺序：先研究后方案，与名单更新路径一致，避免交叉死锁。
    study = _lock_study(db, found.study_id)
    plan = db.scalar(select(AllocationPlan).where(AllocationPlan.id == id).with_for_update())
    if not plan: raise HTTPException(404, '分流方案不存在')
    if plan.status == 'published':
        claims = list(db.scalars(select(HouseholdClaim).where(HouseholdClaim.plan_id == id)))
        return {'plan': get_plan(db, id), 'already_published': True, 'claimed_households': len(claims)}
    if plan.status == 'stale':
        raise HTTPException(409, '方案已因源数据更新失效，请按当前名单重新生成')
    if plan.status != 'draft':
        raise HTTPException(409, '只有草稿方案可以发布')
    if not plan.feasible:
        raise HTTPException(409, '方案不可行，不能发布：' + (plan.reason or '整户分流不足'))
    if plan.roster_version != study.roster_version:
        plan.status = 'stale'; db.commit()
        raise HTTPException(409, '源数据已更新，方案失效；请重新生成')
    roster = _roster(db, study.id)
    if _roster_checksum(roster) != plan.snapshot.get('roster_checksum'):
        plan.status = 'stale'; db.commit()
        raise HTTPException(409, '名单内容与冻结依据不一致，方案失效；请重新生成')
    keys = [x['household_key'] for x in plan.allocations]
    conflicts = list(db.scalars(select(HouseholdClaim).where(HouseholdClaim.household_key.in_(keys))))
    if conflicts:
        names = '、'.join(sorted({x.household_key for x in conflicts})[:10])
        raise HTTPException(409, f'家庭已被其他已发布方案占用，请重新生成：{names}')
    stamp = now()
    try:
        for item in plan.allocations:
            db.add(HouseholdClaim(household_key=item['household_key'], stratum=item['stratum'],
                                  group_name=item['group'], study_id=plan.study_id,
                                  plan_id=plan.id, created_at=stamp))
        plan.status = 'published'; plan.published_at = stamp; plan.published_by = user.email
        log(db, user, 'cluster_plan.publish', f'{plan.id}/v{plan.version}')
        db.flush(); db.commit()
    except IntegrityError:
        db.rollback()
        again = db.get(AllocationPlan, id)
        if again and again.status == 'published':
            # 并发重试：另一请求已完成发布，家庭没有被重复占用。
            return {'plan': get_plan(db, id), 'already_published': True,
                    'claimed_households': len(again.allocations)}
        raise HTTPException(409, '并发发布冲突：家庭已被其他方案占用，请重新生成本方案')
    return {'plan': get_plan(db, id), 'already_published': False, 'claimed_households': len(plan.allocations)}


def claims(db):
    return [record(x) for x in db.scalars(select(HouseholdClaim).order_by(HouseholdClaim.id))]


def create_run(db, data, user):
    plan = db.scalar(select(AllocationPlan).where(AllocationPlan.id == data.plan_id).with_for_update())
    if not plan: raise HTTPException(404, '分流方案不存在')
    if plan.status != 'published':
        raise HTTPException(409, '只有已发布方案可以冻结排队研究')
    if plan.snapshot.get('roster_version') != plan.roster_version:
        raise HTTPException(409, '方案冻结名单版本不一致')
    params = dict(plan.parameters)
    params['icc'] = float(plan.snapshot.get('parameters', {}).get('icc', params.get('icc', .15)))
    snapshot = {
        'plan_id': plan.id,
        'plan_version': plan.version,
        'roster_version': plan.roster_version,
        'roster_checksum': plan.snapshot.get('roster_checksum'),
        'icc': params['icc'],
        'parameters': params,
        'allocations': plan.allocations,
        'members': plan.snapshot.get('members', []),
        'plan_checksum': plan.checksum,
    }
    run = ClusterRun(study_id=plan.study_id, plan_id=plan.id, name=data.name,
                     status='queued', parameters=params, snapshot=snapshot,
                     checksum=checksum(snapshot), created_at=now())
    db.add(run); db.flush()
    log(db, user, 'cluster_run.queue', f'{run.id}/plan {plan.id}/roster v{plan.roster_version}')
    db.commit()
    try:
        from redis import Redis
        import os
        Redis.from_url(os.environ['REDIS_URL']).rpush('cluster-queue', run.id)
    except Exception:
        pass
    return record(run)


def get_run(db, id):
    run = db.get(ClusterRun, id)
    if not run: raise HTTPException(404, '成簇研究运行不存在')
    return record(run)


def list_runs(db, study_id=None):
    stmt = select(ClusterRun).order_by(ClusterRun.id.desc())
    if study_id is not None: stmt = stmt.where(ClusterRun.study_id == study_id)
    return [record(x) for x in db.scalars(stmt)]


def run_analysis(snapshot, parameters):
    return cluster_algorithm.analyze(snapshot['allocations'], parameters)
