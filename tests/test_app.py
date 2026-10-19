import os
import sys
from pathlib import Path
import unittest
import queue
import tempfile
from unittest.mock import Mock, patch
APP_DIR = Path(os.environ.get('LOL_ANKI_APP_DIR', str(Path(__file__).resolve().parents[1])))
sys.path.insert(0, str(APP_DIR))
if (APP_DIR / '_deps').is_dir():
    sys.path.insert(0, str(APP_DIR / '_deps'))
from lol_words.game.core import DeathSession, FrameStats, GrayGate, Snapshot, frame_stats, own_snapshot
from lol_words.ui.legacy_app import App, DEFAULTS
from lol_words.game.automation import MatchTracker
from lol_words.targets.adapters import TargetManager
from lol_words.modes.penalty import DeathLedger
from lol_words.targets.discovery import executable_from, locate_anki
from lol_words.game.game_api import GameData, NoRedirect
from lol_words.system.presentation import StudyWindow
from lol_words.system.native import Windows


class SessionTests(unittest.TestCase):
    def session(self):
        state = DeathSession(3, delay=0)
        state.update(Snapshot(False, 0, 'Ahri'), 'game')
        return state

    def test_normal_death_and_return_once(self):
        s = self.session()
        self.assertIsNone(s.update(Snapshot(True, 30, 'Ahri'), 'game', False))
        self.assertEqual(s.update(Snapshot(True, 29, 'Ahri'), 'game', True), 'study')
        s.confirm_study(True)
        self.assertIsNone(s.update(Snapshot(True, 10, 'Ahri'), 'anki'))
        self.assertEqual(s.update(Snapshot(True, 3, 'Ahri'), 'anki'), 'return')
        self.assertIsNone(s.update(Snapshot(True, 2, 'Ahri'), 'game', True))
        self.assertIsNone(s.update(Snapshot(False, 0, 'Ahri'), 'game'))

    def test_early_revive(self):
        s = self.session()
        self.assertEqual(s.update(Snapshot(True, 30, 'Ahri'), 'game', True), 'study')
        s.confirm_study(True)
        self.assertEqual(s.update(Snapshot(False, 0, 'Ahri'), 'anki'), 'return')

    def test_short_death_never_switches(self):
        s = self.session()
        self.assertIsNone(s.update(Snapshot(True, 3, 'Ahri'), 'game', True))
        self.assertIsNone(s.update(Snapshot(True, 1, 'Ahri'), 'game', True))

    def test_initial_dead_waits_for_next_cycle(self):
        s = DeathSession(3, delay=0)
        self.assertIsNone(s.update(Snapshot(True, 25), 'game', True))
        self.assertIsNone(s.update(Snapshot(True, 15), 'game', True))
        self.assertIsNone(s.update(Snapshot(False, 0), 'game'))
        self.assertEqual(s.update(Snapshot(True, 25), 'game', True), 'study')

    def test_manual_return_never_reopens_this_death(self):
        s = self.session()
        s.update(Snapshot(True, 30), 'game', True)
        s.confirm_study(True)
        self.assertEqual(s.update(Snapshot(True, 25), 'game', True, manual_return_confirmed=True), 'manual_return')
        self.assertIsNone(s.update(Snapshot(True, 20), 'game', True))
        self.assertFalse(s.engaged)

    def test_confirmed_death_switches_from_other_app(self):
        s = self.session()
        self.assertEqual(s.update(Snapshot(True, 30), 'other', True), 'study')
        self.assertIsNone(s.update(Snapshot(True, 25), 'game', True))

    def test_user_changes_app_while_waiting_for_gray(self):
        s = self.session()
        s.update(Snapshot(True, 30), 'game', False)
        self.assertIsNone(s.update(Snapshot(True, 22), 'other'))
        self.assertEqual(s.update(Snapshot(True, 21), 'other', True), 'study')

    def test_passive_heroes_wait_for_gray_instead_of_exclusion(self):
        for champion in ['Karthus', 'Sion', 'KogMaw']:
            s = self.session()
            self.assertIsNone(s.update(Snapshot(True, 30, champion), 'game', False))
            self.assertEqual(s.update(Snapshot(True, 25, champion), 'game', True), 'study')

    def test_no_retry_if_focus_failed(self):
        s = self.session()
        self.assertEqual(s.update(Snapshot(True, 30), 'game', True), 'study')
        s.confirm_study(False)
        self.assertIsNone(s.update(Snapshot(True, 25), 'game', True))

    def test_zero_timer_returns_even_before_dead_flag_updates(self):
        s = self.session()
        s.update(Snapshot(True, 30), 'game', True)
        s.confirm_study(True)
        self.assertEqual(s.update(Snapshot(True, 0), 'anki'), 'return')
        self.assertIsNone(s.update(Snapshot(False, 0), 'anki'))

    def test_can_switch_next_death(self):
        s = self.session()
        s.update(Snapshot(True, 30), 'game', True)
        s.confirm_study(True)
        s.update(Snapshot(False, 0), 'anki')
        self.assertEqual(s.update(Snapshot(True, 30), 'game', True), 'study')

    def test_alive_gray_scene_never_triggers(self):
        s = self.session()
        self.assertIsNone(s.update(Snapshot(False, 0), 'game', True))

    def test_death_color_scene_never_triggers(self):
        s = self.session()
        for _ in range(20):
            self.assertIsNone(s.update(Snapshot(True, 25), 'game', False))

