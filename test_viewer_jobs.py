import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from viewer_jobs import Controller,ScanSession


class JobTests(unittest.TestCase):
    def controller(self,folder):
        viewer=SimpleNamespace(directory=folder)
        c=Controller(viewer,None)
        c.job={'status':'running','phase':'permission','target':'C:\\','folder':str(folder),
               'started':time.perf_counter(),'percent':None}
        return c,viewer

    def test_admin_session_reuses_connection(self):
        from unittest.mock import MagicMock
        conn=MagicMock();conn.recv_bytes.return_value=b'{"exit_code":0}'
        listener=MagicMock();listener.__enter__.return_value=listener;listener.accept.return_value=conn
        session=ScanSession()
        with patch('multiprocessing.connection.Listener',return_value=listener),patch('viewer_jobs.launch_elevated') as launch:
            self.assertEqual(session.run(Path('a'*32)),0)
            self.assertEqual(session.run(Path('b'*32)),0)
            self.assertEqual(launch.call_count,1)
            self.assertEqual(conn.send_bytes.call_count,2)
            session.close()
            conn.close.assert_called_once()

    def test_partial_progress_write_does_not_lose_last_real_count(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent/'results') as tmp:
            folder=Path(tmp);(folder/'scan').mkdir();c,_=self.controller(folder)
            (folder/'state.json').write_text(json.dumps({'status':'running','phase':'bootstrapping'}))
            progress=folder/'scan/native-progress.json'
            progress.write_text(json.dumps({'phase':'scanning','percent':25,'processed_slots':25,'total_slots':100}))
            self.assertEqual(c.status()['processed_slots'],25)
            progress.write_text('{')
            self.assertEqual(c.status()['percent'],25)
            progress.write_text(json.dumps({'phase':'done','percent':100,'processed_slots':100,'total_slots':100}))
            result=c.status()
            self.assertEqual(result['phase'],'preparing')
            self.assertNotEqual(result['status'],'done')

    def test_cancelled_admin_prompt_keeps_old_result(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent/'results') as tmp:
            folder=Path(tmp);c,old=self.controller(folder)
            with patch.object(c.session,'run',side_effect=PermissionError('cancelled')):
                c._work(folder)
            self.assertEqual(c.status()['status'],'cancelled')
            self.assertIs(c.viewer,old)


if __name__=='__main__':
    unittest.main()
