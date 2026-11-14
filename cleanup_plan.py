"""Versioned advice plans and read-only selection previews. No source-file mutations."""
from datetime import datetime,timezone
import hashlib,json,os,sys,uuid
from pathlib import PureWindowsPath
from cleanup_advice import build_advice,classify

OPERATIONS={'review':'仅查看','cache_cleanup':'通过应用清缓存','move':'迁移到其他盘','recycle':'移入回收站','uninstall':'通过应用管理卸载'}

def atomic_json(path,value):
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)

def operation_info(viewer,row):
    p=PureWindowsPath(row['path']);parts=tuple(x.casefold() for x in p.parts)
    rule=classify(row['path'],row['directory'])
    runtime=PureWindowsPath(sys.executable).parent
    protected=len(parts)<=1 or (len(parts)>1 and parts[1]=='windows' and not (rule and rule['kind']=='cache'))
    protected=protected or runtime.is_relative_to(p) or p.is_relative_to(runtime)
    if protected:return {'allowed_operations':['review'],'suggested_operation':'review'}
    if rule:
        kind=rule['kind']
        allowed={'cache':['cache_cleanup','review'],'app':['uninstall','review'],'move':['move','uninstall','review'],'review':['review','move','recycle']}[kind]
    elif 'users' in parts and 'appdata' not in parts and not row['directory']:
        allowed=['review','move','recycle']
    else:allowed=['review']
    return {'allowed_operations':allowed,'suggested_operation':allowed[0]}

