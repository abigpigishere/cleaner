"""Validated local file operations. No shell interpolation, no arbitrary commands, no elevated broker."""
import ctypes,hashlib,json,os,shutil,stat,threading,time,uuid
from pathlib import Path
from cleanup_plan import atomic_json,PlanStore
from cleanup_advice import classify

PROTECTED={'windows','program files','program files (x86)','programdata','recovery','system volume information','$recycle.bin'}
REPO=Path(__file__).resolve().parent

def signature(path):
    s=path.lstat();return [s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,stat.S_IFMT(s.st_mode)]

def no_links(path):
    for node in [path,*path.parents]:
        s=node.lstat()
        if stat.S_ISLNK(s.st_mode) or getattr(s,'st_file_attributes',0)&0x400:raise ValueError('不处理符号链接、junction 或其他重解析点')

def guarded_path(value,must_exist=True):
    p=Path(os.path.abspath(value))
    if not p.is_absolute() or (len(p.drive)!=2 or p.drive[1]!=':') or len(p.parts)<=1 or '\x00' in str(p) or ':' in str(p)[2:]:raise ValueError('必须是本地普通文件或具体目录')
    if any(x.casefold() in PROTECTED for x in p.parts[1:]):raise ValueError('不直接处理系统、软件安装或系统数据目录')
    home=Path.home()
    if p==home or p in home.parents:raise ValueError('不处理用户根目录或其上级')
    if p==REPO or p in REPO.parents or p.is_relative_to(REPO):raise ValueError('不处理 Cleaner 自身文件')
    runtime=Path(__import__('sys').executable).parent
    if p==runtime or p.is_relative_to(runtime) or runtime.is_relative_to(p):raise ValueError('不处理当前 Python 运行环境')
    if 'appdata' in [x.casefold() for x in p.parts] or p.name.casefold() in {'.cache','.codex','.agents','.ssh','.aws','.git'}:raise ValueError('应用托管数据和缓存须使用所属应用清理，不直接整目录处理')
    if must_exist:no_links(p)
    else:no_links(p.parent)
    if must_exist and p.is_dir() and ((p/'.git').exists() or (p/'.git').is_symlink()):raise ValueError('不整目录处理 Git 项目或 worktree')
    return p

def manifest(path):
    no_links(path);entries={}
    def add(p):
        no_links(p)
        if not p.is_file() and not p.is_dir():raise ValueError('包含非普通文件')
        if p.name.casefold()=='.git':raise ValueError('包含 Git 项目或 worktree')
        if p.is_dir() and any(name!='::$DATA' for name in stream_paths(p)):raise ValueError('目录包含命名数据流，通用目录处理暂不支持；请使用系统工具')
        entries[str(p.relative_to(path)) if p!=path else '.']=signature(p)
        if len(entries)>100000:raise ValueError('单次目录超过 100000 项，请缩小选择范围')
    add(path)
    if path.is_dir():
        for root,dirs,files in os.walk(path,followlinks=False):
            for name in dirs+files:add(Path(root)/name)
    return entries

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(4*1024*1024),b''):h.update(block)
    return h.hexdigest()

def stream_paths(path):
    class StreamData(ctypes.Structure):
        _fields_=[('size',ctypes.c_longlong),('name',ctypes.c_wchar*296)]
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.FindFirstStreamW.argtypes=[ctypes.c_wchar_p,ctypes.c_int,ctypes.POINTER(StreamData),ctypes.c_ulong];api.FindFirstStreamW.restype=ctypes.c_void_p
    api.FindNextStreamW.argtypes=[ctypes.c_void_p,ctypes.POINTER(StreamData)];api.FindNextStreamW.restype=ctypes.c_int
    api.FindClose.argtypes=[ctypes.c_void_p]
    data=StreamData();handle=api.FindFirstStreamW(str(path),0,ctypes.byref(data),0);result={}
    if handle==ctypes.c_void_p(-1).value:
        error=ctypes.get_last_error()
        if error==38:return {'::$DATA':str(path)}
        raise ctypes.WinError(error)
    try:
        while True:
            name=data.name;result[name]=str(path) if name=='::$DATA' else str(path)+name[:-6]
            if not api.FindNextStreamW(handle,ctypes.byref(data)):
                error=ctypes.get_last_error()
                if error!=38:raise ctypes.WinError(error)
                break
    finally:api.FindClose(handle)
    return result

