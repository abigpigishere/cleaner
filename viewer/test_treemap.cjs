// Run with: node viewer/test_treemap.cjs
const assert=require('assert');
const {decodeMap,layoutMap,extensionColors}=require('./treemap.js');
// Build a payload exactly as View.map_items does: u32 header length, padded JSON, f64 sizes, i32 ids/parents/exts.
function payload(items,exts=['dll','exe','']){
 const n=items.length,names={};items.forEach((it,i)=>{if(it.name)names[i]=it.name});
 let text=Buffer.from(JSON.stringify({count:n,exts,names,root:{path:'C:\\'}}));const pad=(8-(4+text.length)%8)%8;text=Buffer.concat([text,Buffer.alloc(pad,32)]);
 const buffer=new ArrayBuffer(4+text.length+20*n),view=new DataView(buffer);view.setUint32(0,text.length,true);new Uint8Array(buffer,4).set(text);
 let at=4+text.length;items.forEach((it,i)=>{view.setFloat64(at+8*i,it.size,true);view.setInt32(at+8*n+4*i,it.id,true);view.setInt32(at+12*n+4*i,it.parent,true);view.setInt32(at+16*n+4*i,it.ext,true)});
 return buffer;
}
// root(100) -> A(60: a1 40, a2 15, unlisted 5), B(30 file), C(10 file); A is a folder (ext -1).
const items=[{id:5,parent:-1,ext:-1,size:100,name:'C:'},{id:6,parent:0,ext:-1,size:60,name:'A'},{id:7,parent:0,ext:1,size:30},{id:8,parent:0,ext:0,size:10},
 {id:9,parent:1,ext:0,size:40},{id:-12,parent:1,ext:2,size:15}];
const map=decodeMap(payload(items));
assert.strictEqual(map.n,6);assert.deepStrictEqual([...map.kids.subarray(map.first[0],map.first[1])],[1,2,3]);
assert.strictEqual(map.names[1],'A');
const W=400,H=300,L=layoutMap(map,W,H);
const area=i=>L.w[i]*L.h[i],inside=(c,p)=>L.x[c]>=L.x[p]-1e-3&&L.y[c]>=L.y[p]-1e-3&&L.x[c]+L.w[c]<=L.x[p]+L.w[p]+1e-3&&L.y[c]+L.h[c]<=L.y[p]+L.h[p]+1e-3;
// Top level fills the canvas in proportion to size.
for(const i of [1,2,3])assert(Math.abs(area(i)/(W*H)-map.weight[i]/100)<1e-6,'top-level area '+i);
// Children stay inside their framed folder and keep their ratio of the folder's inner area; unlisted size is left as folder area.
assert.strictEqual(L.frame[1],2,'large folder gets a titled frame');
for(const c of [4,5])assert(inside(c,1),'child inside folder '+c);
assert(Math.abs(area(4)/area(5)-40/15)<1e-6,'sibling ratio');
assert(area(4)+area(5)<area(1),'frame and unlisted area remain');
// Siblings do not overlap.
for(const [a,b] of [[1,2],[1,3],[2,3],[4,5]]){const ox=Math.min(L.x[a]+L.w[a],L.x[b]+L.w[b])-Math.max(L.x[a],L.x[b]),oy=Math.min(L.y[a]+L.h[a],L.y[b]+L.h[b])-Math.max(L.y[a],L.y[b]);assert(!(ox>1e-6&&oy>1e-6),'overlap '+a+','+b)}
// Automatic framing: tiny folders have no frame and their children retain exact weight ratios.
const flat=layoutMap(map,40,30);assert.strictEqual(flat.frame[1],0);assert(Math.abs(flat.w[4]*flat.h[4]+flat.w[5]*flat.h[5]-flat.w[1]*flat.h[1]*55/60)<1e-3);
// Colours: the biggest extension (.dll 50 vs .exe 30) gets the first palette entry; distinct colours per extension.
const colors=extensionColors(map);assert.deepStrictEqual(colors.order.slice(0,2),[0,1]);assert.notStrictEqual(colors.css[0],colors.css[1]);
console.log('treemap tests passed');
