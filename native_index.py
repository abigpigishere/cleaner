"""Read the compact native index using mmap; no executable or source-drive access."""
import json
import mmap
from pathlib import Path
import struct

HEADER=struct.Struct('<8sIIQQQQ')
NODE=struct.Struct('<6Q4IHBBIQ')
NAME=struct.Struct('<QI HBB')
STREAM=struct.Struct('<7QIHHHBB')
STATUSES=('empty','parsed','directory_not_file','extension','unsupported_attribute_list','incomplete_or_missing_stream','error','unused')


class Index:
    def __init__(self,path):
        self.file=Path(path).open('rb')
        try:
            self.data=mmap.mmap(self.file.fileno(),0,access=mmap.ACCESS_READ)
            magic,version,node_size,self.count,self.name_count,self.stream_count,self.text_bytes=HEADER.unpack_from(self.data)
            if magic!=b'CLNIDX1\0' or version!=1 or node_size!=NODE.size:
                raise ValueError('Unsupported index format')
            self.node_offset=HEADER.size
            self.name_offset=self.node_offset+self.count*NODE.size
            self.stream_offset=self.name_offset+self.name_count*NAME.size
            self.text_offset=self.stream_offset+self.stream_count*STREAM.size
            if self.text_offset+self.text_bytes!=len(self.data):
                raise ValueError('Index section lengths do not match file')
        except Exception:
            self.close()
            raise

    def close(self):
        if hasattr(self,'data'):
            self.data.close()
        self.file.close()

    def text(self,offset,chars):
        if offset+chars*2>self.text_bytes:
            raise ValueError('Text outside index arena')
        return self.data[self.text_offset+offset:self.text_offset+offset+chars*2].decode('utf-16-le',errors='surrogatepass')

    def node(self,number):
        if not 0<=number<self.count:
            raise ValueError('Record number outside index')
        ref,parent,logical,allocated,tree_logical,tree_allocated,nb,nc,sb,sc,links,status,flags,chosen,unknown=NODE.unpack_from(self.data,self.node_offset+number*NODE.size)
        if status>=len(STATUSES) or nb+nc>self.name_count or sb+sc>self.stream_count:
            raise ValueError('Invalid node sections')
        names=[]
        for i in range(nb,nb+nc):
            parent_ref,text,chars,namespace,_=NAME.unpack_from(self.data,self.name_offset+i*NAME.size)
            names.append({'parent_id':hex(parent_ref),'namespace':namespace,'name':self.text(text,chars)})
        streams=[]
        for i in range(sb,sb+sc):
            lo,alloc,header,runs,initialized,low,high,text,chars,af,instance,resident,kind=STREAM.unpack_from(self.data,self.stream_offset+i*STREAM.size)
            stream={'name':self.text(text,chars),'resident':bool(resident),'attribute_flags':af,'instance':instance,
                    'logical_bytes':None if lo==2**64-1 else lo,'allocated_bytes':None if alloc==2**64-1 else alloc,
                    'allocated_header_bytes':header,'run_allocated_bytes':runs,
                    'storage_kind':('resident_in_mft','nonresident','sparse','compressed')[kind&127]}
            if not resident:
                stream.update(low_vcn=low,high_vcn=high,initialized_bytes=initialized)
                if kind&128:
                    stream['allocation_needs_other_extents']=True
            streams.append(stream)
        return {'file_reference':hex(ref),'parent_reference':hex(parent),'logical_bytes':None if logical==2**64-1 else logical,
                'allocated_bytes':None if allocated==2**64-1 else allocated,'hard_link_count':links,
                'size_status':STATUSES[status],'is_directory':bool(flags&1),'names':names,'streams':streams,
                'tree_logical_bytes':tree_logical,'tree_allocated_bytes':tree_allocated,'unknown_descendants':unknown}

    def verify_oracle(self,oracle_path):
        oracle=json.loads(Path(oracle_path).read_text(encoding='utf-8-sig'))
        results=[]
        for sample in oracle['results']:
            actual=self.node(int(sample['file_reference'],16)&((1<<48)-1))
            expected=sample['mft']
            checks={key:actual[key]==expected[key] for key in ('logical_bytes','allocated_bytes','hard_link_count','names','streams','size_status')}
            checks['identity']=actual['file_reference']==sample['file_reference']
            results.append({'path':sample['path'],'checks':checks,'passed':all(checks.values())})
        return {'fixture_count':len(results),'fixtures_passed':sum(r['passed'] for r in results),
                'all_passed':all(r['passed'] for r in results),'fixtures':results}


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('index',type=Path)
    parser.add_argument('--oracle',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    index=Index(args.index)
    try:
        result=index.verify_oracle(args.oracle)
    finally:
        index.close()
    with args.output.open('x',encoding='utf-8') as output:
        json.dump(result,output,ensure_ascii=True,indent=2)
    print(json.dumps(result,ensure_ascii=True,indent=2))
    raise SystemExit(0 if result['all_passed'] else 1)
