"""新策略回归：使用虚拟时间与窗口替身，不代表真实对局验证。"""
import json
import queue
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from test_app import FakeWindows
import test_app as regression
from lol_words.ui.legacy_app import App, DEFAULTS
from lol_words.game.core import DeathSession, Snapshot
from lol_words.system.presentation import StudyWindow
from lol_words.game.study_flow import StudyFlow


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.w = FakeWindows(True)
        self.p = StudyWindow(self.w, target_kind="anki")
        self.logs, self.returns = [], []
        self.now = 0
        self.config = dict(DEFAULTS)
        self.capture = Mock(backend='模拟画面', detail='')
        self.pixels = bytes([40, 150, 210, 0]) * 100
        self.capture.read.side_effect = lambda *args: self.pixels
        def return_game():
            result = self.p.request_return(self.now)
            self.returns.append(result)
        self.flow = StudyFlow(self.w, self.p, self.config, self.logs.append, return_game)
        self.step(0, dead=False)

    def step(self, now, dead=True, timer=30, gray=False, front=None, champion='Ahri', target=3):
        self.now = now
        if front is not None:
            self.w.front = front
        self.pixels = bytes([100, 100, 100, 0] if gray else [40, 150, 210, 0]) * 100
        return self.flow.step(Snapshot(dead, timer, champion), now, target, self.capture, 'borderless')

    def study(self):
        self.step(1, gray=True)
        self.step(2, gray=True)
        self.step(4, gray=True)
        self.assertTrue(self.p.owned)

    def test_default_delay_is_three_seconds_and_independent_of_return(self):
        self.assertEqual(self.config['death_switch_delay'], 3)
        self.assertEqual(self.flow.session.lead, self.config['return_before_seconds'])
        self.step(1, gray=True)
        self.step(2, gray=True)
        self.step(3.99, gray=True)
        self.assertFalse(self.p.owned)
        self.step(4, gray=True)
        self.assertTrue(self.p.owned)

    def test_five_second_delay_uses_death_time_not_gray_time(self):
        self.flow.session.delay = 5
        self.step(1, gray=False)
        self.step(5.5, gray=True)
        self.assertFalse(self.p.owned)
        self.step(6, gray=True)
        self.assertTrue(self.p.owned)
        self.assertEqual(self.flow.session.dead_since, 1)

    def test_user_can_keep_buying_in_foreground_during_delay(self):
        for t in [1, 2, 3, 3.9]:
            self.step(t, gray=True)
        self.assertEqual(self.w.front, 'game')
        self.assertEqual(self.w.activations, [])
        self.assertEqual(self.w.operations, [])

    def test_background_before_gray_restores_game_then_confirms_new_frames(self):
        self.step(1, front='other')
        self.step(3, front='other')
        self.assertEqual(self.w.operations, [])
        self.step(4, front='other')
        self.assertIn(('restore', 9, True), self.w.operations)
        self.assertFalse(self.p.owned)
        self.step(4.1, gray=True)
        self.assertFalse(self.p.owned)
        self.step(4.5, gray=True)
        self.assertFalse(self.p.owned)
        self.step(4.7, gray=True)
        self.assertTrue(self.p.owned)
        self.step(28, timer=1, front='other')
        self.assertEqual(self.w.front, 'game')
        self.assertEqual(self.returns, ['returned'])
        self.assertFalse(self.w.pinned)

    def test_gray_evidence_survives_manual_switch_before_delay_expires(self):
        self.step(1, gray=True)
        self.step(2, gray=True)
        self.step(4, front='other')
        self.assertEqual(self.w.front, 'anki')
        self.assertNotIn(('restore', 9, True), self.w.operations)

    def test_background_never_samples_other_app_pixels(self):
        self.capture.read.reset_mock()
        self.step(1, front='other', gray=True)
        self.step(2, front='other', gray=True)
        self.capture.read.assert_not_called()
        self.assertFalse(self.flow.confirmed)

    def test_black_capture_is_not_gray_confirmation(self):
        self.capture.read.side_effect = lambda *args: bytes(400)
        for t in range(1, 12):
            self.step(t, gray=True)
        self.assertFalse(self.p.owned)
        self.assertFalse(self.flow.confirmed)

    def test_capture_failure_is_not_gray_confirmation(self):
        self.capture.read.side_effect = lambda *args: None
        for t in range(1, 12):
            self.step(t, gray=True)
        self.assertFalse(self.p.owned)
        self.assertFalse(self.flow.confirmed)

    def test_passive_heroes_still_require_visual_confirmation(self):
        for champion in ['Karthus', 'KogMaw', 'Sion']:
            with self.subTest(champion=champion):
                self.setUp()
                for t in range(1, 15):
                    self.step(t, champion=champion)
                self.assertFalse(self.p.owned)
                self.step(15, gray=True, champion=champion)
                self.assertFalse(self.p.owned)
                self.step(15.2, gray=True, champion=champion)
                self.assertTrue(self.p.owned)

    def test_short_death_during_delay_never_switches(self):
        self.step(1, timer=3, gray=True)
        self.step(2, timer=2, gray=True)
        self.step(3, dead=False)
        self.assertNotIn(3, self.w.activations)

    def test_near_return_window_cancels_delayed_show(self):
        self.step(1, timer=5, gray=True)
        self.step(2, timer=4, gray=True)
        self.step(4, timer=1.3, gray=True)
        self.assertNotIn(3, self.w.activations)
        self.assertTrue(self.flow.session.finished)

    def test_zero_death_timer_does_not_start_a_new_switch(self):
        self.step(1, timer=0, gray=True)
        self.step(5, timer=0, gray=True)
        self.assertNotIn(3, self.w.activations)

    def test_early_revive_returns_once(self):
        self.study()
        self.step(5, dead=False)
        self.step(6, dead=False)
        self.assertEqual(self.returns, ['returned'])

    def test_repeated_game_clicks_keep_pin_without_stealing_focus(self):
        self.study()
        self.w.activate = Mock(side_effect=AssertionError('学习期间不应反复激活'))
        for t in [4.2, 4.4, 4.8, 5, 5.5, 6]:
            self.w.front = 'game'
            self.w.pinned = False
            self.step(t, gray=True)
            # 置顶节流每 0.5 秒，下一次维护会恢复层级。
        self.step(6.5, gray=True)
        self.assertTrue(self.w.pinned)
        self.assertTrue(self.p.owned)
        self.assertEqual(self.w.front, 'game')
        self.w.activate.assert_not_called()

    def test_pin_disabled_does_not_silently_enable_again(self):
        self.config['persistent_anki_topmost'] = False
        self.config['persistent_target_topmost'] = False
        self.study()
        self.assertFalse(self.w.pinned)
        self.step(5, front='other')
        self.step(6, front='anki')
        self.assertFalse(self.w.pinned)
        self.step(7, timer=1, front='other')
        self.assertEqual(self.w.front, 'game')

    def test_original_pin_preference_survives_cleanup(self):
        self.w.pinned = True
        self.study()
        self.flow.cleanup()
        self.assertTrue(self.w.pinned)

    def test_data_loss_returns_and_removes_temporary_pin(self):
        self.study()
        self.flow.data_lost()
        self.assertFalse(self.w.pinned)
        self.assertEqual(self.w.front, 'game')
        self.step(10, gray=True)
        self.assertFalse(self.p.owned)

    def test_data_loss_cancels_unexecuted_cutout_until_next_death(self):
        self.step(1, gray=True)
        self.flow.data_lost()
        self.step(10, gray=True)
        self.step(11, gray=True)
        self.assertFalse(self.p.owned)
        self.step(12, dead=False)
        self.step(13, gray=True)
        self.step(14, gray=True)
        self.step(16, gray=True)
        self.assertTrue(self.p.owned)

    def test_pause_cancels_delay_and_never_later_shows(self):
        self.step(1, gray=True)
        self.flow.cleanup()
        self.step(6, gray=True)
        self.assertNotIn(3, self.w.activations)
        self.assertIsNone(self.flow.session.dead_since)

    def test_pause_flag_checked_before_actual_window_action(self):
        self.step(1, gray=True)
        self.step(2, gray=True)
        self.flow.stopped = lambda: True
        self.step(4, gray=True)
        self.assertFalse(self.p.owned)

    def test_no_reference_never_guesses_from_countdown(self):
        self.flow.gate.reference = None
        self.w.front = 'other'
        for t in range(1, 12):
            self.step(t, gray=True)
        self.assertFalse(self.p.owned)
        self.assertEqual(self.w.operations, [])
        self.assertTrue(any('缺少存活彩色参照' in line for line in self.logs))

    def test_failed_background_restore_has_bounded_retries(self):
        self.w.restore = Mock(return_value=False)
        for t in range(1, 30):
            self.step(t, front='other')
        self.assertEqual(self.w.restore.call_count, 3)
        self.assertFalse(self.p.owned)

    def test_same_wait_reason_does_not_spam_poll_logs(self):
        self.step(1)
        for i in range(1, 200):
            self.step(1 + i / 100, gray=False)
        self.assertLess(len(self.logs), 10)
        self.assertEqual(sum('本人死亡已确认' in line for line in self.logs), 1)

    def test_missing_anki_does_not_create_a_return_task(self):
        self.step(1, gray=True, target=None)
        self.step(2, gray=True, target=None)
        self.step(4, gray=True, target=None)
        self.assertFalse(self.p.owned)
        self.assertEqual(self.returns, [])
        self.step(5, gray=True)
        self.assertTrue(self.p.owned)


