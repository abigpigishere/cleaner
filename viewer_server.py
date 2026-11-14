"""Local UI server: browsing GETs and token-protected explicit scan/picker POSTs."""
import argparse
from datetime import datetime
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit,parse_qs
from view_scan import Viewer
from viewer_jobs import Controller,drives
from viewer_shell import open_explorer


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scan-dir',required=True,type=Path)
    parser.add_argument('--url-file',type=Path)
    parser.add_argument('--idle-minutes',type=int,default=30)
    parser.add_argument('--open',action='store_true')
    parser.add_argument('--reuse-url',action='store_true',help='Reuse the saved local address after a controlled restart')
    parser.add_argument('--latest',action='store_true',help='Restore the last successfully scanned target')
    args=parser.parse_args()
    latest=Path(__file__).parent/'results/latest-view.json'
    if args.latest and latest.exists():
        candidate=Path(json.loads(latest.read_text())['scan_dir']).resolve()
        if candidate.is_relative_to((Path(__file__).parent/'results').resolve()) and (candidate/'report.json').is_file():
            args.scan_dir=candidate
    controller=Controller(Viewer(args.scan_dir),Viewer)
    assets=Path(__file__).parent/'viewer'
    token=secrets.token_urlsafe(24)
    port=0
    if args.reuse_url and args.url_file and args.url_file.exists():
        previous=urlsplit(json.loads(args.url_file.read_text())['url'])
        if previous.hostname!='127.0.0.1' or not previous.port or len(previous.path.strip('/'))<24:
            raise ValueError('Invalid saved local address')
        token=previous.path.strip('/')
        port=previous.port
    last_request=time.monotonic()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*values):
            pass

        def send(self,code,payload,kind='application/json'):
            if not isinstance(payload,bytes):
                payload=json.dumps(payload,ensure_ascii=True).encode()
            self.send_response(code)
            self.send_header('Content-Type',kind)
            self.send_header('Content-Length',str(len(payload)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError,ConnectionResetError):
                pass

        def route(self):
            nonlocal last_request
            if self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}':
                self.send(403,{'error':'Invalid host'})
                return None
            parsed=urlsplit(self.path)
            prefix='/'+token+'/'
            if not parsed.path.startswith(prefix):
                self.send(404,{'error':'Not found'})
                return None
            last_request=time.monotonic()
            return parsed.path[len(prefix):],parse_qs(parsed.query)

        def do_POST(self):
            match=self.route()
            if match is None:
                return
            origin=f'http://127.0.0.1:{self.server.server_port}'
            if self.headers.get('Origin')!=origin or self.headers.get('X-Cleaner-Token')!=token:
                return self.send(403,{'error':'Invalid request origin/token'})
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=8192 or self.headers.get_content_type()!='application/json':
                    return self.send(400,{'error':'Invalid JSON request'})
                body=json.loads(self.rfile.read(length))
                route,_=match
                if route=='api/scan':
                    value=controller.start(body.get('target'))
                elif route=='api/pick-folder':
                    value=controller.pick()
                elif route=='api/explorer':
                    with controller.viewer_lock:
                        value=open_explorer(controller.viewer,int(body['id']))
                else:
                    return self.send(404,{'error':'Not found'})
                self.send(202,value)
            except (ValueError,OSError,TypeError,KeyError) as error:
                self.send(400,{'error':str(error)})

        def do_GET(self):
            match=self.route()
            if match is None:
                return
            route,params=match
            get=lambda key,default:params.get(key,[default])[0]
            try:
                if route in ('','viewer.css','viewer.js','treemap.js'):
                    filename='viewer.html' if route=='' else route
                    kinds={'viewer.html':'text/html; charset=utf-8','viewer.css':'text/css; charset=utf-8','viewer.js':'text/javascript; charset=utf-8','treemap.js':'text/javascript; charset=utf-8'}
                    return self.send(200,(assets/filename).read_bytes(),kinds[filename])
                if route=='api/scan-status':
                    return self.send(200,controller.status())
                if route=='api/picker-status':
                    return self.send(200,controller.picker)
                if route=='api/drives':
                    return self.send(200,{'drives':drives()})
                with controller.viewer_lock:
                    viewer=controller.viewer
                    if route=='api/meta':
                        value={'report':viewer.report,'preparation_seconds':viewer.preparation_seconds,
                               'scope':viewer.scope_info(),'generation':controller.generation,
                               'index_time':datetime.fromtimestamp((viewer.directory/'index.bin').stat().st_mtime).isoformat(timespec='seconds'),
                               'root':viewer.row(viewer.root),'orphans':len(viewer.orphans),'disk':viewer.disk_usage(),'idle_minutes':args.idle_minutes}
                    elif route=='api/list':
                        mode=get('mode','tree')
                        metric=get('metric','allocated')
                        if mode not in ('tree','files') or metric not in ('allocated','logical'):
                            raise ValueError('Invalid list mode/metric')
                        offset=int(get('offset','0')); limit=int(get('limit','100'))
                        query=get('query','')
                        if offset<0 or not 1<=limit<=200 or len(query)>160:
                            raise ValueError('Invalid page bounds/query')
                        value=viewer.listing(mode,int(get('parent',str(viewer.root))),metric,query,offset,limit,get('dirs','0')=='1')
                    elif route=='api/node':
                        number=int(get('id',str(viewer.root)))
                        if not viewer.permits(number):
                            raise ValueError('项目不在当前文件夹范围内')
                        value={**viewer.row(number),'metadata':viewer.index.node(number)}
                    elif route=='api/lineage':
                        value={'rows':viewer.lineage(int(get('id',str(viewer.root))))}
                    elif route=='api/locate':
                        metric=get('metric','allocated')
                        if metric not in ('allocated','logical'):
                            raise ValueError('Invalid metric')
                        value=viewer.locate(int(get('id',str(viewer.root))),metric)
                    elif route=='api/map':
                        metric=get('metric','allocated');pixels=int(get('pixels','1000000'))
                        if metric not in ('allocated','logical') or not 10000<=pixels<=20000000:
                            raise ValueError('Invalid treemap options')
                        return self.send(200,viewer.map_items(int(get('id',str(viewer.root))),metric,pixels),'application/octet-stream')
                    else:
                        return self.send(404,{'error':'Not found'})
                    return self.send(200,value)
            except (ValueError,KeyError,IndexError,OSError) as error:
                self.send(400,{'error':str(error)})
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.daemon_threads=True
    server.timeout=0.5
    url=f'http://127.0.0.1:{server.server_port}/{token}/'
    state={'url':url,'preparation_seconds':controller.viewer.preparation_seconds,'idle_minutes':args.idle_minutes}
    if args.url_file:
        args.url_file.write_text(json.dumps(state,indent=2),encoding='utf-8')
    print(json.dumps(state),flush=True)
    if args.open:
        import webbrowser
        webbrowser.open(url)
    try:
        while time.monotonic()-last_request<args.idle_minutes*60:
            server.handle_request()
    except KeyboardInterrupt:
        pass
    finally:
        controller.session.close()
        server.server_close()
        with controller.viewer_lock:
            controller.viewer.index.close()


if __name__=='__main__':
    main()
