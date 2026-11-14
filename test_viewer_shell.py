import ctypes
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch
from viewer_shell import open_explorer


class ShellTests(unittest.TestCase):
    def viewer(self,directory=True):
        return SimpleNamespace(permits=lambda n:True,report={'drive':'C:'},
            row=lambda n:{'path':'C:\\test folder' if directory else 'C:\\test folder\\sample.txt','directory':directory},
            fields=lambda n:((7<<48)|42,))

    def reader(self,valid=True):
        reader=MagicMock()
        def information(handle,pointer):
            pointer._obj.id_low=42 if valid else 43
            pointer._obj.id_high=7<<16
            return True
        reader.api.GetFileInformationByHandle.side_effect=information
        return reader

    def test_folder_and_file_use_explorer_without_shell_execution(self):
        for directory in (True,False):
            with self.subTest(directory=directory),patch('viewer_shell.Reader',return_value=self.reader()),patch('viewer_shell.subprocess.Popen') as launch:
                result=open_explorer(self.viewer(directory),42)
                args=launch.call_args.args[0]
                self.assertTrue(args[0].lower().endswith('explorer.exe'))
                self.assertEqual(result['mode'],'folder' if directory else 'select_file')
                self.assertEqual('/select,' in args,not directory)
                self.assertNotIn('shell',launch.call_args.kwargs)

    def test_stale_file_identity_never_launches_explorer(self):
        with patch('viewer_shell.Reader',return_value=self.reader(False)),patch('viewer_shell.subprocess.Popen') as launch:
            with self.assertRaises(ValueError):
                open_explorer(self.viewer(),42)
            launch.assert_not_called()


if __name__=='__main__':
    unittest.main()
