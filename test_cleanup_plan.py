import json,tempfile,unittest
from pathlib import Path
from cleanup_plan import PlanStore,unique_occupied
import test_viewer_scope as fixtures
from view_scan import Viewer

class PlanTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.directory=Path(self.tmp.name);fixtures.ViewerScopeTests().fixture(self.directory,False);self.viewer=Viewer(self.directory);self.store=PlanStore(self.viewer)
 def tearDown(self):self.viewer.index.close();self.tmp.cleanup()
 def request(self,items):
  _,revision=self.store.load();return {'scan_id':self.store.scan_id,'revision':revision,'items':items}
 def test_agent_plan_roundtrip_rebuilds_authoritative_size(self):
  value={'schema_version':1,'scan_id':self.store.scan_id,'items':[{'id':7,'reference':hex(self.viewer.fields(7)[0]),'operation':'review','reason':'确认这个文件的用途','evidence':['来自指定扫描']}]}
  result=self.store.save(value);self.assertEqual(result['source'],'agent');self.assertEqual(result['items'][0]['allocated'],4096);self.assertTrue(self.store.path.exists())
 def test_rejects_stale_scan_and_identity_and_arbitrary_commands(self):
  value={'schema_version':1,'scan_id':'old','items':[]}
  with self.assertRaises(ValueError):self.store.save(value)
  value.update(scan_id=self.store.scan_id,items=[{'id':7,'reference':'0x1','operation':'review'}])
  with self.assertRaises(ValueError):self.store.save(value)
  value['items']=[{'id':7,'reference':hex(self.viewer.fields(7)[0]),'command':'delete everything'}]
  with self.assertRaises(ValueError):self.store.save(value)
 def test_preview_is_readonly_and_deduplicates_nested_selections(self):
  data=self.store.preview(self.request([{'id':6,'operation':'review'},{'id':7,'operation':'review'}]))
  self.assertFalse(data['executed']);self.assertTrue(data['read_only']);self.assertEqual(data['occupied_bytes'],8192);self.assertEqual(data['covered_count'],1);self.assertEqual(data['items'][1]['covered_by'],6)
  self.assertTrue((self.directory/'preview.json').exists());self.assertIsNone(data['estimated_release_bytes'])
 def test_preview_rejects_stale_revision_and_disallowed_action(self):
  request=self.request([{'id':7,'operation':'review'}]);request['revision']='old'
  with self.assertRaises(ValueError):self.store.preview(request)
  with self.assertRaises(ValueError):self.store.preview(self.request([{'id':5,'operation':'recycle'}]))
 def test_folder_scope_rejects_outside_items(self):
  self.viewer.set_scope((1<<48)|6)
  scoped=PlanStore(self.viewer)
  with self.assertRaises(ValueError):scoped.checked_row(8)
 def test_file_filters_combine_size_type_and_name(self):
  rows=self.viewer.listing('files',5,'allocated','',0,100,False,4096,'all')['rows'];self.assertEqual(len(rows),3)
  self.assertEqual(self.viewer.listing('files',5,'allocated','',0,100,False,4097,'all')['total'],0)
  self.assertEqual(self.viewer.listing('files',5,'allocated','',0,100,False,0,'video')['total'],0)
  self.assertEqual(self.viewer.listing('files',5,'allocated','inside',0,100,False,0,'all')['total'],1)
  with self.assertRaises(ValueError):self.viewer.listing('files',5,'allocated','',0,100,False,0,'invalid')
if __name__=='__main__':unittest.main()
