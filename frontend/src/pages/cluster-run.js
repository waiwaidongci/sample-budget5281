import {clusterRuns,clusterPlans} from '../api/cluster.js';
import {table} from '../components/common/table.js';
import {status} from '../components/common/status.js';
import {esc,fmt} from '../utils/format.js';

export async function render(id){
  if(!id)return `<h1>成簇研究结果</h1><p>从成簇研究页选择已发布方案并排队。</p>`;
  const run=await clusterRuns.get(id);
  const plan=await clusterPlans.get(run.plan_id);
  const result=run.result;
  const basis=run.snapshot;
  return `<div class="eyebrow">CLUSTER RESULTS · RUN ${run.id}</div><h1>${esc(run.name)} ${status(run.status)}</h1><div class="checksum">冻结 SHA256 ${run.checksum}</div><p>依据方案 v${basis.plan_version} · 名单版本 v${basis.roster_version} · 户内相关 ICC ${fmt(basis.icc)} · 冻结于 ${esc(run.created_at)}</p>${run.status==='failed'?`<div class="error">计算失败：${esc(run.error)}</div>`:result?`<section class="result-intro"><strong>${esc(result.interpretation)}</strong><p>${esc(result.caveat)}</p></section><h2>核心数值 <small>按簇（整户）模拟功效</small></h2>${table(['指标','计算值'],Object.entries(result.metrics).map(([k,v])=>[k,fmt(v)]))}<h2>${esc(result.table_title||'分层核对')}</h2>${table(result.columns,result.rows)}<details><summary>本次冻结的整户分组与同版名单依据</summary><pre>${esc(JSON.stringify({plan_id:run.plan_id,plan_version:basis.plan_version,roster_version:basis.roster_version,roster_checksum:basis.roster_checksum,icc:basis.icc,parameters:run.parameters,allocations:basis.allocations.slice(0,200)},null,2))}</pre></details>`:'<p class="method-note">任务正在队列中；结果写入数据库后刷新本页查看。</p>'}<h2>方案冻结依据</h2><p><a href="#cluster-plan/${plan.id}">查看方案 v${plan.version} 的 SHA256、名单版本与分组 →</a></p>`;
}
export function bind(){}
