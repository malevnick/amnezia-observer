'use strict';
const $=id=>document.getElementById(id);
const csrf=document.querySelector('meta[name="csrf-token"]').content;
const base=document.querySelector('meta[name="app-base"]').content.replace(/\/$/,'');
let data=null, pending=null, busy=false, fresh=false, refreshing=false, refreshTimer=null;
async function api(path, body){
 const isStatus=path==='/api/status'&&!body;
 const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),isStatus?1000:28000);
 try{
  const response=await fetch(base+path,{method:body?'POST':'GET',credentials:'same-origin',signal:controller.signal,
   headers:body?{'Content-Type':'application/json','X-CSRF-Token':csrf}:{},body:body?JSON.stringify(body):undefined});
  const result=await response.json();
  if(response.status===401&&path!='/login')location.href=base+'/';
  if(!response.ok){const error=Error(result.error||'Не удалось выполнить запрос');error.status=response.status;throw error;}
  return result;
 }catch(e){
  if(e.name==='AbortError')throw Error(isStatus?'Ответ задержался. Повторяем запрос…':'Сервер не ответил вовремя. Попробуй обновить данные.');
  throw e;
 }
 finally{clearTimeout(timer);}
}
function bytes(value){if(value===null||value===undefined)return '—';let n=value,u=0;const units=['Б','КиБ','МиБ','ГиБ','ТиБ'];while(n>=1024&&u<4){n/=1024;u++;}return n.toFixed(u?1:0)+' '+units[u];}
function ago(ts){if(!ts)return 'Нет обмена';const s=Math.max(0,Math.floor(Date.now()/1000-ts));return s<60?s+' сек назад':s<3600?Math.floor(s/60)+' мин назад':Math.floor(s/3600)+' ч назад';}
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function toast(message){$('toast').textContent=message;$('toast').classList.remove('hidden');setTimeout(()=>$('toast').classList.add('hidden'),5000);}
function renderPeers(){
 const target=$('peers');target.replaceChildren();const query=$('search').value.toLowerCase();
 const peers=(data?.peers||[]).filter(p=>(p.name+' '+p.addresses).toLowerCase().includes(query));
 $('empty').classList.toggle('hidden',peers.length>0);
 for(const p of peers){
  const tr=el('tr'), name=el('td');name.append(el('strong',p.name),el('small',p.addresses));tr.append(name);
  const state=el('td');state.append(el('span',({online:'Активен',offline:'Неактивен',blocked:'Заблокирован',pending:'Применяется'})[p.state]||'Неизвестно','badge '+p.state));tr.append(state);
  tr.append(el('td',ago(p.handshake)),el('td',bytes(p.sent)+' / '+bytes(p.received)));
  const quota=el('td',bytes(p.month_used)+' / '+(p.quota?bytes(p.quota):'без лимита'));
  quota.append(el('small',p.quota_blocked?'Лимит исчерпан':p.month||'', 'quota-period'));tr.append(quota);
  const manual=p.manual_blocked??p.blocked;
  const cell=el('td'),button=el('button',manual?'Разблокировать':(p.quota_blocked?'Запретить вручную':'Заблокировать'),manual?'allow':'block');
  button.disabled=!fresh||busy;button.addEventListener('click',()=>{
   pending={id:p.id,block:!manual};$('confirm-title').textContent=manual?'Вернуть доступ?':'Заблокировать устройство?';
   $('confirm-text').textContent=p.name+(manual?' сможет снова подключиться, если месячный лимит не исчерпан.':' получит ручной запрет доступа, который сохранится и в новом месяце. Остальные устройства продолжат работать.');
   $('apply').textContent=manual?'Разблокировать':'Заблокировать';$('confirm').showModal();
  });cell.append(button);
  const edit=el('button','Настроить');edit.disabled=!fresh||busy;
  edit.addEventListener('click',()=>{
   $('edit-id').value=p.id;$('client-name').value=p.name;
   $('client-quota').value=p.quota?String(p.quota/1024**3):'';
   $('edit-error').textContent='';$('edit-client').showModal();
  });cell.append(edit);tr.append(cell);target.append(tr);
 }
}
function render(){
 fresh=!data.error&&!!data.collected_at&&(Date.now()/1000-data.collected_at)<15;
 $('error').textContent=data.error||(!fresh?'Данные устарели. Действия временно недоступны.':'');$('error').classList.toggle('hidden',fresh);
 $('health').textContent=fresh?'● Служба доступна':'● Нет свежих данных';$('health').className='badge '+(fresh?'online':'pending');
 $('updated').textContent=data.collected_at?'Обновлено '+new Date(data.collected_at*1000).toLocaleTimeString('ru-RU'):'Ожидание данных';
 const m=data.metrics;
 if(m){
  $('cpu').textContent=m.cpu===null?'—':m.cpu+'%';$('cpu-bar').value=m.cpu||0;
  $('ram').textContent=bytes(m.memory_available);$('ram-total').textContent='Доступно из '+bytes(m.memory_total);$('ram-bar').value=100*m.memory_available/m.memory_total;
  $('disk').textContent=bytes(m.disk_free);$('disk-total').textContent='Свободно из '+bytes(m.disk_total);$('disk-bar').value=100*m.disk_free/m.disk_total;
  $('rx').textContent=bytes(m.rx_rate)+'/с';$('tx').textContent=bytes(m.tx_rate)+'/с';$('interface').textContent=m.interface||'Интерфейс не найден';
  $('uptime').textContent=Math.floor(m.uptime/86400)+' д '+Math.floor(m.uptime%86400/3600)+' ч';
 }
 $('online').textContent=fresh?(data.peers||[]).filter(p=>p.state==='online').length:'—';
 $('peer-total').textContent='Из '+(data.peers||[]).length+' устройств';renderPeers();
 $('month-zone').textContent=data.timezone||'Europe/Moscow';
 $('actions').replaceChildren();for(const a of data.actions||[]){$('actions').append(el('li',a.name+' · '+(a.block?'Доступ заблокирован':'Доступ восстановлен')+' · '+ago(a.time)));}
 if(!data.actions?.length)$('actions').append(el('li','Действий пока нет'));
}
async function refresh(){
 if(refreshing)return;
 clearTimeout(refreshTimer);
 if(busy||document.hidden){refreshTimer=setTimeout(refresh,1000);return;}
 refreshing=true;const started=Date.now();let delay=5000;
 try{data=await api('/api/status');render();}
 catch(e){
  fresh=false;$('error').textContent=e.message;$('error').classList.remove('hidden');
  $('health').textContent='● Повтор подключения';renderPeers();
  // Abort settles before the next request starts. At most one status request
  // is active, and even immediate network failures cannot create a tight loop.
  delay=e.status===401?null:Math.max(0,1000-(Date.now()-started));
 }finally{
  refreshing=false;
  if(delay!==null)refreshTimer=setTimeout(refresh,delay);
 }
}
if($('login-form')){
 $('login-form').addEventListener('submit',async event=>{event.preventDefault();const button=event.currentTarget.querySelector('button');button.disabled=true;button.textContent='Проверяем…';$('login-error').textContent='';
  try{await api('/login',{username:$('username').value,password:$('password').value});location.href=base+'/';}
  catch(e){$('login-error').textContent=e.message;}finally{button.disabled=false;button.textContent='Войти в панель →';}
 });
}else{
 $('search').addEventListener('input',renderPeers);$('refresh').addEventListener('click',refresh);
 $('logout').addEventListener('click',async()=>{try{await api('/api/logout',{});location.href=base+'/';}catch(e){toast(e.message);}});
 $('cancel').addEventListener('click',()=>$('confirm').close());
 $('apply').addEventListener('click',async()=>{
  if(busy||!pending)return;busy=true;$('apply').disabled=true;
  try{data=await api('/api/action',pending);toast('Изменение подтверждено');$('confirm').close();render();}
  catch(e){toast(e.message);}finally{busy=false;$('apply').disabled=false;await refresh();}
 });
 $('edit-cancel').addEventListener('click',()=>$('edit-client').close());
 $('edit-form').addEventListener('submit',async event=>{
  event.preventDefault();if(busy)return;
  const value=$('client-quota').value.trim(), quota=value===''?0:Math.round(Number(value)*1024**3);
  if(!Number.isSafeInteger(quota)||quota<0||quota>1024**5){$('edit-error').textContent='Проверьте лимит';return;}
  busy=true;$('edit-save').disabled=true;
  try{data=await api('/api/edit',{id:$('edit-id').value,name:$('client-name').value,quota});$('edit-client').close();render();toast('Настройки сохранены');}
  catch(e){$('edit-error').textContent=e.message;}
  finally{busy=false;$('edit-save').disabled=false;await refresh();}
 });
 let qrGeneration=0;
 $('qr-open').addEventListener('click',()=>$('qr-dialog').showModal());
 function clearQr(){qrGeneration++;$('qr-image').removeAttribute('src');$('qr-image').classList.add('hidden');$('qr-text').value='';$('qr-file').value='';$('qr-error').textContent='';}
 $('qr-close').addEventListener('click',()=>$('qr-dialog').close());
 $('qr-dialog').addEventListener('close',clearQr);
 $('qr-file').addEventListener('change',async()=>{
  const file=$('qr-file').files[0];if(!file)return;
  if(file.size>2200){$('qr-error').textContent='Файл больше 2200 байт. Импортируйте его в приложение напрямую.';return;}
  $('qr-text').value=await file.text();
 });
 $('qr-form').addEventListener('submit',async event=>{
  event.preventDefault();const generation=++qrGeneration;
  $('qr-image').removeAttribute('src');$('qr-image').classList.add('hidden');$('qr-error').textContent='';$('qr-create').disabled=true;
  try{
   const result=await api('/api/qr',{text:$('qr-text').value});
   if(generation!==qrGeneration)return;
   $('qr-image').src=result.image;$('qr-image').classList.remove('hidden');$('qr-text').value='';$('qr-file').value='';
  }catch(e){if(generation===qrGeneration)$('qr-error').textContent=e.message;}
  finally{$('qr-create').disabled=false;}
 });
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
 refresh();
}
