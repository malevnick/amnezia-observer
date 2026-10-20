const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const nodes=new Map();
function node(id){
 if(!nodes.has(id))nodes.set(id,{textContent:'',addEventListener(){},classList:{remove(){},add(){}}});
 return nodes.get(id);
}
let calls=0,active=0,peak=0,aborted=0,rendered=0,firstAt=0,secondAt=0;
let actionResolve,actionSignal;
const context=vm.createContext({
 document:{hidden:false,querySelector:()=>({content:''}),getElementById:node},
 location:{href:''},AbortController,Date,setTimeout,clearTimeout,
 fetch:async(path,options)=>{
  if(path!='/api/status'){
   actionSignal=options.signal;
   return new Promise(resolve=>{actionResolve=()=>resolve({ok:true,status:200,json:async()=>({ok:true})});});
  }
  calls++;active++;peak=Math.max(peak,active);
  if(calls===1){
   firstAt=Date.now();
   // Headers arrived (HTTP 200), but the response body is stuck, as in the screenshot.
   return {ok:true,status:200,json:()=>new Promise((resolve,reject)=>{
    options.signal.addEventListener('abort',()=>{
     aborted++;active--;const e=new Error('aborted');e.name='AbortError';reject(e);
    },{once:true});
   })};
  }
  secondAt=Date.now();active--;
  return {ok:true,status:200,json:async()=>({peers:[]})};
 },
 recordRender:()=>rendered++
});
vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../static/ui.js'),'utf8'),context);
vm.runInContext('render=()=>recordRender(); renderPeers=()=>{}; refresh(); refresh();',context);
(async()=>{
 await new Promise(r=>setTimeout(r,1250));
 assert.equal(aborted,1);assert.equal(calls,2);assert.equal(peak,1);assert.equal(rendered,1);
 assert.ok(secondAt-firstAt>=950&&secondAt-firstAt<2000);
 vm.runInContext('clearTimeout(refreshTimer)',context);
 const action=vm.runInContext("api('/api/action',{id:'test',block:true})",context);
 await new Promise(r=>setTimeout(r,1100));
 assert.equal(actionSignal.aborted,false);
 actionResolve();await action;
 console.log('PASS: stalled HTTP 200 body aborted at 1s, next request starts immediately, no overlap, POST timeout unchanged.');
})().catch(e=>{console.error(e);process.exitCode=1;});
