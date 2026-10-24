"""冻结程序的路径和管理员重启兼容；不执行真实系统授权。"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from lol_words.system.paths import application_root
from lol_words.system.privileges import restart_elevated


class FrozenRuntimeTests(unittest.TestCase):
    def test_frozen_data_root_follows_executable_instead_of_extraction_directory(self):
        with patch('sys.frozen',True,create=True),patch('sys.executable',r'E:\便携 程序\BeforeRespawn.exe'):
            self.assertEqual(application_root(),Path(r'E:\便携 程序').resolve())

    def test_frozen_admin_restart_does_not_require_python_or_source_launcher(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent,prefix='程序 空格') as folder:
            root=Path(folder);executable=root/'BeforeRespawn.exe';executable.write_bytes(b'fixture-not-executable')
            shell=Mock();shell.ShellExecuteW.return_value=42
            with patch('sys.frozen',True,create=True),patch('sys.executable',str(executable)),patch('lol_words.system.privileges.C.WinDLL',return_value=shell):
                restart_elevated(root)
            args=shell.ShellExecuteW.call_args.args
            self.assertEqual(args[2],str(executable.resolve()));self.assertEqual(args[3],'--paused')
            self.assertFalse((root/'launchers/start.pyw').exists())
