import {feePools as feesApi} from '../api/fees.js';
import {datasets} from '../api/entities.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {table} from '../components/common/table.js';
import {esc,fmt,date,notify} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';

const canWrite=()=>session.user.role!=='viewer';
const isAdmin=()=>session.user.role==='admin';
const yuan=cents=>new Intl.NumberFormat('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}).format(cents/100);
const STATUS={draft:'待试算',trialed:'已试算',confirmed:'已确认',revoked:'已撤销'};
const statusBadge=s=>'<span class="status '+(s==='revoked'?'failed':'')+'">'+esc(STATUS[s]||s)+'</span>';

export async function feePage(id){
 const [pools,sheets,datasetsList]=await Promise.all([feesApi.list(),feesApi.sheets(),datasets.list()]);
 workspace.feePools=pools;
 let sheet=null,balance=null;
 const [kind,raw]=String(id||'').split('-');
 if(kind==='sheet'&&raw)sheet=await feesApi.sheet(raw);
 if(kind==='pool'&&raw)balance=await feesApi.balance(raw);
 const editable=canWrite();
 return heading('06 / FEE POOLS','月度费用池与分摊单','渠道收入未扣共享运营费，盈利会被高估。先把月度费用建成整数分费用池，再按选定源账的收入或访问占比分摊，确认后从净收入中扣减。','<a class="button" href="#/fee-pools">刷新余额 ↻</a>')
 +poolSection(pools,datasetsList,editable)
 +sheetSection(sheets,pools,editable)
 +(sheet?sheetDetail(sheet):'')
 +(balance?balanceDetail(balance):'');
}

function poolSection(pools,datasetsList,editable){
 const options=datasetsList.map(d=>'<option value="'+d.id+'">'+esc(d.name)+' · '+d.rows+' 条</option>').join('');
 return '<div class="fee-grid"><section class="panel"><div class="section-title"><h2>费用池余额</h2><span>已分摊 + 剩余 = 月额度</span></div>'
 +'<div class="pool-list">'+(pools.length?pools.map(p=>'<article class="pool-card">'
   +'<div class="pool-head"><div><strong>'+esc(p.name)+'</strong><small>'+esc(p.month)+' · '+esc(p.basis_label)+' · 版本 v'+p.version+'</small></div><span class="status">'+(p.invariant.balanced?'余额一致':'需核对')+'</span></div>'
   +'<div class="pool-numbers"><div><span>月额度</span><strong>¥'+yuan(p.amount_cents)+'</strong></div><div><span>已确认分摊</span><strong>¥'+yuan(p.allocated_cents)+'</strong></div><div><span>剩余额度</span><strong>¥'+yuan(p.remaining_cents)+'</strong></div></div>'
   +'<div class="pool-actions"><a class="text-button" href="#/fees/pool-'+p.id+'">追查余额与流水 ↗</a>'
   +(isAdmin()?'<button class="text-button" data-pool-edit="'+p.id+'">改额 / 换账</button>':'')
   +'</div></article>').join(''):'<p class="empty">尚无费用池。先建立月度共享运营费用池，再创建分摊单。</p>')+'</div>'
 +(isAdmin()&&pools.length?'<form id="pool-edit-form" hidden><div class="section-title"><h2>调整费用池</h2><span>改额或换账会作废未确认试算</span></div><label>费用池<select name="pool_id">'+pools.map(p=>'<option value="'+p.id+'">'+esc(p.name)+' · '+esc(p.month)+'</option>').join('')+'</select></label><div class="parameter-grid"><label>新额度（元）<input name="amount" type="number" step="0.01" min="0.01" required></label><label>分摊基数<select name="basis"><option value="revenue">按净收入占比</option><option value="visits">按访问占比</option></select></label></div><label>换用源账<select name="dataset_id">'+options+'</select></label><button type="submit" class="primary">保存调整</button></form>':'')
 +(isAdmin()?'<div class="section-title"><h2>建立费用池</h2><span>金额以分为单位入账</span></div><form id="pool-form"><label>费用池名称<input name="name" required minlength="2" value="共享运营费池 · 9月（补录）"></label><div class="parameter-grid"><label>所属月份<input name="month" type="month" required value="2026-09"></label><label>月度额度（元）<input name="amount" type="number" step="0.01" min="0.01" required value="5000.00"></label></div><label>分摊基数<select name="basis"><option value="revenue">按选定源账的净收入占比</option><option value="visits">按选定源账的访问占比</option></select></label><label>选定源账<select name="dataset_id" required>'+options+'</select></label><button type="submit" class="primary" '+(options?'':'disabled')+'>建立费用池</button><p class="hint">改额、换账或更换基数会使已试算未确认的分摊单失效；已确认金额不受影响。</p></form>':'')
 +'</section>'
 +'<section class="method-sheet compact"><p class="eyebrow">ALLOCATION CONTRACT</p><h3>分摊规则</h3><ul><li>分摊以“分”为最小单位，最大余数法分配，同余时按渠道名排序固定归属，重复试算尾差一致。</li><li>多池合单确认在同一事务内占额：任一池剩余额度不足，本单所有池都不占用额度。</li><li>试算冻结额度、源账指纹与权重；费用池改额或换账后旧试算失效，拒绝确认。</li><li>确认固定分摊额、源账与权重，后续池子变化不改已确认金额；整单撤销返还全部额度。</li><li>确认与撤销幂等：中断后重试沿用同一分摊单，不重复扣减或返还。</li></ul><p class="hint">净收入 = 源账净收入 − 已分摊共享费用；每访问净收入 = 净收入 ÷ 访问量。</p></section></div>';
}

function sheetSection(sheets,pools,editable){
 const poolOptions=pools.map(p=>'<label class="line-input"><input type="checkbox" name="pool-'+p.id+'" value="'+p.id+'"><span>'+esc(p.name)+' · 剩余 ¥'+yuan(p.remaining_cents)+'</span><input type="number" name="amount-'+p.id+'" step="0.01" min="0.01" placeholder="留空则用全部剩余"></label>').join('');
 return '<div class="fee-grid"><section class="panel"><div class="section-title"><h2>分摊单</h2><span>'+sheets.length+' 张</span></div>'
 +'<div class="sheet-list">'+(sheets.length?sheets.map(s=>'<a class="sheet-row" href="#/fees/sheet-'+s.id+'"><div><strong>'+esc(s.name)+'</strong><small>'+date(s.updated_at)+' · 合计 ¥'+yuan(s.total_fee_cents)+'</small></div>'+statusBadge(s.status)+'</a>').join(''):'<p class="empty">尚无分摊单。</p>')+'</div>'
 +(editable?'<div class="section-title"><h2>新建分摊单</h2><span>可单池或多池合单</span></div><form id="sheet-form"><label>分摊单名称<input name="name" required minlength="2" value="9月共享运营费分摊（补录）"></label><label>备注<input name="note" maxlength="500" placeholder="如：按9月渠道净收入占比"></label><fieldset class="line-fieldset"><legend>选择费用池与分摊额</legend>'+poolOptions+'</fieldset><button type="submit" class="primary" '+(pools.length?'':'disabled')+'>保存为待试算</button><p class="hint">最多选择 20 个费用池。留空分摊额表示使用该池全部剩余额度。</p></form>':'')
 +'</section></div>';
}

function sheetDetail(sheet){
 const metrics=[
   {label:'分摊合计',value:sheet.total_fee,unit:'CNY'},
   {label:'试算状态',value:STATUS[sheet.status]||sheet.status,unit:''},
   {label:'失效',value:sheet.stale?'旧试算已失效':'有效',unit:''},
   {label:'零权重',value:sheet.zero_weight?'拒绝确认':'无',unit:''}
 ];
 return '<section class="panel sheet-detail"><div class="section-title"><h2>'+esc(sheet.name)+'</h2><span>'+statusBadge(sheet.status)+' · 更新 '+date(sheet.updated_at)+'</span></div>'
 +'<p class="hint">'+esc(sheet.note||'无备注')+'</p>'
 +'<div class="metrics">'+metrics.map(m=>'<div><span>'+esc(m.label)+'</span><strong>'+esc(typeof m.value==='number'?fmt(m.value):m.value)+'<small>'+esc(m.unit)+'</small></strong></div>').join('')+'</div>'
 +'<div class="section-title"><h2>费用池行与校验</h2><span>各池已分摊 + 剩余 = 原额度</span></div>'
 +sheet.lines.map(line=>'<article class="line-card"><div class="pool-head"><div><strong>'+esc(line.pool_name)+'</strong><small>'+esc(line.month)+' · '+esc(line.basis_label)+' · 快照额度 ¥'+fmt(line.pool_amount_snapshot)+' · 版本 v'+line.pool_version+'</small></div>'
   +'<span class="status '+(line.allocation_check.balanced?'':'failed')+'">'+(line.allocation_check.balanced?'明细合计一致':'明细不一致')+'</span></div>'
   +'<p class="hint">分摊 ¥'+fmt(line.amount)+' · 权重合计 '+esc(line.weight_total)+' · 源账指纹 '+esc(line.dataset_checksum.slice(0,16))+'…'
   +(line.zero_weight?' · <strong class="danger">存在零权重渠道，拒绝确认</strong>':'')+(line.stale?' · <strong class="danger">费用池已改额或换账，旧试算失效</strong>':'')+'</p>'
   +'<div class="pool-numbers line-economics"><div><span>源账净收入</span><strong>¥'+fmt(line.economics.revenue)+'</strong></div><div><span>分摊共享费用</span><strong>¥'+fmt(line.economics.fee)+'</strong></div><div><span>扣费后净收入</span><strong>¥'+fmt(line.economics.net_revenue)+'</strong></div><div><span>每访问净收入</span><strong>'+(line.economics.net_per_visit===null?'—':'¥'+fmt(line.economics.net_per_visit))+'</strong></div></div>'
   +table([{key:'dimension',label:'渠道'},{key:'weight',label:'权重'},{key:'kind',label:'权重口径'},{key:'fee',label:'分摊费用（元）'},{key:'revenue',label:'源账净收入（元）'},{key:'net',label:'扣费后净收入（元）'},{key:'visit',label:'每访问净收入（元）'}],line.details.map(d=>({dimension:d.dimension,weight:d.weight_display,kind:d.weight_label,fee:d.fee,revenue:d.revenue,net:d.net_revenue,visit:d.net_per_visit??'—'})))
   +'</article>').join('')
 +'<div class="sheet-actions">'+(sheet.status==='draft'||sheet.status==='trialed'?'<button class="button" data-trial="'+sheet.id+'" '+(canWrite()?'':'disabled')+'>重新试算</button>':'')
   +(sheet.status==='trialed'?'<button class="primary" data-confirm="'+sheet.id+'" '+(isAdmin()?'':'disabled')+'>确认分摊并占额</button>':'')
   +(sheet.status==='confirmed'?'<button class="button" data-revoke="'+sheet.id+'" '+(isAdmin()?'':'disabled')+'>整单撤销并返还额度</button>':'')
   +(sheet.status==='confirmed'?'<span class="hint">已确认金额固定，费用池后续改额或换账不影响本单。</span>':'')
   +(sheet.status==='revoked'?'<span class="hint">已整单撤销，额度已返还；如需恢复请新建分摊单。</span>':'')+'</div></section>';
}

function balanceDetail(balance){
 const p=balance.pool;
 return '<section class="panel"><div class="section-title"><h2>余额追查 · '+esc(p.name)+'</h2><span>'+esc(p.month)+' · '+esc(p.basis_label)+'</span></div>'
 +'<div class="metrics"><div><span>月额度</span><strong>'+yuan(p.amount_cents)+'<small>元</small></strong></div><div><span>已确认分摊</span><strong>'+yuan(p.allocated_cents)+'<small>元</small></strong></div><div><span>剩余</span><strong>'+yuan(p.remaining_cents)+'<small>元</small></strong></div><div><span>计数器校验</span><strong>'+(p.invariant.balanced?'一致':'不一致')+'</strong></div></div>'
 +'<div class="two-columns"><div><div class="section-title"><h2>关联分摊单</h2><span>'+balance.sheets.length+' 行</span></div>'+table([{key:'sheet_name',label:'分摊单'},{key:'sheet_status_label',label:'状态'},{key:'amount',label:'分摊额'},{key:'reserved_label',label:'已占额'},{key:'returned_label',label:'已返还'}],balance.sheets.map(x=>({...x,reserved_label:x.reserved?'是':'否',returned_label:x.returned?'是':'否'})))+'</div>'
 +'<div><div class="section-title"><h2>额度流水</h2><span>只追加证据</span></div>'+table([{key:'kind_label',label:'动作'},{key:'amount',label:'金额'},{key:'balance_after',label:'操作后剩余'},{key:'actor',label:'操作人'},{key:'created_at',label:'时间'}],balance.ledger.map(e=>({...e,created_at:date(e.created_at)})))+'</div></div></section>';
}

export function bindFees(refresh){
 const rerender=()=>refresh();
 const poolForm=document.querySelector('#pool-form');
 if(poolForm)submit(poolForm,async f=>{
   await feesApi.create({name:f.get('name'),month:f.get('month'),amount:Number(f.get('amount')),basis:f.get('basis'),dataset_id:Number(f.get('dataset_id'))});
   notify('费用池已建立');await rerender();
 });
 const sheetForm=document.querySelector('#sheet-form');
 if(sheetForm)submit(sheetForm,async f=>{
   const lines=[];
   for(const p of workspace.feePools){if(f.get('pool-'+p.id)===null)continue;const amount=f.get('amount-'+p.id);lines.push({pool_id:p.id,amount:amount?Number(amount):null});}
   if(!lines.length)throw new Error('请至少选择一个费用池');
   const sheet=await feesApi.createSheet({name:f.get('name'),note:f.get('note'),lines});
   notify('分摊单已保存，请试算核对');location.hash='#/fees/sheet-'+sheet.id;await rerender();
 });
 document.querySelectorAll('[data-pool-edit]').forEach(b=>b.onclick=()=>{
   const form=document.querySelector('#pool-edit-form'),pool=workspace.feePools.find(p=>String(p.id)===b.dataset.poolEdit);if(!pool||!form)return;
   form.hidden=false;form.querySelector('[name=pool_id]').value=pool.id;
   form.querySelector('[name=amount]').value=pool.amount.toFixed(2);
   form.querySelector('[name=basis]').value=pool.basis;
   form.querySelector('[name=dataset_id]').value=pool.dataset_id;
   form.scrollIntoView({behavior:'smooth',block:'center'});
 });
 const editForm=document.querySelector('#pool-edit-form');
 if(editForm)submit(editForm,async f=>{
   const pool=workspace.feePools.find(p=>String(p.id)===f.get('pool_id'));if(!pool)throw new Error('请选择费用池');
   await feesApi.update(pool.id,{amount:Number(f.get('amount')),basis:f.get('basis'),dataset_id:Number(f.get('dataset_id'))});
   notify('费用池已更新；未确认的旧试算已失效，需重新试算');await rerender();
 });
 document.querySelectorAll('[data-trial]').forEach(b=>b.onclick=async()=>{try{await feesApi.trial(b.dataset.trial);notify('试算完成，请核对明细后确认');await rerender();}catch(e){notify(e.message,true);}});
 document.querySelectorAll('[data-confirm]').forEach(b=>b.onclick=async()=>{try{await feesApi.confirm(b.dataset.confirm);notify('分摊已确认并占用费用池额度');await rerender();}catch(e){notify(e.message,true);}});
 document.querySelectorAll('[data-revoke]').forEach(b=>b.onclick=async()=>{try{await feesApi.revoke(b.dataset.revoke);notify('整单已撤销，额度已返还');await rerender();}catch(e){notify(e.message,true);}});
}
