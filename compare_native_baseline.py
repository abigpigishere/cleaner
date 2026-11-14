"""Offline broad comparison; distinct live scans may contain genuinely changed files."""
import argparse
import json
from pathlib import Path
import sqlite3
from native_index import Index,NODE


def compare(index_path,baseline_path):
    index=Index(index_path)
    db=sqlite3.connect(baseline_path.resolve().as_uri()+'?mode=ro',uri=True)
    result={'same_identity_records':0,'missing_or_reused_records':0,'matching_core_fields':0,
            'core_differences':[],'sampled_metadata_records':0,'sampled_metadata_differences':[],
            'note':'Scans taken at different times; differences must be inspected, not automatically blamed on live changes.'}
    try:
        for number,reference,directory,links,names,logical,allocated,status,streams in db.execute('SELECT * FROM records'):
            if number>=index.count:
                result['missing_or_reused_records']+=1
                continue
            fields=NODE.unpack_from(index.data,index.node_offset+number*NODE.size)
            if hex(fields[0])!=reference:
                result['missing_or_reused_records']+=1
                continue
            result['same_identity_records']+=1
            same=(fields[2] if fields[2]!=2**64-1 else None)==logical and (fields[3] if fields[3]!=2**64-1 else None)==allocated and fields[10]==links and bool(fields[12]&1)==bool(directory)
            if same:
                result['matching_core_fields']+=1
            else:
                result['core_differences'].append({'number':number,'reference':reference,'names':json.loads(names),
                     'old_logical':logical,'new_logical':None if fields[2]==2**64-1 else fields[2],
                     'old_allocated':allocated,'new_allocated':None if fields[3]==2**64-1 else fields[3]})
            if number%257==0:
                actual=index.node(number)
                checks={'names':actual['names']==json.loads(names),'links':actual['hard_link_count']==links}
                if status=='parsed':
                    checks['streams']=actual['streams']==json.loads(streams)
                result['sampled_metadata_records']+=1
                if not all(checks.values()):
                    result['sampled_metadata_differences'].append({'number':number,'checks':checks})
        return result
    finally:
        db.close()
        index.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--index',required=True,type=Path)
    p.add_argument('--baseline',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    args=p.parse_args()
    result=compare(args.index,args.baseline)
    with args.output.open('x',encoding='utf-8') as target:
        json.dump(result,target,ensure_ascii=True,indent=2)
    print(json.dumps({key:(len(value) if isinstance(value,list) else value) for key,value in result.items()},indent=2))
