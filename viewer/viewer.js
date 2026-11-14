const $=id=>document.getElementById(id);const base=new URL('.',location.href);let meta,mode='tree',parent=5,offset=0,total=0,query='',busy=false,selected=null,requestId=0;const pageSize=100;const treeNodes=new Map();let rootId=5,scanning=false,pollTimer=null,mapView=null,mapRootId=5,mapRequest=0;
const fmt=n=>n==null?'待解析':n===0?'0 B':n>=1073741824?(n/1073741824).toFixed(2)+' GiB':n>=1048576?(n/1048576).toFixed(2)+' MiB':n>=1024?(n/1024).toFixed(1)+' KiB':n+' B';const num=n=>n.toLocaleString('zh-CN');
function el(tag,text,cls){const e=document.createElement(tag);if(text!=null)e.textContent=text;if(cls)e.className=cls;return e}function status(text){$('status').textContent=text;$('status').classList.remove('hidden')}function clearStatus(){$('status').classList.add('hidden')}
async function api(route,params={},body){
 const url=new URL('api/'+route,base);Object.entries(params).forEach(([k,v])=>url.searchParams.set(k,v));
 const options={cache:'no-store'};
 if(body!==undefined){options.method='POST';options.headers={'Content-Type':'application/json','X-Cleaner-Token':base.pathname.split('/').filter(Boolean)[0]};options.body=JSON.stringify(body)}
 const response=await fetch(url,options);const value=await response.json();
 if(!response.ok)throw new Error(value.error||'本地服务无法完成请求');return value;
}
async function apiBuffer(route,params){const url=new URL('api/'+route,base);Object.entries(params).forEach(([k,v])=>url.searchParams.set(k,v));const response=await fetch(url,{cache:'no-store'});if(!response.ok)throw new Error((await response.json()).error||'本地服务无法完成请求');return response.arrayBuffer()}
function metric(){return $('metric').value}function setBusy(value){busy=value;$('prev').disabled=value||offset===0;$('next').disabled=value||offset+pageSize>=total}
let adviceData=null,adviceFilter='all',selectedItems=new Map(),pageRows=[],adviceRowsVisible=[],previewRequest=0;
function applyMode(){
 $('treeTab').classList.toggle('active',mode==='tree');$('filesTab').classList.toggle('active',mode==='files');$('adviceTab').classList.toggle('active',mode==='advice');
 $('advicePanel').classList.toggle('hidden',mode!=='advice');
 for(const selector of ['.content > .location','.content > .table-wrap','.content > .pagination'])document.querySelector(selector).classList.toggle('hidden',mode==='advice');
 $('searchForm').classList.toggle('hidden',mode!=='files');$('metric').disabled=mode==='advice';$('back').disabled=mode!=='tree'||parent===rootId;
}
async function switchMode(next){mode=next;offset=0;applyMode();if(mode==='advice'){requestId++;clearStatus();await Promise.all([loadAdvice(),loadMap(rootId)]);return}await Promise.all([load(),loadMap(mode==='files'?rootId:parent)])}
function renderGoal(){
 const input=$('goalSpace'),target=Number(input.value)*1073741824,free=meta?.disk?.free;
 if(!(target>0)||!Number.isFinite(target)||free==null){$('goalSummary').textContent='输入目标后查看空间缺口';return}
 $('goalSummary').textContent='当前可用 '+fmt(free)+' · '+(free>=target?'已达到目标':'还需释放 '+fmt(target-free));
}
async function loadAdvice(){
 const generation=meta.generation;
 if(adviceData?.generation===generation){renderAdvice();return}
 $('adviceRows').replaceChildren(el('div','正在从本次扫描中整理候选项目…','advice-empty'));
 try{const data=await api('advice');$('exportPlan').href=new URL('api/plan',base).href;if(meta.generation!==generation)return;adviceData={...data,generation};if(mode==='advice')renderAdvice()}
 catch(error){if(mode==='advice')$('adviceRows').replaceChildren(el('div','无法读取建议：'+error.message,'advice-empty'))}
}
function renderAdvice(){
 if(!adviceData)return;renderGoal();
 $('adviceSummary').textContent=num(adviceData.candidate_count)+' 个候选 · 合计占用 '+fmt(adviceData.candidate_bytes)+'；这是占用量，不代表可以全部删除或等额释放。';
 const rows=adviceData.rows.filter(r=>adviceFilter==='all'||(adviceFilter==='apps'?['app','move'].includes(r.kind):r.kind===adviceFilter));
 adviceRowsVisible=rows;const box=$('adviceRows');box.replaceChildren();
 document.querySelectorAll('[data-advice-filter]').forEach(b=>b.classList.toggle('active',b.dataset.adviceFilter===adviceFilter));
 if(!rows.length){box.append(el('div','此类别没有识别到合适的候选项目。可以切换类别，或通过目录树继续查看。','advice-empty'));return}
 for(const row of rows){
  const card=el('article',null,'advice-card'),heading=el('div',null,'advice-card-heading'),name=el('div',null,'advice-name');
  name.append(pickBox(row),icon(row.directory),el('b',row.name));heading.append(name,el('strong',(row.unknown?'至少 ':'')+fmt(row.allocated),'advice-size'));
  const path=el('div',row.path,'advice-path');path.title=row.path;
  const tag=el('span',row.label,'advice-tag '+row.kind),reason=el('p',row.reason,'advice-reason'),action=el('p',row.action,'advice-action');
  const actions=el('div',null,'advice-card-actions'),locate=el('button','定位查看'),open=el('button','在资源管理器中查看');
  locate.type=open.type='button';locate.onclick=()=>row.directory?navigate(row.id):revealFile(row.id);open.onclick=()=>explorer(row);
  actions.append(locate,open);card.append(heading,path,tag,reason,action,actions);card.onclick=e=>{if(!e.target.closest('button,input,label,select,a'))select(row.id)};box.append(card);
 }
}
function pickBox(row){
 const checkbox=el('input');checkbox.type='checkbox';checkbox.className='item-pick';checkbox.dataset.pickId=row.id;checkbox.checked=selectedItems.has(row.id);checkbox.setAttribute('aria-label','加入清单 '+row.name);
 checkbox.onclick=e=>e.stopPropagation();checkbox.onchange=()=>{if(checkbox.checked)addSelection(row);else selectedItems.delete(row.id);syncSelection()};return checkbox;
}
function addSelection(row){
 if(selectedItems.size>=200&&!selectedItems.has(row.id)){status('一次最多预览 200 项，请分批选择。');return}
 if(!selectedItems.has(row.id))selectedItems.set(row.id,{row,operation:row.operation||row.suggested_operation||'review'});
}
function selectionOccupied(){
 const paths=[];let bytes=0;
 for(const {row} of [...selectedItems.values()].sort((a,b)=>a.row.path.length-b.row.path.length)){
  const p=row.path.replace(/\\+$/,'').toLowerCase();if(paths.some(q=>p===q||p.startsWith(q+'\\')))continue;paths.push(p);bytes+=row.allocated||0;
 }return bytes;
}
function syncSelection(){
 document.querySelectorAll('[data-pick-id]').forEach(c=>c.checked=selectedItems.has(Number(c.dataset.pickId)));
 $('selectionSummary').textContent=selectedItems.size?'已选 '+num(selectedItems.size)+' 项 · 去重占用 '+fmt(selectionOccupied()):'尚未勾选项目';
 $('previewSelection').disabled=!selectedItems.size;$('clearSelection').disabled=!selectedItems.size;
 const count=pageRows.filter(r=>selectedItems.has(r.id)).length;$('selectPage').checked=!!pageRows.length&&count===pageRows.length;$('selectPage').indeterminate=count>0&&count<pageRows.length;
}
async function previewSelection(){
 if(!selectedItems.size)return;
 const request=++previewRequest;const dialog=$('planPreview');if(!dialog.open)dialog.showModal();
 $('previewRows').replaceChildren(el('div','正在核对本次扫描和处理方式…','advice-empty'));$('previewSaved').textContent='';$('downloadPreview').classList.add('hidden');
 try{
  if(adviceData?.generation!==meta.generation)await loadAdvice();
  const data=await api('preview',{}, {scan_id:adviceData.scan_id,revision:adviceData.revision,items:[...selectedItems].map(([id,v])=>({id,operation:v.operation}))});
  if(request!==previewRequest)return;
  $('previewSummary').textContent='已选 '+num(data.selected_count)+' 项 · 去重占用 '+fmt(data.occupied_bytes)+(data.covered_count?' · '+num(data.covered_count)+' 项包含在父目录中':'')+'。可释放量需在后续执行后核实。';
  const rows=$('previewRows');rows.replaceChildren();
  for(const item of data.items){
   const card=el('article',null,'preview-item'),heading=el('div',null,'advice-card-heading');heading.append(el('b',item.name),el('strong',fmt(item.allocated),'advice-size'));
   const path=el('div',item.path,'advice-path'),label=el('label',null,'preview-operation'),operation=el('select');operation.setAttribute('aria-label','处理方式 '+item.name);
   for(const code of item.allowed_operations){const option=el('option',adviceData.operation_labels[code]||code);option.value=code;operation.append(option)}operation.value=item.operation;
   operation.onchange=()=>{const entry=selectedItems.get(item.id);if(entry)entry.operation=operation.value;previewSelection()};label.append(el('span','计划方式'),operation);
   card.append(heading,path,label,el('p',item.reason,'advice-reason'));if(item.covered_by!=null)card.append(el('p','占用已计入所选父目录，不重复相加。','preview-covered'));
   for(const note of item.notes)card.append(el('p',note,'preview-note'));
   if(item.estimated_release_bytes===0)card.append(el('p','此方式预计立即释放：0 B','preview-note'));rows.append(card);
  }
  $('previewSaved').textContent='只读清单已保存，未执行文件操作。';$('downloadPreview').href=new URL('api/preview',base).href;$('downloadPreview').classList.remove('hidden');
 }catch(error){if(request===previewRequest){$('previewSummary').textContent='无法生成预览';$('previewRows').replaceChildren(el('div',error.message,'advice-empty'))}}
}
async function navigate(id){
 parent=id;mode='tree';offset=0;selected=null;applyMode();$('treeTab').classList.add('active');$('filesTab').classList.remove('active');$('searchForm').classList.add('hidden');$('back').disabled=id===rootId;$('rows').parentElement.parentElement.scrollTop=0;
 const row=await api('node',{id});if(parent!==id)return;$('location').textContent=row.path;showDetail(row);
 await Promise.all([load(),loadMap(id),revealTree(id)]);
}
async function load(){const rid=++requestId;setBusy(true);status(mode==='files'&&query?'正在搜索全部已索引文件名…':'正在读取已有索引…');try{const [data,owner]=await Promise.all([api('list',{mode,parent,metric:metric(),query:mode==='files'?query:'',offset,limit:pageSize,min_bytes:mode==='files'?Number($('minFileSize').value)*1048576:0,type:mode==='files'?$('fileType').value:'all'}),mode==='files'?meta.root:api('node',{id:parent})]);if(rid!==requestId)return;total=data.total;renderRows(data.rows,owner[metric()]);$('pageInfo').textContent=(total?num(offset+1)+'–'+num(Math.min(offset+pageSize,total)):'0')+' / '+num(total)+' 项 · 查询 '+data.query_seconds.toFixed(3)+'s';$('location').textContent=mode==='files'?(query?'全部文件 · 名称包含 “'+query+'”':'全部可关联文件 · 大小降序'):owner.path;setBusy(false);clearStatus();if(selected!=null){const row=$('rows').querySelector('[data-id="'+selected+'"]');row?.scrollIntoView({block:'nearest'})}}catch(error){if(rid!==requestId)return;setBusy(false);status(error.message)}}
function icon(directory){return el('span',null,'icon '+(directory?'folder':'file'))}
function renderRows(rows,whole){pageRows=rows;$('rows').replaceChildren();$('empty').classList.toggle('hidden',rows.length>0);$('empty').textContent=mode==='files'?'没有符合文件名的结果。':'此目录没有已关联的子项。';for(const row of rows){const tr=el('tr');tr.dataset.id=row.id;tr.tabIndex=0;if(row.id===selected)tr.className='selected';const name=el('td'),wrap=el('div',null,'file-name');wrap.append(icon(row.directory),el('strong',row.name));name.append(wrap);if(mode==='files')name.append(el('span',row.path,'file-sub'));const ratio=whole>0&&row[metric()]>0?row[metric()]/whole:0,share=el('td',null,'share-cell'),meter=el('div',null,'share'),fill=el('i');fill.style.width=Math.min(100,ratio*100)+'%';meter.append(fill);share.append(meter,el('span',ratio?(ratio<.001?'<0.1%':(ratio*100).toFixed(1)+'%'):'—','number'));
 const pick=el('td',null,'pick-column');pick.append(pickBox(row));tr.append(pick,name,share,el('td',fmt(row.logical),'number'),el('td',fmt(row.allocated),'number'));const state=el('td');state.append(el('span',row.unknown?(row.directory?num(row.unknown)+' 项未知':'待解析'):row.directory?'目录':'已解析','badge'+(row.unknown?' unknown':'')));tr.append(state);tr.addEventListener('click',e=>{if(!e.target.closest('input'))select(row.id)});tr.addEventListener('dblclick',()=>row.directory?navigate(row.id):explorer(row));tr.addEventListener('contextmenu',e=>{e.preventDefault();select(row.id);contextMenu(row,e)});tr.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();row.directory?navigate(row.id):select(row.id)}if(e.key==='F10'&&e.shiftKey){e.preventDefault();contextMenu(row,null,tr)}};name.title=row.path;$('rows').append(tr)}syncSelection()}