class PlanStore:
    def __init__(self,viewer):
        self.viewer=viewer;self.path=viewer.directory/'plan.json'
        stat=(viewer.directory/'index.bin').stat()
        identity=[viewer.report.get('volume',{}).get('volume_serial'),stat.st_size,stat.st_mtime_ns,viewer.fields(viewer.root)[0]]
        self.scan_id=hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24]

    def checked_row(self,number):
        if type(number) is not int or not self.viewer.permits(number) or not self.viewer.linked[number]:raise ValueError('项目不在当前扫描范围内')
        row=self.viewer.row(number)
        if self.viewer.fields(number)[11] not in (1,2,4,5):raise ValueError('不是可查看的文件或目录')
        return {**row,**operation_info(self.viewer,row)}

    def normalize(self,value,source=None):
        if not isinstance(value,dict) or value.get('schema_version')!=1 or value.get('scan_id')!=self.scan_id:raise ValueError('方案与当前扫描不匹配，请基于最新扫描重新生成')
        if set(value)-{'schema_version','scan_id','plan_id','source','created_at','items'}:raise ValueError('方案包含不支持的字段')
        items=value.get('items')
        if not isinstance(items,list) or len(items)>200:raise ValueError('方案最多支持 200 个项目')
        normalized=[];seen=set()
        for item in items:
            if not isinstance(item,dict) or set(item)-{'id','reference','operation','reason','evidence'}:raise ValueError('方案项目只能包含记录、操作、理由和证据')
            row=self.checked_row(item.get('id'));number=row['id']
            if number in seen:raise ValueError('方案中存在重复项目')
            seen.add(number)
            reference=hex(self.viewer.fields(number)[0])
            if item.get('reference')!=reference:raise ValueError('文件记录身份与扫描不符')
            operation=item.get('operation','review')
            if operation not in row['allowed_operations']:raise ValueError('此项目不支持指定的处理方式')
            reason=item.get('reason','待人工确认用途');evidence=item.get('evidence',[])
            if not isinstance(reason,str) or len(reason)>1500 or not isinstance(evidence,list) or len(evidence)>10 or any(not isinstance(x,str) or len(x)>1000 for x in evidence):raise ValueError('建议理由或证据格式不正确')
            normalized.append({'id':number,'reference':reference,'operation':operation,'reason':reason,'evidence':evidence})
        return {'schema_version':1,'scan_id':self.scan_id,'plan_id':value.get('plan_id') if isinstance(value.get('plan_id'),str) else uuid.uuid4().hex,
                'source':source or value.get('source','agent'),'created_at':value.get('created_at') or datetime.now(timezone.utc).isoformat(), 'items':normalized}

    def load(self):
        plan=None
        if self.path.exists():
            if self.path.stat().st_size>2*1024*1024:raise ValueError('方案文件过大')
            plan=self.normalize(json.loads(self.path.read_text(encoding='utf-8-sig')))
        if plan is None or plan['source']=='rules':
            advice=self.viewer.cached(('cleanup-advice',),lambda:build_advice(self.viewer))
            items=[]
            for row in advice['rows']:
                info=operation_info(self.viewer,row)
                items.append({'id':row['id'],'reference':hex(self.viewer.fields(row['id'])[0]),'operation':info['suggested_operation'],'reason':row['reason'],'evidence':[row['action']]})
            generated=self.normalize({**(plan or {}),'schema_version':1,'scan_id':self.scan_id,'source':'rules','items':items})
            if generated!=plan:atomic_json(self.path,generated)
            plan=generated
        revision=hashlib.sha256(json.dumps(plan,sort_keys=True,ensure_ascii=True).encode()).hexdigest()[:24]
        return plan,revision

    def save(self,value):
        plan=self.normalize(value,source='agent');atomic_json(self.path,plan)
        return self.response()

    def response(self):
        plan,revision=self.load();rows=[]
        for item in plan['items']:
            row=self.checked_row(item['id']);rule=classify(row['path'],row['directory']) or {'kind':'review','label':'自定义建议'}
            rows.append({**row,**rule,'reason':item['reason'],'evidence':item['evidence'],'action':'；'.join(item['evidence']), 'operation':item['operation']})
        return {**plan,'items':rows,'revision':revision,'candidate_count':len(rows),'read_only':True,
                'candidate_bytes':unique_occupied(rows)[0],'operation_labels':OPERATIONS}

    def preview(self,request):
        plan,revision=self.load()
        if request.get('scan_id')!=self.scan_id or request.get('revision')!=revision:raise ValueError('扫描或建议方案已变化，请刷新后重新预览')
        items=request.get('items')
        if not isinstance(items,list) or not 1<=len(items)<=200:raise ValueError('请选择 1 到 200 个项目')
        defaults={i['id']:i for i in plan['items']};rows=[];seen=set()
        for item in items:
            if not isinstance(item,dict) or set(item)-{'id','operation'}:raise ValueError('清单项目格式不正确')
            row=self.checked_row(item.get('id'))
            if row['id'] in seen:raise ValueError('清单存在重复项目')
            seen.add(row['id']);operation=item.get('operation','review')
            if operation not in row['allowed_operations']:raise ValueError('此项目不支持指定的处理方式')
            reference=hex(self.viewer.fields(row['id'])[0]);row.update(reference=reference,operation=operation,operation_label=OPERATIONS[operation],reason=defaults.get(row['id'],{}).get('reason','手动加入清单，请确认用途'))
            row['notes']=['基于扫描快照；执行前仍需核对文件身份、变化和使用状态。']
            row['estimated_release_bytes']=None
            if operation=='recycle':row['notes'].append('移入同盘回收站不会立即增加该盘可用空间。');row['estimated_release_bytes']=0
            elif operation=='review':row['notes'].append('仅查看，不安排空间处理。');row['estimated_release_bytes']=0
            elif operation=='move':row['notes'].append('尚未指定目标位置；后续需要验证目标容量、复制结果及原件移除。')
            elif operation=='uninstall':row['notes'].append('需要通过所属应用或启动器卸载，不直接删除安装目录。')
            elif operation=='cache_cleanup':row['notes'].append('需要通过所属应用确认缓存范围；部分内容可能正在使用或会重新生成。')
            if row['links']>1 and not row['directory']:row['notes'].append('此文件存在硬链接，处理一个路径不保证释放数据簇。')
            rows.append(row)
        occupied,covered=unique_occupied(rows)
        for row in rows:row['covered_by']=covered.get(row['id'])
        result={'schema_version':1,'scan_id':self.scan_id,'plan_id':plan['plan_id'],'revision':revision,'read_only':True,'executed':False,
                'created_at':datetime.now(timezone.utc).isoformat(),'items':rows,'selected_count':len(rows),'covered_count':len(covered),
                'occupied_bytes':occupied,'estimated_release_bytes':None,'preview_file':'preview.json'}
        atomic_json(self.viewer.directory/'preview.json',result)
        return result

def unique_occupied(rows):
    kept=[];total=0;covered={}
    for row in sorted(rows,key=lambda r:len(PureWindowsPath(r['path']).parts)):
        p=PureWindowsPath(row['path'])
        ancestor=next((r for r in kept if p.is_relative_to(PureWindowsPath(r['path']))),None)
        if ancestor:covered[row['id']]=ancestor['id']
        else:kept.append(row);total+=row.get('allocated') or 0
    return total,covered
