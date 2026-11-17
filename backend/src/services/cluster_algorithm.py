import math
from decimal import Decimal, ROUND_HALF_UP

import numpy as np
from scipy import stats

from src.services.algorithm import required_n

# 单次向量化模拟的最大 模拟次数×家庭数 单元格，避免大名单占满内存。
_CHUNK_CELLS = 2_000_000


def _beta_params(p, rho):
    # Beta(a,b) 的户内相关系数为 1/(1+a+b)；保持均值 p。
    k = 1.0 / rho - 1.0
    return p * k, (1.0 - p) * k


def design_effect(mean_size, icc):
    """Kish 设计效应：1 + (平均户规模 - 1) * 户内相关系数。"""
    return 1.0 + (mean_size - 1.0) * icc


def arm_households(allocations, group, target_members=None):
    """按家庭键稳定排序取整户；预算不足时在户边界截断，不拆户。"""
    rows = sorted(
        [(x['household_key'], int(x['size'])) for x in allocations if x['group'] == group],
        key=lambda x: x[0],
    )
    sizes = []
    total = 0
    for key, size in rows:
        if target_members is not None and total + size > target_members:
            break
        sizes.append(size)
        total += size
    return np.asarray(sizes, dtype=int), total


def simulate_cluster_power(p0, p1, sizes_a, sizes_b, icc, alpha, simulations, seed):
    """按整户二项-贝塔随机化模拟；用家庭稳健三明治方差做双侧检验。

    每户命中率由 Beta 分布给出（户内相关），户内成员为独立伯努利。
    方差按家庭聚类残差平方和估计，不假装个体独立。
    """
    if len(sizes_a) < 2 or len(sizes_b) < 2:
        return 0.0, 0.0, '两组各需至少2个家庭才能估计户间方差'
    rng = np.random.default_rng(seed)
    B = simulations

    def draw(p, sizes):
        n = int(sizes.sum())
        p_hats = []
        variances = []
        step = max(1, _CHUNK_CELLS // max(1, len(sizes)))
        for start in range(0, B, step):
            count = min(step, B - start)
            if icc > 0:
                a, b = _beta_params(p, icc)
                household_p = rng.beta(a, b, size=(count, len(sizes)))
            else:
                household_p = np.full((count, len(sizes)), p, dtype=float)
            ys = rng.binomial(sizes[None, :], household_p)
            p_hat = ys.sum(axis=1) / n
            residual = ys - sizes[None, :] * p_hat[:, None]
            variance = (residual ** 2).sum(axis=1) / (n ** 2)
            p_hats.append(p_hat)
            variances.append(variance)
        return np.concatenate(p_hats), np.concatenate(variances)

    p_a, v_a = draw(p0, sizes_a)
    p_b, v_b = draw(p1, sizes_b)
    se = np.sqrt(v_a + v_b)
    z = np.divide(p_b - p_a, se, out=np.zeros(B, dtype=float), where=se > 0)
    rejected = np.abs(z) > stats.norm.ppf(1 - alpha / 2)
    power = float(np.mean(rejected))
    return power, math.sqrt(power * (1 - power) / B), ''


def empty_analysis(parameters):
    """没有可分流家庭时的占位结果，供不可行方案仍展示原因与冻结依据。"""
    return {
        'metrics': {}, 'interpretation': '没有可用于整户分流的家庭，方案不可行。',
        'caveat': '来源名单为空或所有家庭均被其他已发布方案占用；不拆分家庭凑数。',
        'columns': ['分层', '最大户规模', 'A组户数', 'A组人数', 'B组户数', 'B组人数', '两组人数差', '允许上限', '判断'],
        'rows': [], 'table_title': '分层整户分组与均衡核对', 'strata': [],
        'totals': {'households': 0, 'members': 0, 'a_households': 0, 'a_members': 0,
                   'b_households': 0, 'b_members': 0},
        'design': {'mean_household_size': 0, 'icc': float(parameters.get('icc', .15)),
                   'design_effect': 0, 'required_individual_n': 0,
                   'required_households_per_arm': 0, 'required_contacts': 0},
        'budget': {'cost_per_contact': float(parameters.get('cost_per_contact', 0)),
                   'budget': float(parameters.get('budget', 0)), 'affordable_contacts': 0,
                   'planned_contacts': 0, 'funded_a_members': 0, 'funded_b_members': 0,
                   'required_cost': 0, 'verdict': '不可行'},
        'power': {'planned': 0.0, 'planned_se': 0.0, 'budget': 0.0, 'budget_se': 0.0,
                  'target': float(parameters.get('power', .8))},
        'feasible': False,
    }


def analyze(allocations, parameters):
    """对整户分配方案做设计效应、预算与Monte Carlo功效核算。"""
    p0 = float(parameters['p0'])
    p1 = float(parameters['p1'])
    cost = float(parameters['cost_per_contact'])
    budget = float(parameters['budget'])
    alpha = float(parameters.get('alpha', .05))
    power_target = float(parameters.get('power', .8))
    simulations = int(parameters.get('simulations', 10000))
    icc = float(parameters.get('icc', .15))
    seed = int(parameters.get('seed', 42))

    if not 0 < p0 < 1 or not 0 < p1 < 1 or p0 == p1:
        raise ValueError('两概率须在0和1之间且不同')
    if cost <= 0 or budget <= 0:
        raise ValueError('成本与预算须为正')
    if not 0 <= icc < 1:
        raise ValueError('户内相关系数须在0到1之间')
    if not 200 <= simulations <= 20000:
        raise ValueError('模拟次数超出范围')

    strata = {}
    for item in allocations:
        s = strata.setdefault(item['stratum'], {'A': [0, 0], 'B': [0, 0], 'max': 0})
        s[item['group']][0] += 1
        s[item['group']][1] += int(item['size'])
        s['max'] = max(s['max'], int(item['size']))

    rows = []
    feasible = True
    for name in sorted(strata):
        s = strata[name]
        diff = abs(s['A'][1] - s['B'][1])
        ok = s['A'][0] >= 1 and s['B'][0] >= 1 and diff <= s['max']
        feasible = feasible and ok
        rows.append([name, s['max'], s['A'][0], s['A'][1], s['B'][0], s['B'][1], diff, s['max'], '符合' if ok else '不符合'])

    households = len(allocations)
    members_a = sum(int(x['size']) for x in allocations if x['group'] == 'A')
    members_b = sum(int(x['size']) for x in allocations if x['group'] == 'B')
    total_members = members_a + members_b
    mean_size = total_members / households if households else 0.0
    deff = design_effect(mean_size, icc) if households else 0.0

    n_ind = required_n(p0, p1, alpha, power_target)
    required_households = math.ceil(n_ind * deff / mean_size) if mean_size > 0 else 0
    required_contacts = required_households * 2 * mean_size

    affordable = int((Decimal(str(budget)) / Decimal(str(cost))).to_integral_value(rounding='ROUND_FLOOR'))
    target_per_arm = affordable // 2
    sizes_a, funded_a = arm_households(allocations, 'A', target_per_arm)
    sizes_b, funded_b = arm_households(allocations, 'B', target_per_arm)
    all_a = arm_households(allocations, 'A')[0]
    all_b = arm_households(allocations, 'B')[0]

    planned_power, planned_se, planned_note = simulate_cluster_power(
        p0, p1, all_a, all_b, icc, alpha, simulations, seed,
    )
    budget_power, budget_se, budget_note = simulate_cluster_power(
        p0, p1, sizes_a, sizes_b, icc, alpha, simulations, seed,
    )
    note = budget_note or planned_note
    budget_short = len(sizes_a) < 2 or len(sizes_b) < 2

    required_cost = float((Decimal(str(cost)) * Decimal(str(required_contacts))).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
    planner = '足够' if Decimal(str(budget)) >= Decimal(str(required_cost)) else '不足'

    metrics = {
        'A组家庭数': int(len(arm_households(allocations, 'A')[0])),
        'A组人数': members_a,
        'B组家庭数': int(len(arm_households(allocations, 'B')[0])),
        'B组人数': members_b,
        '户内相关系数ICC': icc,
        '平均户规模': round(mean_size, 3),
        '整户设计效应': round(deff, 3),
        '所需每组个体n': n_ind,
        '所需每组家庭数': required_households,
        '所需总触达': round(required_contacts),
        '所需预算(元)': required_cost,
        '预算可负担触达': affordable,
        '预算下A组人数': funded_a,
        '预算下B组人数': funded_b,
        '方案模拟功效': round(planned_power, 4),
        '预算下模拟功效': round(budget_power, 4),
        '模拟功效MC标准误': round(budget_se, 5),
        '目标功效': power_target,
        '双侧显著性α': alpha,
        '模拟次数': simulations,
        '随机种子': seed,
    }
    interpretation = (
        f"按整户分流，A组{metrics['A组家庭数']}户/{members_a}人，B组{metrics['B组家庭数']}户/{members_b}人；"
        f"平均户规模{mean_size:.2f}，ICC={icc:.2f}，设计效应{deff:.2f}。"
        f"按该设计每组约需{required_households}户（个体n={n_ind}）。"
        f"预算{cost:.2f}元/触达，可负担{affordable}次触达，预算下模拟功效{budget_power:.3f}（目标{power_target:.2f}），预算判断：{planner}。"
    )
    warnings = []
    smaller_arm = min(len(all_a), len(all_b))
    if required_households and smaller_arm < required_households:
        warnings.append(
            f"当前可分流名单每组仅{smaller_arm}户，低于整户设计所需约{required_households}户/组；"
            f"预算虽覆盖所需触达，名单规模本身不足，实际功效低于目标。"
        )
    if budget_short:
        warnings.append('预算可负担触达不足每组2户，整户截断后无法模拟；不拆户凑数，预算下功效记为0。')
    if warnings:
        interpretation += ' ' + ' '.join(warnings)
    if note:
        interpretation += ' 预算下模拟：' + note + '。'
    caveat = (
        '以家庭为随机化单位，同户成员同组；预算不足时按整户截取，不拆户凑数。'
        '模拟采用二项-贝塔户内相关结构与家庭稳健三明治方差，ICC为假设值，需在正式预注册前用先导数据核实；'
        '不等户规模会让有效样本偏离名义人数，本核算不保证真实业务效应。'
    )
    return {
        'metrics': metrics,
        'interpretation': interpretation,
        'caveat': caveat,
        'columns': ['分层', '最大户规模', 'A组户数', 'A组人数', 'B组户数', 'B组人数', '两组人数差', '允许上限', '判断'],
        'rows': rows,
        'table_title': '分层整户分组与均衡核对',
        'strata': [
            {'stratum': name, 'max_household_size': strata[name]['max'],
             'a_households': strata[name]['A'][0], 'a_members': strata[name]['A'][1],
             'b_households': strata[name]['B'][0], 'b_members': strata[name]['B'][1],
             'difference': abs(strata[name]['A'][1] - strata[name]['B'][1])}
            for name in sorted(strata)
        ],
        'totals': {'households': households, 'members': total_members,
                   'a_households': metrics['A组家庭数'], 'a_members': members_a,
                   'b_households': metrics['B组家庭数'], 'b_members': members_b},
        'design': {'mean_household_size': mean_size, 'icc': icc, 'design_effect': deff,
                   'required_individual_n': n_ind, 'required_households_per_arm': required_households,
                   'required_contacts': round(required_contacts)},
        'budget': {'cost_per_contact': cost, 'budget': budget, 'affordable_contacts': affordable,
                   'planned_contacts': total_members, 'funded_a_members': funded_a,
                   'funded_b_members': funded_b, 'required_cost': required_cost, 'verdict': planner},
        'power': {'planned': planned_power, 'planned_se': planned_se,
                  'budget': budget_power, 'budget_se': budget_se, 'target': power_target},
        'feasible': feasible,
    }