class GrayTests(unittest.TestCase):
    def test_colored_pixels_and_gray_pixels(self):
        colorful = frame_stats(bytes([40, 150, 210, 0]) * 100)
        gray = frame_stats(bytes([100, 105, 110, 0]) * 100)
        self.assertEqual(colorful.color_fraction, 1)
        self.assertEqual(gray.gray_fraction, 1)

    def test_pure_black_is_not_a_valid_death_frame(self):
        black = frame_stats(bytes(400))
        gate = GrayGate()
        gate.observe(False, FrameStats(0, 1, 1))
        for _ in range(10):
            self.assertFalse(gate.observe(True, black))

    def test_gray_requires_live_reference_and_two_frames(self):
        gate = GrayGate()
        gray, color = FrameStats(0.95, 0.05, 1), FrameStats(0.1, 0.9, 1)
        self.assertFalse(gate.observe(True, gray))
        self.assertFalse(gate.observe(False, color))
        self.assertFalse(gate.observe(True, gray))
        self.assertTrue(gate.observe(True, gray))

    def test_invalid_or_color_frame_breaks_consecutive_detection(self):
        gate = GrayGate()
        color, gray = FrameStats(0.1, 0.9, 1), FrameStats(0.95, 0.05, 1)
        gate.observe(False, color)
        gate.observe(True, gray)
        self.assertFalse(gate.observe(True, None))
        self.assertFalse(gate.observe(True, gray))
        self.assertFalse(gate.observe(True, color))
        self.assertFalse(gate.observe(True, gray))

    def test_dim_frames_are_rejected(self):
        gate = GrayGate()
        gate.observe(False, FrameStats(0.1, 0.9, 1))
        for _ in range(5):
            self.assertFalse(gate.observe(True, FrameStats(1, 0, 0.05)))

    def test_gray_living_scene_does_not_create_reference(self):
        gate = GrayGate()
        gray = FrameStats(1, 0, 1)
        gate.observe(False, gray)
        self.assertIsNone(gate.reference)
        self.assertFalse(gate.observe(True, gray))

    def test_relative_drop_is_required(self):
        gate = GrayGate(threshold=0.5)
        gate.observe(False, FrameStats(0.8, 0.2, 1))
        for _ in range(5):
            self.assertFalse(gate.observe(True, FrameStats(0.8, 0.2, 1)))

    def test_revive_resets_confirmation(self):
        gate = GrayGate()
        color, gray = FrameStats(0.1, 0.9, 1), FrameStats(0.95, 0.05, 1)
        gate.observe(False, color)
        gate.observe(True, gray)
        gate.observe(False, color)
        self.assertFalse(gate.observe(True, gray))

class IdentityTests(unittest.TestCase):
    def payload(self):
        return {'activePlayer': {'riotId': '本人#123'}, 'allPlayers': [
            {'riotId': '队友#456', 'isDead': True, 'respawnTimer': 25},
            {'riotId': '本人#123', 'isDead': False, 'respawnTimer': 0, 'championName': 'Ahri'}]}

    def test_own_player_not_first_player(self):
        name, sample = own_snapshot(self.payload())
        self.assertEqual(name, '本人#123')
        self.assertFalse(sample.dead)

    def test_manual_identity(self):
        data = self.payload()
        data['activePlayer'] = {}
        self.assertEqual(own_snapshot(data, '本人#123')[0], '本人#123')

    def test_never_guess_identity(self):
        data = self.payload()
        data['activePlayer'] = {}
        with self.assertRaises(ValueError):
            own_snapshot(data)

    def test_missing_or_nonboolean_death_rejected(self):
        for value in [None, 'false', 0, 1]:
            data = self.payload()
            data['allPlayers'][1]['isDead'] = value
            with self.assertRaises(ValueError):
                own_snapshot(data)

    def test_ambiguous_player_rejected(self):
        data = self.payload()
        data['allPlayers'].append(dict(data['allPlayers'][1]))
        with self.assertRaises(ValueError):
            own_snapshot(data)

    def test_invalid_respawn_timer_rejected(self):
        for timer in [-2, 'NaN', 'Infinity']:
            data = self.payload()
            data['allPlayers'][1]['respawnTimer'] = timer
            with self.assertRaises(ValueError):
                own_snapshot(data)

    def test_localized_champion_uses_stable_identifier(self):
        data = self.payload()
        data['allPlayers'][1].update(championName='死亡颂唱者',
                                    rawChampionName='game_character_displayname_Karthus')
        self.assertEqual(own_snapshot(data)[1].champion, 'Karthus')

