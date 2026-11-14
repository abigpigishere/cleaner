"""Open/select a verified indexed item in Windows Explorer. Never execute files."""
import ctypes
import os
from pathlib import Path,PureWindowsPath
import subprocess
from size_demo import Reader,FileInfo


def verified_item(viewer,number):
    if not viewer.permits(number):
        raise ValueError('项目不在当前查看范围内')
    row=viewer.row(number)
    path=row['path']
    if PureWindowsPath(path).drive.upper()!=viewer.report['drive'].upper() or '\x00' in path:
        raise ValueError('此项目没有可核实的完整路径')
    reader=Reader()
    handle=reader.open('\\\\?\\'+path)
    try:
        info=FileInfo()
        if not reader.api.GetFileInformationByHandle(handle,ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        reference=info.id_low | info.id_high<<32
        expected=viewer.fields(number)[0]
        if reference!=expected:
            raise ValueError('该路径对应的文件已变化，请重新扫描后操作')
        serial=viewer.report.get('volume',{}).get('volume_serial')
        if serial and info.serial!=int(serial,16)&0xffffffff:
            raise ValueError('盘符对应的卷已变化，请重新扫描')
    finally:
        reader.api.CloseHandle(handle)
        reader.close()
    return row


def open_explorer(viewer,number):
    row=verified_item(viewer,number)
    explorer=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'explorer.exe'
    args=[str(explorer),row['path']] if row['directory'] else [str(explorer),'/select,',row['path']]
    subprocess.Popen(args,creationflags=subprocess.CREATE_NO_WINDOW)
    return {'opened':True,'path':row['path'],'mode':'folder' if row['directory'] else 'select_file'}
