const assert=require('assert'),fs=require('fs'),vm=require('vm');
const source=fs.readFileSync(__dirname+'/viewer.js','utf8');
const scan=source.slice(source.indexOf('async function pollScan(){'),source.indexOf('let watchedPlanScan='));
const plan=source.slice(source.indexOf('let watchedPlanScan='),source.indexOf('async function startScan(){'));
(async()=>{
 for(const [status,phase,delay]of [['idle',null,1500],['done','done',1500],['running','scanning',160]]){
  const timers=[],nodes={};const ctx={pollTimer:1,meta:{generation:1},scanning:false,phases:{done:'done',scanning:'scanning'},api:async()=>({status,phase,generation:1,percent:100,elapsed_seconds:1,target:'C:'}),$:id=>nodes[id]??=( {removeAttribute(){}}),scanControls(){},clearStatus(){},status(){},num:String,setTimeout:(fn,ms)=>{timers.push(ms);return 2},clearTimeout(){}};
  vm.createContext(ctx);vm.runInContext(scan,ctx);await vm.runInContext('pollScan()',ctx);assert.equal(timers.at(-1),delay,'scan poll '+status);
 }
 let revision='r1',switches=0,loads=0;const nodes={advicePanel:{scrollTop:42},adviceTab:{},planPreview:{open:false}};
 const ctx={meta:{generation:1},document:{hidden:false},scanning:false,mode:'tree',parent:5,rootId:5,selected:null,adviceData:{},adviceFilter:'all',api:async()=>({scan_id:'s',revision,source:'agent'}),$:id=>nodes[id],switchMode:async()=>{switches++;ctx.mode='advice'},loadAdvice:async()=>{loads++},setTimeout(){}};
 vm.createContext(ctx);vm.runInContext(plan,ctx);await vm.runInContext('watchPlan()',ctx);assert.equal(switches,1,'first draft becomes visible');
 await vm.runInContext('watchPlan()',ctx);assert.equal(loads,0,'unchanged plan does not reload');
 ctx.mode='tree';ctx.parent=6;revision='r2';await vm.runInContext('watchPlan()',ctx);assert.equal(switches,1,'do not interrupt folder browsing');assert(nodes.adviceTab.textContent.includes('更新'));
 ctx.mode='advice';revision='r3';await vm.runInContext('watchPlan()',ctx);assert.equal(loads,1,'advice refreshes on revision');assert.equal(nodes.advicePanel.scrollTop,42,'keep reading position');
 console.log('Live workflow tests passed');
})().catch(e=>{console.error(e);process.exit(1)});
