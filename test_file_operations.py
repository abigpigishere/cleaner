import json,os,threading,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from file_operations import guarded_path,manifest,remove_exact,move_verified,verified_copy
from installed_apps import open_uninstall_settings
WORK=Path(os.environ.get('CLEANER_TEST_ROOT',str(Path.home()/'Documents'/'CleanerTests')))

class FileOperationTests(unittest.TestCase):
 def setUp(self):
  WORK.mkdir(parents=True,exist_ok=True)
  self.temp=tempfile.TemporaryDirectory(prefix='cleaner-operation-unit-',dir=WORK);self.root=Path(self.temp.name);self.source=self.root/'source.bin';self.source.write_bytes(b'cleaner-test'*1000)
 def tearDown(self):
  self.assertTrue(self.root.resolve().is_relative_to(WORK.resolve()));self.temp.cleanup()
 def test_permanent_delete_only_unchanged_target(self):
  expected=manifest(self.source);self.source.write_bytes(b'changed')
  with self.assertRaises(ValueError):remove_exact(self.source,expected)
  self.assertTrue(self.source.exists());remove_exact(self.source,manifest(self.source));self.assertFalse(self.source.exists())
 def test_open_writer_prevents_permanent_delete(self):
  expected=manifest(self.source)
  with self.source.open('r+b') as writer:
   with self.assertRaises(OSError):remove_exact(self.source,expected)
   self.assertTrue(self.source.exists())
 def test_move_refuses_overwrite(self):
  dest=self.root/'exists.bin';dest.write_bytes(b'original')
  with self.assertRaises(ValueError):move_verified(self.source,dest,manifest(self.source))
  self.assertEqual(dest.read_bytes(),b'original');self.assertTrue(self.source.exists())
 def test_same_volume_move_keeps_bytes(self):
  data=self.source.read_bytes();dest=self.root/'moved.bin';result=move_verified(self.source,dest,manifest(self.source))
  self.assertTrue(result['verified']);self.assertFalse(self.source.exists());self.assertEqual(dest.read_bytes(),data)
 def test_directory_named_stream_is_not_silently_lost(self):
  folder=self.root/'with-stream';folder.mkdir();Path(str(folder)+':metadata').write_bytes(b'directory metadata')
  with self.assertRaises(ValueError):manifest(folder)
 def test_named_data_stream_is_preserved(self):
  stream=Path(str(self.source)+':cleaner_test');stream.write_bytes(b'named stream content')
  dest=self.root/'ads-copy.bin';verified_copy(self.source,dest)
  self.assertEqual(Path(str(dest)+':cleaner_test').read_bytes(),b'named stream content')
 def test_copy_function_accepts_shutil_string_arguments(self):
  dest=self.root/'copy.bin';verified_copy(str(self.source),str(dest));self.assertEqual(dest.read_bytes(),self.source.read_bytes())
 def test_copy_detects_corruption_without_removing_original(self):
  with patch('file_operations.digest',side_effect=['source-hash','corrupt-copy-hash']):
   with self.assertRaises(ValueError):verified_copy(self.source,self.root/'copy.bin')
  self.assertTrue(self.source.exists())
 def test_project_and_links_are_rejected(self):
  folder=self.root/'project';folder.mkdir();(folder/'.git').write_text('gitdir: somewhere')
  with self.assertRaises(ValueError):guarded_path(folder)
  with patch('file_operations.no_links',side_effect=ValueError('link')):
   with self.assertRaises(ValueError):manifest(self.source)
 def test_uninstall_is_a_handoff_not_a_success_claim(self):
  with patch('installed_apps.os.startfile') as launch:
   result=open_uninstall_settings();launch.assert_called_once_with('ms-settings:appsfeatures')
  self.assertEqual(result['status'],'awaiting_user')
if __name__=='__main__':unittest.main()
