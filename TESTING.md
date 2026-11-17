# 测试与验证记录

## 运行方式

```bash
cd backend
python3 -m pytest tests -q
```

测试使用独立的临时 SQLite 文件（`tests/conftest.py` 在导入应用前设置 `DATABASE_URL`），不触碰 Docker Compose 的 PostgreSQL 数据卷。前端模块用 `node --check` 做语法校验。

## 已覆盖的验证点

| 需求 | 测试 | 结果 |
|---|---|---|
| 同户同组、不拆户 | `test_same_household_never_split_and_stratum_balance` | 通过 |
| 各层两组人数差 ≤ 该层最大户规模 | 同上的分层断言；另用 20000 组随机户规模穷举 `_allocate`，0 例越界 | 通过 |
| 某层整户不足时明确不可行、不拆户 | `test_single_household_stratum_is_infeasible_without_splitting` | 通过 |
| 已发布家庭跨计划互斥 | `test_household_conflict_across_plans`、`test_duplicate_claim_is_rejected_by_database_constraint` | 通过 |
| 并发重复发布不重复占用家庭 | `test_republish_is_idempotent_and_does_not_double_claim` | 通过 |
| 源数据更新使未发布方案失效、已发布保持原依据 | `test_roster_update_invalidates_draft_but_freezes_published` | 通过 |
| 排队研究冻结同版名单、校验和与 ICC | 同上（`cluster_runs.snapshot` 断言）及 HTTP 流程测试 | 通过 |
| 预算按整户边界截断 | `test_budget_truncates_on_household_boundary`、`test_budget_never_splits_an_unequal_household` | 通过 |
| 簇模拟功效对设计效应区间 | `test_design_effect_monotone_and_cluster_power_close_to_target`；另做 ICC∈{0,0.05,0.15,0.3}×户规模∈{1,2,3,5} 的模拟，功效稳定在 0.79–0.81 | 通过 |
| HTTP 权限与参数校验 | `test_viewer_cannot_write_and_anonymous_rejected`、`test_invalid_probabilities_and_parameters_rejected` | 通过 |
| 旧独立研究不受影响 | 手工回归：`calculate` 对 p0=.08、p1=.1 仍输出每组 n=3213、模拟功效 0.8054 | 通过 |

## 手工端到端

用 seed 的家庭名单（3 层 × 30 户，139 人，户规模 1–4）经 API 走通：生成方案 → 发布 90 户 → 重试发布返回 `already_published` 且占用总数不变 → 同研究再生成方案因家庭已被占用而明确不可行 → 名单更新后旧草稿转 `stale`、已发布方案仍可排队并冻结 v0 名单。worker 用假 Redis 跑通 `cluster-queue`，产物写入 `cluster-run-1.json`，数据库状态变为 `completed`。

## 未在此环境验证

当前容器没有 Docker，因此 `docker compose up --build` 未实际执行；也没有本机 PostgreSQL/Redis 服务，多连接并发发布只在服务层与数据库唯一约束上验证，未做真实的双进程争抢压测。
