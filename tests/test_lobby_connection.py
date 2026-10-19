"""权限差异、重新发现与管理员重启门控；所有提权和网络调用均为替身。"""
from lol_words.system import runtime_bootstrap
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from lol_words.lobby.lcu_api import ClientDiscovery,Connection,LCUError
from lol_words.lobby.lobby_flow import LobbyStatus
import test_lobby_ui as lobby_fixture
from lol_words.system.privileges import restart_elevated


class DiscoveryTests(unittest.TestCase):
    def discover(self,elevated=False,client_elevated=True,command=None):
        native=Mock();native.processes.return_value=[(123,'leagueclientux.exe')];native.k.OpenProcess.return_value=0
        result=Mock(stdout=json.dumps([dict(ProcessId=123,CommandLine=command)]).encode())
        with patch('winreg.OpenKey',side_effect=OSError),patch('lol_words.lobby.lcu_api.subprocess.run',return_value=result),\
             patch('lol_words.lobby.lcu_api.is_elevated',return_value=elevated),patch('lol_words.lobby.lcu_api.process_elevated',return_value=client_elevated):
            discovery=ClientDiscovery(native);connection=discovery.find(10)
        return discovery,connection

    def test_empty_lockfile_and_elevated_client_get_actionable_reason(self):
        discovery,connection=self.discover()
        self.assertIsNone(connection);self.assertTrue(discovery.needs_admin)
        self.assertIn('客户端以管理员权限运行',discovery.reason)

    def test_same_permissions_do_not_claim_elevation_will_fix_unknown_error(self):
        for tool_admin,client_admin in [(False,False),(False,None),(True,True)]:
            with self.subTest(tool_admin=tool_admin,client_admin=client_admin):
                discovery,connection=self.discover(tool_admin,client_admin)
                self.assertIsNone(connection);self.assertFalse(discovery.needs_admin)
                self.assertNotIn('本工具权限较低',discovery.reason)

    def test_readable_parameters_work_without_requiring_elevation(self):
        discovery,connection=self.discover(command='--app-port=12345 --remoting-auth-token=fixture-secret')
        self.assertIsInstance(connection,Connection);self.assertFalse(discovery.needs_admin)
        self.assertNotIn('fixture-secret',discovery.reason+repr(connection))

    def test_client_exit_clears_previous_permission_warning(self):
        native=Mock();native.processes.return_value=[]
        discovery=ClientDiscovery(native);discovery.needs_admin=True;discovery.cached=Connection(123,12345,'fixture')
        self.assertIsNone(discovery.find(5));self.assertFalse(discovery.needs_admin)
        self.assertEqual(discovery.reason,'等待客户端启动')

    def test_elevation_command_uses_original_directory_and_starts_paused(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent,prefix='提权 空格') as folder:
            root=Path(folder);(root/'launchers').mkdir();(root/'launchers/start.pyw').write_text('# fixture',encoding='utf-8')
            shell=Mock();shell.ShellExecuteW.return_value=42
            with patch('lol_words.system.privileges.C.WinDLL',return_value=shell):restart_elevated(root)
            args=shell.ShellExecuteW.call_args.args
            self.assertEqual(args[1],'runas');self.assertEqual(args[4],str(root.resolve()))
            self.assertIn('--paused --lobby',args[3]);self.assertIn('start.pyw"',args[3])

    def test_windows_consent_cancellation_is_reported_without_retry(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root=Path(folder);(root/'launchers').mkdir();(root/'launchers/start.pyw').write_text('# fixture',encoding='utf-8')
            shell=Mock();shell.ShellExecuteW.return_value=0
            with patch('lol_words.system.privileges.C.WinDLL',return_value=shell),patch('lol_words.system.privileges.C.get_last_error',return_value=1223):
                with self.assertRaisesRegex(ValueError,'已取消管理员重启'):restart_elevated(root)
            self.assertEqual(shell.ShellExecuteW.call_count,1)


# 只借用窗口隔离夹具，不重复运行原有测试方法。
class ConnectionUiTests(unittest.TestCase):
    setUp=lobby_fixture.LobbyUiTests.setUp
    tearDown=lobby_fixture.LobbyUiTests.tearDown

    def test_readonly_refresh_discards_old_discovery_cache_before_connecting(self):
        runtime=self.service.lobby;runtime.discovery.next_query=999;runtime.discovery.cached=object()
        def fail():
            self.assertIsNone(runtime.discovery.cached);self.assertEqual(runtime.discovery.next_query,0)
            raise LCUError('fixture permission warning',needs_admin=True)
        with patch.object(runtime,'connect',side_effect=fail):
            with self.assertRaises(LCUError):runtime.read_only()
        self.assertTrue(runtime.last_value.needs_admin)
        self.assertEqual(runtime.last_value.connection,'客户端权限不匹配')

    def test_admin_button_only_appears_for_permission_diagnostic(self):
        self.assertTrue(self.window.admin_restart_button.isHidden())
        self.window.apply_lobby_state(LobbyStatus(needs_admin=True))
        self.assertFalse(self.window.admin_restart_button.isHidden())
        self.window.apply_lobby_state(LobbyStatus(connection='客户端已连接'))
        self.assertTrue(self.window.admin_restart_button.isHidden())

    def test_unsaved_settings_prevent_restart_and_are_preserved(self):
        self.window.auto_accept.setChecked(True);self.window.request_admin_restart()
        self.assertFalse(self.window.admin_restart_in_progress)
        self.assertTrue(self.window.auto_accept.isChecked());self.assertIn('尚未保存',self.window.lobby_notice.text())

    def test_restart_waits_for_cleanup_and_cannot_resume_monitoring_while_pending(self):
        with patch.object(self.service,'settled',return_value=False),patch('lol_words.ui.desktop_ui.restart_elevated') as launch:
            self.window.request_admin_restart();self.window.tick()
            launch.assert_not_called();self.assertFalse(self.window.pause_button.isEnabled())
        self.assertTrue(self.service.stop_event.is_set())

    def test_uac_cancellation_retains_original_window_paused(self):
        self.window.request_admin_restart()
        self.window.handle_job('elevation','error','已取消管理员重启')
        self.assertFalse(self.window.closing);self.assertFalse(self.window.admin_restart_in_progress)
        self.assertTrue(self.service.stop_event.is_set());self.assertTrue(self.window.admin_restart_button.isEnabled())
