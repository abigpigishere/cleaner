"""Read installed-app metadata. Uninstall handoff uses Windows Settings, never registry commands."""
import hashlib,os
from pathlib import PureWindowsPath

def inventory():
    import winreg
    rows=[];seen=set()
    for hive,label in [(winreg.HKEY_CURRENT_USER,'HKCU'),(winreg.HKEY_LOCAL_MACHINE,'HKLM')]:
        for view in [winreg.KEY_WOW64_64KEY,winreg.KEY_WOW64_32KEY]:
            try:root=winreg.OpenKey(hive,r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',0,winreg.KEY_READ|view)
            except OSError:continue
            with root:
                for index in range(winreg.QueryInfoKey(root)[0]):
                    try:
                        name=winreg.EnumKey(root,index)
                        with winreg.OpenKey(root,name) as key:
                            def get(n,default=''):
                                try:return winreg.QueryValueEx(key,n)[0]
                                except OSError:return default
                            title=get('DisplayName');location=os.path.expandvars(str(get('InstallLocation'))).strip('" ')
                            if not title or get('SystemComponent',0) or get('NoRemove',0):continue
                            identity=hashlib.sha256((label+str(view)+name).encode()).hexdigest()[:20]
                            signature=(title,str(get('DisplayVersion')),location)
                            if signature in seen:continue
                            seen.add(signature)
                            rows.append({'app_id':identity,'name':title,'version':str(get('DisplayVersion')),'publisher':str(get('Publisher')),
                                         'install_location':location,'estimated_kib':get('EstimatedSize',0),'uninstall_available':bool(get('UninstallString')),'method':'windows_settings'})
                    except OSError:continue
    return sorted(rows,key=lambda r:r['name'].casefold())

def related_apps(path):
    target=PureWindowsPath(path);result=[]
    for row in inventory():
        location=row['install_location']
        if not location:continue
        p=PureWindowsPath(location)
        if not p.is_absolute() or len(p.parts)<=1:continue
        if p==target or p.is_relative_to(target) or target.is_relative_to(p):result.append(row)
    return result

def open_uninstall_settings():
    os.startfile('ms-settings:appsfeatures')
    return {'status':'awaiting_user','message':'已打开 Windows 已安装的应用；请在系统界面确认卸载。未报告卸载成功，完成后需要重新核对。'}
