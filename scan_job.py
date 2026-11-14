"""Elevated helper: read-only scan selected local NTFS target; write only job output."""
import argparse
import ctypes
import json
from pathlib import Path
import subprocess
import sys
import time
from size_demo import Reader,FileInfo


def write_state(folder,**values):
    (folder/'state.json').write_text(json.dumps(values,ensure_ascii=True),encoding='utf-8')


def execute(folder):
    request=json.loads((folder/'request.json').read_text(encoding='utf-8'))
    target=Path(request['target']).resolve(strict=True)
    if not target.is_dir() or len(target.drive)!=2 or target.drive[1]!=':':
        raise ValueError('请选择本地 NTFS 文件夹或盘符')
    drive=target.drive.upper()
    scope_reference=None
    if str(target).rstrip('\\/').casefold()!=drive.casefold():
        reader=Reader()
        handle=reader.open('\\\\?\\'+str(target))
        try:
            info=FileInfo()
            if not reader.api.GetFileInformationByHandle(handle,ctypes.byref(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            scope_reference=info.id_low | info.id_high<<32
        finally:
            reader.api.CloseHandle(handle)
            reader.close()
    write_state(folder,status='running',phase='bootstrapping',target=str(target))
    result=subprocess.run([sys.executable,str(Path(__file__).parent/'fast_scan.py'),'--drive',drive,
                           '--output-dir',str(folder/'scan')],capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
    (folder/'scanner.log').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
    if result.returncode:
        message=result.stderr.strip()
        if (folder/'scan/report.json').exists():
            report=json.loads((folder/'scan/report.json').read_text())
            message=f"扫描状态：{report['status']}；错误记录 {report.get('errors',0)}"
        raise RuntimeError(message[-1500:] or '扫描未完成，请查看 scanner.log')
    write_state(folder,status='scanned',phase='preparing',target=str(target),scope_reference=scope_reference)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--job-dir',type=Path,required=True)
    args=p.parse_args()
    root=(Path(__file__).parent/'results/ui-jobs').resolve()
    folder=args.job_dir.resolve(strict=True)
    if folder.parent!=root:
        raise SystemExit('Invalid job directory')
    try:
        execute(folder)
    except Exception as error:
        write_state(folder,status='error',phase='error',error=str(error))
        raise SystemExit(1)