class MatchTests(unittest.TestCase):
    def test_lobby_and_loading_do_not_enter(self):
        t = MatchTracker()
        self.assertEqual(t.update(None, False, False, 0), [])
        self.assertEqual(t.update(1, False, False, 1), [])
        self.assertEqual(t.update(1, True, False, 2, 0), ['enter'])
        self.assertEqual(t.update(1, True, False, 3, 1), [])

    def test_api_loss_does_not_end(self):
        t = MatchTracker()
        t.update(1, True, False, 0, 10)
        for now in range(1, 10):
            self.assertEqual(t.update(1, False, False, now), [])
            self.assertTrue(t.active)

    def test_game_end_does_not_reopen_until_new_match(self):
        t = MatchTracker()
        t.update(1, True, False, 0, 10)
        self.assertEqual(t.update(1, True, True, 1, 11), ['exit'])
        self.assertEqual(t.update(1, True, False, 2, 12), [])
        self.assertEqual(t.update(2, True, False, 3, 0), ['enter'])

    def test_process_exit_requires_confirmation(self):
        t = MatchTracker()
        t.update(1, True, False, 0, 10)
        self.assertEqual(t.update(None, False, False, 1), [])
        self.assertEqual(t.update(None, False, False, 3), [])
        self.assertEqual(t.update(None, False, False, 4), ['exit'])

    def test_brief_process_gap_recovers(self):
        t = MatchTracker()
        t.update(1, True, False, 0, 10)
        t.update(None, False, False, 1)
        self.assertEqual(t.update(1, True, False, 2, 12), [])
        self.assertTrue(t.active)

    def test_pid_replaced_ends_old_and_enters_new(self):
        t = MatchTracker()
        t.update(1, True, False, 0, 100)
        self.assertEqual(t.update(2, True, False, 1, 0), ['exit', 'enter'])

    def test_postgame_start_does_not_open_anki(self):
        t = MatchTracker()
        self.assertEqual(t.update(1, True, True, 0, 1000), [])
        self.assertFalse(t.active)

    def test_game_time_reset_in_same_process_is_new_match(self):
        t = MatchTracker()
        t.update(1, True, False, 0, 1000)
        t.update(1, True, True, 1, 1001)
        self.assertEqual(t.update(1, True, False, 2, 0), ['enter'])


class FakeWindows:
    def __init__(self, existing=False):
        self.anki = existing
        self.running = existing
        self.front = 'game'
        self.closes = []
        self.activations = []
        self.pinned = False
        self.iconic = set()
        self.operations = []
        self.input_stamp = 0
        self.other_hwnd = 100
    def find(self, kind):
        return (3 if self.anki else None) if kind == 'anki' else 9
    def anki_running(self):
        return self.running
    def current_game(self):
        return 9 if self.front == 'game' else None
    def foreground(self):
        return self.front
    def foreground_handle(self):
        return self.other_hwnd if self.front == 'other' else {'game':9, 'anki':3}.get(self.front, 0)
    def kind(self, hwnd):
        return 'anki' if hwnd == 3 and self.anki else 'game' if hwnd == 9 else 'other'
    def activate(self, hwnd):
        self.activations.append(hwnd)
        self.iconic.discard(hwnd)
        self.front = 'anki' if hwnd == 3 else 'game'
        return True
    def is_topmost(self, hwnd):
        return self.pinned
    def set_topmost(self, hwnd, enabled):
        self.operations.append(('pin', hwnd, enabled))
        self.pinned = enabled
        return True
    def is_iconic(self, hwnd):
        return hwnd in self.iconic
    def is_visible(self, hwnd):
        return bool((hwnd == 3 and self.anki) or hwnd == 9)
    def minimize(self, hwnd):
        self.operations.append(('minimize', hwnd))
        self.iconic.add(hwnd)
        if (hwnd == 3 and self.front == 'anki') or (hwnd == 9 and self.front == 'game'):
            self.front = 'desktop'
        return True
    def restore(self, hwnd, activate=True):
        self.operations.append(('restore', hwnd, activate))
        self.iconic.discard(hwnd)
        return self.activate(hwnd) if activate else True
    def desktop_foreground(self):
        return self.front == 'desktop'
    def focus_diagnostics(self, hwnd):
        return 'test: focus denied'
    def input_serial(self):
        return self.input_stamp
    def close_anki(self, hwnd):
        self.closes.append(hwnd)
        self.anki = self.running = False
        return True
    def client_running(self):
        return False