def verified_copy(source,destination):
    source=Path(source);destination=Path(destination);no_links(source)
    before=signature(source);streams=stream_paths(source);hashes={name:digest(Path(p)) for name,p in streams.items()}
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.CopyFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_wchar_p,ctypes.c_int];api.CopyFileW.restype=ctypes.c_int
    if not api.CopyFileW(str(source),str(destination),True):raise ctypes.WinError(ctypes.get_last_error())
    copied=stream_paths(destination)
    if signature(source)!=before or stream_paths(source)!=streams or set(copied)!=set(streams) or any(digest(Path(copied[n]))!=h for n,h in hashes.items()):raise ValueError('复制校验失败或源文件已变化；保留源文件')
    return str(destination)

def remove_exact(path,expected):
    path=guarded_path(path)
    if manifest(path)!=expected:raise ValueError('源内容已变化，跳过处理')
    # Hold DELETE-capable handles without write/delete sharing before the final validation.
    # This prevents an existing writer from losing bytes between copy verification and unlink.
    from size_demo import FileInfo
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_ulong,ctypes.c_ulong,ctypes.c_void_p,ctypes.c_ulong,ctypes.c_ulong,ctypes.c_void_p];api.CreateFileW.restype=ctypes.c_void_p
    api.CloseHandle.argtypes=[ctypes.c_void_p]
    api.SetFileInformationByHandle.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_ulong];api.SetFileInformationByHandle.restype=ctypes.c_int
    api.GetFileInformationByHandle.argtypes=[ctypes.c_void_p,ctypes.POINTER(FileInfo)];api.GetFileInformationByHandle.restype=ctypes.c_int
    handles=[];removed=0
    try:
        for relative in sorted(expected,key=lambda x:len(Path(x).parts)):
            item=path if relative=='.' else path/relative
            if item!=path and not item.resolve().is_relative_to(path.resolve()):raise ValueError('删除清单越界')
            no_links(item)
            handle=api.CreateFileW(str(item),0x10000|0x80,1,None,3,0x02200000,None)
            if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
            handles.append((item,handle));info=FileInfo()
            if not api.GetFileInformationByHandle(handle,ctypes.byref(info)):raise ctypes.WinError(ctypes.get_last_error())
            if info.attributes&0x400 or (info.id_low|info.id_high<<32)!=expected[relative][1]:raise ValueError('文件身份或链接状态已变化')
        if manifest(path)!=expected:raise ValueError('锁定前源内容已变化，跳过处理')
        while handles:
            item,handle=handles[-1];delete=ctypes.c_int(1)
            if not api.SetFileInformationByHandle(handle,4,ctypes.byref(delete),ctypes.sizeof(delete)):raise ctypes.WinError(ctypes.get_last_error())
            api.CloseHandle(handle);handles.pop();removed+=1
    except Exception as error:
        if removed:raise OSError(f'{error}；已有 {removed} 个子项完成移除，其余保留') from error
        raise
    finally:
        for item,handle in handles:api.CloseHandle(handle)

def move_verified(source,destination,expected):
    source=guarded_path(source);destination=guarded_path(destination,False)
    if destination.exists():raise ValueError('目标已存在，不覆盖')
    if destination.is_relative_to(source) or source.is_relative_to(destination):raise ValueError('迁移目标与源目录重叠')
    if manifest(source)!=expected:raise ValueError('源内容已变化')
    if source.stat().st_dev==destination.parent.stat().st_dev:
        os.rename(source,destination)
        return {'method':'same_volume_rename','verified':True,'destination':str(destination)}
    required=sum(s[2] for s in expected.values() if s[4]==stat.S_IFREG)
    if shutil.disk_usage(destination.parent).free<required+1024*1024:raise ValueError('目标盘空间不足')
    staging=destination.with_name(destination.name+'.cleaner-'+uuid.uuid4().hex+'.partial')
    try:
        if source.is_dir():shutil.copytree(source,staging,copy_function=verified_copy,symlinks=False)
        else:verified_copy(source,staging)
        if manifest(source)!=expected:raise ValueError('复制期间源内容发生变化')
        if destination.exists():raise ValueError('复制期间目标被创建，不覆盖')
        os.rename(staging,destination)
    except Exception as error:
        raise ValueError(f'{error}；原件保留，复制暂存位置：{staging}') from error
    try:remove_exact(source,expected)
    except Exception as error:raise ValueError(f'{error}；完整副本已保留在 {destination}，原件移除未全部完成') from error
    return {'method':'cross_volume_verified_copy','verified':True,'destination':str(destination)}

class GUID(ctypes.Structure):
    _fields_=[('data',ctypes.c_ubyte*16)]
    def __init__(self,value):super().__init__();self.data[:]=uuid.UUID(value).bytes_le

