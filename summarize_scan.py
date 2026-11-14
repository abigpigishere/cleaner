"""Make a compact read-only Markdown overview from the native metadata index."""
import argparse
from collections import Counter
import heapq
import json
from pathlib import Path
import shutil
from native_index import Index,NODE,NAME


def gib(value):
    return f'{value/1024**3:.2f} GiB'


def summarize(directory):
    report=json.loads((directory/'report.json').read_text())
    index=Index(directory/'index.bin')
    top=[]
    roots=[]
    unknown=Counter()
    cache={5:report['drive']+'\\'}
    def path(number,trail=None):
        if number in cache:
            return cache[number]
        trail=set() if trail is None else trail
        if number in trail or len(trail)>256:
            return f'[循环或过深路径 #{number}]'
        trail.add(number)
        row=NODE.unpack_from(index.data,index.node_offset+number*NODE.size)
        chosen=row[13]
        if chosen==2**32-1 or chosen>=index.name_count:
            return f'[无名称记录 #{number}]'
        parent,text,chars,space,_=NAME.unpack_from(index.data,index.name_offset+chosen*NAME.size)
        name=index.text(text,chars)
        parent_number=parent&((1<<48)-1)
        if parent_number>=index.count:
            result=f'[父引用缺失]\\{name}'
        else:
            actual=NODE.unpack_from(index.data,index.node_offset+parent_number*NODE.size)
            if actual[0]!=parent:
                result=f'[父引用过期]\\{name}'
            else:
                result=path(parent_number,trail).rstrip('\\')+'\\'+name
        cache[number]=result
        return result
    try:
        for number in range(index.count):
            row=NODE.unpack_from(index.data,index.node_offset+number*NODE.size)
            status=row[11]
            if status==1:
                candidate=(row[3],number,row[2])
                if len(top)<15:
                    heapq.heappush(top,candidate)
                elif candidate>top[0]:
                    heapq.heapreplace(top,candidate)
            elif status in (4,5):
                unknown[status]+=1
            if status==2 and (row[1]&((1<<48)-1))==5 and number!=5:
                roots.append((row[5],number,row[4],row[14]))
        files=[{'path':path(number),'logical_bytes':logical,'allocated_bytes':allocated} for allocated,number,logical in sorted(top,reverse=True)]
        directories=[{'path':path(number),'known_logical_bytes':logical,'known_allocated_bytes':allocated,'unknown_files':missing}
                     for allocated,number,logical,missing in sorted(roots,reverse=True)[:12]]
        usage=shutil.disk_usage(report['drive']+'\\')
        coverage=100*report['sizes_parsed']/report['files']
        result={'scan':report,'coverage_percent':coverage,'disk_space_observed_after_scan':dict(zip(('total','used','free'),usage)),
                'largest_files':files,'root_directories':directories,'unresolved_status_counts':dict(unknown)}
        (directory/'summary.json').write_text(json.dumps(result,ensure_ascii=True,indent=2),encoding='utf-8')
        lines=[f"# {report['drive']} 只读扫描结果",'',
               f"扫描并取得大小和目录汇总：**{report['scan_ready_seconds']:.3f} 秒**；含索引保存：**{report['total_seconds']:.3f} 秒**。",'',
               f"文件 {report['files']:,} 个，目录 {report['directories']:,} 个。已取得 {report['sizes_parsed']:,} 个文件的大小（{coverage:.2f}%）；另有 {report['sizes_unresolved']:,} 个未解析大小，解析错误 {report['errors']}。",'',
               f"扫描后系统报告：容量 {gib(usage.total)}，已用 {gib(usage.used)}，可用 {gib(usage.free)}。",'',
               '## 根目录占用（已解析部分）','',
               '| 目录 | 已知数据簇占用 | 未知大小文件 |','|---|---:|---:|']
        for item in directories:
            safe=item['path'].replace('|','\\|').replace('`','\\`')
            lines.append(f"| `{safe}` | {gib(item['known_allocated_bytes'])} | {item['unknown_files']:,} |")
        lines+=['','## 最大文件（已解析部分）','','| 文件 | 逻辑大小 | 数据簇占用 |','|---|---:|---:|']
        for item in files:
            safe=item['path'].replace('|','\\|').replace('`','\\`')
            lines.append(f"| `{safe}` | {gib(item['logical_bytes'])} | {gib(item['allocated_bytes'])} |")
        lines+=['','## 结果范围','',
                '仅扫描元数据。源卷没有删除、写入或改权限；索引和本报告写在 E 盘。',
                '目录汇总按每个文件的一个长名称父目录计入一次，保留全部已解析链接名称。硬链接归属方式会影响 Windows/WinSxS 等目录之间的分配。',
                '未知大小主要包括跨扩展记录的 ATTRIBUTE_LIST；未知没有按 0 算作已确认占用。目录显示的是已知主数据流部分，不能与系统已用空间直接等同，也不能当作可释放量。',
                f"本次另有 {report['orphan_nodes']:,} 个节点未能关联有效父目录，未强行放入根目录；全局文件排行仍按记录保留。",
                '这是动态卷的一次实测，不是快照；系统缓存未清空，也没有测量 GUI 绘制时间。GiB 按 1024³ 字节换算。']
        (directory/'C盘扫描结果.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
        print(json.dumps({'scan_ready_seconds':report['scan_ready_seconds'],'total_seconds':report['total_seconds'],
                          'coverage_percent':coverage,'largest_files':files[:8],'root_directories':directories[:5],
                          'unresolved':dict(unknown),'free_gib':usage.free/1024**3},ensure_ascii=True,indent=2))
    finally:
        index.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scan-dir',required=True,type=Path)
    summarize(p.parse_args().scan_dir)