async function select(id){selected=id;mapView?.select(id);document.querySelectorAll('#rows tr').forEach(r=>r.classList.toggle('selected',Number(r.dataset.id)===id));try{showDetail(await api('node',{id}))}catch(e){status(e.message)}}
function showDetail(row){const content=el('div',null,'detail-content');content.append(el('span',null,'icon big '+(row.directory?'folder':'file')),el('h2',row.name),el('div',row.path,'detail-path'));const open=el('button',row.directory?'在资源管理器中打开':'在资源管理器中选中');open.onclick=()=>explorer(row);const copy=el('button','复制完整路径');copy.onclick=()=>copyPath(row);content.append(open,copy);const dl=el('dl');const add=(label,value)=>dl.append(el('dt',label),el('dd',value));add(row.directory?'已知逻辑总量':'逻辑大小',fmt(row.logical));add(row.directory?'已归属磁盘占用':'磁盘占用',fmt(row.allocated));if(row.directory){add('直属子项',num(row.child_count));add('后代未知大小文件',num(row.unknown))}else{add('硬链接数量',num(row.links));add('解析状态',row.unknown?'未完成大小解析':'已解析');const streams=row.metadata?.streams||[];add('当前记录中的数据流',num(streams.length))}add('文件记录号',String(row.id));content.append(dl);const note=row.unknown?'大小未完整解析，不能根据此数值判断可以释放多少空间。':row.directory?(meta.report.allocation_model?'磁盘占用包含命名数据流、目录索引和属性列表。硬链接按一个父目录归属计入，跨目录不重复累计。':'目录按一个父目录归属累计；命名流与目录索引等尚未计入。'):'数据簇占用不是删除后保证释放量，硬链接仍存在时可能不释放。';content.append(el('p',note,'detail-note'));$('details').replaceChildren(content)}
function treeNode(row,depth){
 const item=el('div'),line=el('div',null,'tree-item');line.dataset.id=row.id;line.style.paddingLeft=(6+depth*13)+'px';const toggle=el('button','▸','toggle');toggle.setAttribute('aria-label','展开 '+row.name);line.append(toggle,icon(true),el('span',row.name,'tree-name'));line.title=row.path;const children=el('div',null,'hidden');item.append(line,children);
 const entry={row,depth,item,line,toggle,children,open:false,loaded:false,loading:null};
 entry.expand=async()=>{entry.open=true;toggle.textContent='▾';toggle.setAttribute('aria-label','收起 '+row.name);children.classList.remove('hidden');if(!entry.loaded){if(!entry.loading)entry.loading=appendFolders(row.id,children,depth+1,0).then(()=>{entry.loaded=true});await entry.loading}};
 toggle.onclick=async e=>{e.stopPropagation();if(entry.open){entry.open=false;toggle.textContent='▸';toggle.setAttribute('aria-label','展开 '+row.name);children.classList.add('hidden')}else await entry.expand()};line.onclick=()=>navigate(row.id);line.ondblclick=()=>entry.expand();line.oncontextmenu=e=>{e.preventDefault();contextMenu(row,e)};treeNodes.set(row.id,entry);return item;
}
async function appendFolders(id,target,depth,start){try{const data=await api('list',{mode:'tree',parent:id,metric:'allocated',dirs:1,offset:start,limit:100});for(const row of data.rows)if(!treeNodes.has(row.id))target.append(treeNode(row,depth));if(start+data.rows.length<data.total){const more=el('button','继续显示目录…','tree-more');more.onclick=async()=>{more.remove();await appendFolders(id,target,depth,start+data.rows.length)};target.append(more)}else if(data.total===0)target.append(el('div','无子目录','tree-more'))}catch(e){target.append(el('div',e.message,'tree-more'))}}
async function revealTree(id){
 try{const data=await api('lineage',{id});for(let i=0;i<data.rows.length;i++){const row=data.rows[i];if(!treeNodes.has(row.id)&&i){const owner=treeNodes.get(data.rows[i-1].id);if(owner)owner.children.prepend(treeNode(row,owner.depth+1))}if(i<data.rows.length-1)await treeNodes.get(row.id)?.expand()}
 document.querySelectorAll('.tree-item').forEach(e=>e.classList.toggle('selected',Number(e.dataset.id)===id));treeNodes.get(id)?.line.scrollIntoView({block:'nearest'});
 }catch(e){status(e.message)}
}
async function revealFile(id){try{const target=await api('locate',{id,metric:metric()});await navigate(target.parent);selected=id;offset=Math.floor(target.rank/pageSize)*pageSize;await load();await select(id)}catch(e){status(e.message)}}
async function copyPath(row){try{await navigator.clipboard.writeText(row.path);status('已复制完整路径');setTimeout(clearStatus,1600)}catch{showDetail(row);status('请选中右侧完整路径复制')}}
async function explorer(row){try{await api('explorer',{}, {id:row.id});status(row.directory?'已在资源管理器中打开文件夹':'已请求在资源管理器中选中文件');setTimeout(clearStatus,2200)}catch(e){status('无法打开：'+e.message)}}
function hideContext(){$('contextMenu').classList.add('hidden')}
function contextMenu(row,event,anchor){
 mapView?.hideTooltip();const menu=$('contextMenu');menu.replaceChildren();menu.append(el('div',row.name,'menu-title'));
 const add=(label,key,action)=>{const button=el('button');button.type='button';button.setAttribute('role','menuitem');button.append(el('span',label),el('small',key));button.onclick=()=>{hideContext();Promise.resolve(action()).catch(e=>status(e.message))};menu.append(button)};
 add(row.directory?'打开文件夹':'定位到所属文件夹',row.directory?'Enter':'',()=>row.directory?navigate(row.id):revealFile(row.id));
 add(row.directory?'在资源管理器中打开':'在资源管理器中选中','',()=>explorer(row));menu.append(el('div',null,'menu-separator'));add('复制完整路径','',()=>copyPath(row));add('查看详细信息','',()=>select(row.id));
 menu.classList.remove('hidden');const rect=anchor?.getBoundingClientRect();const x=event?.clientX||rect?.left||80,y=event?.clientY||rect?.bottom||100;menu.style.left=Math.max(6,Math.min(x,innerWidth-menu.offsetWidth-8))+'px';menu.style.top=Math.max(6,Math.min(y,innerHeight-menu.offsetHeight-8))+'px';menu.querySelector('button')?.focus({preventScroll:true});
}
async function loadMap(id){
 const request=++mapRequest;mapRootId=id;$('mapCaption').textContent='正在绘制…';
 try{const buffer=await apiBuffer('map',{id,metric:metric(),pixels:mapView.pixels()});if(request!==mapRequest)return;mapView.setData(buffer);const root=mapView.map.header.root;$('mapPath').textContent=root.path+' · '+fmt(root[metric()]);$('mapPath').title=root.path;$('mapUp').disabled=id===rootId;mapView.select(selected);mapLegend()}catch(e){if(request===mapRequest){$('mapCaption').textContent='无法生成矩形图：'+e.message}}
}
async function init(){
 try{
  meta=await api('meta');selectedItems.clear();pageRows=[];adviceData=null;if($('planPreview').open)$('planPreview').close();syncSelection();const r=meta.report;const scope=meta.scope||r;rootId=meta.root.id;parent=rootId;mapRequest++;hideContext();mode='tree';offset=0;query='';selected=null;requestId++;
  $('search').value='';$('treeTab').classList.add('active');$('filesTab').classList.remove('active');$('searchForm').classList.add('hidden');
  $('heading').textContent=(scope.folder?meta.root.name:r.drive)+' 空间浏览';$('indexTime').textContent='索引生成于 '+meta.index_time.replace('T',' ');
  $('targetPath').value=scope.target||r.drive+'\\';$('driveSelect').value=r.drive+'\\';
  renderStats(r,scope);
  $('treeCount').textContent=num(scope.directories);$('tree').replaceChildren();treeNodes.clear();const root=treeNode(meta.root,0);$('tree').append(root);
  const rn=treeNodes.get(rootId);rn.line.classList.add('selected');rn.toggle.click();await navigate(rootId);
 }catch(e){status(e.message)}
}
/* The summary keeps to what decides a cleanup: capacity, used, free, and how much of the used space the scan accounts for. */
function renderStats(r,scope){
 const audit=r.allocation_audit;
 const audited=!!audit&&['windows_used_bytes','windows_free_bytes','reserved_clusters','cluster_bytes'].every(k=>Number.isFinite(audit[k])&&audit[k]>=0);
 // Compare allocation totals captured in the same scan, rather than today's free space against an old index.
 const disk=audited?{used:audit.windows_used_bytes,free:audit.windows_free_bytes,total:audit.windows_used_bytes+audit.windows_free_bytes}:meta.disk;
 const reserved=audited?audit.reserved_clusters*audit.cluster_bytes:0,scanned=meta.root.allocated||0;
 $('stats').replaceChildren();
 const card=(label,value,note,cls)=>{const c=el('div',null,'stat'+(cls?' '+cls:''));c.append(el('small',label),el('strong',value));if(note)c.append(el('em',note));$('stats').append(c);return c};
 if(disk){
  const used=card('已用空间',fmt(disk.used),(100*disk.used/disk.total).toFixed(1)+'% · 共 '+fmt(disk.total),'used');const bar=el('div',null,'usage'),fill=el('i');fill.style.width=(100*disk.used/disk.total).toFixed(2)+'%';bar.append(fill);used.append(bar);
  card('可用空间',fmt(disk.free),r.drive+(audited?' · 扫描时':' 当前'));
 }
 if(scope.folder)card('所选文件夹',fmt(scanned),num(scope.files)+' 个文件');
 else{
  const fileUsed=disk?Math.max(0,disk.used-reserved):0;
  card('已扫描计入',fmt(scanned),audited&&fileUsed>0?(100*scanned/fileUsed).toFixed(1)+'% 已分配 · '+num(scope.files)+' 个文件':num(scope.files)+' 个文件');
  if(audited&&reserved>0)card('NTFS 预留空间',fmt(reserved),'卷预留 · 不属于文件占用','muted');
  const missing=disk?Math.max(0,disk.used-scanned-reserved):0;
  // Ignore at most 1 MiB of scan-time drift; retain the measured figures in report.json.
  if(disk&&missing>1048576)card('未计入',fmt(missing),audited?'已排除卷预留空间':'重新扫描以核对卷预留空间','muted');
 }
 const hints=[];if(scope.sizes_unresolved)hints.push(num(scope.sizes_unresolved)+' 个文件大小未能解析，未计入。');
 $('notice').textContent=hints.join(' ');$('notice').classList.toggle('hidden',!hints.length);
}
function scanControls(value){scanning=value;for(const id of ['startScan','pickFolder','driveSelect','targetPath'])$(id).disabled=value;$('startScan').textContent=value?'扫描中…':'开始扫描'}
const phases={permission:'等待 Windows 管理员授权',bootstrapping:'读取卷信息',initializing:'初始化扫描',scanning:'读取并解析文件记录',merging:'整理名称与数据流',aggregating:'汇总目录大小',saving:'保存扫描索引',preparing:'载入新目录树与文件列表',done:'扫描完成',error:'扫描未完成'};
async function pollScan(){
 try{const job=await api('scan-status');if(job.status==='idle')return;
 const active=!['done','error','cancelled'].includes(job.status);scanControls(active);
 $('scanPhase').textContent=phases[job.phase]||job.phase;$('scanElapsed').textContent='总用时 '+(job.elapsed_seconds||0).toFixed(2)+'s';
 if(job.percent==null)$('scanProgress').removeAttribute('value');else $('scanProgress').value=Math.min(100,job.percent);
 $('scanRecords').textContent=job.processed_slots!=null?'已处理 '+num(job.processed_slots)+' / '+num(job.total_slots)+' 个记录槽 · '+Math.min(100,job.percent).toFixed(1)+'%':job.target||'准备中';
 if(job.phase==='preparing')$('scanRecords').textContent='卷扫描已结束，正在准备浏览索引；完成后自动刷新。';
 if(job.status==='done'){$('scanRecords').textContent='已更新目录树和文件列表 · '+job.target;if(!meta||job.generation!==meta.generation)await init();clearStatus()}
 else if(job.status==='error'||job.status==='cancelled'){$('scanPhase').textContent=job.status==='cancelled'?'已取消授权':'扫描失败，保留原结果';$('scanRecords').textContent=job.error||'请重试';status(job.error||'扫描未完成')}
 if(active)pollTimer=setTimeout(pollScan,160);
 }catch(error){scanControls(false);status(error.message)}
}
async function startScan(){if(scanning)return;const target=$('targetPath').value.trim();try{scanControls(true);$('scanPhase').textContent='提交只读扫描请求';$('scanProgress').removeAttribute('value');$('scanElapsed').textContent='总用时 0.00s';$('scanRecords').textContent='如弹出 Windows 管理员授权，请确认后继续。';await api('scan',{}, {target});await pollScan()}catch(e){scanControls(false);status(e.message);$('scanPhase').textContent='未开始扫描';$('scanRecords').textContent=e.message;$('scanProgress').value=0}}
async function pickFolder(){try{$('pickFolder').disabled=true;await api('pick-folder',{},{});$('scanPhase').textContent='请在系统窗口选择文件夹';const check=async()=>{try{const state=await api('picker-status');if(state.status==='waiting')return setTimeout(check,300);$('pickFolder').disabled=scanning;if(state.path){$('targetPath').value=state.path;$('driveSelect').value=state.path.slice(0,2).toUpperCase()+'\\';$('scanPhase').textContent='文件夹已选择，点击开始扫描';$('scanRecords').textContent='文件夹模式会扫描所在卷，再显示所选目录范围。'}else if(state.error)status(state.error);else $('scanPhase').textContent='已取消选择'}catch(e){$('pickFolder').disabled=scanning;status(e.message)}};check()}catch(e){$('pickFolder').disabled=scanning;status(e.message)}}
$('reloadPlan').onclick=()=>{adviceData=null;loadAdvice()};$('previewSelection').onclick=previewSelection;$('closePreview').onclick=()=>{previewRequest++;$('planPreview').close()};$('clearSelection').onclick=()=>{selectedItems.clear();syncSelection()};$('selectAdvice').onclick=()=>{for(const row of adviceRowsVisible)addSelection(row);syncSelection()};$('selectPage').onchange=()=>{for(const row of pageRows){if($('selectPage').checked)addSelection(row);else selectedItems.delete(row.id)}syncSelection()};$('minFileSize').onchange=$('fileType').onchange=()=>{offset=0;load()};
$('adviceTab').onclick=()=>switchMode('advice');$('adviceGoal').onsubmit=e=>{e.preventDefault();renderGoal()};$('goalSpace').oninput=renderGoal;document.querySelectorAll('[data-advice-filter]').forEach(b=>b.onclick=()=>{adviceFilter=b.dataset.adviceFilter;renderAdvice()});
$('treeTab').onclick=()=>switchMode('tree');$('filesTab').onclick=()=>switchMode('files');$('metric').onchange=()=>{offset=0;load();loadMap(mode==='files'?rootId:parent)};$('home').onclick=()=>navigate(rootId);
$('back').onclick=async()=>{if(parent<0||parent===rootId)return navigate(rootId);const row=await api('node',{id:parent});const id=Number(BigInt(row.metadata.parent_reference)&((1n<<48n)-1n));navigate(id===parent?rootId:id)};
$('prev').onclick=()=>{offset=Math.max(0,offset-pageSize);load()};$('next').onclick=()=>{offset+=pageSize;load()};$('searchForm').onsubmit=e=>{e.preventDefault();query=$('search').value.trim();offset=0;load()};
$('startScan').onclick=startScan;$('pickFolder').onclick=pickFolder;$('driveSelect').onchange=()=>{$('targetPath').value=$('driveSelect').value};$('targetPath').onkeydown=e=>{if(e.key==='Enter')startScan()};
mapView=new SpaceTreemap($('treemap'),$('mapTooltip'),{format:fmt,lookup:id=>api('node',{id}),
 onChoose:item=>item.directory||item.group?navigate(item.id):revealFile(item.id),
 onContext:async(item,event)=>contextMenu(await api('node',{id:item.id}),event),
 onRendered:count=>{$('mapCaption').textContent=num(count)+' 个区块 · 单击进入 · 右键更多操作'}});

