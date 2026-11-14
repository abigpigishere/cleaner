"""Loopback metadata viewer with explicitly requested read-only scan jobs."""
from array import array
from collections import OrderedDict
import json
from pathlib import Path
import struct
import time
import shutil
import numpy as np
from native_index import Index,NODE,NAME

UNKNOWN=np.uint64(2**64-1)
NODE_DTYPE=np.dtype([('reference','<u8'),('parent','<u8'),('logical','<u8'),('allocated','<u8'),('tree_logical','<u8'),
                     ('tree_allocated','<u8'),('name_begin','<u4'),('name_count','<u4'),('stream_begin','<u4'),
                     ('stream_count','<u4'),('links','<u2'),('status','u1'),('flags','u1'),('chosen','<u4'),('unknown','<u8')])
NAME_DTYPE=np.dtype([('parent','<u8'),('text','<u4'),('chars','<u2'),('space','u1'),('reserved','u1')])
# Map tiles are packed little-endian: x,y,w,h as float32 then a uint32 colour key (extension id, or a folder flag).
MAP_FOLDER=0xffffffff


class Viewer:
    def __init__(self,directory):
        started=time.perf_counter()
        self.directory=directory
        self.report=json.loads((directory/'report.json').read_text())
        self.index=Index(directory/'index.bin')
        self.parents=array('i',[-2])*self.index.count
        counts=array('I',[0])*self.index.count
        self.files=array('I')
        self.orphans=array('I')
        self.cache=OrderedDict()
        self.path_cache={5:self.report['drive']+'\\'}
        self.root=5
        self.allowed=None
        self.scope_target=self.report['drive']+'\\'
        data=self.index.data
        for number in range(self.index.count):
            row=self.fields(number)
            if row[11] not in (1,2,4,5):
                continue
            if row[11]!=2:
                self.files.append(number)
            if number==5:
                self.parents[number]=-3
                continue
            candidate=row[1]&((1<<48)-1)
            valid=False
            if candidate<self.index.count and candidate!=number and row[13]!=2**32-1:
                offset=self.index.node_offset+candidate*NODE.size
                valid=struct.unpack_from('<Q',data,offset)[0]==row[1] and data[offset+66]==2
            if valid:
                self.parents[number]=candidate
                counts[candidate]+=1
            else:
                self.parents[number]=-1
                self.orphans.append(number)
        self.offsets=array('I',[0])
        total=0
        for count in counts:
            total+=count
            self.offsets.append(total)
        self.children=array('I',[0])*total
        positions=array('I',self.offsets[:-1])
        for number,parent in enumerate(self.parents):
            if parent>=0:
                self.children[positions[parent]]=number
                positions[parent]+=1
        self.linked=bytearray(self.index.count)
        reachable=array('I',[5]);self.linked[5]=1
        position=0
        while position<len(reachable):
            number=reachable[position];position+=1
            for child in self.children[self.offsets[number]:self.offsets[number+1]]:
                if not self.linked[child]:
                    self.linked[child]=1;reachable.append(child)
        self.hidden_unassociated_records=self.report['files']+self.report['directories']-len(reachable)
        self.files=array('I',(n for n in self.files if self.linked[n]))
        target_file=directory/'view-target.json'
        if target_file.exists():
            target=json.loads(target_file.read_text())
            self.scope_target=target['target']
            reference=target.get('scope_reference')
            if reference is not None:
                self.set_scope(reference)
        self.file_orders={'allocated':array('I',sorted(self.files,key=lambda n:self.size(n,'allocated'),reverse=True))}
        self.prepare_map_arrays()
        self.preparation_seconds=time.perf_counter()-started

    def prepare_map_arrays(self):
        """Copy per-record sizes and extension ids into numpy arrays (copies, so the mmap can still close)."""
        index=self.index
        nodes=np.frombuffer(index.data,dtype=NODE_DTYPE,count=index.count,offset=index.node_offset)
        status=nodes['status'].copy()
        directory=status==2
        self.map_weights={}
        for metric,own,tree in (('allocated','allocated','tree_allocated'),('logical','logical','tree_logical')):
            values=np.where(directory,nodes[tree],nodes[own])
            values[values==UNKNOWN]=0
            values[~np.isin(status,(1,2,4,5))]=0
            values[np.frombuffer(self.linked,dtype=np.uint8)==0]=0
            self.map_weights[metric]=values.astype(np.int64)
        chosen=nodes['chosen'].copy()
        del nodes
        names=np.frombuffer(index.data,dtype=NAME_DTYPE,count=index.name_count,offset=index.name_offset)
        text=np.frombuffer(index.data,dtype='<u2',count=index.text_bytes//2,offset=index.text_offset)
        ext=np.full(index.count,-1,dtype=np.int32)
        files=np.nonzero(~directory&(chosen!=2**32-1)&np.isin(status,(1,4,5)))[0]
        codes=np.zeros(len(files),dtype=np.int64)
        if len(files):
            picked=names[chosen[files]]
            start=(picked['text']//2).astype(np.int64);chars=picked['chars'].astype(np.int64)
            back=np.arange(1,9)
            positions=start[:,None]+chars[:,None]-back
            valid=back<chars[:,None]
            letters=np.where(valid,text[np.clip(positions,0,len(text)-1)],0).astype(np.int64)
            dots=letters==46
            first=np.where(dots.any(1),dots.argmax(1),-1)
            lower=np.where((letters>=65)&(letters<=90),letters+32,letters)
            for k in range(1,8):
                usable=first==k
                if not usable.any():
                    continue
                block=lower[usable,:k]
                ok=((block>=48)&(block<=57)|(block>=97)&(block<=122)|(block==95)|(block==45)).all(1)
                weights=128**np.arange(k-1,-1,-1)
                rows=np.nonzero(usable)[0][ok]
                codes[rows]=(block[ok]*weights).sum(1)
            del picked
        unique,inverse=np.unique(np.concatenate((codes,[0])),return_inverse=True)
        ext[~directory]=inverse[-1]
        ext[files]=inverse[:-1].astype(np.int32)
        self.map_dir=directory
        def decode(code):
            letters=[]
            while code:
                letters.append(chr(code%128));code//=128
            return ''.join(letters)
        self.map_exts=[decode(int(code)) for code in unique]
        self.map_ext=ext
        self.map_children=np.frombuffer(self.children,dtype=np.uint32)
        self.map_offsets=np.frombuffer(self.offsets,dtype=np.uint32)
        del names,text

    def set_scope(self,reference):
        root=reference&((1<<48)-1)
        if self.fields(root)[0]!=reference or self.fields(root)[11]!=2:
            raise ValueError('扫描中的文件夹记录已变化，请重新选择目标')
        self.root=root
        self.path_cache[root]=self.scope_target
        self.allowed=bytearray(self.index.count)
        queue=array('I',[root])
        self.allowed[root]=1
        current=0
        while current<len(queue):
            number=queue[current]
            current+=1
            for child in self.children[self.offsets[number]:self.offsets[number+1]]:
                if not self.allowed[child]:
                    self.allowed[child]=1
                    queue.append(child)
        self.files=array('I',(n for n in queue if self.fields(n)[11] in (1,4,5)))
        self.orphans=array('I')
        self.scope_directories=sum(self.fields(n)[11]==2 for n in queue)

    def permits(self,number):
        return 0<=number<self.index.count and (self.allowed is None or bool(self.allowed[number]))

    def scope_info(self):
        if self.allowed is None:
            return {'root':5,'target':self.scope_target,'folder':False,'files':self.report['files'],
                    'directories':self.report['directories'],'sizes_parsed':self.report['sizes_parsed'],
                    'sizes_unresolved':self.report['sizes_unresolved'],'visible_files':len(self.files),
                    'hidden_unassociated_records':self.hidden_unassociated_records}
        row=self.fields(self.root)
        return {'root':self.root,'target':self.scope_target,'folder':True,'files':len(self.files),
                'directories':self.scope_directories,
                'sizes_unresolved':row[14],'sizes_parsed':len(self.files)-row[14],
                'visible_files':len(self.files),'hidden_unassociated_records':0}

    def fields(self,number):
        if not 0<=number<self.index.count:
            raise ValueError('Record number outside index')
        return NODE.unpack_from(self.index.data,self.index.node_offset+number*NODE.size)

    def name(self,number):
        chosen=self.fields(number)[13]
        if chosen==2**32-1:
            return f'无名称记录 #{number}'
        if chosen>=self.index.name_count:
            raise ValueError('Invalid chosen name index')
        _,offset,chars,_,_=NAME.unpack_from(self.index.data,self.index.name_offset+chosen*NAME.size)
        return self.index.text(offset,chars)

    def path(self,number):
        chain=[]
        current=number
        seen=set()
        while current not in self.path_cache:
            if current in seen or len(chain)>256:
                return '[目录关联循环]\\'+self.name(number)
            seen.add(current)
            chain.append(current)
            parent=self.parents[current]
            if parent<0:
                self.path_cache[current]='[未关联父目录]\\'+self.name(current)
                break
            current=parent
        for node in reversed(chain):
            if node not in self.path_cache:
                self.path_cache[node]=self.path_cache[self.parents[node]].rstrip('\\')+'\\'+self.name(node)
        result=self.path_cache[number]
        if len(self.path_cache)>30000:
            self.path_cache={5:self.report['drive']+'\\'}
        return result

    def size(self,number,metric):
        row=self.fields(number)
        value=row[(4 if metric=='logical' else 5) if row[11]==2 else (2 if metric=='logical' else 3)]
        return -1 if value==2**64-1 else value

    def row(self,number):
        data=self.fields(number)
        directory=data[11]==2
        logical=data[4] if directory else data[2]
        allocated=data[5] if directory else data[3]
        return {'id':number,'name':self.report['drive']+'\\' if number==5 else self.name(number),
                'path':self.path(number),'directory':directory,
                'logical':None if logical==2**64-1 else logical,'allocated':None if allocated==2**64-1 else allocated,
                'unknown':data[14] if directory else int(data[11] in (4,5)),
                'status':data[11],'links':data[10],
                'child_count':self.offsets[number+1]-self.offsets[number] if directory else 0}

    def cached(self,key,build):
        if key not in self.cache:
            self.cache[key]=build()
            while len(self.cache)>8:
                self.cache.popitem(last=False)
        self.cache.move_to_end(key)
        return self.cache[key]

    def listing(self,mode,parent,metric,query,offset,limit,dirs_only):
        started=time.perf_counter()
        if mode=='files':
            if metric not in self.file_orders:
                self.file_orders[metric]=array('I',sorted(self.files,key=lambda n:self.size(n,metric),reverse=True))
            ids=self.file_orders[metric]
            if query:
                lowered=query.casefold()
                ids=self.cached(('search',metric,lowered),lambda:array('I',(n for n in ids if lowered in self.name(n).casefold())))
        else:
            if parent==-1:
                source=()
            else:
                if not self.permits(parent):
                    raise ValueError('目录不在当前查看范围内')
                self.fields(parent)
                source=self.children[self.offsets[parent]:self.offsets[parent+1]]
            ids=self.cached(('children',parent,metric,dirs_only),
                            lambda:array('I',sorted((n for n in source if not dirs_only or self.fields(n)[11]==2),
                                key=lambda n:(self.fields(n)[11]!=2,-self.size(n,metric),n))))
        selected=ids[offset:offset+limit]
        return {'rows':[self.row(n) for n in selected],'total':len(ids),'offset':offset,'limit':limit,
                'query_seconds':time.perf_counter()-started}

    def lineage(self,number):
        if not self.permits(number):
            raise ValueError('项目不在当前范围内')
        chain=[];seen=set()
        while True:
            if number in seen or len(chain)>256:
                raise ValueError('目录关联不完整')
            seen.add(number);chain.append(number)
            if number==self.root:
                break
            number=self.parents[number]
            if number<0:
                raise ValueError('项目未关联到当前目录树')
        return [self.row(n) for n in reversed(chain)]

    def locate(self,number,metric):
        if not self.permits(number):
            raise ValueError('项目不在当前范围内')
        parent=self.parents[number]
        if number==self.root:
            return {'parent':self.root,'rank':0}
        if parent<0:
            raise ValueError('项目未关联到目录树')
        self.listing('tree',parent,metric,'',0,1,False)
        ids=self.cache[('children',parent,metric,False)]
        return {'parent':parent,'rank':ids.index(number)}

    def disk_usage(self):
        try:
            usage=shutil.disk_usage(self.report['drive']+'\\')
            return {'total':usage.total,'used':usage.used,'free':usage.free}
        except OSError:
            return None

    def map_items(self,number,metric,pixels,limit=300000):
        """Breadth-first list of every item covering at least ~1 px² of a canvas with `pixels` area.
        Smaller items are summed per folder and extension into groups (id = -file count); groups that are
        still below 1 px² stay as unlisted area inside their folder, so proportions always hold."""
        started=time.perf_counter()
        if not self.permits(number) or self.fields(number)[11]!=2:
            raise ValueError('请选择当前范围内的文件夹')
        weights=self.map_weights[metric]
        total=int(weights[number])
        threshold=max(1.0,total/max(1,pixels))
        while True:
            levels=self.collect_map_levels(number,weights,threshold,limit)
            if levels is not None:
                break
            threshold*=2
        ids,parents,exts,sizes=(np.concatenate(part) for part in zip(*levels))
        # Unlisted subpixel items and the directory's own index/attribute clusters retain their area.
        # Keep the remainder as folder background; never renormalize children to fill their parent.

        named=np.nonzero((ids>=0)&self.map_dir[np.maximum(ids,0)]&(sizes>=threshold*256))[0]
        names={int(position):self.name(int(ids[position])) for position in named}
        names[0]=self.row(number)['name']
        header={'metric':metric,'total':total,'threshold':threshold,'count':len(ids),'exts':self.map_exts,
                'names':names,'root':self.row(number),'unknown_files':self.fields(number)[14],
                'query_seconds':time.perf_counter()-started}
        text=json.dumps(header,ensure_ascii=True).encode()
        text+=b' '*(-(4+len(text))%8)
        return b''.join((struct.pack('<I',len(text)),text,sizes.astype('<f8').tobytes(),
                         ids.astype('<i4').tobytes(),parents.astype('<i4').tobytes(),exts.astype('<i4').tobytes()))

    def collect_map_levels(self,number,weights,threshold,limit):
        children=self.map_children;offsets=self.map_offsets;kinds=len(self.map_exts)+1
        frontier=np.array([number],dtype=np.int64)
        levels=[(frontier,np.array([-1],dtype=np.int64),np.array([-1],dtype=np.int64),weights[frontier].astype(np.float64))]
        positions=np.array([0],dtype=np.int64);count=1
        while len(frontier):
            begin=offsets[frontier].astype(np.int64);counts=offsets[frontier+1].astype(np.int64)-begin
            if not counts.sum():
                break
            starts=np.cumsum(counts)-counts
            kids=children[np.repeat(begin-starts,counts)+np.arange(int(counts.sum()))].astype(np.int64)
            owners=np.repeat(positions,counts);parents=owners
            sizes=weights[kids]
            keep=sizes>=threshold
            folder=self.map_dir[kids]
            hidden=~keep&folder&(sizes>0)
            small=~keep&~folder&(sizes>0)
            keys=owners[small]*kinds+self.map_ext[kids[small]]+1
            groups,inverse=np.unique(keys,return_inverse=True)
            sums=np.bincount(inverse,weights=sizes[small].astype(np.float64)) if len(groups) else np.zeros(0)
            members=np.bincount(inverse) if len(groups) else np.zeros(0,dtype=np.int64)
            dust=sums>=threshold
            ids=np.concatenate((kids[keep],-members[dust]))
            owners=np.concatenate((owners[keep],groups[dust]//kinds))
            exts=np.concatenate((self.map_ext[kids[keep]],groups[dust]%kinds-1))
            sizes=np.concatenate((sizes[keep].astype(np.float64),sums[dust]))
            order=np.lexsort((-sizes,owners))
            ids,owners,exts,sizes=ids[order],owners[order],exts[order],sizes[order]
            here=count+np.arange(len(ids))
            count+=len(ids)
            if count>limit:
                return None
            levels.append((ids,owners,exts,sizes))
            # Folders too small to draw are walked anyway; their files join the groups of the drawn ancestor.
            folders=(ids>=0)&self.map_dir[np.maximum(ids,0)]
            frontier=np.concatenate((ids[folders],kids[hidden]))
            positions=np.concatenate((here[folders],parents[hidden]))
        return levels


def main():
    from viewer_server import main as serve
    serve()


if __name__=='__main__':
    main()