class SettingsTests(unittest.TestCase):
    def fake_app(self):
        app = App.__new__(App)
        app.config_error = ''
        app.mode = Mock(get=lambda:'study')
        app.target_choice = Mock(get=lambda:'Anki')
        app.target_pin = Mock(get=lambda:True)
        app.site = Mock(get=lambda:'哔哩哔哩')
        app.lead = Mock(get=lambda: '1')
        app.threshold = Mock(get=lambda: '82')
        app.switch_delay = Mock(get=lambda: '5')
        app.persistent_pin = Mock(get=lambda: False)
        app.display_mode = Mock(get=lambda: '无边框')
        app.config = dict(DEFAULTS)
        app.detected_path = ''
        app.append = Mock()
        return app

    def test_new_settings_saved_and_legacy_close_setting_removed(self):
        app = self.fake_app()
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder, patch('lol_words.ui.legacy_app.ROOT', Path(folder)):
            self.assertTrue(app.save())
            data = json.loads((Path(folder) / 'config.json').read_text(encoding='utf-8'))
        self.assertEqual(data['death_switch_delay'], 5)
        self.assertFalse(data['persistent_anki_topmost'])
        self.assertNotIn('preserve_existing_anki', data)
        self.assertIn('暂停并恢复后生效', app.append.call_args.args[0])

    def test_invalid_delay_is_rejected_without_writing_config(self):
        for value in ['-1', '121', 'nan', 'inf', 'abc']:
            with self.subTest(value=value):
                app = self.fake_app()
                app.switch_delay = Mock(get=lambda: value)
                with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder, patch('lol_words.ui.legacy_app.ROOT', Path(folder)), patch('lol_words.ui.legacy_app.messagebox.showerror'):
                    self.assertFalse(app.save())
                    self.assertFalse((Path(folder) / 'config.json').exists())