function mapLegend(){const box=$('mapLegend');box.replaceChildren();for(const {label,share,color}of mapView.legend()){const item=el('span',label+' '+(share<.01?'<1':(100*share).toFixed(0))+'%','legend');item.style.setProperty('--swatch',color);box.append(item)}}
$('mapReset').onclick=()=>navigate(rootId);$('mapUp').onclick=async()=>{if(mapRootId===rootId)return;const row=await api('node',{id:mapRootId});navigate(Number(BigInt(row.metadata.parent_reference)&((1n<<48n)-1n)))};
document.addEventListener('pointerdown',event=>{if(!$('contextMenu').contains(event.target))hideContext()});document.addEventListener('keydown',event=>{if(event.key==='Escape'){hideContext();mapView.hideTooltip()}});document.querySelector('.data-deck').addEventListener('scroll',hideContext,true);
const grip=$('mapResizer');let resizeStart=null;grip.onpointerdown=e=>{resizeStart={y:e.clientY,height:$('mapPanel').getBoundingClientRect().height};grip.setPointerCapture(e.pointerId);e.preventDefault()};function mapHeight(height){const max=Math.max(140,$('dataDeck').clientHeight-165);const value=Math.max(140,Math.min(height,max));$('mapPanel').style.height=value+'px';grip.setAttribute('aria-valuenow',Math.round(value))}grip.onpointermove=e=>{if(resizeStart)mapHeight(resizeStart.height+resizeStart.y-e.clientY)};grip.onpointerup=()=>resizeStart=null;grip.onpointercancel=()=>resizeStart=null;grip.onkeydown=e=>{if(e.key==='ArrowUp'||e.key==='ArrowDown'){e.preventDefault();mapHeight($('mapPanel').getBoundingClientRect().height+(e.key==='ArrowUp'?20:-20))}};
(async()=>{try{const data=await api('drives');for(const drive of data.drives){const option=el('option',drive.drive+' · '+drive.filesystem);option.value=drive.drive;option.disabled=!drive.supported;$('driveSelect').append(option)}await init();await pollScan()}catch(e){status(e.message)}})();
