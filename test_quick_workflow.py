import contextlib,io,json,tempfile,unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
import cleaner_cli

class QuickWorkflowTests(unittest.TestCase):
 def test_overview_batches_summary_and_supplies_a_three_minute_deadline(self):
  row={'id':7,'name':'archive.zip','path':r'C:\Users\u\Downloads\archive.zip','allocated':1024,'directory':False,'kind':'review','operation':'review','allowed_operations':['review','move']}
  def api(endpoint,body=None,query=None):
   if endpoint=='api/meta':return {'disk':{'free':14*2**30},'root':{'id':5},'report':{'total_seconds':2.6,'sizes_unresolved':0}}
   if endpoint=='api/advice':return {'rows':[row]}
   if endpoint=='api/plan':return {'scan_id':'scan','plan_id':'plan','items':[{'id':7,'reference':'0x1000000000007'}]}
   if endpoint=='api/list':return {'rows':[row]}
   raise AssertionError(endpoint)
  with patch('cleaner_cli.start') as start,patch('cleaner_cli.call',side_effect=api) as call,patch('cleaner_cli.address',return_value='http://127.0.0.1/test/'),patch('sys.argv',['cleaner','overview','--goal-free-gib','30']),contextlib.redirect_stdout(io.StringIO()) as output:
   cleaner_cli.main()
  result=json.loads(output.getvalue());self.assertEqual(result['needed_bytes'],16*2**30);self.assertEqual(call.call_count,5);start.assert_called_once()
  self.assertEqual(result['candidates'][0]['reference'],'0x1000000000007')
  self.assertEqual((datetime.fromisoformat(result['analysis_deadline_utc'])-datetime.fromisoformat(result['analysis_started_utc'])).total_seconds(),180)
 def test_scan_opens_frontend_by_default_and_can_explicitly_opt_out(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'results').mkdir();(root/'results/latest-view.json').write_text('{}')
   for extra,expected in [([],True),(['--no-open'],False)]:
    with self.subTest(extra=extra),patch('cleaner_cli.ROOT',root),patch('cleaner_cli.start') as start,patch('cleaner_cli.call',return_value={'status':'waiting'}),patch('sys.argv',['cleaner','scan','--target','C:\\','--no-wait',*extra]),contextlib.redirect_stdout(io.StringIO()):
     cleaner_cli.main();start.assert_called_once_with(expected)
if __name__=='__main__':unittest.main()