class DiscoveryTests(unittest.TestCase):
    def test_non_default_drive_style_install_discovered_without_cache(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            base = Path(folder)
            install = base / 'codex' / 'Anki'
            install.mkdir(parents=True)
            exe = install / 'Anki.exe'
            exe.write_bytes(b'test fixture')
            with patch('lol_words.targets.discovery.local_roots', return_value=[base]), patch('lol_words.targets.discovery.registry_candidates', return_value=[]), patch('lol_words.targets.discovery.shutil.which', return_value=None):
                self.assertEqual(locate_anki(), str(exe.resolve()))

    def test_registry_display_icon_arguments_parsed(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            exe = Path(folder) / 'Anki.exe'
            exe.write_bytes(b'fixture')
            self.assertEqual(executable_from(f'"{exe}",0'), exe.resolve())

    def test_launcher_in_subdirectory(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            install = Path(folder) / 'Launcher'
            install.mkdir()
            exe = install / 'anki.exe'
            exe.write_bytes(b'fixture')
            self.assertEqual(executable_from(folder), exe.resolve())

    def test_unrelated_executable_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            exe = Path(folder) / 'unrelated.exe'
            exe.write_bytes(b'fixture')
            self.assertIsNone(executable_from(exe))

    def test_shortcuts_with_unicode_targets(self):
        import json
        from lol_words.targets.discovery import shortcut_candidates
        result = Mock(stdout=json.dumps(['E:/学习/Anki.exe'], ensure_ascii=False).encode('utf-8'))
        with patch('lol_words.targets.discovery.subprocess.run', return_value=result):
            self.assertEqual(shortcut_candidates(), ['E:/学习/Anki.exe'])


class ApiTests(unittest.TestCase):
    def test_identity_uses_activeplayername_fallback(self):
        source = GameData()
        data = {'activeplayer': {}, 'playerlist': [{'riotId': '本人#123', 'isDead': False, 'respawnTimer': 0}], 'activeplayername': '本人#123'}
        source.get = lambda endpoint: data[endpoint]
        self.assertEqual(source.read()[0], '本人#123')

    def test_game_end_event(self):
        source = GameData()
        source.get = lambda endpoint: {'gameTime': 100} if endpoint == 'gamestats' else {'Events': [{'EventName': 'GameEnd'}]}
        self.assertEqual(source.status(), (100, True))

    def test_missing_event_api_does_not_block_match_detection(self):
        source = GameData()
        def get(endpoint):
            if endpoint == 'gamestats':
                return {'gameTime': 10}
            raise OSError('event API unavailable')
        source.get = get
        self.assertEqual(source.status(), (10, False))

    def test_redirect_never_followed(self):
        with self.assertRaises(ValueError):
            NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.com')


class IntegrationTests(unittest.TestCase):
    def scenario(self, frames, present=False, overrides=None, states=None):
        w = FakeWindows(present)
        class Stop:
            step = 0
            def is_set(self):
                return self.step >= len(frames)
            def wait(self, interval):
                self.step += 1
        stop = Stop()
        launches = []
        def launch(path):
            launches.append(stop.step)
            w.anki = w.running = True
        w.league_pid = lambda: frames[stop.step].get('pid')
        class Source:
            def status(self):
                current = frames[stop.step]
                if not current.get('ready', False):
                    raise OSError('not ready')
                return current.get('time', 10), current.get('end', False)
            def read(self, identity):
                current = frames[stop.step]
                if 'front' in current:
                    w.front = current['front']
                if 'mode' in current:
                    app.config['game_display_mode'] = current['mode']
                return '本人#123', Snapshot(current.get('dead', False), current.get('timer', 20), 'Ahri')
        class Capture:
            def close(self): pass
            def read(self, *args):
                pixel = [100, 100, 100, 0] if frames[stop.step].get('gray') else [40, 150, 210, 0]
                return bytes(pixel) * 100
        app = App.__new__(App)
        app.windows, app.events, app.detected_path = w, queue.Queue(), 'auto-found/Anki.exe'
        original_restore = w.restore
        def restore(hwnd, activate=True):
            if hwnd == 9 and activate and frames[stop.step].get('delay_return', False):
                w.operations.append(('restore-pending', hwnd))
                return False
            return original_restore(hwnd, activate)
        w.restore = restore
        config = DEFAULTS.copy()
        config['death_switch_delay'] = 0
        config.update(overrides or {})
        app.config = dict(config)
        if states is not None:
            app.publish_state = states.append
            app.last_return = ''
            app.availability = {}
        w.process_identity = lambda pid: dict(pid=pid, created=1, path='fixture.exe')
        class Adapter:
            spec = dict(id='anki',name='Anki',pin=True)
            reason = 'test'
            def find(self): return 3 if w.anki else None
            def running(self): return w.running
            def start(self): launch('Anki.exe')
        manager = TargetManager(Adapter(), lambda text: app.events.put(('log',text)))
        app.registry = Mock(get=lambda spec:manager)
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as state_dir, patch('lol_words.ui.legacy_app.GameData', Source), patch('lol_words.ui.legacy_app.SceneCapture', Capture), patch('lol_words.ui.legacy_app.time.monotonic', side_effect=lambda: float(stop.step)):
            app.ledger = DeathLedger(Path(state_dir)/'penalty.json')
            app.monitor(config, stop)
        return launches, w, list(app.events.queue)

    def test_two_games_launch_once_keep_and_reuse(self):
        frames = [dict(pid=None), dict(pid=1, ready=True), dict(pid=1, ready=True, dead=True, gray=True), dict(pid=1, ready=True, dead=True, gray=True), dict(pid=1, ready=True, end=True), dict(pid=1, ready=True, end=True), dict(pid=None), dict(pid=None), dict(pid=None), dict(pid=None), dict(pid=2, ready=True), dict(pid=2, ready=True, end=True)]
        launches, w, events = self.scenario(frames)
        self.assertEqual(launches, [3])
        self.assertEqual(w.closes, [])
        self.assertIn(3, w.activations)
        self.assertTrue(w.anki)
        self.assertTrue(any('等待下一局' in text for _, text in events))

    def test_network_interruption_does_not_close_anki(self):
        frames = [dict(pid=1, ready=True), dict(pid=1), dict(pid=1, ready=True), dict(pid=1, ready=True, end=True)]
        launches, w, events = self.scenario(frames)
        self.assertEqual(launches, [])
        self.assertEqual(w.closes, [])
        self.assertTrue(any('暂不可用' in text for _, text in events))

    def test_no_game_never_launches_anki(self):
        launches, w, events = self.scenario([dict(pid=None)] * 3)
        self.assertEqual(launches, [])
        self.assertEqual(w.closes, [])

    def test_loading_without_game_data_does_not_launch_anki(self):
        launches, w, events = self.scenario([dict(pid=1)] * 3)
        self.assertEqual(launches, [])

    def test_pausing_during_match_does_not_close_anki(self):
        launches, w, events = self.scenario([dict(pid=1, ready=True)])
        self.assertEqual(launches, [])
        self.assertEqual(w.closes, [])

    def test_api_timeout_cancels_pending_cutout_for_this_death(self):
        frames = [dict(pid=1, ready=True), dict(pid=1, ready=True, dead=True, gray=True),
                  dict(pid=1), dict(pid=1, ready=True, dead=True, gray=True),
                  dict(pid=1, ready=True, dead=True, gray=True), dict(pid=1, ready=True, end=True)]
        launches, w, events = self.scenario(frames)
        self.assertEqual(launches, [])
        self.assertNotIn(3, w.activations)
        self.assertFalse(w.pinned)

    def test_borderless_focus_bounce_does_not_discard_revive_return(self):
        frames = [dict(pid=1, ready=True, mode='borderless'),
                  dict(pid=1, ready=True, dead=True, gray=True),
                  dict(pid=1, ready=True, dead=True, gray=True),
                  dict(pid=1, ready=True, dead=True, gray=True, front='game'),
                  dict(pid=1, ready=True, dead=True, gray=True, front='game'),
                  dict(pid=1, ready=True, dead=True, timer=2),
                  dict(pid=1, ready=True), dict(pid=1, ready=True, end=True)]
        launches, w, events = self.scenario(frames)
        self.assertEqual(launches, [2])
        self.assertTrue(any('持续置顶正常' in text for _, text in events))
        self.assertTrue(any('返回游戏成功' in text for _, text in events))
        self.assertFalse(any('已主动返回游戏' in text for _, text in events))
        self.assertFalse(any('模式=auto' in text for _, text in events))
        self.assertTrue(any('模式=borderless' in text for _, text in events))
        self.assertNotIn(('minimize', 9), w.operations)
        self.assertIn(('minimize', 3), w.operations)

    def test_borderless_game_already_front_still_hides_anki_at_revive(self):
        frames = [dict(pid=1, ready=True, mode='borderless'),
                  dict(pid=1, ready=True, dead=True, gray=True),
                  dict(pid=1, ready=True, dead=True, gray=True)]
        frames += [dict(pid=1, ready=True, dead=True, gray=True, front='game') for _ in range(4)]
        frames += [dict(pid=1, ready=True, dead=True, timer=2, front='game'),
                   dict(pid=1, ready=True), dict(pid=1, ready=True, end=True)]
        _, w, events = self.scenario(frames)
        self.assertIn(('minimize', 3), w.operations)
        self.assertTrue(any('游戏取得前台且目标窗口不再遮挡' in text for _, text in events))
        self.assertFalse(any('已主动返回游戏' in text for _, text in events))

    def test_return_continues_after_revive_trigger_during_api_outage(self):
        frames = [dict(pid=1, ready=True), dict(pid=1, ready=True, dead=True, gray=True),
                  dict(pid=1, ready=True, dead=True, gray=True),
                  dict(pid=1, ready=True, dead=True, timer=2, delay_return=True),
                  dict(pid=1, delay_return=True), dict(pid=1),
                  dict(pid=1, ready=True), dict(pid=1, ready=True, end=True)]
        launches, w, events = self.scenario(frames)
        self.assertEqual(launches, [2])
        self.assertIn(9, w.activations)
        self.assertTrue(any('返回游戏成功' in text for _, text in events))
        self.assertFalse(any('返回游戏失败' in text for _, text in events))
        self.assertFalse(w.pinned)


class PresentationTests(unittest.TestCase):
    def test_borderless_pins_and_focuses_without_minimizing(self):
        w = FakeWindows(True)
        p = StudyWindow(w, target_kind="anki")
        self.assertTrue(p.show(3, 9, 'borderless'))
        self.assertTrue(w.pinned)
        self.assertEqual(w.front, 'anki')
        self.assertFalse(w.iconic)
        p.release()
        self.assertFalse(w.pinned)
        self.assertEqual(w.front, 'game')

    def test_fullscreen_and_auto_minimize_then_restore_game(self):
        for mode in ['fullscreen', 'auto']:
            w, p = FakeWindows(True), None
            p = StudyWindow(w, target_kind="anki")
            self.assertTrue(p.show(3, 9, mode))
            self.assertIn(9, w.iconic)
            self.assertTrue(w.pinned)
            self.assertTrue(p.release())
            self.assertFalse(w.pinned)
            self.assertNotIn(9, w.iconic)
            self.assertEqual(w.front, 'game')

    def test_original_topmost_state_is_preserved(self):
        w = FakeWindows(True)
        w.pinned = True
        p = StudyWindow(w, target_kind="anki")
        p.show(3, 9, 'borderless')
        p.release()
        self.assertTrue(w.pinned)

    def test_other_application_never_stolen_on_return(self):
        w = FakeWindows(True)
        p = StudyWindow(w, target_kind="anki")
        p.show(3, 9, 'fullscreen')
        w.front = 'other'
        self.assertFalse(p.release())
        self.assertEqual(w.front, 'other')
        self.assertFalse(w.pinned)
        self.assertNotIn(9, w.iconic)

    def test_automatic_show_works_after_manual_switch(self):
        w = FakeWindows(True)
        w.front = 'other'
        p = StudyWindow(w, target_kind="anki")
        self.assertTrue(p.show(3, 9))
        self.assertEqual(w.front, 'anki')
        p.release()

    def test_manual_test_can_start_from_helper_window(self):
        w = FakeWindows(True)
        w.front = 'other'
        p = StudyWindow(w, target_kind="anki")
        self.assertTrue(p.show(3, 9, manual=True))
        p.release()
        self.assertFalse(w.pinned)

    def test_transient_focus_failure_has_bounded_retry(self):
        w = FakeWindows(True)
        original = w.activate
        calls = []
        def activate(hwnd):
            calls.append(hwnd)
            return original(hwnd) if len(calls) > 1 else False
        w.activate = activate
        p = StudyWindow(w, target_kind="anki")
        with patch('lol_words.system.presentation.time.sleep'):
            self.assertTrue(p.show(3, 9, 'fullscreen'))
        self.assertEqual(len(calls), 2)
        p.release()

    def test_borderless_focus_denial_uses_minimize_fallback(self):
        w = FakeWindows(True)
        original = w.activate
        w.activate = lambda hwnd: False if w.front == 'game' else original(hwnd)
        p = StudyWindow(w, target_kind="anki")
        with patch('lol_words.system.presentation.time.sleep'):
            self.assertTrue(p.show(3, 9, 'borderless'))
        self.assertIn(('minimize', 9), w.operations)
        p.release()

    def test_total_failure_unpins_and_cleans_owned_state(self):
        w = FakeWindows(True)
        w.activate = Mock(return_value=False)
        p = StudyWindow(w, target_kind="anki")
        with patch('lol_words.system.presentation.time.sleep'):
            self.assertFalse(p.show(3, 9, 'fullscreen'))
        self.assertFalse(w.pinned)
        self.assertFalse(p.owned)
        self.assertIn('focus denied', p.last_reason)
        self.assertLessEqual(w.activate.call_count, 6)

    def test_focus_is_not_reported_as_success_when_topmost_fails(self):
        w = FakeWindows(True)
        w.set_topmost = Mock(return_value=False)
        p = StudyWindow(w, target_kind="anki")
        with patch('lol_words.system.presentation.time.sleep'):
            self.assertFalse(p.show(3, 9, 'borderless'))
        self.assertFalse(p.owned)

    def test_return_activation_retries(self):
        w = FakeWindows(True)
        p = StudyWindow(w, target_kind="anki")
        p.show(3, 9, 'fullscreen')
        original, calls = w.restore, []
        def restore(hwnd, activate=True):
            calls.append(hwnd)
            return original(hwnd, activate) if len(calls) > 1 else False
        w.restore = restore
        with patch('lol_words.system.presentation.time.sleep'):
            self.assertTrue(p.release())
        self.assertEqual(calls, [9, 9])
        self.assertFalse(w.pinned)


class CaptureBackendTests(unittest.TestCase):
    def capture(self, bounds=(-1920, 0, 0, 1080)):
        import numpy as np
        from types import SimpleNamespace as S
        from lol_words.system.capture import SceneCapture
        cap = SceneCapture.__new__(SceneCapture)
        cap.gdi = Mock()
        cap.gdi.read.return_value = b'gdi'
        cap.backend, cap.detail, cap.cameras, cap.retry_after = '', '', {}, 0
        cap.import_attempted = True
        output = S(update_desc=Mock(), desc=S(DesktopCoordinates=S(left=bounds[0], top=bounds[1], right=bounds[2], bottom=bounds[3])))
        camera = Mock()
        camera.grab.return_value = np.full((120, 240, 4), 100, dtype=np.uint8)
        cap.module = S(__factory=S(outputs=[[output]]), create=Mock(return_value=camera))
        cap._region = Mock(return_value=(-1800, 120, -600, 720))
        return cap, camera

    def test_dxgi_crops_negative_monitor_coordinates_and_samples_bgra(self):
        cap, camera = self.capture()
        pixels = cap.read(9)
        self.assertEqual(len(pixels), 96 * 54 * 4)
        self.assertEqual(cap.backend, 'DXGI')
        camera.grab.assert_called_once_with(region=(120, 120, 1320, 720), new_frame_only=True)
        cap.gdi.read.assert_not_called()
        cap.close()

    def test_dxgi_error_releases_camera_and_falls_back_without_crash(self):
        cap, camera = self.capture()
        camera.grab.side_effect = OSError('DXGI access lost')
        self.assertEqual(cap.read(9), b'gdi')
        self.assertEqual(cap.backend, 'GDI 回退')
        camera.release.assert_called_once()
        self.assertEqual(cap.cameras, {})

    def test_unavailable_region_never_reads_desktop(self):
        cap, camera = self.capture()
        cap._region.return_value = None
        self.assertIsNone(cap.read(9))
        camera.grab.assert_not_called()
        cap.gdi.read.assert_not_called()


class AsyncReturnTests(unittest.TestCase):
    def showing(self):
        w = FakeWindows(True)
        p = StudyWindow(w, target_kind="anki")
        self.assertTrue(p.show(3, 9, 'fullscreen'))
        return w, p

    def test_return_minimizes_anki_and_confirms_game_focus(self):
        w, p = self.showing()
        self.assertEqual(p.request_return(0), 'returned')
        self.assertIn(('minimize', 3), w.operations)
        self.assertEqual(w.front, 'game')
        self.assertIn(3, w.iconic)
        self.assertFalse(w.pinned)
        self.assertFalse(p.owned)

    def test_async_restore_retains_record_until_game_ready(self):
        w, p = self.showing()
        original, clock = w.restore, [0]
        def delayed(hwnd, activate=True):
            return False if hwnd == 9 and activate and clock[0] < 0.7 else original(hwnd, activate)
        w.restore = delayed
        self.assertEqual(p.request_return(0), 'pending')
        for instant in [0.2, 0.4, 0.6]:
            clock[0] = instant
            self.assertEqual(p.poll_return(instant), 'pending')
            self.assertTrue(p.owned)
            self.assertEqual(p.game, 9)
        clock[0] = 0.8
        self.assertEqual(p.poll_return(0.8), 'returned')
        self.assertFalse(p.owned)
        self.assertFalse(p.returning)

    def test_native_success_does_not_count_before_real_foreground(self):
        w, p = self.showing()
        w.restore = Mock(return_value=True)
        self.assertEqual(p.request_return(0), 'pending')
        self.assertTrue(p.owned)
        self.assertEqual(p.poll_return(0.3), 'pending')
        w.front = 'game'
        w.iconic.discard(9)
        self.assertEqual(p.poll_return(0.5), 'returned')

    def test_duplicate_return_request_keeps_original_deadline(self):
        w, p = self.showing()
        w.restore = Mock(return_value=False)
        self.assertEqual(p.request_return(0), 'pending')
        self.assertEqual(p.request_return(2.5), 'pending')
        self.assertEqual(p.poll_return(3.1), 'failed')
        self.assertFalse(p.owned)
        self.assertFalse(p.returning)
        self.assertEqual(p.poll_return(4), 'idle')

    def test_revive_return_works_from_other_application(self):
        w, p = self.showing()
        w.front = 'other'
        w.activations.clear()
        self.assertEqual(p.request_return(0), 'returned')
        self.assertEqual(w.front, 'game')
        self.assertIn(9, w.activations)

    def test_other_application_input_does_not_drop_return_task(self):
        w, p = self.showing()
        w.restore = Mock(return_value=False)
        p.request_return(0)
        w.front = 'other'
        self.assertEqual(p.poll_return(0.1), 'pending')
        w.input_stamp, w.other_hwnd = 1, 200
        self.assertEqual(p.poll_return(0.3), 'pending')
        self.assertTrue(p.returning)
        self.assertEqual(p.poll_return(3.1), 'failed')
        self.assertFalse(p.returning)
        self.assertFalse(w.pinned)
        self.assertEqual(w.front, 'other')

    def test_other_window_exposed_by_minimize_does_not_cancel_transition(self):
        w, p = self.showing()
        original = w.restore
        w.restore = Mock(return_value=False)
        p.request_return(0)
        w.front = 'other'
        w.restore = original
        self.assertEqual(p.poll_return(0.2), 'returned')
        self.assertEqual(w.front, 'game')

    def test_missing_saved_handle_is_reacquired(self):
        w, p = self.showing()
        original_kind = w.kind
        w.kind = lambda hwnd: 'game' if hwnd == 10 else 'other' if hwnd == 9 else original_kind(hwnd)
        w.find = lambda kind: 10 if kind == 'game' else 3
        w.current_game = lambda: 10 if w.front == 'game' else None
        self.assertEqual(p.request_return(0), 'returned')
        self.assertIn(10, w.activations)

    def test_missing_game_finishes_cleanly(self):
        w, p = self.showing()
        original_kind = w.kind
        w.kind = lambda hwnd: 'other' if hwnd == 9 else original_kind(hwnd)
        w.find = lambda kind: None if kind == 'game' else 3
        self.assertEqual(p.request_return(0), 'failed')
        self.assertFalse(p.owned)
        self.assertFalse(w.pinned)

    def test_new_show_is_blocked_while_game_is_restoring(self):
        w, p = self.showing()
        w.restore = Mock(return_value=False)
        p.request_return(0)
        self.assertFalse(p.show(3, 9, manual=True))
        self.assertTrue(p.returning)

    def test_cleanup_cancels_pending_return(self):
        w, p = self.showing()
        w.restore = Mock(return_value=False)
        p.request_return(0)
        p.release(activate=False)
        self.assertFalse(p.returning)
        self.assertFalse(p.owned)


class NativeReturnTests(unittest.TestCase):
    def test_still_minimized_window_does_not_receive_focus_attempt(self):
        w = Windows.__new__(Windows)
        w.u = Mock()
        w.is_iconic = Mock(return_value=True)
        w.activate = Mock(return_value=True)
        with patch('lol_words.system.native.time.monotonic', return_value=10):
            self.assertFalse(w.restore(9))
        w.u.ShowWindowAsync.assert_called_once_with(9, 9)
        w.activate.assert_not_called()

    def test_restore_requests_are_throttled_until_unminimized(self):
        w = Windows.__new__(Windows)
        w.u = Mock()
        w.is_iconic = Mock(return_value=True)
        w.activate = Mock(return_value=True)
        with patch('lol_words.system.native.time.monotonic', return_value=10):
            w.restore(9)
        with patch('lol_words.system.native.time.monotonic', return_value=10.1):
            w.restore(9)
        self.assertEqual(w.u.ShowWindowAsync.call_count, 1)
        w.is_iconic.return_value = False
        self.assertTrue(w.restore(9))
        w.activate.assert_called_once_with(9)

    def test_anki_dialog_without_anki_title_is_still_anki(self):
        w = Windows.__new__(Windows)
        w.process = Mock(return_value='anki.exe')
        w.title = Mock(return_value='编辑当前卡片')
        self.assertEqual(w.kind(123), 'anki')


class BorderlessReturnTests(unittest.TestCase):
    def showing(self):
        w, p = FakeWindows(True), None
        p = StudyWindow(w, target_kind="anki")
        self.assertTrue(p.show(3, 9, 'borderless'))
        return w, p

    def test_game_focus_alone_does_not_cancel_while_anki_visible(self):
        w, p = self.showing()
        w.front = 'game'
        self.assertEqual(p.observe_foreground(1), 'game_under_target')
        self.assertTrue(p.owned)
        self.assertTrue(w.pinned)
        s = DeathSession(3, delay=0)
        s.update(Snapshot(False, 0), 'game')
        s.update(Snapshot(True, 20), 'game', True)
        s.confirm_study(True)
        self.assertIsNone(s.update(Snapshot(True, 10), 'game', manual_return_confirmed=False))
        self.assertTrue(s.engaged)
        self.assertEqual(s.update(Snapshot(True, 2), 'game', manual_return_confirmed=False), 'return')

    def test_continuous_pin_never_repeatedly_activates_window(self):
        w, p = self.showing()
        w.front = 'game'
        w.activate = Mock(return_value=False)
        for tick in range(20):
            self.assertEqual(p.observe_foreground(tick * 0.2), 'game_under_target')
        self.assertEqual(w.activate.call_count, 0)
        self.assertTrue(p.owned)

    def test_user_minimize_retains_revive_return_context(self):
        w, p = self.showing()
        w.iconic.add(3)
        w.front = 'game'
        self.assertEqual(p.observe_foreground(1), 'target_hidden')
        self.assertTrue(p.owned)
        self.assertEqual(p.request_return(2), 'returned')
        self.assertFalse(w.pinned)

    def test_visiting_other_app_keeps_pin_and_return_context(self):
        w, p = self.showing()
        w.front = 'other'
        self.assertEqual(p.observe_foreground(1), 'other')
        self.assertTrue(w.pinned)
        self.assertTrue(p.owned)
        w.front = 'anki'
        self.assertEqual(p.observe_foreground(2), 'anki')
        self.assertTrue(w.pinned)
        self.assertEqual(p.request_return(3), 'returned')

    def test_already_foreground_game_does_not_skip_minimizing_anki(self):
        w, p = self.showing()
        w.front = 'game'
        self.assertEqual(p.request_return(1), 'returned')
        self.assertIn(('minimize', 3), w.operations)
        self.assertTrue(w.is_iconic(3))
        self.assertFalse(w.pinned)

    def test_first_exposed_background_window_with_input_does_not_cancel(self):
        w, p = self.showing()
        original = w.restore
        w.restore = Mock(return_value=False)
        self.assertEqual(p.request_return(1), 'pending')
        w.front, w.input_stamp = 'other', 5
        self.assertEqual(p.poll_return(1.2), 'pending')
        w.restore = original
        self.assertEqual(p.poll_return(1.4), 'returned')

    def test_asynchronous_anki_hide_must_finish_before_success(self):
        w, p = self.showing()
        w.front = 'game'
        calls = []
        def delayed_hide(hwnd):
            calls.append(hwnd)
            return True
        w.minimize = delayed_hide
        self.assertEqual(p.request_return(1), 'pending')
        self.assertEqual(p.poll_return(1.2), 'pending')
        self.assertTrue(p.owned)
        self.assertEqual(calls, [3])
        w.iconic.add(3)
        self.assertEqual(p.poll_return(1.3), 'returned')

    def test_game_foreground_but_visible_anki_times_out_without_false_success(self):
        w, p = self.showing()
        w.front = 'game'
        w.minimize = Mock(return_value=False)
        self.assertEqual(p.request_return(1), 'pending')
        self.assertEqual(p.poll_return(4.1), 'failed')
        self.assertIn('目标窗口遮挡已清除=False', p.last_return_reason)

    def test_preserve_original_pin_still_requires_hidden_anki(self):
        w = FakeWindows(True)
        w.pinned = True
        p = StudyWindow(w, target_kind="anki")
        p.show(3, 9, 'borderless')
        w.front = 'game'
        self.assertEqual(p.request_return(1), 'returned')
        self.assertTrue(w.pinned)
        self.assertTrue(w.is_iconic(3))

    def test_mode_change_is_read_without_restarting_monitor(self):
        app = App.__new__(App)
        app.config = {'game_display_mode': 'borderless'}
        self.assertEqual(app.current_display_mode('fullscreen'), 'borderless')
        app.config['game_display_mode'] = 'fullscreen'
        self.assertEqual(app.current_display_mode('borderless'), 'fullscreen')
        app.config['game_display_mode'] = 'invalid'
        self.assertEqual(app.current_display_mode('auto'), 'auto')

    def test_changing_pin_does_not_reshow_a_hidden_window(self):
        w = Windows.__new__(Windows)
        w.u = Mock()
        w.u.IsWindowVisible.return_value = False
        w.set_topmost(3, False)
        flags = w.u.SetWindowPos.call_args.args[-1]
        self.assertEqual(flags & 0x0040, 0)

    def test_visible_window_pin_preserves_original_show_flags(self):
        w = Windows.__new__(Windows)
        w.u = Mock()
        w.u.IsWindowVisible.return_value = True
        w.set_topmost(3, True)
        flags = w.u.SetWindowPos.call_args.args[-1]
        self.assertEqual(flags & 0x0040, 0x0040)


class GuiStartupTests(unittest.TestCase):
    def test_opening_tool_automatically_starts_monitor_without_button(self):
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        windows = FakeWindows()
        windows.stop_pressed = lambda: False
        try:
            with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder, \
                 patch('lol_words.ui.legacy_app.ROOT', Path(folder)), patch('lol_words.ui.legacy_app.Windows', return_value=windows), \
                 patch.object(App, 'rediscover'), patch.object(App, 'append'), \
                 patch.object(App, 'monitor') as monitor:
                instance = App(root)
                root.after(650, root.quit)
                root.mainloop()
                if instance.worker:
                    instance.worker.join(timeout=1)
                self.assertEqual(monitor.call_count, 1)
                self.assertTrue((Path(folder) / 'config.json').is_file())
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main(verbosity=1)
