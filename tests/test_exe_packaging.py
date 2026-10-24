"""Portable startup gate and frozen external-process regression tests."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import Mock, patch
from lol_words.system.external_process import external_environment, popen_external, run_external
from tools.verify_exe import APP_NAME, check_inventory, clean_environment, verify_folder, verify_zip
from tools.package_app import CODE

from tools.build_exe import source_identity

class ExePackagingTests(unittest.TestCase):
    @staticmethod
    def successful_startup(command, **kwargs):
        app = Path(command[0]).parent
        result = dict(ok=True, frozen=True, pages=5, resources_valid=True, bytes=12,
                      backend='fixture', root=str(app), application_name=APP_NAME,
                      window_title='泉水时间管理 · '+APP_NAME)
        Path(command[-1]).write_text(json.dumps(result), encoding='utf-8')
        return Mock(returncode=0, stderr=b'')

    @staticmethod
    def make_bundle(root, folder_name, executable):
        raw = b'fixture-not-executable'
        manifest = dict(files={executable:dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())})
        path = root/'fixture.zip'
        with zipfile.ZipFile(path, 'w') as bundle:
            bundle.writestr(folder_name+'/'+executable, raw)
            bundle.writestr(folder_name+'/BUILD-MANIFEST.json', json.dumps(manifest))
        return path

    def test_spec_uses_current_brand(self):
        spec = (Path(__file__).resolve().parents[1]/'build_config/windows.spec').read_text(encoding='utf-8')
        self.assertEqual(spec.count("name='BeforeRespawn'"), 2)
        self.assertNotIn("name='LOL-Words'", spec)

    def test_inventory_supports_current_and_legacy_executables(self):
        for name in (APP_NAME, 'LOL-Words'):
            with self.subTest(name=name), tempfile.TemporaryDirectory(dir=os.environ.get('PI_SCRATCH_DIR')) as temporary:
                root = Path(temporary)
                (root/(name+'.exe')).write_bytes(b'fixture')
                self.assertEqual(check_inventory(root), name+'.exe')

    def test_inventory_rejects_two_brand_executables(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('PI_SCRATCH_DIR')) as temporary:
            root = Path(temporary)
            for name in (APP_NAME, 'LOL-Words'):
                (root/(name+'.exe')).write_bytes(b'fixture')
            with self.assertRaisesRegex(ValueError, '只能包含一个'):
                check_inventory(root)

    def test_current_executable_requires_new_application_identity(self):
        for field, value in (('application_name', None), ('window_title', None),
                             ('application_name', 'LOL-Words'), ('window_title', '泉水时间管理 · LOL-Words')):
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory(dir=os.environ.get('PI_SCRATCH_DIR')) as temporary:
                root = Path(temporary)
                (root/(APP_NAME+'.exe')).write_bytes(b'fixture')
                def launch(command, **kwargs):
                    self.successful_startup(command, **kwargs)
                    report = Path(command[-1])
                    result = json.loads(report.read_text(encoding='utf-8'))
                    if value is None:
                        result.pop(field)
                    else:
                        result[field] = value
                    report.write_text(json.dumps(result), encoding='utf-8')
                    return Mock(returncode=0, stderr=b'')
                with patch('tools.verify_exe.subprocess.run', side_effect=launch):
                    with self.assertRaisesRegex(RuntimeError, '隔离启动失败'):
                        verify_folder(root)

    def test_legacy_startup_without_brand_fields_remains_supported(self):
        def launch(command, **kwargs):
            self.successful_startup(command, **kwargs)
            report = Path(command[-1])
            result = json.loads(report.read_text(encoding='utf-8'))
            result.pop('application_name'); result.pop('window_title')
            report.write_text(json.dumps(result), encoding='utf-8')
            return Mock(returncode=0, stderr=b'')
        with tempfile.TemporaryDirectory(dir=os.environ.get('PI_SCRATCH_DIR')) as temporary:
            root = Path(temporary)
            (root/'LOL-Words.exe').write_bytes(b'fixture')
            with patch('tools.verify_exe.subprocess.run', side_effect=launch):
                self.assertEqual(verify_folder(root)['executable'], 'LOL-Words.exe')

    def test_zip_accepts_current_and_legacy_names(self):
        for name in (APP_NAME, 'LOL-Words'):
            with self.subTest(name=name), tempfile.TemporaryDirectory(dir=os.environ.get('PI_SCRATCH_DIR')) as temporary:
                path = self.make_bundle(Path(temporary), name, name+'.exe')
                with patch('tools.verify_exe.subprocess.run', side_effect=self.successful_startup):
                    result = verify_zip(path)
                self.assertTrue(result['manifest_verified'])
                self.assertEqual(result['executable'], name+'.exe')

    def test_zip_rejects_mismatched_directory_and_executable(self):
        for folder, executable in ((APP_NAME, 'LOL-Words.exe'), ('LOL-Words', APP_NAME+'.exe')):
            with self.subTest(folder=folder), tempfile.TemporaryDirectory(dir=os.environ.get('PI_SCRATCH_DIR')) as temporary:
                path = self.make_bundle(Path(temporary), folder, executable)
                with patch('tools.verify_exe.verify_folder') as launch:
                    with self.assertRaisesRegex(ValueError, '目录与程序名称不一致'):
                        verify_zip(path)
                    launch.assert_not_called()

    def test_zip_rejects_mixed_brand_roots(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('PI_SCRATCH_DIR')) as temporary:
            path = Path(temporary)/'fixture.zip'
            with zipfile.ZipFile(path, 'w') as bundle:
                for name in (APP_NAME, 'LOL-Words'):
                    bundle.writestr(name+'/'+name+'.exe', b'fixture')
            with self.assertRaisesRegex(ValueError, '唯一'):
                verify_zip(path)

    def test_source_package_contains_rebuild_inputs(self):
        self.assertIn('requirements-build.txt', CODE)
        self.assertIn('build_config/windows.spec', CODE)


    def test_source_zip_build_does_not_require_git(self):
        with patch('tools.build_exe.subprocess.check_output', side_effect=FileNotFoundError('git')):
            self.assertEqual(source_identity(Path.cwd()), (None, None))
    def test_private_runtime_rejected_without_modification(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('PI_SCRATCH_DIR')) as temporary:
            root = Path(temporary)
            (root/'BeforeRespawn.exe').write_bytes(b'fixture')
            private = root/'config.json'
            private.write_text('private', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '混入'):
                check_inventory(root)
            self.assertEqual(private.read_text(encoding='utf-8'), 'private')

    def test_mixed_case_private_paths_rejected_on_windows(self):
        for relative in ('Config.JSON', 'Runtime/browsers/owner.json', 'Runtime-Python.TXT'):
            with self.subTest(path=relative), tempfile.TemporaryDirectory(dir=os.environ.get('PI_SCRATCH_DIR')) as temporary:
                root = Path(temporary)
                (root/'BeforeRespawn.exe').write_bytes(b'fixture')
                private = root/relative
                private.parent.mkdir(parents=True, exist_ok=True)
                private.write_text('private', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, '混入'):
                    check_inventory(root)
                self.assertEqual(private.read_text(encoding='utf-8'), 'private')

    def test_clean_environment_excludes_python_and_qt_overrides(self):
        with patch.dict(os.environ, {'PYTHONPATH':'source', 'PYTHONHOME':'python',
                                    'QT_PLUGIN_PATH':'other', 'PATH':'python;other'}):
            env = clean_environment()
            self.assertNotIn('PYTHONPATH', env)
            self.assertNotIn('PYTHONHOME', env)
            self.assertNotIn('QT_PLUGIN_PATH', env)
            self.assertEqual(Path(env['PATH'].split(os.pathsep)[0]), Path(os.environ['SystemRoot'])/'System32')

    def test_failed_startup_is_not_accepted(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('PI_SCRATCH_DIR')) as temporary:
            root = Path(temporary)
            (root/'BeforeRespawn.exe').write_bytes(b'fixture')
            with patch('tools.verify_exe.subprocess.run', return_value=Mock(returncode=1, stderr=b'bad DLL')):
                with self.assertRaisesRegex(RuntimeError, '隔离启动失败'):
                    verify_folder(root)
            self.assertEqual(list(root.iterdir()), [root/'BeforeRespawn.exe'])

    def test_success_requires_frozen_pages_resources_and_correct_root(self):
        def launch(command, **kwargs):
            app = Path(command[0]).parent
            result = dict(ok=True, frozen=True, pages=5, resources_valid=True, bytes=12,
                          backend='fixture', root=str(app), application_name=APP_NAME,
                          window_title='泉水时间管理 · '+APP_NAME)
            Path(command[-1]).write_text(json.dumps(result), encoding='utf-8')
            self.assertNotEqual(Path(kwargs['cwd']), app)
            return Mock(returncode=0, stderr=b'')
        with tempfile.TemporaryDirectory(dir=os.environ.get('PI_SCRATCH_DIR')) as temporary:
            root = Path(temporary)
            (root/'BeforeRespawn.exe').write_bytes(b'fixture')
            with patch('tools.verify_exe.subprocess.run', side_effect=launch):
                self.assertTrue(verify_folder(root)['ok'])


class ExternalProcessTests(unittest.TestCase):
    def test_environment_is_copied_and_only_bundle_paths_removed(self):
        root = Path(os.environ.get('PI_SCRATCH_DIR', '.')).resolve()/'bundle'
        env = dict(PATH=os.pathsep.join([str(root), str(root/'PySide6'), str(root)+'-other']),
                   QT_PLUGIN_PATH=str(root/'plugins'), HOME='preserved')
        with patch('sys._MEIPASS', str(root), create=True):
            result = external_environment(env)
        self.assertEqual(result['PATH'], str(root)+'-other')
        self.assertEqual(result['QT_PLUGIN_PATH'], '')
        self.assertEqual(result['HOME'], 'preserved')
        self.assertIn(str(root/'PySide6'), env['PATH'])

    def test_dll_directory_is_restored_on_spawn_failure(self):
        kernel = Mock()
        def previous(size, buffer):
            buffer.value = r'E:\bundle\_internal'
            return len(buffer.value)
        kernel.GetDllDirectoryW.side_effect = previous
        kernel.SetDllDirectoryW.return_value = 1
        with patch('sys.frozen', True, create=True), patch('lol_words.system.external_process.ctypes.WinDLL', return_value=kernel), patch('lol_words.system.external_process.subprocess.Popen', side_effect=OSError('fixture')):
            with self.assertRaises(OSError):
                popen_external(['fixture.exe'])
        self.assertEqual([c.args[0] for c in kernel.SetDllDirectoryW.call_args_list], [None, r'E:\bundle\_internal'])

    def test_wait_occurs_after_directory_restored_and_timeout_kills_child(self):
        process = Mock()
        process.__enter__ = Mock(return_value=process)
        process.__exit__ = Mock(return_value=False)
        process.communicate.side_effect = [subprocess.TimeoutExpired('fixture', 1), (b'', b'')]
        with patch('lol_words.system.external_process.popen_external', return_value=process):
            with self.assertRaises(subprocess.TimeoutExpired):
                run_external(['fixture'], capture_output=True, timeout=1)
        process.kill.assert_called_once()
        self.assertEqual(process.communicate.call_count, 2)

    def test_real_child_does_not_inherit_bundle_dll_directory(self):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
        buffer = ctypes.create_unicode_buffer(32768)
        kernel.GetDllDirectoryW(len(buffer), buffer)
        previous = buffer.value or None
        marker = os.environ.get('PI_SCRATCH_DIR', str(Path.cwd()))
        code = "import ctypes; b=ctypes.create_unicode_buffer(32768); ctypes.windll.kernel32.GetDllDirectoryW(len(b),b); print(b.value)"
        try:
            self.assertTrue(kernel.SetDllDirectoryW(marker))
            with patch('sys.frozen', True, create=True):
                child = run_external([sys.executable, '-I', '-c', code], capture_output=True, timeout=15)
            self.assertEqual(child.returncode, 0, child.stderr)
            self.assertEqual(child.stdout.strip(), b'')
            kernel.GetDllDirectoryW(len(buffer), buffer)
            self.assertEqual(buffer.value, marker)
        finally:
            kernel.SetDllDirectoryW(previous)
