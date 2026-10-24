"""Qt 与真实服务连接测试；外部软件、对局与下载由替身隔离。"""
from lol_words.system import runtime_bootstrap
import copy
import json
from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from PySide6.QtCore import Qt, QRect
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton
from lol_words.application.application_service import ApplicationService
from lol_words.ui.desktop_ui import MainWindow, DownloadPrompt, TargetEditor
from lol_words.modes.mode_config import migrate_config, selected_spec
from lol_words.ui.ui_state import RuntimeState, flow_status
from lol_words.targets.adapters import DesktopAdapter, AnkiAdapter
from lol_words.application.monitoring import run_monitor
from lol_words.game.core import Snapshot


QT = QApplication.instance() or QApplication([])


class FakeNative:
    def __init__(self):
        self.rows = []
        self.restores = []
    def stop_pressed(self): return False
    def window_inventory(self): return copy.deepcopy(self.rows)
    def processes(self): return []
    def league_pid(self): return None
    def anki_paths(self): return []
    def restore(self, hwnd, activate=True): self.restores.append(hwnd); return True
    def minimize(self, hwnd): pass


class ServiceCase(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.area.name)
        self.native = FakeNative()
        self.service = ApplicationService(self.root, self.native)
    def tearDown(self):
        self.service.close()
        deadline = time.monotonic()+3
        while not self.service.settled() and time.monotonic()<deadline:
            QT.processEvents(); time.sleep(.01)
        self.assertTrue(self.service.settled())
        self.area.cleanup()
    def job(self, key):
        deadline = time.monotonic()+2
        while time.monotonic()<deadline:
            try: kind, value = self.service.events.get(timeout=.05)
            except queue.Empty: continue
            if kind=='job' and value[0]==key and value[1]!='busy': return value
        self.fail('任务未完成：'+key)

    def test_migrates_legacy_config_and_preserves_unknown_fields(self):
        self.service.close()
        old = dict(anki_path='C:/Study/Anki.exe', death_switch_delay=5.5, return_before_seconds=2.25,
                   persistent_anki_topmost=False, custom_user_setting={'keep': 1})
        (self.root/'config.json').write_text(json.dumps(old), encoding='utf-8')
        self.service = ApplicationService(self.root, self.native)
        self.assertEqual(self.service.config['targets'][0]['command'], ['C:/Study/Anki.exe'])
        self.service.update_config({'game_display_mode':'auto'})
        saved = json.loads((self.root/'config.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['custom_user_setting'], {'keep':1})
        self.assertEqual(saved['death_switch_delay'], 5.5)
        self.assertFalse(saved['persistent_target_topmost'])

    def test_invalid_file_is_never_overwritten_or_started(self):
        self.service.close(); original=b'{bad config'
        (self.root/'config.json').write_bytes(original)
        self.service=ApplicationService(self.root,self.native)
        self.assertTrue(self.service.config_error); self.assertFalse(self.service.start())
        with self.assertRaises(ValueError): self.service.update_config({'death_switch_delay':5})
        self.assertEqual((self.root/'config.json').read_bytes(), original)

    def test_invalid_time_never_mutates_memory_or_disk(self):
        for value in [float('nan'), float('inf'), -1, 121, '5', True]:
            with self.subTest(value=value), self.assertRaises(ValueError): self.service.update_config({'death_switch_delay':value})
        self.assertEqual(self.service.config['death_switch_delay'],3)
        self.assertFalse((self.root/'config.json').exists())

    def test_write_failure_does_not_publish_new_configuration(self):
        before=copy.deepcopy(self.service.config)
        with patch.object(Path,'replace',side_effect=OSError('write denied')):
            with self.assertRaises(OSError): self.service.update_config({'death_switch_delay':5})
        self.assertEqual(self.service.config,before)

    def test_modes_and_targets_have_separate_identity_and_revision(self):
        self.service.update_config({'mode':'entertainment','entertainment_site':'douyin'})
        self.assertEqual(selected_spec(self.service.config)['name'],'抖音')
        first=self.service.epoch
        target=dict(id='custom',name='自选学习程序',adapter='desktop',command=['C:/Study/Words.exe'],process_path='C:/Study/Words.exe',pin=False)
        self.service.update_config(target=target)
        self.assertEqual(selected_spec(self.service.config)['id'],'custom')
        self.assertEqual(self.service.config['mode'],'study'); self.assertGreater(self.service.epoch,first)
        self.assertTrue(any(t['id']=='anki' for t in self.service.config['targets']))

    def test_busy_job_is_rejected_without_duplicate_execution(self):
        entered, release=threading.Event(),threading.Event()
        self.assertTrue(self.service.submit('scan',lambda:(entered.set(),release.wait(1))))
        self.assertTrue(entered.wait(1)); self.assertFalse(self.service.submit('scan',lambda:None))
        release.set(); self.assertEqual(self.job('scan')[1],'ok')

    def test_scan_empty_and_failure_are_different_results(self):
        with patch('lol_words.application.application_service.scan_learning_apps',return_value=[]) as scan:
            self.service.scan(); self.assertEqual(self.job('scan'),('scan','ok',[])); self.assertTrue(scan.call_args.kwargs['strict'])
        with patch('lol_words.application.application_service.scan_learning_apps',side_effect=OSError()):
            self.service.scan(); result=self.job('scan'); self.assertEqual(result[1],'error'); self.assertIn('失败',result[2])

    def test_catalog_search_is_offline_and_candidates_are_not_saved(self):
        with patch('lol_words.application.application_service.online_candidates') as remote:
            self.service.search('Anki'); result=self.job('search'); self.assertEqual(result[2]['kind'],'catalog'); remote.assert_not_called()
        candidate=dict(title='未收录学习软件',url='https://example.org/',domain='example.org',verification='unverified')
        with patch('lol_words.application.application_service.online_candidates',return_value=[candidate]):
            self.service.search('未收录学习软件'); result=self.job('search'); self.assertEqual(result[2]['kind'],'candidates')
        self.assertFalse((self.root/'runtime/catalog-overrides.json').exists())

    def test_unknown_name_network_error_and_no_result_are_distinct(self):
        with patch('lol_words.application.application_service.online_candidates',return_value=[]):
            self.service.search('未收录名称'); self.assertEqual(self.job('search')[2]['items'],[])
        with patch('lol_words.application.application_service.online_candidates',side_effect=OSError('connection')):
            self.service.search('未收录名称'); result=self.job('search'); self.assertEqual(result[1],'error'); self.assertIn('网络',result[2])

    def test_user_confirmed_website_is_persisted_and_selected(self):
        self.service.choose_web(dict(url='https://example.org/'),'测试学习品牌')
        self.assertEqual(self.service.config['mode'],'study')
        self.assertEqual(selected_spec(self.service.config)['url'],'https://example.org/')
        self.assertEqual(self.service.catalog.resolve('测试学习品牌')['verification'],'user_confirmed')

    def test_stale_manual_open_never_activates_previous_target(self):
        spec=selected_spec(self.service.config)
        self.service.update_config({'mode':'entertainment'})
        with patch.object(self.service.registry,'get') as manager:
            result=self.service.manual_open(spec,0)
            self.assertIn('取消',result); manager.assert_not_called(); self.assertEqual(self.native.restores,[])

    def test_manual_open_reports_actual_restore_failure(self):
        manager=Mock(state='ready',freshly_started=7)
        manager.poll.return_value=7
        self.native.restore=Mock(return_value=False)
        with patch.object(self.service.registry,'get',return_value=manager):
            self.assertIn('未获得前台',self.service.manual_open(selected_spec(self.service.config),self.service.epoch))
        self.assertIsNone(manager.freshly_started)

    def test_manual_open_waits_for_a_running_process_that_is_still_creating_its_window(self):
        manager=Mock(state='waiting',freshly_started=7,pending_since=time.monotonic(),last_failure='')
        manager.poll.side_effect=[None,7]
        manager.adapter.reason='窗口仍在初始化'
        self.native.restore=Mock(return_value=True)
        with patch.object(self.service.registry,'get',return_value=manager):
            self.assertIn('已显示',self.service.manual_open(selected_spec(self.service.config),self.service.epoch))
        self.assertEqual(manager.poll.call_count,2)

    def test_malformed_configuration_is_reported_without_overwriting_user_file(self):
        for value in [None,[],{'targets':[None]},{'targets':{}},{'targets':[{'id':'anki','name':17,'adapter':'anki'}]}]:
            with self.subTest(value=value):
                text=json.dumps(value);path=self.root/'config.json';path.write_text(text,encoding='utf-8')
                candidate=None
                try:
                    candidate=ApplicationService(self.root,FakeNative())
                    self.assertTrue(candidate.config_error)
                    self.assertEqual(path.read_text(encoding='utf-8'),text)
                finally:
                    if candidate:candidate.close()

    def test_manual_closed_target_inspection_does_not_restart(self):
        adapter=Mock(reason='用户已关闭'); adapter.spec=selected_spec(self.service.config)
        adapter.discover.return_value='C:/Study/Anki.exe'
        manager=Mock(adapter=adapter,state='closed',last_failure='')
        with patch.object(self.service.registry,'get',return_value=manager):
            self.service.inspect_target(); result=self.job('inspect'); self.assertIn('已关闭',result[2]['state'])
            manager.ensure.assert_not_called(); adapter.start.assert_not_called()

    def test_discovered_anki_path_is_saved_without_reverting_concurrent_mode_change(self):
        path=str(self.root/'Anki.exe');Path(path).write_bytes(b'fixture-not-executable')
        adapter=Mock(reason='未运行');adapter.spec=copy.deepcopy(selected_spec(self.service.config))
        def discover():
            self.service.update_config({'mode':'entertainment'})
            adapter.spec['command']=[path]
            return path
        adapter.discover.side_effect=discover;adapter.configuration_updates.return_value={'command':[path]}
        manager=Mock(adapter=adapter,state='idle',last_failure='')
        with patch.object(self.service.registry,'get',return_value=manager):
            self.service.inspect_target();self.assertEqual(self.job('inspect')[1],'ok')
        self.assertEqual(self.service.config['mode'],'entertainment')
        self.assertEqual(self.service.config['targets'][0]['command'],[path])

    def test_structured_monitor_runs_real_orchestration_and_cleans_on_stop(self):
        capture=Mock(); source=Mock()
        class Stop:
            counter=0
            def is_set(self): return self.counter>=2
            def wait(self,seconds): self.counter+=1
        stop=Stop()
        with patch('lol_words.application.monitoring.ModeRuntime') as runtime_type:
            runtime=runtime_type.return_value
            runtime.manager.adapter.spec={'id':'anki','adapter':'anki','name':'Anki'};runtime.manager.state='idle';runtime.manager.adapter.reason='未运行'
            runtime.manager.last_failure=''; runtime.primary=runtime.manager;runtime.presentation.owned=False
            run_monitor(self.service,copy.deepcopy(self.service.config),stop,self.root,lambda:source,lambda:capture)
            runtime.cleanup.assert_called(); capture.close.assert_called_once()
        states=[value for kind,value in self.service.events.queue if kind=='state']
        self.assertTrue(any(s.monitoring=='正在监测' and s.game=='等待游戏' for s in states))
        self.assertEqual(states[-1].monitoring,'已暂停')

    def test_real_state_machine_projects_delay_visual_confirmation_and_return(self):
        from test_app import IntegrationTests
        states=[]
        frames=[dict(pid=1,ready=True)]
        frames += [dict(pid=1,ready=True,dead=True,front='other') for _ in range(4)]
        frames += [dict(pid=1,ready=True,dead=True,gray=True) for _ in range(4)]
        frames += [dict(pid=1,ready=True,dead=True,timer=1),dict(pid=1,ready=True)]
        _,windows,_=IntegrationTests().scenario(frames,overrides={'death_switch_delay':3},states=states)
        phases={s.phase for s in states}
        self.assertTrue({'等待切出延迟','等待画面确认','学习中'}.issubset(phases))
        self.assertTrue(any(s.respawn==1 for s in states));self.assertFalse(windows.pinned)


class QtCase(unittest.TestCase):
    def setUp(self):
        ServiceCase.setUp(self); self.window=MainWindow(self.service,autostart=False);self.window.timer.stop()
    def tearDown(self):
        self.window.closing=True;self.service.close();QT.processEvents();self.window.close();self.window.deleteLater();QT.processEvents()
        ServiceCase.tearDown(self)

    def test_window_and_header_use_before_respawn_brand(self):
        self.assertEqual(self.window.windowTitle(), '泉水时间管理 · BeforeRespawn')
        texts = [widget.text() for widget in self.window.findChildren(QLabel)]
        self.assertIn('BeforeRespawn  /  游戏伴侣', texts)
        self.assertNotIn('LOL-WORDS  /  游戏伴侣', texts)
        self.assertEqual(self.window.navigation.count(), 5)

    def test_home_reflects_independent_real_service_state(self):
        state=RuntimeState(monitoring='接口暂不可用',game='对局中',phase='等待接口恢复',reason='过期任务已取消',
            target_name='已存在的目标',target_state='窗口已唯一识别',switch_result='返回游戏未完成',pin='已解除')
        self.service.publish_state(state);self.window.tick()
        self.assertIn('接口暂不可用',self.window.monitor_badge.text())
        self.assertEqual(self.window.target_status.text(),'窗口已唯一识别')
        self.assertEqual(self.window.switch_label.text(),'返回游戏未完成');self.assertFalse(self.window.respawn_label.isVisible())

    def test_mode_buttons_write_service_config_without_faking_success(self):
        entertainment=next(b for b in self.window.mode_buttons.buttons() if b.property('mode')=='entertainment')
        entertainment.click();self.assertEqual(self.service.config['mode'],'entertainment')
        self.assertNotEqual(self.window.target_status.text(),'窗口已唯一识别')
        self.window.site_buttons.buttons()[1].click();self.assertEqual(self.service.config['entertainment_site'],'douyin')

    def test_unsaved_settings_survive_target_or_mode_refresh(self):
        self.window.delay.setValue(7.25);self.window.lead.setValue(2.5)
        self.window.set_mode('entertainment')
        self.assertEqual(self.window.delay.value(),7.25)
        self.assertEqual(self.service.config['death_switch_delay'],3)
        self.window.save_settings()
        self.assertEqual(self.service.config['death_switch_delay'],7.25)
        self.assertEqual(self.service.config['return_before_seconds'],2.5)

    def test_saving_other_setting_preserves_existing_fractional_precision(self):
        self.service.update_config({'death_switch_delay':3.12345,'gray_threshold':.82456})
        self.window.refresh_config();self.window.lead.setValue(2);self.window.save_settings()
        self.assertEqual(self.service.config['death_switch_delay'],3.12345)
        self.assertEqual(self.service.config['gray_threshold'],.82456)

    def test_scan_empty_failure_and_library_are_distinct_and_actionable(self):
        self.window.handle_job('scan','ok',[]);self.assertIn('未找到',self.window.scan_status.text())
        self.assertEqual(self.window.app_table.rowCount(),0)
        self.window.missing.setChecked(True);self.assertGreater(self.window.app_table.rowCount(),0)
        self.window.app_table.selectRow(0);self.assertFalse(self.window.use_app_button.isEnabled())
        self.window.handle_job('scan','error','读取安装信息失败');self.assertIn('失败',self.window.scan_status.text())

    def test_detected_application_selection_uses_real_spec(self):
        item=dict(name='实际替身软件',path='C:/Study/Words.exe',process_path='C:/Study/Words.exe',command=['C:/Study/Words.exe'],catalog_id='fixture',source='测试安装登记')
        self.window.handle_job('scan','ok',[item]);self.window.app_table.selectRow(0)
        self.assertTrue(self.window.use_app_button.isEnabled());self.window.use_app_button.click()
        self.assertEqual(Path(selected_spec(self.service.config)['process_path']),Path(item['path']))

    def test_search_error_clears_stale_candidates_and_disables_open(self):
        self.window.render_sites(self.service.catalog.search('Anki'),'catalog');self.window.site_list.setCurrentRow(0)
        self.assertTrue(self.window.use_site_button.isEnabled())
        self.window.handle_job('search','error','网络不可用，请重试')
        self.assertEqual(self.window.site_list.count(),0);self.assertFalse(self.window.use_site_button.isEnabled())

    def test_unknown_candidate_requires_confirmation_before_persist_or_open(self):
        item=dict(title='测试站',url='https://example.org/',domain='example.org')
        self.window.render_sites([item],'candidates','测试站');self.window.site_list.setCurrentRow(0)
        with patch('lol_words.ui.desktop_ui.QMessageBox.question',return_value=QMessageBox.StandardButton.Cancel), patch.object(self.service,'reopen_target') as open_target:
            self.window.select_site();open_target.assert_not_called()
        self.assertFalse((self.root/'runtime/catalog-overrides.json').exists())

    def test_download_prompt_cancels_on_revive_and_pause(self):
        for cancelled_by_pause in [False,True]:
            self.service.stop_event.clear()
            reply,answer,cancelled=threading.Event(),{'approved':False},threading.Event()
            prompt=DownloadPrompt(self.window,self.service,(reply,answer,cancelled));prompt.show()
            (self.service.stop_event if cancelled_by_pause else cancelled).set();prompt.watch()
            self.assertTrue(reply.is_set());self.assertFalse(answer['approved']);self.assertFalse(prompt.isVisible())

    def test_download_request_approval_is_explicit_and_does_not_download(self):
        self.service.stop_event.clear();reply,answer,cancelled=threading.Event(),{'approved':False},threading.Event()
        prompt=DownloadPrompt(self.window,self.service,(reply,answer,cancelled));prompt.approve_button.click()
        self.assertTrue(reply.is_set());self.assertTrue(answer['approved'])

    def wait_for_log_copy(self):
        deadline = time.monotonic() + 5
        while self.window.log_copy_timer.isActive() and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertFalse(self.window.log_copy_timer.isActive(), self.window.log_status.text())

    def test_logs_filter_and_copy_selected_rows(self):
        self.window.append_log('本人死亡已确认：其他', persist=False, timestamp='12:00:00')
        self.window.append_log('目标复用：替身', persist=False, timestamp='12:00:01')
        self.window.append_log('本人死亡已确认：替身', persist=False, timestamp='12:00:02')
        self.window.log_categories.setCurrentText('死亡');self.window.log_search.setText('替身')
        self.assertEqual(self.window.log_proxy.rowCount(),1)
        self.window.log_table.selectRow(0)
        self.assertEqual([index.row() for index in self.window.log_table.selectionModel().selectedRows()], [0])
        self.window.copy_log()
        self.wait_for_log_copy()
        self.assertEqual(self.window.log_status.text(), '已复制 1 条日志。')
        self.assertEqual(QT.clipboard().text(), '12:00:02\t死亡\t本人死亡已确认：替身')

    def test_log_copy_retries_busy_clipboard_with_selection_snapshot(self):
        self.window.append_log('本人死亡已确认：复制测试甲', persist=False, timestamp='12:00:00')
        self.window.append_log('目标复用：复制测试', persist=False, timestamp='12:00:01')
        self.window.append_log('本人死亡已确认：复制测试乙', persist=False, timestamp='12:00:02')
        self.window.log_categories.setCurrentText('死亡');self.window.log_search.setText('复制测试')
        self.window.log_table.selectAll()
        self.assertEqual([index.row() for index in self.window.log_table.selectionModel().selectedRows()], [0, 1])
        expected = '12:00:00\t死亡\t本人死亡已确认：复制测试甲\n12:00:02\t死亡\t本人死亡已确认：复制测试乙'
        clipboard = Mock()
        clipboard.ownsClipboard.side_effect = [False, False, True]
        clipboard.text.return_value = expected
        with patch.object(QApplication, 'clipboard', return_value=clipboard), patch.object(QApplication, 'platformName', return_value='windows'):
            self.window.copy_log()
            self.assertEqual(self.window.log_status.text(), '剪贴板暂不可用，正在重试…')
            self.window.log_search.setText('没有匹配行')
            self.wait_for_log_copy()
        self.assertEqual(clipboard.setText.call_count, 3)
        self.assertTrue(all(call.args == (expected,) for call in clipboard.setText.call_args_list))
        self.assertEqual(self.window.log_status.text(), '已复制 2 条日志。')

    def test_log_copy_reports_persistent_clipboard_failure_and_allows_retry(self):
        self.window.log_table.selectRow(0)
        expected = '\t'.join(self.window.log_proxy.index(0, col).data() for col in range(3))
        clipboard = Mock()
        clipboard.ownsClipboard.return_value = False
        clipboard.text.return_value = ''
        with patch.object(QApplication, 'clipboard', return_value=clipboard), patch.object(QApplication, 'platformName', return_value='windows'):
            self.window.copy_log()
            self.wait_for_log_copy()
            self.assertEqual(clipboard.setText.call_count, 5)
            self.assertEqual(self.window.log_status.text(), '复制失败：剪贴板暂不可用，请稍后重试。')
            clipboard.ownsClipboard.return_value = True
            clipboard.text.return_value = expected
            self.window.copy_log()
        self.assertEqual(clipboard.setText.call_count, 6)
        self.assertEqual(self.window.log_status.text(), '已复制 1 条日志。')

    def test_log_copy_without_selection_does_not_touch_clipboard(self):
        with patch.object(QApplication, 'clipboard') as clipboard:
            self.window.copy_log()
        clipboard.assert_not_called()
        self.assertFalse(self.window.log_copy_timer.isActive())
        self.assertEqual(self.window.log_status.text(), '请先选择需要复制的日志行。')

    def test_log_dedup_and_no_internal_controls(self):
        before=self.window.log_model.rowCount();self.window.append_log('目标复用');self.window.append_log('目标复用')
        self.assertEqual(self.window.log_model.rowCount(),before+1)
        texts=' '.join(w.text() for w in self.window.findChildren(QPushButton))
        self.assertNotIn('惩罚',texts);self.assertNotIn('20',texts)

    def test_editor_file_picker_preserves_advanced_match_conditions(self):
        exe=self.root/'words.exe';exe.write_bytes(b'fixture-not-executable')
        editor=TargetEditor(self.window)
        with patch('lol_words.ui.desktop_ui.QFileDialog.getOpenFileName',return_value=(str(exe),'')): editor.browse()
        editor.title_match.setText('背词');editor.class_match.setText('WordsWindow');editor.pin.setChecked(False);editor.save()
        self.assertEqual(editor.result(),QDialog.DialogCode.Accepted)
        self.assertEqual(editor.result_spec['process_path'],str(exe));self.assertEqual(editor.result_spec['title_contains'],'背词');self.assertFalse(editor.result_spec['pin'])

    def test_navigation_resize_keyboard_and_focus(self):
        self.window.show();self.window.resize(900,660);QT.processEvents()
        self.window.navigation.setFocus();QTest.keyClick(self.window.navigation,Qt.Key.Key_Right);QT.processEvents()
        self.assertEqual(self.window.stack.currentIndex(),1)
        self.window.navigation.setCurrentRow(3);self.window.delay.setFocus();QTest.keyClick(self.window.delay,Qt.Key.Key_Tab)
        self.assertIsNotNone(QT.focusWidget())
        for index in range(5):
            self.window.navigation.setCurrentRow(index);QT.processEvents()
            self.assertLessEqual(self.window.stack.width(),self.window.width())

    def test_high_scaling_small_work_area_keeps_window_accessible(self):
        self.window.adapt_to_area(QRect(0,0,960,520))
        self.assertLessEqual(self.window.height(),464)
        self.assertLessEqual(self.window.minimumHeight(),464)
        self.assertLessEqual(self.window.width(),928)

    def test_companion_flow_indicator_uses_backend_phase_and_clears_on_cancel(self):
        for phase,index in [('等待切出延迟',1),('等待画面确认',2),('学习中',3),('正在返回',4),('本次切出已取消',-1),('已暂停',-1)]:
            self.window.apply_state(RuntimeState(phase=phase))
            self.assertEqual([i for i,step in enumerate(self.window.flow_steps) if step.property('active')],[] if index<0 else [index])
        self.assertTrue(self.window.respawn_label.isHidden())

    def test_companion_layout_stacks_without_losing_target_operations(self):
        self.window.resize(780,660);self.window.update_home_layout()
        # 模拟较窄工作区解除常规最小宽度。
        self.window.setMinimumWidth(600);self.window.resize(780,660);self.window.update_home_layout()
        self.assertTrue(self.window._home_stacked)
        self.assertEqual(self.window.home_grid.getItemPosition(self.window.home_grid.indexOf(self.window.target_panel))[:2],(1,0))
        self.window.resize(1120,780);self.window.update_home_layout()
        self.assertFalse(self.window._home_stacked)
        self.assertEqual(self.window.home_grid.getItemPosition(self.window.home_grid.indexOf(self.window.target_panel))[:2],(0,1))

    def test_companion_rules_show_active_snapshot_until_monitor_restarts(self):
        self.service.worker=Mock(is_alive=lambda:True)
        self.service.runtime=Mock(config=copy.deepcopy(self.service.config))
        self.service.config['death_switch_delay']=8
        self.window.refresh_rules();self.assertIn('死亡后 3 秒',self.window.rules_label.text())
        self.service.worker=None;self.window.refresh_rules();self.assertIn('死亡后 8 秒',self.window.rules_label.text())

    def test_numeric_settings_have_visible_edit_area_and_accept_keyboard_input(self):
        self.window.show();self.window.navigation.setCurrentRow(3);QT.processEvents()
        self.assertGreater(self.window.delay.lineEdit().width(),80)
        self.assertGreater(self.window.lead.lineEdit().width(),80)
        self.window.delay.setFocus();self.window.delay.selectAll();QTest.keyClicks(self.window.delay,'5.25');self.window.delay.interpretText()
        self.assertEqual(self.window.delay.value(),5.25)


# 确认弹窗选项只用于断言，不会访问网络。
from PySide6.QtWidgets import QMessageBox
