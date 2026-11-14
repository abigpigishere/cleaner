"""Cleaner agent entry point. JSON interface to the local scanner, plans and confirmed operations."""
import argparse,ctypes,json,os,re,subprocess,sys,time,urllib.parse,urllib.request,uuid,webbrowser
from pathlib import Path
ROOT=Path(__file__).resolve().parent
URL_FILE=ROOT/'results/running-viewer.json'

def output(value):print(json.dumps(value,ensure_ascii=True),flush=True)

def address():
    value=json.loads(URL_FILE.read_text(encoding='utf-8'))['url'];p=urllib.parse.urlsplit(value)
    if p.scheme!='http' or p.hostname!='127.0.0.1' or not p.port or not re.fullmatch(r'/[A-Za-z0-9_-]{24,}/',p.path):raise ValueError('本地服务地址无效')
    return value

def call(endpoint,body=None,query=None):
    if not re.fullmatch(r'api/[a-z/-]+',endpoint):raise ValueError('接口路径无效')
    url=address();parts=urllib.parse.urlsplit(url)
    target=url+endpoint+('?' +urllib.parse.urlencode(query) if query else '')
    headers={}
    if body is not None:headers={'Content-Type':'application/json','Origin':f'http://127.0.0.1:{parts.port}','X-Cleaner-Token':parts.path.strip('/')}
    request=urllib.request.Request(target,data=json.dumps(body).encode() if body is not None else None,headers=headers)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request,timeout=120) as response:return json.load(response)
    except urllib.error.HTTPError as error:
        try:message=json.load(error).get('error',str(error))
        except Exception:message=str(error)
        raise ValueError(message) from error

def start(open_page=False):
    try:
        call('api/meta');state={'url':address(),'reused':True}
    except (OSError,ValueError,KeyError):
        latest=ROOT/'results/latest-view.json'
        if not latest.exists():raise ValueError('还没有扫描结果，请先执行 scan --target C:\\')
        directory=Path(json.loads(latest.read_text())['scan_dir'])
        if not (directory/'index.bin').is_file():raise ValueError('最近扫描结果不存在，请重新 scan')
        URL_FILE.parent.mkdir(parents=True,exist_ok=True)
        command=[sys.executable,str(ROOT/'view_scan.py'),'--scan-dir',str(directory),'--latest','--url-file',str(URL_FILE),'--idle-minutes','480']
        if URL_FILE.exists():command.append('--reuse-url')
        with (URL_FILE.parent/'viewer.log').open('ab') as log:
            process=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW|subprocess.CREATE_NEW_PROCESS_GROUP,close_fds=True)
        deadline=time.monotonic()+30
        while True:
            try:call('api/meta');break
            except (OSError,ValueError,KeyError):
                if process.poll() is not None or time.monotonic()>deadline:raise RuntimeError('查看服务未启动，请查看 results/viewer.log')
                time.sleep(.2)
        state={'url':address(),'reused':False,'pid':process.pid}
    if open_page:webbrowser.open(state['url'])
    return state

def first_scan(target):
    from viewer_jobs import normalize_target,launch_elevated
    target=normalize_target(target);folder=ROOT/'results/ui-jobs'/uuid.uuid4().hex;folder.mkdir(parents=True)
    (folder/'request.json').write_text(json.dumps({'target':target}),encoding='utf-8')
    process=launch_elevated([str(ROOT/'scan_job.py'),'--job-dir',str(folder)])
    if isinstance(process,int):
        kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_ulong];kernel.CloseHandle.argtypes=[ctypes.c_void_p]
        try:kernel.WaitForSingleObject(process,0xffffffff)
        finally:kernel.CloseHandle(process)
    else:process.wait()
    state=json.loads((folder/'state.json').read_text())
    if state.get('status')!='scanned':raise RuntimeError(state.get('error','首次扫描失败'))
    (folder/'scan/view-target.json').write_text(json.dumps({'target':target,'scope_reference':state.get('scope_reference')}),encoding='utf-8')
    (ROOT/'results/latest-view.json').write_text(json.dumps({'scan_dir':str(folder/'scan')}),encoding='utf-8')
    return str(folder/'scan')

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('doctor');st=sub.add_parser('start');st.add_argument('--open',action='store_true')
    sc=sub.add_parser('scan');sc.add_argument('--target',required=True);sc.add_argument('--no-wait',action='store_true');sc.add_argument('--open',action='store_true')
    sub.add_parser('status')
    c=sub.add_parser('call');c.add_argument('endpoint');c.add_argument('--body-file',type=Path);c.add_argument('--query',default='{}')
    a=p.parse_args()
    if a.command=='doctor':
        import numpy
        output({'platform':sys.platform,'python':sys.version.split()[0],'numpy':numpy.__version__,'native_engine':(ROOT/'native/build/cleaner-scan.exe').is_file(),
                'capabilities':{'scan':'Windows NTFS only','file_delete':'recycle or explicitly confirmed permanent','personal_file_move':'copy/hash verification before source removal','uninstall':'Windows Settings handoff, not automatic uninstall','game_move':'launcher handoff','cache_cleanup':'application-specific manual handling'}});return
    if a.command=='start':output(start(a.open));return
    if a.command=='scan':
        if not (ROOT/'results/latest-view.json').exists():first_scan(a.target);output({'status':'done',**start(a.open)});return
        start(a.open);state=call('api/scan',{'target':a.target})
        if a.no_wait:output(state);return
        last=None
        while state['status'] not in ('done','error','cancelled'):
            if state.get('phase')!=last:output({'status':state['status'],'phase':state.get('phase')});last=state.get('phase')
            time.sleep(.3);state=call('api/scan-status')
        output({**state,'url':address()})
        if state['status']!='done':raise SystemExit(1)
        return
    if a.command=='status':start();output({'scan':call('api/scan-status'),'meta':call('api/meta')});return
    if a.command=='call':
        start();body=None
        if a.body_file:
            if a.body_file.stat().st_size>262144:raise ValueError('请求文件过大')
            body=json.loads(a.body_file.read_text(encoding='utf-8-sig'))
        query=json.loads(a.query)
        if not isinstance(query,dict):raise ValueError('query 必须为 JSON 对象')
        output(call(a.endpoint,body,query))
if __name__=='__main__':
    try:main()
    except Exception as error:output({'error':str(error)});raise SystemExit(1)
