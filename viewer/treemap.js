/* WizTree-style space map: squarified nested layout, folders as grey frames, files coloured by extension and
   painted with soft directional gradients, so items down to about one pixel stay visible.
   Reads the compact payload of api/map; no network access, source files or file contents here. */
const MAP_HEAD=15,MAP_PAD=1,MAP_HEIGHT=.65,MAP_FALLOFF=1;
// Distinct saturated colours handed out to the largest extensions first, like WizTree's file type list.
const MAP_PALETTE=[[28,92,56],[52,92,54],[118,58,50],[208,78,58],[300,58,60],[0,74,60],[178,64,46],[266,58,64],[84,60,50],[330,78,68],
 [42,78,42],[156,56,44],[232,60,68],[14,84,68],[194,78,62],[284,44,50],[64,72,62],[140,48,64],[350,46,50],[220,40,52]];
function hslRgb(h,s,l){s/=100;l/=100;const k=n=>(n+h/30)%12,a=s*Math.min(l,1-l),f=n=>l-a*Math.max(-1,Math.min(k(n)-3,9-k(n),1));return[f(0)*255,f(8)*255,f(4)*255]}
function mapHash(text){let hash=7;for(const c of text)hash=(hash*31+c.charCodeAt(0))|0;return Math.abs(hash)}
function decodeMap(buffer){
 const length=new DataView(buffer).getUint32(0,true),header=JSON.parse(new TextDecoder().decode(new Uint8Array(buffer,4,length)));
 const n=header.count;let at=4+length;const weight=new Float64Array(buffer,at,n);at+=8*n;
 const id=new Int32Array(buffer,at,n);at+=4*n;const parent=new Int32Array(buffer,at,n);at+=4*n;const ext=new Int32Array(buffer,at,n);
 // Children per item, largest first; the payload is breadth first, so every parent precedes its children.
 const count=new Int32Array(n),first=new Int32Array(n+1),kids=new Int32Array(Math.max(0,n-1));
 for(let i=1;i<n;i++)count[parent[i]]++;for(let i=0;i<n;i++)first[i+1]=first[i]+count[i];
 const fill=first.slice(0,n);for(let i=1;i<n;i++)kids[fill[parent[i]]++]=i;
 for(let i=0;i<n;i++)if(count[i]>1)kids.subarray(first[i],first[i+1]).sort((a,b)=>weight[b]-weight[a]||a-b);
 return{header,n,weight,id,parent,ext,count,first,kids,names:header.names,exts:header.exts};
}
function squarifyInto(map,p,x0,y0,W,H,x,y,w,h){
 const {weight,kids}=map,start=map.first[p],end=map.first[p+1];let total=0;for(let k=start;k<end;k++)total+=weight[kids[k]];
 if(!(total>0&&W>0&&H>0))return;const whole=Math.max(total,weight[p]),scale=W*H/whole,fill=total>=whole*(1-1e-9);let k=start;
 while(k<end){
  const side=Math.min(W,H),side2=side*side,top=weight[kids[k]]*scale;let sum=0,stop=k,best=Infinity;
  while(stop<end){const a=weight[kids[stop]]*scale,s=sum+a,worst=Math.max(side2*top/(s*s),s*s/(side2*a));if(stop>k&&worst>best)break;best=worst;sum=s;stop++}
  // Without unlisted remainder the last strip absorbs rounding; otherwise the remainder stays as folder area.
  const last=fill&&stop>=end;
  if(W>=H){const strip=last?W:Math.min(W,sum/H);let t=y0;for(let j=k;j<stop;j++){const c=kids[j],size=j===stop-1?y0+H-t:H*weight[c]*scale/sum;x[c]=x0;y[c]=t;w[c]=strip;h[c]=Math.max(0,size);t+=size}x0+=strip;W=Math.max(0,W-strip)}
  else{const strip=last?H:Math.min(H,sum/W);let t=x0;for(let j=k;j<stop;j++){const c=kids[j],size=j===stop-1?x0+W-t:W*weight[c]*scale/sum;x[c]=t;y[c]=y0;w[c]=Math.max(0,size);h[c]=strip;t+=size}y0+=strip;H=Math.max(0,H-strip)}
  k=stop;
 }
}
/* Folders up to `frameDepth` levels get a grey frame (title bar when large enough); deeper folders are
   still subdivided, just without a frame, so small files keep their own blocks at every depth. */
