/* Only loaded on history/client pages; the dashboard never requests daily series. */
'use strict';
const peerId=document.querySelector('meta[name="peer-id"]').content;
let historyData=null,historyTimer=null,historyLoading=false,historyBusy=false,settingsLoaded=false,settingsDirty=false,historyRevision=0;
function stamp(value){return value?new Date(value*1000).toLocaleString('ru-RU',{timeZone:historyData?.timezone||'Europe/Moscow'}):'Нет данных';}
function svgNode(tag,attrs,text){const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,String(v));if(text!==undefined)n.textContent=text;return n;}
function drawHistory(days){
 const svg=$('history-chart');svg.replaceChildren();
 const width=Math.max(280,Math.min(720,svg.clientWidth||720));svg.setAttribute('viewBox','0 0 '+width+' 240');
 const max=Math.max(1,...days.map(d=>d.total)),x=i=>68+i*(width-88)/Math.max(1,days.length-1),y=v=>200-v/max*175;
 for(let i=0;i<=4;i++){
  const value=max*i/4;
  svg.append(svgNode('line',{x1:68,y1:y(value),x2:width-20,y2:y(value),stroke:'#2b374b'}),svgNode('text',{x:2,y:y(value)+4,fill:'#9baac1','font-size':11},bytes(value)));
 }
 for(const [field,color] of [['rx','#91baff'],['tx','#e9bd7b'],['total','#9ce4ce']]){
  svg.append(svgNode('polyline',{points:days.map((d,i)=>x(i)+','+y(d[field])).join(' '),fill:'none',stroke:color,'stroke-width':2}));
  days.forEach((d,i)=>{const dot=svgNode('circle',{cx:x(i),cy:y(d[field]),r:3,fill:color});dot.append(svgNode('title',{},d.day+' '+field.toUpperCase()+': '+bytes(d[field])));svg.append(dot);});
 }
 [...new Set([0,Math.floor((days.length-1)/2),days.length-1])].forEach(i=>{if(days[i])svg.append(svgNode('text',{x:x(i),y:226,fill:'#9baac1','font-size':12,'text-anchor':i===0?'start':i===days.length-1?'end':'middle'},days[i].day.slice(5)));});
 $('chart-empty').classList.toggle('hidden',days.some(d=>d.total>0));
 $('history-days').replaceChildren();
 for(const d of days){const tr=el('tr');tr.append(el('td',d.day),el('td',bytes(d.rx)),el('td',bytes(d.tx)),el('td',bytes(d.total)));$('history-days').append(tr);}
}
function detailEnabled(){
 const fresh=historyData&&!historyData.error&&Date.now()/1000-historyData.collected_at<15;
 if($('detail-save'))$('detail-save').disabled=!fresh||historyBusy;
 if($('detail-block'))$('detail-block').disabled=!fresh||historyBusy||(!historyData.peer.manual_blocked&&historyData.peer.quota_blocked);
}
function showHistory(result){
 historyData=result;
 $('history-error').textContent=result.error||'';$('history-error').classList.toggle('hidden',!result.error);
 $('history-updated').textContent='Данные сборщика: '+stamp(result.collected_at);
 $('history-zone').textContent='Часовой пояс: '+result.timezone;
 for(const [key,value] of Object.entries(result.summary)){$('sum-'+key).textContent=bytes(value.total);$('rx-'+key).textContent='RX '+bytes(value.rx);$('tx-'+key).textContent='TX '+bytes(value.tx);}
 $('history-coverage').textContent='Дневная история ведётся с '+stamp(result.history_started)+'. Более ранние дни не восстановлены; 7 дней включают сегодня. При простое прирост относится ко дню следующего замера.';
 $('history-unallocated').textContent=result.unallocated_month.total?'В месячной сумме есть '+bytes(result.unallocated_month.total)+' прежнего учёта без разбивки по дням. На графике этот объём не распределяется.':'';
 drawHistory(result.days);
 const p=result.peer;
 if(p){
  $('page-title').textContent=p.name;
  $('detail-alias').textContent=p.observer_name||'Не задано (используется имя Amnezia)';$('detail-amnezia').textContent=p.amnezia_name||'Не задано';
  $('detail-ip').textContent=p.addresses||'—';$('detail-state').textContent=({online:'Online',offline:'Offline',blocked:'Blocked',pending:'Блокировка применяется'})[p.state]||p.state;
  $('detail-reason').textContent=[p.manual_blocked?'manual — вручную':'',p.quota_blocked?'quota — месячный лимит':''].filter(Boolean).join('; ')||'Нет';
  $('detail-handshake').textContent=stamp(p.handshake);$('detail-started').textContent=stamp(p.accounting_started);
  $('detail-quota').textContent=p.quota?bytes(p.quota):'Без лимита';$('detail-percent').textContent=p.quota?(100*p.month_used/p.quota).toFixed(1)+'%':'Нет квоты';
  $('detail-block').textContent=p.manual_blocked?'Разблокировать вручную':p.quota_blocked?'Исчерпан месячный лимит':'Заблокировать вручную';
  if(!settingsLoaded||!settingsDirty){$('detail-name').value=p.observer_name||'';$('detail-limit').value=p.quota?String(p.quota/1024**3):'';settingsLoaded=true;}
 }
 detailEnabled();
}
async function loadHistory(){
 if(historyLoading)return;
 clearTimeout(historyTimer);
 if(historyBusy||document.hidden){historyTimer=setTimeout(loadHistory,1000);return;}
 historyLoading=true;const started=Date.now(),period=$('period').value,revision=historyRevision;let delay=5000;
 try{const result=await api('/api/history?period='+period+(peerId?'&id='+peerId:''));if(period===$('period').value&&revision===historyRevision)showHistory(result);else delay=0;}
 catch(e){$('history-error').textContent=e.message;$('history-error').classList.remove('hidden');if(historyData)historyData.error=e.message;detailEnabled();delay=e.status===401||e.status===409?null:Math.max(0,1000-(Date.now()-started));}
 finally{historyLoading=false;if(delay!==null)historyTimer=setTimeout(loadHistory,delay);}
}
$('history-refresh').addEventListener('click',loadHistory);$('period').addEventListener('change',loadHistory);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)loadHistory();});
async function changeClient(path,payload){
 if(historyBusy)return;historyBusy=true;historyRevision++;detailEnabled();
 try{await api(path,payload);settingsDirty=false;toast('Изменение подтверждено');$('detail-message').textContent='Сохранено';}
 catch(e){$('detail-message').textContent=e.message;}
 finally{historyBusy=false;await loadHistory();detailEnabled();}
}
if(peerId){
 $('detail-form').addEventListener('input',()=>settingsDirty=true);
 $('detail-form').addEventListener('submit',e=>{
  e.preventDefault();const value=$('detail-limit').value,quota=value===''?0:Math.round(Number(value)*1024**3);
  if(!Number.isSafeInteger(quota)||quota<0||quota>1024**5){$('detail-message').textContent='Проверьте лимит';return;}
  changeClient('/api/edit',{id:peerId,name:$('detail-name').value,quota});
 });
 $('detail-block').addEventListener('click',()=>{
  if(!historyData?.peer)return;const block=!historyData.peer.manual_blocked;
  if(confirm((block?'Заблокировать ':'Разблокировать ')+historyData.peer.name+'? Если это текущее VPN-подключение, доступ к панели может пропасть.'))changeClient('/api/action',{id:peerId,block});
 });
}
loadHistory();
