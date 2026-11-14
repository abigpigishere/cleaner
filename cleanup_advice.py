"""Read-only cleanup candidates from an existing index; never executes file operations."""
from pathlib import PureWindowsPath
import time
import sys
import numpy as np

MIB=1024**2
CACHE_NAMES={'cache','caches','code cache','gpucache','shadercache','d3dscache','.cache','nv_cache','npm-cache'}
DISCOVERY_NAMES=CACHE_NAMES|{'temp','downloads','common','programs','program files','program files (x86)','epic games'}
ARCHIVES={'.zip','.7z','.rar','.iso','.msi','.msix','.msixbundle','.mp4','.mkv','.mov','.vhd','.vhdx','.vmdk'}

def classify(path,directory):
    p=PureWindowsPath(path);parts=tuple(x.casefold() for x in p.parts);name=parts[-1] if parts else '';s='\\'.join(parts)
    if directory and name=='temp' and ('\\appdata\\local\\temp' in s or s.endswith('\\windows\\temp')):
        return dict(kind='cache',label='临时文件候选',priority=0,reason='位于系统或用户的临时目录，可能包含已结束任务留下的数据。',action='关闭相关应用后检查内容；保留仍在使用的文件，通过系统临时文件清理功能处理。')
    if directory and name in CACHE_NAMES and ('\\appdata\\' in s or ('users' in parts and name=='.cache') or 'nvidia corporation' in parts):
        return dict(kind='cache',label='缓存候选',priority=0,reason='位于应用缓存目录，通常可重新生成或重新下载；仍需确认所属应用。',action='优先使用所属应用的清缓存功能。开发工具缓存可能需要重新下载依赖，确认后再处理。')
    if directory and name=='downloads' and 'users' in parts and 'appdata' not in parts:
        return dict(kind='review',label='下载文件待确认',priority=1,reason='下载目录可能保留安装包、压缩包和重复下载，也可能包含仍需保留的个人文件。',action='先检查文件用途，保留原件和未完成下载；将需要长期保留的大文件迁移到其他盘。')
    if directory and len(parts)>=3 and parts[-3:-1]==('steamapps','common'):
        return dict(kind='move',label='游戏迁移候选',priority=2,reason='这是 Steam 游戏安装目录，可以考虑迁移到其他盘或卸载不玩的游戏。',action='在 Steam 的“设置 → 存储空间”中迁移或卸载，不直接删除游戏目录。')
    if directory and len(parts)>=3 and parts[-2]=='epic games':
        return dict(kind='move',label='游戏迁移候选',priority=2,reason='位于游戏安装目录，可检查是否仍使用。',action='使用对应启动器提供的迁移或卸载方式，先确认存档与安装位置。')
    app_root=len(parts)>=3 and parts[-2] in {'program files','program files (x86)'}
    local_app=len(parts)>=5 and parts[-4:-1]==('appdata','local','programs')
    if directory and (app_root or local_app) and name not in {'common files','windowsapps','modifiablewindowsapps','microsoft shared','internet explorer','windows defender','windows nt','windows mail','windows media player','windows photo viewer','windows portable devices','windows security','windows sidebar','msbuild','reference assemblies','nvidia corporation','amd','intel'}:
        return dict(kind='app',label='软件目录待评估',priority=3,reason='位于软件安装位置，占用较大；需要在应用列表中确认对应软件及其用途。',action='在 Windows“设置 → 应用 → 已安装的应用”中确认用途并卸载；不直接删除安装文件夹。')
    if not directory and p.suffix.casefold() in ARCHIVES and 'users' in parts and any(x in parts for x in ('downloads','desktop','documents','videos','桌面','文档','下载')) and 'appdata' not in parts:
        return dict(kind='review',label='大文件待确认',priority=4,reason='这是个人目录中的大型压缩包、镜像、视频或虚拟磁盘，能否处理取决于用途。',action='先查看内容与备份，优先迁移需要保留的文件；虚拟磁盘应先关闭并检查所属虚拟机。')
    return None

def non_overlapping(items,limit=40,per_kind=None):
    chosen=[];paths=[];counts={}
    for item in sorted(items,key=lambda x:(x['priority'],-x['allocated'],x['path'].casefold())):
        kind=item.get('kind')
        if per_kind and counts.get(kind,0)>=per_kind.get(kind,limit):continue
        path=item['path'].rstrip('\\').casefold()
        if any(path==other or path.startswith(other+'\\') or other.startswith(path+'\\') for other in paths):continue
        chosen.append(item);paths.append(path);counts[kind]=counts.get(kind,0)+1
        if len(chosen)>=limit:break
    return chosen

def build_advice(viewer):
    started=time.perf_counter();candidates={}
    def consider(number):
        number=int(number)
        if number in candidates or not viewer.permits(number) or not viewer.linked[number]:return
        row=viewer.row(number)
        if not row['allocated'] or row['allocated']<32*MIB:return
        rule=classify(row['path'],row['directory'])
        if rule and rule['kind']=='app' and PureWindowsPath(sys.executable).is_relative_to(PureWindowsPath(row['path'])):return
        if rule:candidates[number]={**row,**rule}
    for number in np.flatnonzero(viewer.map_dir):
        number=int(number)
        if not viewer.permits(number) or not viewer.linked[number] or viewer.size(number,'allocated')<32*MIB:continue
        name=viewer.name(number).casefold()
        if name not in DISCOVERY_NAMES:continue
        consider(number)
        if name in {'common','programs','program files','program files (x86)','epic games'}:
            for child in viewer.children[viewer.offsets[number]:viewer.offsets[number+1]]:
                if viewer.map_dir[child]:consider(child)
    for number in viewer.file_orders['allocated'][:300]:
        if viewer.size(number,'allocated')<256*MIB:break
        consider(number)
    rows=non_overlapping(candidates.values(),per_kind={'cache':12,'review':10,'move':8,'app':10})
    return {'rows':rows,'candidate_count':len(rows),'candidate_bytes':sum(r['allocated'] for r in rows),
            'query_seconds':time.perf_counter()-started,'read_only':True,
            'basis':'按已扫描的路径、类型和占用筛选；不推断长期未使用，不保证可全部删除或等额释放。'}
