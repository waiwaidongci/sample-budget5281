import {clusterStudies,clusterRuns,clusterPlans} from '../api/cluster.js';
import {store} from '../stores/research.js';
import {table} from '../components/common/table.js';
import {status} from '../components/common/status.js';
import {bindForm} from '../hooks/forms.js';
import {esc} from '../utils/format.js';

const STATUS_LABELS={draft:'草稿（可重排）',published:'已发布（锁定）',stale:'已失效（名单更新）',superseded:'已被新版取代'};

function studySelect(list){
  return `<label>成簇研究<select id="cluster-study-select">${list.map(s=>`<option value="${s.id}" ${s.id===store.clusterStudyId?'selected':''}>${esc(s.name)} · ${s.household_count}户/${s.member_count}人 · 名单v${s.roster_version}</option>`).join('')}</select></label>`;
}

function planTable(plans,writable){
  if(!plans?.length)return '<div class="table-wrap"><table><thead><tr><th>版本</th><th>状态</th><th>可行性</th><th>名单版本</th><th>原因 / 说明</th><th>操作</th></tr></thead><tbody><tr><td colspan="6">尚未生成方案。录入名单后点击“生成 / 重排方案”。</td></tr></tbody></table></div>';
  return `<div class="table-wrap"><table><thead><tr><th>版本</th><th>状态</th><th>可行性</th><th>名单版本</th><th>原因 / 说明</th><th>发布 / 依据</th></tr></thead><tbody>${plans.map(p=>`<tr><td>v${p.version}</td><td>${STATUS_LABELS[p.status]||p.status}</td><td>${p.feasible?'可行':'不可行'}</td><td>v${p.roster_version}</td><td>${esc(p.reason||'—')}</td><td>${p.status==='draft'&&writable?`<button class="text" data-publish="${p.id}" ${p.feasible?'':'disabled'}>发布并锁定</button> `:''}<a href="#cluster-plan/${p.id}">冻结依据 →</a></td></tr>`).join('')}</tbody></table></div>`;
}

export async function render(studyId,planId){
  if(planId)return renderPlan(planId);
  if(studyId==='new')return renderNew();
  const [list,runs]=await Promise.all([clusterStudies.list(),clusterRuns.list()]);
  store.clusterStudies=list;store.clusterRuns=runs;
  if(!store.clusterStudyId||!list.some(s=>s.id===store.clusterStudyId))store.clusterStudyId=list[0]?.id||0;
  const current=list.find(s=>s.id===store.clusterStudyId)||null;
  const detail=current?await clusterStudies.get(current.id):null;
  const members=detail?await clusterStudies.members(current.id):[];
  const studyRuns=runs.filter(r=>!current||r.study_id===current.id);
  return `<div class="eyebrow">CLUSTER PRE-REGISTRATION / HOUSEHOLD RANDOMISATION</div><h1>成簇预注册研究</h1><p>以家庭为随机化单位：同户成员同组；各层两组人数差不超过该层最大户规模；未发布方案可重排，已发布家庭跨计划互斥且不挪组；不足时明确不可行，不拆户凑数。</p><div class="toolbar">${list.length?studySelect(list):''}${store.clusterStudies&&localStorage.getItem('research-role')!=='viewer'?'<a class="button" href="#cluster-new">新建成簇研究 →</a>':''}</div>${current?`<section class="strip"><span>${current.member_count} 位受众</span><span>${current.household_count} 个家庭</span><span>名单版本 v${current.roster_version}</span><span>${current.published_plans} 个已发布方案</span></section><h2>名单录入 <small>受众、家庭与分层归属；同户不可跨层</small></h2>${localStorage.getItem('research-role')!=='viewer'?`<form id="cluster-member-form" class="inline-form"><label>源记录编号<input name="source_key" placeholder="如 HH01-01" required></label><label>家庭编号<input name="household_key" placeholder="如 HH01" required></label><label>分层归属<input name="stratum" placeholder="如 高活跃" required></label><button type="submit">添加受众</button></form><details><summary>批量粘贴（源记录,家庭,分层）</summary><form id="cluster-import-form"><label>每行一条<textarea name="csv" placeholder="A-01,HH01,高活跃&#10;A-02,HH01,高活跃" required></textarea></label><button type="submit">批量导入</button></form></details>`:'<p class="method-note">观察员可查看名单与分组依据。</p>'}<h2>受众与家庭 <small>${members.length} 条 · 更新名单使未发布方案失效并按新名单重算</small></h2>${table(['源记录','家庭','分层',...(localStorage.getItem('research-role')!=='viewer'?['操作']:[])],members.map(m=>[m.source_key,m.household_key,m.stratum,...(localStorage.getItem('research-role')!=='viewer'?[{html:`<button class="text" data-remove-member="${m.id}">移除</button>`}]:[])]))}<h2>分流方案 <small>未发布可重排；已发布方案锁定家庭且跨计划互斥</small></h2>${localStorage.getItem('research-role')!=='viewer'?`<form id="cluster-plan-form" class="worksheet"><p>按当前名单整户分流。已被其他已发布方案占用的家庭自动排除；某层可分流家庭不足2户时明确不可行。</p><div class="inline-form"><label>ICC（户内相关）<input name="icc" type="number" step="0.01" min="0" max="0.99" value="${current.icc}"></label><label>模拟次数<input name="simulations" type="number" step="1" min="200" max="20000" value="${current.simulations}"></label><button type="submit">生成 / 重排方案</button></div></form>`:''}${planTable(detail.plans,localStorage.getItem('research-role')!=='viewer')}<h2>排队研究 <small>冻结同版名单、整户分组与户内相关性</small></h2>${table(['编号','名称','状态','名单版本','依据方案','SHA256'],studyRuns.map(r=>[{html:`<a href="#cluster-run/${r.id}">#${r.id}</a>`},r.name,{html:status(r.status)},`v${r.snapshot.roster_version}`,`v${r.snapshot.plan_version}`,r.checksum.slice(0,12)]))}`:'<p class="method-note">还没有成簇研究。点击右上角新建，录入家庭名单后生成分流方案。</p>'}`;
}

