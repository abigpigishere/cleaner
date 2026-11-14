"""Offline whole-index accounting and directory invariants, plus fixture oracle."""
import argparse
from collections import Counter,defaultdict
import json
from pathlib import Path
import struct
from native_index import Index,NODE


def verify(directory,oracle):
    report=json.loads((directory/'report.json').read_text())
    index=Index(directory/'index.bin')
    counts=Counter()
    expected=defaultdict(lambda:[0,0,0])
    failures=[]
    parsed_logical=parsed_allocated=orphans=0
    try:
        fixture=index.verify_oracle(oracle) if oracle else None
        for number in range(index.count):
            fields=NODE.unpack_from(index.data,index.node_offset+number*NODE.size)
            reference,parent,logical,allocated,tree_logical,tree_allocated,nb,nc,sb,sc,links,status,flags,chosen,unknown=fields
            counts[status]+=1
            if status not in (1,2,4,5):
                continue
            if (reference&((1<<48)-1))!=number or nb+nc>index.name_count or sb+sc>index.stream_count:
                failures.append({'record':number,'error':'identity_or_section_bounds'})
            if status!=2:
                expected_own=(logical,allocated,0) if status==1 else (0,0,1)
                if (tree_logical,tree_allocated,unknown)!=expected_own:
                    failures.append({'record':number,'error':'file_tree_totals'})
            if status==1:
                parsed_logical+=logical
                parsed_allocated+=allocated
            candidate=parent&((1<<48)-1)
            if candidate==number==5:
                continue
            valid=False
            if candidate<index.count and candidate!=number and chosen!=2**32-1:
                offset=index.node_offset+candidate*NODE.size
                valid=struct.unpack_from('<Q',index.data,offset)[0]==parent and index.data[offset+66]==2
            if valid:
                target=expected[candidate]
                target[0]+=tree_logical; target[1]+=tree_allocated; target[2]+=unknown
            else:
                orphans+=1
        for number in range(index.count):
            offset=index.node_offset+number*NODE.size
            if index.data[offset+66]!=2:
                continue
            actual=(struct.unpack_from('<Q',index.data,offset+32)[0],
                    struct.unpack_from('<Q',index.data,offset+40)[0],
                    struct.unpack_from('<Q',index.data,offset+72)[0])
            if actual!=tuple(expected.get(number,(0,0,0))):
                failures.append({'record':number,'error':'directory_child_sum'})
        checks={'slots':sum(counts.values())==report['slots'],
                'files':counts[1]+counts[4]+counts[5]==report['files'],
                'directories':counts[2]==report['directories'],
                'parsed_sizes':counts[1]==report['sizes_parsed'],
                'unknown_sizes':counts[4]+counts[5]==report['sizes_unresolved'],
                'errors':counts[6]==report['errors'],
                'logical_total':parsed_logical==report['known_main_logical_bytes'],
                'allocation_total':parsed_allocated==report['known_main_allocated_bytes'],
                'orphans':orphans==report['orphan_nodes'],
                'directory_aggregation':not failures}
        if fixture:
            checks['fixture_oracle']=fixture['all_passed']
        return {'all_passed':all(checks.values()),'checks':checks,'fixture_count':fixture['fixture_count'] if fixture else None,
                'fixtures_passed':fixture['fixtures_passed'] if fixture else None,'whole_index_slots_checked':index.count,
                'directory_nodes_checked':counts[2],'failure_count':len(failures),'failure_sample':failures[:20]}
    finally:
        index.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scan-dir',required=True,type=Path)
    p.add_argument('--oracle',type=Path,help='Optional same-volume fixture oracle')
    p.add_argument('--output',required=True,type=Path)
    args=p.parse_args()
    result=verify(args.scan_dir,args.oracle)
    with args.output.open('x',encoding='utf-8') as target:
        json.dump(result,target,indent=2)
    print(json.dumps(result,indent=2))
    raise SystemExit(0 if result['all_passed'] else 1)
