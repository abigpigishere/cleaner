import unittest
from cleanup_advice import classify,non_overlapping

class AdviceTests(unittest.TestCase):
 def test_only_specific_cache_locations(self):
  self.assertEqual(classify(r'C:\Users\user\AppData\Local\Temp',True)['kind'],'cache')
  self.assertEqual(classify(r'C:\Users\user\.cache',True)['kind'],'cache')
  self.assertIsNone(classify(r'C:\Users\user\Documents\Cache',True))
 def test_system_trees_are_not_delete_candidates(self):
  for path in [r'C:\Windows\WinSxS',r'C:\Windows\System32',r'C:\Windows\Installer',r'C:\Program Files\WindowsApps',r'C:\Program Files\Common Files',r'C:\Program Files\NVIDIA Corporation']:
   with self.subTest(path=path):self.assertIsNone(classify(path,True))
 def test_apps_and_games_require_their_management_tools(self):
  self.assertEqual(classify(r'C:\Program Files\Steam\steamapps\common\Game',True)['kind'],'move')
  self.assertEqual(classify(r'C:\Users\user\AppData\Local\Programs\Python',True)['kind'],'app')
  self.assertIsNone(classify(r'C:\Users\user\Documents\Python',True))
 def test_downloads_and_personal_files_are_review_only(self):
  self.assertEqual(classify(r'C:\Users\user\Downloads',True)['kind'],'review')
  self.assertEqual(classify(r'C:\Users\user\Documents\archive.zip',False)['kind'],'review')
  self.assertIsNone(classify(r'C:\Users\user\Documents\account.db',False))
  self.assertIsNone(classify(r'C:\Users\user\AppData\Roaming\App\downloads',True))
 def test_overlapping_directory_sizes_are_not_added_twice(self):
  def item(path,size,priority=0):return {'path':path,'allocated':size,'priority':priority}
  rows=non_overlapping([item(r'C:\Cache',100),item(r'C:\Cache\child',60),item(r'C:\Cache-other',50)])
  self.assertEqual(sum(r['allocated'] for r in rows),150)
  rows=non_overlapping([item(r'C:\Apps',200,3),item(r'C:\Apps\Cache',50,0)])
  self.assertEqual([r['path'] for r in rows],[r'C:\Apps\Cache'])
if __name__=='__main__':unittest.main()