function renderNew(){
  return `<div class="eyebrow">CLUSTER PRE-REGISTRATION / NEW STUDY</div><h1>新建成簇研究</h1><p>先声明基线、候选、成本、预算与户内相关假设；录入家庭名单后才能生成整户分流方案。</p><form id="cluster-new-form" class="worksheet"><div class="inline-form"><label>研究名称<input name="name" required minlength="2" value="家庭成簇研究 · 冬季"></label><label>来源文件<input name="source_filename" value="household-roster-202611.csv" required></label><label>说明<input name="description" value="以家庭为单位的预注册设计"></label></div><div class="inline-form"><label>基线点击率 p0<input name="p0" type="number" step="any" min="0" max="1" value="0.08" required></label><label>候选点击率 p1<input name="p1" type="number" step="any" min="0" max="1" value="0.1" required></label><label>单次触达成本<input name="cost_per_contact" type="number" step="any" min="0" value="0.12" required></label><label>总预算<input name="budget" type="number" step="any" min="0" value="3000" required></label><label>ICC 户内相关<input name="icc" type="number" step="0.01" min="0" max="0.99" value="0.15" required></label><label>模拟次数<input name="simulations" type="number" step="1" min="200" max="20000" value="10000" required></label></div><button type="submit">保存成簇研究 →</button></form>`;
}

