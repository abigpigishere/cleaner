import json
from pathlib import Path
import struct
import tempfile
import unittest
from native_index import HEADER,NODE,NAME
from view_scan import Viewer
from viewer_jobs import normalize_target


class ViewerScopeTests(unittest.TestCase):
    def fixture(self,directory,scoped):
        # Root 5 -> chosen folder 6 -> file 7 and dir 10 -> file 11.
        # Sibling dir 9 -> unrelated file 8 must never enter folder mode.
        parents={2:0,5:5,6:5,7:6,9:5,8:9,10:6,11:10}
        names={2:'unlinked.bin',5:'.',6:'chosen',7:'inside.bin',9:'sibling',8:'outside.bin',10:'nested',11:'deep.bin'}
        directories={5,6,9,10}
        totals={5:(26,12288),6:(18,8192),9:(8,4096),10:(11,4096)}
        text=bytearray(); name_rows=[]; nodes=[]
        for number in range(12):
            if number not in parents:
                nodes.append(NODE.pack(0,0,2**64-1,2**64-1,0,0,0,0,0,0,0,0,0,2**32-1,0))
                continue
            name=names[number].encode('utf-16-le')
            chosen=len(name_rows); parent=(1<<48)|parents[number]
            name_rows.append(NAME.pack(parent,len(text),len(name)//2,1,0));text.extend(name)
            isdir=number in directories
            nodes.append(NODE.pack((1<<48)|number,parent,2**64-1 if isdir else number,2**64-1 if isdir else 4096,
                                   totals[number][0] if isdir else number,totals[number][1] if isdir else 4096,chosen,1,0,0,1,2 if isdir else 1,int(isdir),chosen,0))
        (directory/'index.bin').write_bytes(HEADER.pack(b'CLNIDX1\0',1,80,12,len(name_rows),0,len(text))+b''.join(nodes)+b''.join(name_rows)+text)
        (directory/'report.json').write_text(json.dumps({'drive':'C:','files':4,'directories':4,'sizes_parsed':4,'sizes_unresolved':0}))
        if scoped:
            (directory/'view-target.json').write_text(json.dumps({'target':'C:\\chosen','scope_reference':(1<<48)|6}))

    def test_folder_scope_contains_descendants_and_excludes_sibling(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);self.fixture(directory,True);viewer=Viewer(directory)
            try:
                self.assertEqual(viewer.root,6)
                self.assertEqual(set(viewer.files),{7,11})
                self.assertFalse(viewer.permits(8))
                self.assertFalse(viewer.permits(5))
                self.assertEqual(viewer.scope_info()['directories'],2)
                self.assertEqual(viewer.scope_info()['files'],2)
                rows=viewer.listing('files',6,'allocated','',0,100,False)['rows']
                self.assertEqual({r['name'] for r in rows},{'inside.bin','deep.bin'})
                with self.assertRaises(ValueError):
                    viewer.listing('tree',9,'allocated','',0,100,False)
            finally:
                viewer.index.close()

    def test_full_volume_includes_both_branches(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);self.fixture(directory,False);viewer=Viewer(directory)
            try:
                self.assertEqual(set(viewer.files),{7,8,11})
                self.assertEqual(viewer.path(11),'C:\\chosen\\nested\\deep.bin')
                self.assertEqual(viewer.hidden_unassociated_records,1)
                self.assertEqual(viewer.listing('tree',-1,'allocated','',0,100,False)['total'],0)
            finally:
                viewer.index.close()

    def test_map_items_conserve_area_and_cut_small_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);self.fixture(directory,False);viewer=Viewer(directory)
            try:
                def decode(payload):
                    length=struct.unpack_from('<I',payload)[0];header=json.loads(payload[4:4+length])
                    n=header['count'];base=4+length
                    weights=struct.unpack_from(f'<{n}d',payload,base);base+=8*n
                    ids,parents,exts=(struct.unpack_from(f'<{n}i',payload,base+4*n*k) for k in range(3))
                    return header,weights,ids,parents,exts
                header,weights,ids,parents,exts=decode(viewer.map_items(5,'allocated',10**7))
                self.assertEqual(ids,(5,6,9,7,10,8,11))
                self.assertEqual(weights[0],12288)
                for position in range(1,len(ids)):
                    self.assertLess(parents[position],position)
                    self.assertEqual(viewer.parents[ids[position]],ids[parents[position]])
                for position in range(len(ids)):
                    kids=[weights[k] for k in range(len(ids)) if parents[k]==position]
                    self.assertLessEqual(sum(kids),weights[position])
                    self.assertEqual(kids,sorted(kids,reverse=True))
                self.assertEqual(header['exts'][exts[ids.index(7)]],'bin')
                self.assertEqual(exts[ids.index(6)],-1)
                _,_,ids,_,_=decode(viewer.map_items(5,'allocated',2))
                self.assertEqual(ids,(5,6))
                _,_,ids,_,_=decode(viewer.map_items(5,'allocated',10**7,limit=3))
                self.assertLessEqual(len(ids),3)
                self.assertEqual([r['id'] for r in viewer.lineage(11)],[5,6,10,11])
                self.assertEqual(viewer.locate(11,'allocated'),{'parent':10,'rank':0})
            finally:
                viewer.index.close()

    def test_reject_remote_relative_and_non_path_inputs_before_launch(self):
        for target in ('\\\\server\\share','..','C:relative',None,'C:\\\x00bad'):
            with self.subTest(target=target),self.assertRaises(ValueError):
                normalize_target(target)


if __name__=='__main__':
    unittest.main()