def recycle(path):
    # Windows IFileOperation explicitly requests recycling; no fallback permanent-delete call exists.
    ole=ctypes.OleDLL('ole32');shell=ctypes.OleDLL('shell32')
    ole.CoInitializeEx(None,2)
    operation=ctypes.c_void_p();item=ctypes.c_void_p()
    def method(obj,index,*args):
        table=ctypes.cast(obj,ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(ctypes.c_long,ctypes.c_void_p,*args)(table[index])
    def check(hr):
        if hr<0:raise OSError(f'Windows 文件操作失败 0x{hr&0xffffffff:08X}')
    try:
        cls=GUID('3ad05575-8857-4850-9277-11b85bdb8e09');iid=GUID('947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8')
        ole.CoCreateInstance.argtypes=[ctypes.POINTER(GUID),ctypes.c_void_p,ctypes.c_ulong,ctypes.POINTER(GUID),ctypes.POINTER(ctypes.c_void_p)]
        check(ole.CoCreateInstance(ctypes.byref(cls),None,1,ctypes.byref(iid),ctypes.byref(operation)))
        shell.SHCreateItemFromParsingName.argtypes=[ctypes.c_wchar_p,ctypes.c_void_p,ctypes.POINTER(GUID),ctypes.POINTER(ctypes.c_void_p)]
        item_iid=GUID('43826d1e-e718-42ee-bc55-a1e261c37bfe')
        check(shell.SHCreateItemFromParsingName(str(path),None,ctypes.byref(item_iid),ctypes.byref(item)))
        # WANTNUKEWARNING retains a Windows confirmation if recycling cannot be honored.
        check(method(operation,5,ctypes.c_ulong)(operation,0x40|0x10|0x4|0x400|0x4000|0x80000|0x100000|0x20000000))
        check(method(operation,18,ctypes.c_void_p,ctypes.c_void_p)(operation,item,None))
        check(method(operation,21)(operation))
        aborted=ctypes.c_int();check(method(operation,22,ctypes.POINTER(ctypes.c_int))(operation,ctypes.byref(aborted)))
        if aborted.value or path.exists():raise OSError('移入回收站未完成')
    finally:
        for obj in [item,operation]:
            if obj.value:method(obj,2)(obj)
        ole.CoUninitialize()

class OperationManager:
    def __init__(self,controller):
        self.controller=controller;self.lock=threading.RLock();self.active=None;self.tickets={};self.results={}

    def prepare(self,body):
        with self.lock:
            with self.controller.viewer_lock:
                store=PlanStore(self.controller.viewer);preview=store.preview(body);entries=[];manual=[]
                if len(preview['items'])>20:raise ValueError('单批最多 20 项')
                destinations=body.get('destinations',{});delete_modes=body.get('delete_modes',{})
                if not isinstance(destinations,dict) or not isinstance(delete_modes,dict):raise ValueError('目标或删除方式必须是 JSON 对象')
                for row in preview['items']:
                    op=row['operation']
                    if row['covered_by'] is not None:continue
                    if op in ('review','cache_cleanup'):
                        manual.append({'id':row['id'],'name':row['name'],'operation':op});continue
                    rule=classify(row['path'],row['directory'])
                    if op=='uninstall' or (rule and rule['kind']=='move'):
                        handling='game_storage' if op=='move' else 'uninstall_settings'
                        entries.append({'id':row['id'],'name':row['name'],'source':row['path'],'operation':handling,'signature':None});continue
                    source=guarded_path(row['path']);expected=manifest(source)
                    if len(expected)+sum(len(e.get('manifest',{})) for e in entries)>100000:raise ValueError('单批文件清单超过 100000 项，请分批处理')
                    if source.is_file() and row.get('logical') is not None and source.stat().st_size!=row['logical']:raise ValueError('文件大小与扫描结果不符，请重新扫描')
                    if expected['.'][1]!=self.controller.viewer.fields(row['id'])[0]:raise ValueError('文件身份与扫描记录不符，请重新扫描')
                    entry={'id':row['id'],'name':row['name'],'source':str(source),'operation':op,'manifest':expected,'current_items':len(expected),'current_logical_bytes':sum(v[2] for v in expected.values() if v[4]==stat.S_IFREG)}
                    if op=='recycle':
                        mode=delete_modes.get(str(row['id']),'recycle')
                        if mode not in ('recycle','permanent'):raise ValueError('删除方式无效')
                        entry['operation']='delete' if mode=='permanent' else 'recycle'
                    if op=='move':
                        value=destinations.get(str(row['id']))
                        if not isinstance(value,str) or not value:raise ValueError('请填写每项迁移的完整目标路径')
                        target=guarded_path(value,False)
                        if target.exists() or target.is_relative_to(source) or source.is_relative_to(target):raise ValueError('迁移目标已存在或与源目录重叠')
                        entry['destination']=str(target)
                    entries.append(entry)
                if not entries:raise ValueError('所选方式需要在所属应用中处理，或仅供查看；没有可执行项目')
                token=uuid.uuid4().hex;ack=uuid.uuid4().hex
                ticket={'ticket':token,'ack':ack,'created':time.monotonic(),'scan_id':store.scan_id,'revision':preview['revision'],'entries':entries,'directory':str(store.viewer.directory)}
                with self.lock:
                    self.tickets={k:v for k,v in self.tickets.items() if time.monotonic()-v['created']<=600}
                    if len(self.tickets)>=10:self.tickets.pop(min(self.tickets,key=lambda k:self.tickets[k]['created']))
                    self.tickets[token]=ticket
                return {'ticket':token,'ack':ack,'expires_seconds':600,'entries':[{k:v for k,v in e.items() if k!='manifest'} for e in entries],
                        'permanent_delete':any(e['operation']=='delete' for e in entries),'requires_confirmation':True,'manual_items':manual}

    def execute(self,body):
        with self.lock:
            ticket=self.tickets.get(body.get('ticket'))
            if not ticket or body.get('ack')!=ticket['ack'] or time.monotonic()-ticket['created']>600:raise ValueError('确认清单已失效，请重新准备')
            if self.active:raise ValueError('已有操作正在进行')
            with self.controller.viewer_lock:
                store=PlanStore(self.controller.viewer);_,revision=store.load()
                if store.scan_id!=ticket['scan_id'] or revision!=ticket['revision']:raise ValueError('扫描或方案已变化')
            with self.controller.job_lock:
                if self.controller.job and self.controller.job['status'] not in ('done','error','cancelled'):raise ValueError('扫描正在进行，请等待完成')
            # Validate the whole batch before starting a mutation.
            for entry in ticket['entries']:
                if entry['operation'] not in ('uninstall_settings','game_storage') and manifest(guarded_path(entry['source']))!=entry['manifest']:raise ValueError('文件已变化，请重新准备清单')
            self.tickets.pop(ticket['ticket']);self.active=ticket['ticket']
            self.results[self.active]={'status':'running','items':[],'ticket':self.active}
            threading.Thread(target=self._work,args=(ticket,),daemon=True).start()
            return dict(self.results[self.active])

    def _work(self,ticket):
        result=self.results[ticket['ticket']];drives={Path(e['source']).anchor for e in ticket['entries']};before={}
        try:
            before={d:shutil.disk_usage(d).free for d in drives}
            for entry in ticket['entries']:
                row={k:v for k,v in entry.items() if k!='manifest'}
                try:
                    p=Path(entry['source']);op=entry['operation']
                    if op=='game_storage':
                        if 'steamapps' in [x.casefold() for x in p.parts]:os.startfile('steam://open/settings/storage')
                        else:os.startfile(str(p))
                        row.update(status='awaiting_user',message='已打开启动器存储入口或游戏目录；请在对应启动器完成迁移，未直接移动游戏文件。')
                    elif op=='uninstall_settings':
                        from installed_apps import open_uninstall_settings
                        row.update(open_uninstall_settings())
                    elif op=='move':row.update(move_verified(p,Path(entry['destination']),entry['manifest']),status='completed')
                    elif op=='recycle':
                        guarded_path(p)
                        if manifest(p)!=entry['manifest']:raise ValueError('文件已变化')
                        recycle(p);row['status']='completed'
                    elif op=='delete':remove_exact(p,entry['manifest']);row['status']='completed'
                    else:raise ValueError('操作无效')
                except Exception as error:row.update(status='failed',error=str(error))
                with self.lock:result['items'].append(row)
                atomic_json(Path(ticket['directory'])/('operation-'+ticket['ticket']+'.json'),result)
            after={d:shutil.disk_usage(d).free for d in drives}
            result['free_space_delta_bytes']={d:after[d]-before[d] for d in drives}
            result['status']='finished' if all(r['status']!='failed' for r in result['items']) else 'partial_failure'
        except Exception as error:result.update(status='failed',error=str(error))
        finally:
            with self.lock:self.active=None
            atomic_json(Path(ticket['directory'])/('operation-'+ticket['ticket']+'.json'),result)

    def status(self,ticket=None):
        with self.lock:
            if not ticket:return {'active':self.active,'recent':[{'ticket':k,'status':v['status']} for k,v in list(self.results.items())[-10:]]}
            if ticket not in self.results:raise ValueError('操作记录不存在')
            return json.loads(json.dumps(self.results[ticket]))
