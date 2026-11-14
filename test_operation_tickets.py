import os,tempfile,threading,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from file_operations import OperationManager
WORK=Path(os.environ.get('CLEANER_TEST_ROOT',str(Path.home()/'Documents'/'CleanerTests')))

class TicketTests(unittest.TestCase):
 def setUp(self):
  WORK.mkdir(parents=True,exist_ok=True)
  self.temp=tempfile.TemporaryDirectory(prefix='cleaner-ticket-test-',dir=WORK);self.root=Path(self.temp.name);self.files={1:self.root/'one.bin',2:self.root/'two.bin'}
  for p in self.files.values():p.write_bytes(b'owned ticket fixture')
  viewer=SimpleNamespace(directory=self.root,fields=lambda n:(self.files[n].stat().st_ino,))
  self.controller=SimpleNamespace(viewer=viewer,viewer_lock=threading.RLock(),job_lock=threading.RLock(),job=None)
  outer=self
  class Store:
   def __init__(self,v):self.viewer=v;self.scan_id='scan'
   def load(self):return {},'rev'
   def preview(self,body):
    return {'revision':'rev','items':[{'id':i['id'],'name':outer.files[i['id']].name,'path':str(outer.files[i['id']]),'operation':i['operation'],'covered_by':None,'directory':False} for i in body['items']]}
  self.patch=patch('file_operations.PlanStore',Store);self.patch.start();self.manager=OperationManager(self.controller)
 def tearDown(self):
  deadline=time.monotonic()+3
  while self.manager.active and time.monotonic()<deadline:time.sleep(.01)
  self.patch.stop();self.assertTrue(self.root.resolve().is_relative_to(WORK.resolve()));self.temp.cleanup()
 def prepare(self,ids):return self.manager.prepare({'items':[{'id':n,'operation':'recycle'} for n in ids],'delete_modes':{str(n):'permanent' for n in ids}})
 def test_ticket_is_single_use_and_result_reports_actual_state(self):
  ticket=self.prepare([1]);self.manager.execute({'ticket':ticket['ticket'],'ack':ticket['ack']})
  deadline=time.monotonic()+3
  while self.manager.status(ticket['ticket'])['status']=='running' and time.monotonic()<deadline:time.sleep(.01)
  result=self.manager.status(ticket['ticket']);self.assertEqual(result['status'],'finished');self.assertEqual(result['items'][0]['status'],'completed');self.assertFalse(self.files[1].exists());self.assertTrue(self.files[2].exists())
  with self.assertRaises(ValueError):self.manager.execute({'ticket':ticket['ticket'],'ack':ticket['ack']})
 def test_changed_second_item_prevents_entire_batch_from_starting(self):
  ticket=self.prepare([1,2]);self.files[2].write_bytes(b'changed after review')
  with self.assertRaises(ValueError):self.manager.execute({'ticket':ticket['ticket'],'ack':ticket['ack']})
  self.assertTrue(self.files[1].exists());self.assertTrue(self.files[2].exists())
 def test_wrong_ack_is_not_an_authorization(self):
  ticket=self.prepare([1])
  with self.assertRaises(ValueError):self.manager.execute({'ticket':ticket['ticket'],'ack':'wrong'})
  self.assertTrue(self.files[1].exists())
if __name__=='__main__':unittest.main()
