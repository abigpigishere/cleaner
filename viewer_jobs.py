"""Local scan/picker orchestration. No deletion and no shell-built target commands."""
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid


def drives():
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetLogicalDrives.restype=w.DWORD
    kernel.GetDriveTypeW.argtypes=[w.LPCWSTR]
    kernel.GetVolumeInformationW.argtypes=[w.LPCWSTR,w.LPWSTR,w.DWORD,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,w.LPWSTR,w.DWORD]
    result=[]
    mask=kernel.GetLogicalDrives()
    for number in range(26):
        root=f'{chr(65+number)}:\\'
        if mask & (1<<number) and kernel.GetDriveTypeW(root) in (2,3):
            filesystem=ctypes.create_unicode_buffer(64)
            ok=kernel.GetVolumeInformationW(root,None,0,None,None,None,filesystem,64)
            result.append({'drive':root,'filesystem':filesystem.value if ok else '不可读取',
                           'supported':bool(ok and filesystem.value=='NTFS')})
    return result


def normalize_target(value):
    if not isinstance(value,str) or not value or len(value)>2048 or '\x00' in value:
        raise ValueError('请选择盘符或输入本地文件夹路径')
    if re.fullmatch(r'[a-zA-Z]:',value):
        value+='\\'
    if not re.match(r'^[a-zA-Z]:[\\/]',value):
        raise ValueError('只支持本地绝对路径，例如 C:\\ 或 E:\\桌面\\cleaner')
    target=Path(value).resolve(strict=True)
    if not target.is_dir():
        raise ValueError('目标必须是文件夹或盘符')
    supported={item['drive'][:2].upper() for item in drives() if item['supported']}
    if target.drive.upper() not in supported:
        raise ValueError('高速扫描目前只支持本地 NTFS 卷')
    return str(target)


def launch_elevated(params):
    script=Path(params[0])
    if ctypes.windll.shell32.IsUserAnAdmin():
        return subprocess.Popen([sys.executable,*params],creationflags=subprocess.CREATE_NO_WINDOW)
    class ShellInfo(ctypes.Structure):
        _fields_=[('cbSize',w.DWORD),('fMask',w.ULONG),('hwnd',w.HWND),('lpVerb',w.LPCWSTR),
                  ('lpFile',w.LPCWSTR),('lpParameters',w.LPCWSTR),('lpDirectory',w.LPCWSTR),
                  ('nShow',ctypes.c_int),('hInstApp',w.HINSTANCE),('lpIDList',ctypes.c_void_p),
                  ('lpClass',w.LPCWSTR),('hkeyClass',w.HKEY),('dwHotKey',w.DWORD),
                  ('hIcon',w.HANDLE),('hProcess',w.HANDLE)]
    shell=ctypes.WinDLL('shell32',use_last_error=True)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    shell.ShellExecuteExW.argtypes=[ctypes.POINTER(ShellInfo)]
    shell.ShellExecuteExW.restype=w.BOOL
    kernel.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD]
    kernel.GetExitCodeProcess.argtypes=[w.HANDLE,ctypes.POINTER(w.DWORD)]
    kernel.CloseHandle.argtypes=[w.HANDLE]
    request=ShellInfo()
    request.cbSize=ctypes.sizeof(request)
    request.fMask=0x40
    request.lpVerb='runas'
    request.lpFile=sys.executable
    request.lpParameters=subprocess.list2cmdline(params)
    request.lpDirectory=str(script.parent)
    request.nShow=0
    if not shell.ShellExecuteExW(ctypes.byref(request)):
        error=ctypes.get_last_error()
        if error==1223:
            raise PermissionError('已取消 Windows 管理员授权，未启动扫描')
        raise ctypes.WinError(error)
    return request.hProcess


class ScanSession:
    def __init__(self):
        self.connection=None
        self.process=None

    def run(self,folder):
        from multiprocessing.connection import Listener
        import secrets
        if self.connection is None:
            pipe='cleaner-'+uuid.uuid4().hex
            key=secrets.token_bytes(32)
            # The normal viewer owns the pipe; the elevated reader connects to it.
            # This avoids granting general access to an elevated process's default pipe ACL.
            with Listener('\\\\.\\pipe\\'+pipe,family='AF_PIPE',authkey=key) as listener:
                self.process=launch_elevated([str(Path(__file__).parent/'scan_worker.py'),
                    '--pipe',pipe,'--key',key.hex(),'--parent',str(os.getpid())])
                accepted=[]
                def accept():
                    try:accepted.append(listener.accept())
                    except Exception as error:accepted.append(error)
                threading.Thread(target=accept,daemon=True).start()
                deadline=time.monotonic()+30
                while not accepted and time.monotonic()<deadline:time.sleep(.05)
                if not accepted:raise RuntimeError('管理员扫描进程未能连接')
                if isinstance(accepted[0],Exception):raise accepted[0]
                self.connection=accepted[0]
        try:
            self.connection.send_bytes(json.dumps({'job':folder.name}).encode())
            return json.loads(self.connection.recv_bytes(4096))['exit_code']
        except (OSError,EOFError):
            self.close()
            raise RuntimeError('管理员扫描会话已结束，请重新开始扫描')

    def close(self):
        if self.connection:
            try:self.connection.send_bytes(b'{"stop":true}')
            except OSError:pass
            self.connection.close();self.connection=None
        if isinstance(self.process,int):
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.CloseHandle.argtypes=[w.HANDLE];kernel.CloseHandle(self.process)
        self.process=None