async function renderPlan(planId){
  const plan=await clusterPlans.get(planId);
  store.clusterPlanId=plan.id;
  const a=plan.summary?.analysis||{};
  const balance=plan.summary?.balance||[];
  const reasons=plan.summary?.reasons||[];
  const writable=localStorage.getItem('research-role')!=='viewer';
  return `<div class="eyebrow">CLUSTER PRE-REGISTRATION · PLAN ${plan.id}</div><h1>分流方案 v${plan.version} · ${STATUS_LABELS[plan.status]||plan.status}</h1><div class="checksum">冻结 SHA256 ${plan.checksum}</div><p>名单版本 v${plan.roster_version} · 随机种子 ${plan.seed} · 可分流 ${plan.summary?.eligible_households??0} 户 · 已排除占用 ${plan.summary?.claimed_excluded??0} 户</p>${plan.reason?`<div class="error">不可行原因：${esc(plan.reason)}</div>`:''}${a.interpretation?`<section class="result-intro"><strong>${esc(a.interpretation)}</strong><p>${esc(a.caveat)}</p></section>`:''}${balance.length?`<h2>分层均衡核对 <small>各层两组人数差 ≤ 该层最大户规模</small></h2>${table(['分层','最大户规模','A组户数','A组人数','B组户数','B组人数','人数差','允许上限','判断'],balance.map(x=>[x.stratum,x.max_household_size,x.a_households,x.a_members,x.b_households,x.b_members,x.difference,x.max_household_size,x.difference<=x.max_household_size&&x.a_households&&x.b_households?'符合':'不符合']))}`:''}${reasons.length?`<h2>不可行说明 <small>整户不足时不拆户凑数</small></h2><ul>${reasons.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:''}<h2>整户分组名单 <small>同户同组</small></h2>${table(['家庭','分层','户规模','组别'],(plan.allocations||[]).map(x=>[x.household_key,x.stratum,x.size,x.group]))}${plan.claims?.length?`<h2>发布占用登记</h2>${table(['家庭','分层','组别','占用时间'],plan.claims.map(x=>[x.household_key,x.stratum,x.group_name,x.created_at]))}`:''}<details><summary>冻结的名单版本与参数</summary><pre>${esc(JSON.stringify({roster_version:plan.roster_version,checksum:plan.checksum,parameters:plan.parameters,roster_checksum:plan.snapshot?.roster_checksum},null,2))}</pre></details>${plan.status==='published'&&writable?`<section class="worksheet"><h2>冻结并排队功效研究</h2><p>排队时冻结已发布名单版本、整户分组与户内相关性；此后更新源数据只影响未发布方案，本次研究保持原依据。</p><form id="cluster-run-form"><label>研究名称<input name="name" required minlength="2" value="成簇核算 · 方案v${plan.version}"></label><button type="submit">冻结依据并排队</button></form></section>`:''}`;
}

export function bind(refresh){
  const sel=document.getElementById('cluster-study-select');
  if(sel)sel.onchange=()=>{store.clusterStudyId=+sel.value;refresh();};
  bindForm('cluster-member-form',async data=>{await clusterStudies.addMember(store.clusterStudyId,data);refresh();});
  bindForm('cluster-import-form',async data=>{const rows=data.csv.split(/\r?\n/).map(x=>x.trim()).filter(Boolean).map(line=>{const [source_key,household_key,stratum]=line.split(',').map(v=>(v||'').trim());return {source_key,household_key,stratum};});const result=await clusterStudies.importMembers(store.clusterStudyId,rows);document.getElementById('notice').textContent=`已导入 ${result.imported} 位受众，名单版本 v${result.roster_version}；未发布方案已按新名单重算。`;refresh();});
  bindForm('cluster-plan-form',async data=>{const plan=await clusterStudies.generate(store.clusterStudyId,{icc:+data.icc,simulations:+data.simulations});document.getElementById('notice').textContent=plan.feasible?`已生成方案 v${plan.version}，可发布。`:`方案 v${plan.version} 不可行：${plan.reason}`;location.hash='cluster-plan/'+plan.id;});
  bindForm('cluster-new-form',async data=>{const study=await clusterStudies.create({...data,p0:+data.p0,p1:+data.p1,cost_per_contact:+data.cost_per_contact,budget:+data.budget,icc:+data.icc,simulations:+data.simulations});store.clusterStudyId=study.id;location.hash='cluster';});
  bindForm('cluster-run-form',async data=>{const run=await clusterRuns.create({plan_id:store.clusterPlanId,name:data.name});location.hash='cluster-run/'+run.id;});
  document.querySelectorAll('[data-publish]').forEach(el=>el.onclick=async()=>{try{const result=await clusterPlans.publish(+el.dataset.publish);document.getElementById('notice').textContent=result.already_published?`方案此前已发布，家庭未重复占用（${result.claimed_households}户）。`:`已发布并锁定 ${result.claimed_households} 个家庭。`;refresh();}catch(e){document.getElementById('notice').textContent=e.message;}});
  document.querySelectorAll('[data-remove-member]').forEach(el=>el.onclick=async()=>{try{await clusterStudies.removeMember(store.clusterStudyId,+el.dataset.removeMember);document.getElementById('notice').textContent='已更新名单，未发布方案已重算。';refresh();}catch(e){document.getElementById('notice').textContent=e.message;}});
}
