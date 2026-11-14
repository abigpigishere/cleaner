"""Session-only elevated reader. Authenticated local named pipe; fixed scan operation only."""
import argparse,ctypes,json,os,re,threading,time
from pathlib import Path
from multiprocessing.connection import Client
from scan_job import execute,write_state

def main():
 p=argparse.ArgumentParser();p.add_argument('--pipe',required=True);p.add_argument('--key',required=True);p.add_argument('--parent',required=True,type=int);a=p.parse_args()
 if not re.fullmatch(r'cleaner-[a-f0-9]{32}',a.pipe):raise ValueError('Invalid pipe')
 kernel=ctypes.WinDLL('kernel32',use_last_error=True)
 kernel.OpenProcess.argtypes=[ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong];kernel.OpenProcess.restype=ctypes.c_void_p
 kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_ulong]
 parent=kernel.OpenProcess(0x100000,False,a.parent)
 if not parent:raise ctypes.WinError(ctypes.get_last_error())
 def monitor():
  kernel.WaitForSingleObject(parent,0xffffffff);os._exit(0)
 threading.Thread(target=monitor,daemon=True).start()
 root=Path(__file__).parent/'results/ui-jobs'
 with Client('\\\\.\\pipe\\'+a.pipe,family='AF_PIPE',authkey=bytes.fromhex(a.key)) as conn:
  while True:
   try:request=json.loads(conn.recv_bytes(4096))
   except EOFError:break
   if request=={'stop':True}:break
   job=request.get('job','')
   if not re.fullmatch('[a-f0-9]{32}',job):raise ValueError('Invalid job')
   folder=(root/job).resolve(strict=True)
   if folder.parent!=root.resolve():raise ValueError('Invalid job folder')
   try:execute(folder);code=0
   except Exception as error:write_state(folder,status='error',phase='error',error=str(error));code=1
   conn.send_bytes(json.dumps({'exit_code':code}).encode())
if __name__=='__main__':
 try:main()
 except Exception:
  import traceback
  (Path(__file__).parent/'results/worker-error.log').write_text(traceback.format_exc(),encoding='utf-8')
  raise