function layoutMap(map,width,height,frameDepth=99,scale=1){
 const n=map.n,x=new Float32Array(n),y=new Float32Array(n),w=new Float32Array(n),h=new Float32Array(n),depth=new Uint8Array(n),frame=new Uint8Array(n),surface=new Float32Array(4*n);
 if(!n)return{x,y,w,h,depth,frame,surface};w[0]=width;h[0]=height;
 const ridge=(i,height)=>{const s=4*i;if(w[i]>0){surface[s]-=4*height/w[i];surface[s+1]+=4*height*(2*x[i]+w[i])/w[i]}if(h[i]>0){surface[s+2]-=4*height/h[i];surface[s+3]+=4*height*(2*y[i]+h[i])/h[i]}};
 ridge(0,MAP_HEIGHT);
 for(let i=0;i<n;i++){
  if(!map.count[i]||!(w[i]>0&&h[i]>0))continue;
  let ix=x[i],iy=y[i],iw=w[i],ih=h[i];
  if(i&&iw>=75*scale&&ih>=48*scale&&iw*ih>=6500*scale*scale){const pad=(iw>=20*scale&&ih>=20*scale?MAP_PAD:1)*scale,head=iw>=50*scale&&ih>=34*scale?MAP_HEAD*scale:pad;frame[i]=head>pad?2:1;ix+=pad;iy+=head;iw-=2*pad;ih-=head+pad}
  squarifyInto(map,i,ix,iy,Math.max(0,iw),Math.max(0,ih),x,y,w,h);
  for(let k=map.first[i];k<map.first[i+1];k++){const c=map.kids[k];depth[c]=Math.min(255,depth[i]+1);surface.copyWithin(4*c,4*i,4*i+4);ridge(c,MAP_HEIGHT*MAP_FALLOFF**depth[c])}
 }
 return{x,y,w,h,depth,frame,surface};
}
const MAP_DUST=[96,98,104];
function folderGrey(depth){const v=Math.max(43,59-depth);return[v,v,v]}
/* Extension colours: the largest extensions in the current view get the distinct palette, the rest a hashed muted hue. */
function extensionColors(map){
 const totals=new Float64Array(map.exts.length);for(let i=1;i<map.n;i++)if(!map.count[i]&&map.ext[i]>=0)totals[map.ext[i]]+=map.weight[i];
 const order=[...totals.keys()].filter(e=>totals[e]>0&&map.exts[e]).sort((a,b)=>totals[b]-totals[a]);
 const rgb=new Float32Array(3*map.exts.length),css=[];
 map.exts.forEach((name,e)=>{const rank=order.indexOf(e);const hue=rank>=0&&rank<MAP_PALETTE.length?MAP_PALETTE[rank]:name?[mapHash(name)%360,34+mapHash(name+'s')%22,52+mapHash(name+'l')%14]:[210,6,62];const c=hslRgb(...hue);rgb.set(c,3*e);css[e]=`rgb(${c.map(Math.round).join(',')})`});
 return{rgb,css,order,totals};
}
class SpaceTreemap{
 constructor(stage,tooltip,options){
  this.stage=stage;this.tooltip=tooltip;this.options=options;this.map=null;this.selected=null;this.depth=99;this.hot=-1;this.frame=0;this.names=new Map();
  this.canvas=document.createElement('canvas');this.overlay=document.createElement('canvas');this.overlay.className='map-overlay';this.canvas.className='map-canvas';
  this.canvas.setAttribute('role','img');stage.replaceChildren(this.canvas,this.overlay);
  new ResizeObserver(()=>{cancelAnimationFrame(this.frame);this.frame=requestAnimationFrame(()=>this.render())}).observe(stage);
  stage.addEventListener('pointermove',e=>this.pointer(e));stage.addEventListener('pointerleave',()=>this.leave());
  stage.addEventListener('click',e=>{const i=this.at(e);if(i>=0){this.leave();this.options.onChoose(this.describe(i))}});
  stage.addEventListener('contextmenu',e=>{const i=this.at(e);if(i<0)return;e.preventDefault();this.leave();this.options.onContext(this.describe(i),e)});
 }
 pixels(){return Math.max(10000,Math.round(this.stage.clientWidth*this.stage.clientHeight))}
 setData(buffer){this.map=decodeMap(buffer);this.colors=extensionColors(this.map);this.names=new Map();this.leave();this.render()}
 setDepth(depth){this.depth=depth;this.render()}
 select(id){this.selected=id;this.paintOverlay()}
 legend(limit=8){if(!this.map)return[];const {order,totals,css}=this.colors,total=this.map.weight[0]||1;return order.slice(0,limit).map(e=>({label:'.'+this.map.exts[e],share:totals[e]/total,color:css[e]}))}
 /* What a position stands for: a record (folder or file) or a group of same-extension small files in a folder. */
 describe(i){
  const m=this.map,id=m.id[i],group=id<0,folderPos=group?m.parent[i]:i,dir=!group&&m.ext[i]<0;
  return{position:i,id:group?m.id[m.parent[i]]:id,group,files:group?-id:1,directory:dir,weight:m.weight[i],ext:m.ext[i]>=0?m.exts[m.ext[i]]:null,
   name:m.names[i]??this.names.get(id)?.name??null,folder:m.names[folderPos]??null,parentId:i?m.id[m.parent[i]]:null};
 }
 at(e){if(!this.hit)return-1;const r=this.canvas.getBoundingClientRect(),px=Math.floor((e.clientX-r.left)*this.scale),py=Math.floor((e.clientY-r.top)*this.scale);return px<0||py<0||px>=this.W||py>=this.H?-1:this.hit[py*this.W+px]}
 pointer(e){const i=this.at(e);if(i<0)return this.leave();if(i!==this.hot){this.hot=i;this.paintOverlay()}this.showTooltip(i,e)}
 leave(){if(this.hot<0&&this.tooltip.classList.contains('hidden'))return;this.hot=-1;this.hideTooltip();this.paintOverlay()}
 hideTooltip(){this.tooltip.classList.add('hidden')}
 showTooltip(i,event){
  const item=this.describe(i),f=this.options.format,box=this.tooltip,total=this.map.weight[0];box.replaceChildren();
  const heading=document.createElement('b'),path=document.createElement('div'),values=document.createElement('div'),hint=document.createElement('small');path.className='tooltip-path';values.className='tooltip-values';
  if(item.group){heading.textContent=item.files.toLocaleString('zh-CN')+' 个'+(item.ext?' .'+item.ext:'无扩展名')+' 小文件';path.textContent='位于 '+(item.folder??'所属文件夹')+'，每个都小于约 1 像素，合并为一块'}
  else{heading.textContent=item.name??(item.directory?'文件夹':'文件')+' #'+item.id;path.textContent=this.names.get(item.id)?.path??'';}
  const share=total>0?100*item.weight/total:0;values.textContent=(item.directory?'文件夹':item.group?'文件组':item.ext?'.'+item.ext+' 文件':'文件')+' · '+f(item.weight)+' · '+(share<.1?'<0.1':share.toFixed(1))+'%';
  hint.textContent=item.directory?'单击进入此文件夹':item.group?'单击进入所属文件夹':'单击定位到所属文件夹并选中';box.append(heading,path,values,hint);box.classList.remove('hidden');
  box.style.left=Math.max(8,Math.min(event.clientX+16,innerWidth-box.offsetWidth-12))+'px';box.style.top=Math.max(8,Math.min(event.clientY+18,innerHeight-box.offsetHeight-12))+'px';
  if(!item.group&&!this.names.has(item.id)&&this.options.lookup){this.names.set(item.id,null);this.options.lookup(item.id).then(row=>{this.names.set(item.id,{name:row.name,path:row.path});if(this.hot===i)this.showTooltip(i,event)},()=>this.names.delete(item.id))}
 }
 render(){
  const m=this.map,cssW=this.stage.clientWidth,cssH=this.stage.clientHeight;if(!m||!cssW||!cssH)return;
  const scale=this.scale=window.devicePixelRatio||1,W=this.W=Math.round(cssW*scale),H=this.H=Math.round(cssH*scale);
  for(const c of [this.canvas,this.overlay]){c.width=W;c.height=H;c.style.width=cssW+'px';c.style.height=cssH+'px'}
  const ctx=this.canvas.getContext('2d');
  if(!(m.weight[0]>0)){ctx.clearRect(0,0,W,H);this.hit=null;this.options.onRendered?.(0);return}
  const L=this.layout=layoutMap(m,W,H,this.depth,scale),{x,y,w,h,frame,depth,surface}=L,image=ctx.createImageData(W,H),px=image.data,hit=this.hit=new Int32Array(W*H).fill(-1),rgb=this.colors.rgb;
  let drawn=0;
  for(let i=0;i<m.n;i++){
   const x0=Math.max(0,Math.round(x[i])),x1=Math.min(W,Math.round(x[i]+w[i])),y0=Math.max(0,Math.round(y[i])),y1=Math.min(H,Math.round(y[i]+h[i]));
   if(x1<=x0||y1<=y0)continue;
   if(m.count[i]&&!frame[i]){for(let r=y0;r<y1;r++){for(let o=4*(r*W+x0),e=4*(r*W+x1);o<e;o+=4){px[o]=px[o+1]=px[o+2]=84;px[o+3]=255}hit.fill(i,r*W+x0,r*W+x1)}continue}
   if(frame[i]){
    const [g]=folderGrey(depth[i]),edge=Math.max(24,g-10);
    for(let r=y0;r<y1;r++){const border=r===y0||r===y1-1;for(let c=x0,o=4*(r*W+x0);c<x1;c++,o+=4){const v=border||c===x0||c===x1-1?edge:g;px[o]=px[o+1]=px[o+2]=v;px[o+3]=255}hit.fill(i,r*W+x0,r*W+x1)}
    continue;
   }
   drawn++;
   const e=m.ext[i];
   const tint=.78+((Math.imul(m.id[i],2654435761)>>>0)%101)/250;
   const base=e>=0?[rgb[3*e],rgb[3*e+1],rgb[3*e+2]]:MAP_DUST;
   const [cr,cg,cb]=base.map(c=>Math.min(255,c*tint));
   const separated=w[i]>=14*scale&&h[i]>=14*scale&&w[i]*h[i]>=650*scale*scale;
   for(let r=y0;r<y1;r++){
    const vertical=(r-y0+.5)/(y1-y0);
    for(let c=x0,o=4*(r*W+x0);c<x1;c++,o+=4){
     // Side lighting only: brightness decreases smoothly from upper-left to lower-right.
     const horizontal=(c-x0+.5)/(x1-x0);
     const v=.72-.18*horizontal-.20*vertical;
     const edge=separated&&(r===y0||c===x0)?.91:1;
     px[o]=cr*v*edge;px[o+1]=cg*v*edge;px[o+2]=cb*v*edge;px[o+3]=255;
    }
    hit.fill(i,r*W+x0,r*W+x1);
   }
  }
  ctx.putImageData(image,0,0);
  ctx.font=`600 ${11*scale}px "Segoe UI","Microsoft YaHei",sans-serif`;ctx.textBaseline='middle';ctx.fillStyle='#f3f4f6';
  for(let i=1;i<m.n;i++){
   if(frame[i]!==2)continue;const name=m.names[i];if(name==null)continue;
   const room=w[i]-8*scale;let text=name+' ('+this.options.format(m.weight[i])+')';
   if(ctx.measureText(text).width>room){text=name;while(text.length>1&&ctx.measureText(text+'…').width>room)text=text.slice(0,-1);text+='…';if(text.length<3)continue}
   ctx.fillText(text,x[i]+4*scale,y[i]+(MAP_HEAD*scale+MAP_PAD*scale)/2);
  }
  this.paintOverlay();this.options.onRendered?.(drawn);
 }
 /* Hover outline on the item plus a lighter outline on the folder frame that contains it; selection in white. */
 paintOverlay(){
  const ctx=this.overlay.getContext('2d'),L=this.layout,m=this.map;ctx.clearRect(0,0,this.overlay.width,this.overlay.height);if(!L||!m)return;
  const box=(i,color,width)=>{if(i<0||!(L.w[i]>0))return;ctx.lineWidth=width*this.scale;ctx.strokeStyle=color;const o=ctx.lineWidth/2;ctx.strokeRect(L.x[i]+o,L.y[i]+o,Math.max(0,L.w[i]-2*o),Math.max(0,L.h[i]-2*o))};
  if(this.selected!=null)for(let i=1;i<m.n;i++)if(m.id[i]===this.selected){box(i,'#000',3);box(i,'#fff',1.5);break}
  if(this.hot>=0){let up=m.parent[this.hot];while(up>0&&!L.frame[up])up=m.parent[up];if(up>0)box(up,'#ffd84a',2);box(this.hot,'#000',3);box(this.hot,'#fff',1.5)}
 }
}
if(typeof module!=='undefined')module.exports={decodeMap,layoutMap,squarifyInto,extensionColors,hslRgb};