class Controller:
    def __init__(self,viewer,factory):
        self.session=ScanSession()
        self.viewer=viewer
        self.factory=factory
        self.viewer_lock=threading.RLock()
        self.job_lock=threading.RLock()
        self.job=None
        self.generation=1
        self.picker={'status':'idle'}
        self.base=Path(__file__).parent/'results/ui-jobs'
        self.progress={}
        saved=viewer.directory/'ui-result.json'
        if saved.exists():
            previous=json.loads(saved.read_text())
            self.job={'status':'done','phase':'done','percent':100,'target':previous['target'],
                      'elapsed_seconds':previous['elapsed_seconds'],'started':time.perf_counter(),
                      'folder':str(viewer.directory.parent),'generation':1,'output_dir':str(viewer.directory)}

    def start(self,target):
        target=normalize_target(target)
        with self.job_lock:
            if self.job and self.job['status'] not in ('done','error','cancelled'):
                raise ValueError('已有扫描正在进行，请等待完成')
            folder=self.base/uuid.uuid4().hex
            folder.mkdir(parents=True,exist_ok=False)
            (folder/'request.json').write_text(json.dumps({'target':target}),encoding='utf-8')
            self.job={'status':'waiting','phase':'bootstrapping' if self.session.connection else 'permission','target':target,'folder':str(folder),
                      'started':time.perf_counter(),'percent':None}
            self.progress={}
            threading.Thread(target=self._work,args=(folder,),daemon=True).start()
        return self.status()

    def _work(self,folder):
        try:
            exit_code=self.session.run(folder)
            state_path=folder/'state.json'
            state=json.loads(state_path.read_text()) if state_path.exists() else {}
            if exit_code or state.get('status')!='scanned':
                raise RuntimeError(state.get('error','扫描未完成，请重试或查看 scanner.log'))
            with self.job_lock:
                self.job.update(status='running',phase='preparing',percent=100)
            target={'target':state['target'],'scope_reference':state.get('scope_reference')}
            (folder/'scan/view-target.json').write_text(json.dumps(target),encoding='utf-8')
            new_viewer=self.factory(folder/'scan')
            with self.viewer_lock:
                old=self.viewer
                self.viewer=new_viewer
                self.generation+=1
                old.index.close()
            with self.job_lock:
                self.job.update(status='done',phase='done',percent=100,elapsed_seconds=time.perf_counter()-self.job['started'],
                                generation=self.generation,output_dir=str(folder/'scan'))
                (folder/'scan/ui-result.json').write_text(json.dumps({'target':self.job['target'],
                    'elapsed_seconds':self.job['elapsed_seconds']}),encoding='utf-8')
                (Path(__file__).parent/'results/latest-view.json').write_text(json.dumps({'scan_dir':str(folder/'scan')}),encoding='utf-8')
        except Exception as error:
            with self.job_lock:
                self.job.update(status='cancelled' if isinstance(error,PermissionError) else 'error',phase='error',
                                error=str(error),elapsed_seconds=time.perf_counter()-self.job['started'])

    def status(self):
        with self.job_lock:
            if not self.job:
                return {'status':'idle','generation':self.generation}
            result=dict(self.job)
            folder=Path(result.pop('folder'))
            started=result.pop('started')
            if result['status'] not in ('done','error','cancelled'):
                result['elapsed_seconds']=time.perf_counter()-started
                try:
                    state=json.loads((folder/'state.json').read_text())
                    if state['status']=='running':
                        result.update(status='running',phase=state['phase'])
                    elif state['status']=='scanned':
                        result.update(status='running',phase='preparing',percent=100)
                    elif state['status']=='error':
                        result.update(status='error',phase='error',error=state['error'])
                except (OSError,ValueError,KeyError):
                    pass
                try:
                    progress=json.loads((folder/'scan/native-progress.json').read_text())
                    self.progress=progress
                except (OSError,ValueError):
                    pass
                if result['phase']!='preparing' and self.progress:
                    result.update(self.progress)
                    if result['phase']=='done':
                        result['phase']='preparing'
            result['generation']=self.generation
            return result

    def pick(self):
        with self.job_lock:
            if self.picker['status']=='waiting':
                raise ValueError('文件夹选择窗口已经打开')
            folder=self.base/('picker-'+uuid.uuid4().hex)
            folder.mkdir(parents=True)
            self.picker={'status':'waiting'}
            def worker():
                try:
                    result=subprocess.run([sys.executable,str(Path(__file__).parent/'pick_folder.py'),
                                           '--output',str(folder/'selection.json')],creationflags=subprocess.CREATE_NO_WINDOW)
                    if result.returncode:
                        raise RuntimeError('无法打开文件夹选择窗口，可以直接输入路径')
                    value=json.loads((folder/'selection.json').read_text())
                    self.picker={'status':'selected' if value['path'] else 'cancelled','path':value['path']}
                except Exception as error:
                    self.picker={'status':'error','error':str(error)}
            threading.Thread(target=worker,daemon=True).start()
            return dict(self.picker)
