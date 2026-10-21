"""独立管理员启动入口；所有授权调用使用替身。"""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock,patch
from lol_words.system.privileges import restart_elevated


class PrivilegeTests(unittest.TestCase):
    def test_elevation_command_uses_original_directory_and_starts_paused(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent,prefix='提权 空格') as folder:
            root=Path(folder);(root/'launchers').mkdir();(root/'launchers/start.pyw').write_text('# fixture',encoding='utf-8')
            shell=Mock();shell.ShellExecuteW.return_value=42
            with patch('lol_words.system.privileges.C.WinDLL',return_value=shell):restart_elevated(root)
            args=shell.ShellExecuteW.call_args.args
            self.assertEqual(args[1],'runas');self.assertEqual(args[4],str(root.resolve()))
            self.assertIn('--paused',args[3]);self.assertNotIn('--lobby',args[3]);self.assertIn('start.pyw"',args[3])

    def test_windows_consent_cancellation_is_reported_without_retry(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root=Path(folder);(root/'launchers').mkdir();(root/'launchers/start.pyw').write_text('# fixture',encoding='utf-8')
            shell=Mock();shell.ShellExecuteW.return_value=0
            with patch('lol_words.system.privileges.C.WinDLL',return_value=shell),patch('lol_words.system.privileges.C.get_last_error',return_value=1223):
                with self.assertRaisesRegex(ValueError,'已取消管理员重启'):restart_elevated(root)
            self.assertEqual(shell.ShellExecuteW.call_count,1)
