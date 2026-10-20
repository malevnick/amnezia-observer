const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const nodes=new Map();
function element(tag='div') {return {tag,children:[],attrs:{},textContent:'',classList:{toggle(){}},append(...children){this.children.push(...children)},replaceChildren(){this.children=[]},setAttribute(k,v){this.attrs[k]=v}};}
const context=vm.createContext({document:{getElementById(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)},querySelector(){return {content:''}},createElement:element,createElementNS:(_,tag)=>element(tag)},Date,setTimeout,clearTimeout,AbortController});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/ui.js'),'utf8').split("if($('login-form')){")[0],context);
const peers=[
 {id:'a',name:'Alpha',addresses:'10.0.0.1',state:'online',blocked:false,month_used:500,quota:0,handshake:null},
 {id:'b',name:'Beta',addresses:'10.0.0.2',state:'offline',blocked:false,month_used:100,quota:200,handshake:100},
 {id:'c',name:'Gamma',addresses:'10.0.0.3',state:'blocked',blocked:true,month_used:200,quota:200,handshake:50}];
context.peers=peers;
function sorted(order,query=''){context.order=order;context.query=query;return Array.from(vm.runInContext('sortedPeers(peers,query,order).map(p=>p.id)',context));}
assert.deepEqual(sorted('online'),['a','b','c']);assert.equal(sorted('offline')[0],'b');assert.equal(sorted('blocked')[0],'c');
assert.deepEqual(sorted('name'),['a','b','c']);assert.deepEqual(sorted('name-desc'),['c','b','a']);
assert.deepEqual(sorted('traffic'),['a','c','b']);assert.deepEqual(sorted('quota'),['c','b','a']);assert.deepEqual(sorted('handshake'),['b','c','a']);
assert.deepEqual(sorted('quota','10.0.0.2'),['b']);assert.deepEqual(sorted('name','absent'),[]);
assert.deepEqual(peers.map(p=>p.id),['a','b','c']);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/history.js'),'utf8').split("$('history-refresh').addEventListener")[0],context);
for(const days of [[],[{day:'2026-11-01',rx:0,tx:0,total:0}],[{day:'2026-11-01',rx:10,tx:20,total:30},{day:'2026-11-02',rx:50,tx:5,total:55}]]){
 context.days=days;vm.runInContext('drawHistory(days)',context);
 assert.equal(nodes.get('history-days').children.length,days.length);
 assert.ok(!JSON.stringify(nodes.get('history-chart')).match(/NaN|Infinity/));
 assert.equal(nodes.get('history-chart').children.filter(n=>n.tag==='polyline').length,3);
}
console.log('PASS: all eight sorts, filter, missing quotas/handshakes, no input mutation, empty/one-day/multi-day charts.');