class NewPolicyIntegrationTests(unittest.TestCase):
    def test_full_monitor_manual_switch_delay_and_respawn_return(self):
        frames = [dict(pid=1, ready=True, mode='borderless')]
        frames += [dict(pid=1, ready=True, dead=True, front='other') for _ in range(4)]
        frames += [dict(pid=1, ready=True, dead=True, gray=True) for _ in range(4)]
        frames += [dict(pid=1, ready=True, dead=True, timer=1, front='other'),
                   dict(pid=1, ready=True), dict(pid=1, ready=True, end=True)]
        launches, w, events = regression.IntegrationTests().scenario(frames, overrides={'death_switch_delay': 3})
        self.assertEqual(launches, [7])
        self.assertIn(3, w.activations)
        self.assertEqual(w.closes, [])
        self.assertTrue(any('返回游戏成功' in text for _, text in events))
        self.assertFalse(w.pinned)

    def test_fullscreen_keeps_existing_switch_and_return_path(self):
        frames = [dict(pid=1, ready=True, mode='fullscreen')]
        frames += [dict(pid=1, ready=True, dead=True, gray=True) for _ in range(5)]
        frames += [dict(pid=1, ready=True, dead=True, timer=1), dict(pid=1, ready=True)]
        _, w, events = regression.IntegrationTests().scenario(frames, overrides={'death_switch_delay': 3})
        self.assertIn(('minimize', 9), w.operations)
        self.assertIn(('minimize', 3), w.operations)
        self.assertTrue(any('返回游戏成功' in text for _, text in events))
        self.assertFalse(w.pinned)
