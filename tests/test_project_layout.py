"""验证目录调整后的启动入口、资源定位和辅助进程协议，不操作真实游戏。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from lol_words.system.paths import ROOT
from lol_words.targets.catalog import LearningCatalog


class PackageEntryTests(unittest.TestCase):
    def run_python(self,*args,cwd=ROOT,input=None):
        env=dict(os.environ,PYTHONUTF8='1')
        env.pop('PYTHONPATH',None)
        result=subprocess.run([sys.executable,'-B',*args],cwd=cwd,env=env,input=input,
            capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=35)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        return result.stdout

    def test_shared_root_preserves_builtin_catalog_and_window_resources(self):
        self.assertEqual(ROOT,Path(__file__).resolve().parents[1])
        self.assertTrue((ROOT/'config.example.json').is_file())
        self.assertTrue((ROOT/'assets/chevron-down.svg').is_file())
        self.assertTrue(LearningCatalog().search('Anki'))

    def test_module_entry_exposes_cli_without_starting_monitoring(self):
        output=self.run_python('-m','lol_words','--help')
        self.assertIn('--check',output);self.assertIn('--admin',output)

    def test_double_click_launcher_works_from_an_unrelated_working_directory(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests',prefix='启动 空格') as folder:
            output=self.run_python(str(ROOT/'launchers/start.pyw'),'--check',cwd=folder)
        self.assertIn('未启动监测、目标或下载',output)

    def test_accessibility_worker_still_returns_json_for_invalid_window(self):
        output=self.run_python('-m','lol_words.lobby.client_accessibility',
            input=json.dumps(dict(hwnd=0,pid=0,created=0,path=''))+'\n')
        self.assertEqual(json.loads(output)['items'],[])
