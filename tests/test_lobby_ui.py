"""大厅配置、异步状态与全局暂停连接；外部客户端完全由替身隔离。"""
import runtime_bootstrap
import copy
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock,patch
from PySide6.QtWidgets import QApplication
from application_service import ApplicationService
from desktop_ui import MainWindow
from lobby_flow import LobbyStatus
from lobby_runtime import LobbyRuntime
from lcu_api import LCUError
from test_desktop_service import FakeNative

QT=QApplication.instance() or QApplication([])


class LobbyUiTests(unittest.TestCase):
    def setUp(self):
        self.area=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.root=Path(self.area.name)
        self.service=ApplicationService(self.root,FakeNative())
        self.window=MainWindow(self.service,False);self.window.timer.stop()
    def tearDown(self):
        self.service.close();self.window.closing=True
        deadline=time.monotonic()+3
        while not self.service.settled() and time.monotonic()<deadline:QT.processEvents();time.sleep(.01)
        self.window.close();self.window.deleteLater();QT.processEvents();self.area.cleanup()

    def test_lobby_settings_are_independent_of_death_settings_and_default_off(self):
        self.assertEqual(self.window.navigation.count(),6)
        self.assertFalse(self.window.auto_return.isChecked());self.assertFalse(self.window.auto_promote.isChecked());self.assertFalse(self.window.auto_accept.isChecked())
        before=self.service.config['death_switch_delay']
        self.window.auto_return.setChecked(True);self.window.auto_promote.setChecked(True);self.window.auto_accept.setChecked(True)
        self.window.lobby_stuck.setValue(45);self.window.save_lobby_settings()
        self.assertTrue(self.service.config['lobby_auto_accept']);self.assertEqual(self.service.config['lobby_stuck_seconds'],45)
        self.assertEqual(self.service.config['death_switch_delay'],before)

    def test_real_lobby_event_updates_ui_without_changing_death_phase(self):
        self.window.tick()
        before=self.window.flow_label.text()
        self.service.events.put(('lobby_state',LobbyStatus('客户端已连接','等待接受对局','等待接受延迟','尚未接受。')))
        self.window.tick()
        self.assertEqual(self.window.lobby_action.text(),'等待接受延迟');self.assertEqual(self.window.flow_label.text(),before)

    def test_preferred_teammate_is_not_replaced_when_absent(self):
        self.service.update_config({'lobby_preferred_member':'puuid:preferred0001'})
        self.window.refresh_config()
        self.assertEqual(self.window.preferred_member.currentData(),'puuid:preferred0001')
        self.assertIn('不在房间',self.window.preferred_member.currentText())
        self.window.apply_lobby_state(LobbyStatus(members=(('puuid:other0001','其他队友',False,True),)))
        self.assertEqual(self.window.preferred_member.currentData(),'puuid:preferred0001')

    def test_member_list_ignores_leader_and_ineligible_members(self):
        value=LobbyStatus(members=(('self','本人',True,False),('bot','机器人',False,False),('buddy','接任队友',False,True)))
        self.window.apply_lobby_state(value)
        self.assertEqual(self.window.preferred_member.count(),2)
        self.assertEqual(self.window.preferred_member.itemData(1),'buddy')

    def test_unsaved_lobby_options_survive_unrelated_mode_refresh(self):
        self.window.auto_accept.setChecked(True)
        self.window.set_mode('entertainment')
        self.assertTrue(self.window.auto_accept.isChecked());self.assertFalse(self.service.config['lobby_auto_accept'])

    def test_missing_client_and_calibration_errors_are_actionable(self):
        self.window.handle_job('lobby_read','error','等待客户端启动')
        self.assertIn('客户端',self.window.lobby_notice.text())
        self.window.handle_job('avatar_frame','error','请先打开客户端')
        self.assertTrue(self.window.avatar_button.isEnabled());self.assertIn('打开客户端',self.window.lobby_notice.text())

    def test_closed_features_do_not_connect_to_client(self):
        stop=threading.Event()
        with patch.object(self.service.lobby,'connect') as connect:
            worker=threading.Thread(target=self.service.lobby.run,args=(stop,));worker.start();time.sleep(.03);stop.set();worker.join(1)
            connect.assert_not_called();self.assertFalse(worker.is_alive())

    def test_global_stop_tracks_both_monitor_threads(self):
        self.service.worker=Mock(is_alive=lambda:False);self.service.lobby_worker=Mock(is_alive=lambda:True)
        self.assertTrue(self.service.running);self.service.stop();self.assertTrue(self.service.stop_event.is_set())
        self.service.lobby_worker=None;self.service.worker=None

    def test_dead_monitor_requests_lobby_thread_stop(self):
        self.service.stop_event.clear();self.service.on_monitor_exit()
        self.assertTrue(self.service.stop_event.is_set())

    def test_primary_lobby_save_is_visible_and_advanced_settings_are_collapsed(self):
        self.window.show();self.window.navigation.setCurrentRow(5);QT.processEvents()
        self.assertFalse(self.window.lobby_save_button.visibleRegion().isEmpty())
        self.assertTrue(self.window.lobby_advanced.isHidden())
