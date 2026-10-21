"""模式、适配器、惩罚的模拟回归；不访问真实下载入口。"""
import copy
import json
import tempfile
import threading
import io
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import test_app as regression
from lol_words.targets.adapters import DesktopAdapter, TargetManager, AdapterRegistry, TargetWindows
from lol_words.targets.browser_target import BrowserAdapter
from lol_words.game.core import Snapshot, own_snapshot
from lol_words.modes.mode_config import migrate_config, validate_config, selected_spec, GENSHIN_HOME
from lol_words.modes.mode_runtime import ModeRuntime
from lol_words.modes.official_download import OfficialDownload, INSPECT_PC, click_expression
from lol_words.modes.penalty import DeathLedger


class RetiredSettingsTests(unittest.TestCase):
    def test_removed_client_automation_settings_do_not_survive_migration(self):
        saved={'death_switch_delay':5.25,'return_before_seconds':1.75,'my_setting':9,
            'lobby_auto_accept':True,'lobby_auto_return':True,'lobby_auto_promote':True}
        config=migrate_config(saved)
        self.assertFalse(any(key.startswith('lobby_') for key in config))
        self.assertEqual(config['death_switch_delay'],5.25);self.assertEqual(config['return_before_seconds'],1.75)
        self.assertEqual(config['my_setting'],9);self.assertTrue(saved['lobby_auto_accept'])


class TempCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)


def desktop_spec(id='custom', path='E:/fixtures/words.exe'):
    return dict(id=id, name=id, adapter='desktop', command=[path], process_path=path,
                title_contains='', window_class='', pin=True)


class ConfigTests(unittest.TestCase):
    def test_old_configuration_preserves_parameters_and_migrates_anki(self):
        old = dict(anki_path='E:/Anki/anki.exe', player_id='name#tag', death_switch_delay=5,
                   return_before_seconds=2, persistent_anki_topmost=False, gray_threshold=.91,
                   preserve_existing_anki=True, unknown_user_setting='keep')
        new = migrate_config(old)
        for key,value in old.items(): self.assertEqual(new[key],value)
        self.assertEqual(new['targets'][0]['command'],['E:/Anki/anki.exe'])
        self.assertFalse(new['targets'][0]['pin'])
        self.assertEqual(new['mode'],'study')
        self.assertNotIn('death_penalty_enabled',new)

    def test_migration_is_idempotent(self):
        config=migrate_config({'death_switch_delay':5})
        self.assertEqual(migrate_config(config),config)

    def test_removed_switch_is_ignored_in_old_configs(self):
        for value in [False,True,'old-value']:
            config=migrate_config({'death_penalty_enabled':value})
            self.assertNotIn('death_penalty_enabled',config)

    def test_custom_executable_and_window_conditions_are_configurable(self):
        config=migrate_config();config['targets'].append(desktop_spec());config['study_target']='custom'
        validate_config(config)
        self.assertEqual(selected_spec(config)['adapter'],'desktop')

    def test_shell_command_string_is_rejected(self):
        config=migrate_config();target=desktop_spec();target['command']='words.exe & calc.exe';config['targets'].append(target)
        with self.assertRaises(ValueError):validate_config(config)

    def test_relative_process_path_is_rejected(self):
        config=migrate_config();config['targets'].append(desktop_spec(path='words.exe'))
        with self.assertRaises(ValueError):validate_config(config)

    def test_web_target_can_be_added_without_claiming_desktop_installation(self):
        config=migrate_config();config['targets'].append(dict(id='web',name='官方网页',adapter='web',url='https://bbdc.cn/lexis/login',pin=True))
        config['study_target']='web';validate_config(config)
        self.assertEqual(selected_spec(config)['url'],'https://bbdc.cn/lexis/login')

    def test_web_credentials_and_file_urls_rejected(self):
        for url in ['https://name:password@example.com/','file:///C:/private','http://example.com/']:
            config=migrate_config();config['targets'].append(dict(id='web',name='web',adapter='web',url=url))
            with self.subTest(url=url),self.assertRaises(ValueError):validate_config(config)

    def test_both_entertainment_targets_are_official_https(self):
        config=migrate_config();config['mode']='entertainment'
        self.assertEqual(selected_spec(config)['url'],'https://www.bilibili.com/')
        config['entertainment_site']='douyin'
        self.assertEqual(selected_spec(config)['url'],'https://www.douyin.com/')

    def test_names_and_ids_must_be_unique(self):
        config=migrate_config();target=desktop_spec();target['name']='Anki';config['targets'].append(target)
        with self.assertRaises(ValueError):validate_config(config)


class AdapterTests(TempCase):
    def setUp(self):
        super().setUp()
        self.w=Mock()
        self.spec=desktop_spec()
        self.row=dict(hwnd=3,pid=30,created=100,path='E:/fixtures/words.exe',title='学习主窗口',window_class='WordsWindow')
        self.w.window_inventory.return_value=[dict(self.row)]
        self.w.processes.return_value=[(30,'words.exe')]
        self.w.process_identity.return_value={k:self.row[k] for k in ['pid','created','path']}
        self.a=DesktopAdapter(self.spec,self.w,self.root)

    def test_matching_full_path_finds_only_correct_window(self):
        other=dict(self.row,hwnd=4,pid=31,path='E:/other/words.exe')
        self.w.window_inventory.return_value=[other,self.row]
        self.assertEqual(self.a.find(),3)

    def test_name_substring_does_not_match_process(self):
        self.w.window_inventory.return_value=[dict(self.row,path='E:/fixtures/words-helper.exe')]
        self.assertIsNone(self.a.find())

    def test_two_initial_matching_windows_refuse_ambiguous_choice(self):
        self.w.window_inventory.return_value=[self.row,dict(self.row,hwnd=4)]
        self.assertIsNone(self.a.find())
        self.assertIn('2 个主窗口',self.a.reason)

    def test_title_and_class_can_disambiguate(self):
        self.a.spec.update(title_contains='学习主',window_class='WordsWindow')
        self.w.window_inventory.return_value=[self.row,dict(self.row,hwnd=4,title='编辑器')]
        self.assertEqual(self.a.find(),3)

    def test_sticky_selection_does_not_jump_to_new_window(self):
        self.assertEqual(self.a.find(),3)

    def test_closed_original_window_is_not_replaced_by_other_window(self):
        self.assertEqual(self.a.find(),3)
        self.w.window_inventory.return_value=[dict(self.row,hwnd=4)]
        self.assertIsNone(self.a.find())
        self.assertIn('不自动改选',self.a.reason)
        self.w.window_inventory.return_value=[dict(self.row,hwnd=4),self.row]
        self.assertEqual(self.a.find(),3)

    def test_recycled_pid_does_not_preserve_cached_identity(self):
        self.a.find();self.w.window_inventory.return_value=[]
        self.assertIsNone(self.a.find())
        self.assertTrue(self.a.bound)

    def test_running_checks_full_process_path_not_only_name(self):
        self.w.process_identity.return_value=dict(pid=30,created=100,path='E:/different/words.exe')
        self.assertFalse(self.a.running())

    def test_missing_install_fails_once_without_shell(self):
        self.a.find=Mock(return_value=None);self.a.running=Mock(return_value=False)
        logs=[];m=TargetManager(self.a,logs.append)
        for _ in range(10):m.ensure()
        self.assertEqual(m.state,'failed')
        self.assertLess(len(logs),6)

    def test_manual_close_is_not_automatically_reopened(self):
        self.a.start=Mock();m=TargetManager(self.a,Mock());self.assertEqual(m.poll(),3)
        self.w.window_inventory.return_value=[];self.w.processes.return_value=[]
        for _ in range(10):m.ensure()
        self.a.start.assert_not_called()

    def test_manual_reopen_is_explicit_and_deduplicated(self):
        self.w.window_inventory.return_value=[];self.w.processes.return_value=[]
        self.a.start=Mock();m=TargetManager(self.a,Mock())
        for _ in range(10):m.ensure(manual=True)
        self.a.start.assert_called_once()

    def test_registry_preserves_start_budget_when_config_changes(self):
        registry=AdapterRegistry(self.w,self.root,Mock());m=registry.get(self.spec);m.attempted=True
        changed=dict(self.spec,title_contains='other')
        updated=registry.get(changed)
        self.assertIsNot(updated,m)
        self.assertTrue(updated.attempted)
        self.assertEqual(updated.adapter.spec['title_contains'],'other')
        self.assertEqual(m.adapter.spec['title_contains'],'')

    def test_browser_recycled_pid_is_not_accepted(self):
        a=BrowserAdapter(dict(id='b',name='b',adapter='web',url='https://www.bilibili.com/'),self.w,self.root)
        expected=dict(pid=30,path='E:/browser.exe',created=10)
        a.owner.transact(lambda d:d.update(identity=expected))
        self.w.process_identity.return_value=dict(expected,created=11)
        self.assertFalse(a.running())

    def test_browser_only_matches_its_recorded_process(self):
        a=BrowserAdapter(dict(id='b',name='b',adapter='web',url='https://www.bilibili.com/'),self.w,self.root)
        expected=dict(pid=30,path='E:/browser.exe',created=10)
        a.owner.transact(lambda d:d.update(identity=expected))
        self.w.process_identity.return_value=expected
        self.assertTrue(a.matches(dict(expected,hwnd=3,title='b',window_class='Chrome_WidgetWin_1')))
        self.assertFalse(a.matches(dict(expected,pid=31,hwnd=4,title='b',window_class='Chrome_WidgetWin_1')))

    def test_browser_start_uses_dedicated_profile_and_never_user_tabs(self):
        a=BrowserAdapter(dict(id='b',name='b',adapter='web',url='https://www.bilibili.com/'),self.w,self.root)
        identity=dict(pid=99,path='C:/Browser/msedge.exe',created=10)
        self.w.process_identity.return_value=identity
        with patch('lol_words.targets.browser_target.find_browser',return_value='C:/Browser/msedge.exe'),patch('lol_words.targets.browser_target.subprocess.Popen',return_value=Mock(pid=99)) as launch:
            a.start();a.start()
        launch.assert_called_once()
        args=launch.call_args.args[0]
        self.assertIn('--app=https://www.bilibili.com/',args)
        self.assertIn('--remote-debugging-port=0',args)
        self.assertTrue(any(x.startswith('--user-data-dir='+str(self.root)) for x in args))

    def test_browser_page_selection_uses_own_port_and_exact_target_id(self):
        a=BrowserAdapter(dict(id='b',name='b',adapter='web',url='https://www.bilibili.com/'),self.w,self.root)
        identity=dict(pid=99,path='C:/Browser/msedge.exe',created=10)
        a.owner.transact(lambda d:d.update(identity=identity,target_id='owned'))
        self.w.process_identity.return_value=identity
        (a.profile/'DevToolsActivePort').write_text('12345\n/devtools/browser/fixture',encoding='utf-8')
        pages=[dict(id='unrelated',type='page',url='https://www.bilibili.com/',webSocketDebuggerUrl='ws://127.0.0.1:12345/devtools/page/unrelated'),
               dict(id='owned',type='page',url='https://www.bilibili.com/',webSocketDebuggerUrl='ws://127.0.0.1:12345/devtools/page/owned')]
        opener=Mock();opener.open.return_value=io.BytesIO(json.dumps(pages).encode())
        with patch('lol_words.targets.browser_target.urllib.request.build_opener',return_value=opener):
            self.assertEqual(a.page()['id'],'owned')
        self.assertEqual(opener.open.call_args.args[0],'http://127.0.0.1:12345/json/list')

    def test_browser_rejects_debug_endpoint_outside_own_loopback_port(self):
        a=BrowserAdapter(dict(id='b',name='b',adapter='web',url='https://www.bilibili.com/'),self.w,self.root)
        identity=dict(pid=99,path='C:/Browser/msedge.exe',created=10)
        a.owner.transact(lambda d:d.update(identity=identity,target_id='owned'))
        self.w.process_identity.return_value=identity
        (a.profile/'DevToolsActivePort').write_text('12345\n/devtools/browser/fixture',encoding='utf-8')
        pages=[dict(id='owned',type='page',url='https://www.bilibili.com/',webSocketDebuggerUrl='ws://example.com:12345/devtools/page/owned')]
        opener=Mock();opener.open.return_value=io.BytesIO(json.dumps(pages).encode())
        with patch('lol_words.targets.browser_target.urllib.request.build_opener',return_value=opener),self.assertRaises(ValueError):a.page()

    def test_easter_egg_navigation_forces_owned_page_back_to_official_home(self):
        adapter=BrowserAdapter(dict(id='genshin-penalty',name='原神官网',adapter='web',url=GENSHIN_HOME),self.w,self.root)
        adapter.page=Mock(return_value={'url':'https://example.com/other-owned-page'})
        adapter.command=Mock(return_value={'frameId':'owned-frame'})
        adapter.navigate_home()
        adapter.command.assert_called_once_with('Page.navigate',{'url':GENSHIN_HOME})

    def test_official_navigation_error_does_not_report_success(self):
        adapter=BrowserAdapter(dict(id='genshin-penalty',name='原神官网',adapter='web',url=GENSHIN_HOME),self.w,self.root)
        adapter.page=Mock(return_value={'url':'https://example.com/other-owned-page'})
        adapter.command=Mock(return_value={'errorText':'net::ERR_FAILED'})
        with self.assertRaises(OSError):adapter.navigate_home()

    def test_official_home_already_open_does_not_reload_or_create_a_tab(self):
        adapter=BrowserAdapter(dict(id='genshin-penalty',name='原神官网',adapter='web',url=GENSHIN_HOME),self.w,self.root)
        adapter.page=Mock(return_value={'url':GENSHIN_HOME})
        adapter.command=Mock()
        adapter.navigate_home()
        adapter.command.assert_not_called()

    def test_failed_start_has_no_automatic_retry(self):
        self.w.window_inventory.return_value=[];self.w.processes.return_value=[]
        self.a.start=Mock(side_effect=OSError('not installed'));m=TargetManager(self.a,Mock())
        for _ in range(10):m.ensure()
        self.a.start.assert_called_once();self.assertEqual(m.state,'failed')


class LedgerTests(TempCase):
    def setUp(self):
        super().setUp();self.path=self.root/'penalty.json';self.logs=[];self.ledger=DeathLedger(self.path,self.logs.append)

    def observe(self,count,now=100,player='本人#123',process='game:1'):
        self.ledger.observe(player,now,process,count,1000+now)

    def test_twentieth_death_triggers_once_in_current_match(self):
        self.observe(19);self.assertFalse(self.ledger.eligible('entertainment'));self.assertFalse(self.ledger.claim())
        self.observe(20,101);self.assertTrue(self.ledger.eligible('entertainment'))
        self.assertTrue(self.ledger.claim());self.assertFalse(self.ledger.claim())
        self.observe(21,102);self.assertFalse(self.ledger.eligible('entertainment'))

    def test_count_jump_over_threshold_triggers(self):
        self.observe(19);self.observe(23,110)
        self.assertTrue(self.ledger.eligible('entertainment'))

    def test_repeated_polls_never_increment_counter(self):
        for now in range(100,120):self.observe(19,now)
        self.assertEqual(self.ledger.current['deaths'],19)
        self.assertEqual(sum('累计死亡' in s for s in self.logs),1)

    def test_reconnect_with_lower_score_never_rearms(self):
        self.observe(21);self.ledger.claim();self.observe(19,101);self.observe(21,102)
        self.assertEqual(self.ledger.current['deaths'],21)
        self.assertFalse(self.ledger.eligible('entertainment'))

    def test_same_match_tool_restart_preserves_claim(self):
        self.observe(21);self.ledger.claim()
        other=DeathLedger(self.path);other.observe('本人#123',120,'game:1',21,1120)
        self.assertTrue(other.current['claimed'])
        self.assertFalse(other.eligible('entertainment'))

    def test_next_match_resets_count_and_trigger(self):
        self.observe(21);self.ledger.claim();self.ledger.end()
        self.ledger.observe('本人#123',10,'game:2',0,1200)
        self.assertEqual(self.ledger.current['deaths'],0)
        self.assertFalse(self.ledger.current['claimed'])

    def test_reused_game_process_with_game_time_reset_is_new_match(self):
        self.observe(21,1000);self.ledger.claim()
        self.ledger.observe('本人#123',10,'game:1',0,2100)
        self.assertFalse(self.ledger.current['claimed'])
        self.assertEqual(self.ledger.current['deaths'],0)

    def test_different_player_does_not_inherit_deaths(self):
        self.observe(21);self.ledger.claim();self.observe(0,101,player='另一人#456')
        self.assertEqual(self.ledger.current['deaths'],0)

    def test_learning_mode_never_triggers(self):
        self.observe(30)
        self.assertFalse(self.ledger.eligible('study'))

    def test_unreliable_missing_count_is_not_guessed(self):
        for value in [None,True,'21',-1]:
            self.observe(value)
            self.assertFalse(self.ledger.eligible('entertainment'))

    def test_persisted_record_contains_no_plain_player_identity(self):
        self.observe(21)
        raw=self.path.read_text(encoding='utf-8')
        self.assertNotIn('本人#123',raw)
        self.assertNotIn('game:1',raw)

    def test_two_instances_claim_at_most_once(self):
        self.observe(21);other=DeathLedger(self.path);other.observe('本人#123',100,'game:1',21,1100)
        results=[]
        threads=[threading.Thread(target=lambda ledger=ledger:results.append(ledger.claim())) for ledger in [self.ledger,other]]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertEqual(sorted(results),[False,True])

    def test_corrupt_record_disables_automatic_download(self):
        self.path.write_text('{bad json',encoding='utf-8');self.observe(21)
        self.assertTrue(self.ledger.failed)
        self.assertFalse(self.ledger.claim())

    def test_wrong_player_scores_not_used(self):
        payload={'activePlayer':{'riotId':'本人#1'},'allPlayers':[
            {'riotId':'队友#2','isDead':True,'respawnTimer':21,'scores':{'deaths':25}},
            {'riotId':'本人#1','isDead':False,'respawnTimer':0,'scores':{'deaths':19}}]}
        _,snapshot=own_snapshot(payload)
        self.assertEqual(snapshot.deaths,19)


class SimWindows(regression.FakeWindows):
    def __init__(self):
        super().__init__(False);self.rows={};self.front_hwnd=9;self.pins={}
    def add(self,hwnd,path):self.rows[hwnd]=dict(hwnd=hwnd,pid=hwnd+100,created=10,path=path,title='study',window_class='Test')
    def window_inventory(self):return list(self.rows.values())
    def foreground_handle(self):return self.front_hwnd
    def foreground(self):return 'game' if self.front_hwnd==9 else 'other'
    def current_game(self):return 9 if self.front_hwnd==9 else None
    def kind(self,hwnd):return 'game' if hwnd==9 else 'other'
    def find(self,kind):return 9 if kind=='game' else None
    def activate(self,hwnd):self.activations.append(hwnd);self.front_hwnd=hwnd;self.iconic.discard(hwnd);return True
    def restore(self,hwnd,activate=True):self.iconic.discard(hwnd);return self.activate(hwnd) if activate else True
    def is_topmost(self,hwnd):return self.pins.get(hwnd,False)
    def set_topmost(self,hwnd,enabled):self.pins[hwnd]=enabled;return True
    def is_visible(self,hwnd):return hwnd in self.rows or hwnd==9
    def minimize(self,hwnd):self.iconic.add(hwnd);self.operations.append(('minimize',hwnd));self.front_hwnd=0 if self.front_hwnd==hwnd else self.front_hwnd;return True
    def desktop_foreground(self):return self.front_hwnd==0


class SimRegistry:
    def __init__(self,w):self.w=w;self.managers={};self.starts=[]
    def get(self,spec):
        key=spec['id']
        if key not in self.managers:
            w,registry=self.w,self
            hwnd={'anki':3,'custom':4,'entertainment-bilibili':5,'entertainment-douyin':7,'genshin-penalty':6}.get(key,8)
            class Adapter:
                reason='模拟唯一窗口'
                def __init__(self):self.spec=spec
                def find(self):return hwnd if hwnd in w.rows else None
                def running(self):return hwnd in w.rows
                def start(self):registry.starts.append(key);w.add(hwnd,'E:/sim/'+key+'.exe')
            self.managers[key]=TargetManager(Adapter(),lambda _:None)
        return self.managers[key]


class ModeTests(TempCase):
    def setUp(self):
        super().setUp();self.w=SimWindows();self.registry=SimRegistry(self.w)
        self.config=migrate_config();self.config.update(mode='entertainment')
        self.ledger=DeathLedger(self.root/'ledger.json')
        self.logs=[];self.results=[]
        self.runtime=ModeRuntime(self.w,self.root,self.config,self.ledger,self.logs.append,lambda p,r:self.results.append(r),self.registry)
        self.download=Mock();self.runtime.download_factory=Mock(return_value=self.download)
        self.capture=Mock(backend='模拟',detail='')
        self.step(0,dead=False,count=19)

    def step(self,now,dead=True,count=20,gray=False,timer=30):
        self.capture.read.return_value=bytes([100,100,100,0] if gray else [40,150,210,0])*100
        self.runtime.step('本人#123',Snapshot(dead,timer,'Karthus',count),now,100+now,'game:1',1100+now,self.capture)

    def study(self,count=20):
        self.step(1,count=count,gray=True);self.step(2,count=count,gray=True);self.step(4,count=count,gray=True)

    def test_twentieth_death_still_waits_delay_and_operable_phase(self):
        for now in range(1,12):self.step(now,gray=False)
        self.assertFalse(self.ledger.current['claimed']);self.assertEqual(self.registry.starts,[])
        self.step(12,gray=True);self.step(12.2,gray=True)
        self.assertEqual(self.registry.starts,['genshin-penalty'])
        self.download.start.assert_called_once()
        self.assertEqual(self.runtime.manager.adapter.spec['url'],GENSHIN_HOME)

    def test_twentieth_death_only_triggers_one_easter_egg(self):
        self.study(count=20)
        self.assertEqual(self.registry.starts,['genshin-penalty'])
        self.assertTrue(self.ledger.current['claimed'])
        for now in range(5,10):self.step(now,count=20,gray=True)
        self.download.start.assert_called_once()

    def test_easter_egg_reopens_previously_closed_official_window_once(self):
        manager=self.registry.get(dict(id='genshin-penalty',name='原神官网',adapter='web',url=GENSHIN_HOME,pin=True))
        manager.attempted=True
        self.study(count=21)
        self.assertEqual(self.registry.starts,['genshin-penalty'])
        for now in range(5,10):self.step(now,count=21,gray=True)
        self.assertEqual(self.registry.starts,['genshin-penalty'])
        self.download.start.assert_called_once()

    def test_threshold_seen_alive_waits_next_allowed_death(self):
        self.step(1,dead=False,count=21);self.assertEqual(self.registry.starts,[])
        self.step(2,gray=True);self.step(3,gray=True);self.step(5,gray=True)
        self.assertEqual(self.registry.starts,['genshin-penalty'])

    def test_penalty_page_wins_over_normal_entertainment(self):
        self.study()
        self.assertEqual(self.registry.starts,['genshin-penalty'])
        self.assertEqual(self.w.front_hwnd,6)
        self.step(6,timer=1)
        self.assertEqual(self.w.front_hwnd,9)
        self.assertFalse(self.w.is_topmost(6))

    def test_repeated_death_polls_do_not_download_again(self):
        self.study()
        for now in range(5,15):self.step(now,gray=True)
        self.download.start.assert_called_once()
        self.assertEqual(self.registry.starts,['genshin-penalty'])

    def test_next_death_after_penalty_uses_entertainment_and_reuses_later(self):
        self.study();self.step(5,dead=False,count=21)
        self.step(6,count=21,gray=True);self.step(7,count=21,gray=True);self.step(9,count=21,gray=True)
        self.assertEqual(self.registry.starts,['genshin-penalty','entertainment-bilibili'])
        self.step(10,dead=False,count=21)
        self.step(11,count=22,gray=True);self.step(12,count=22,gray=True);self.step(14,count=22,gray=True)
        self.assertEqual(self.registry.starts,['genshin-penalty','entertainment-bilibili'])

    def test_learning_mode_never_triggers_penalty(self):
        self.config['mode']='study';self.runtime.switch(self.config);self.step(.5,dead=False)
        self.study()
        self.assertEqual(self.registry.starts,['anki'])
        self.assertFalse(self.ledger.current['claimed'])
        self.download.start.assert_not_called()

    def test_legacy_false_switch_does_not_disable_entertainment_event(self):
        self.config=migrate_config({**self.config,'death_penalty_enabled':False})
        self.assertNotIn('death_penalty_enabled',self.config)
        self.runtime.switch(self.config);self.step(.5,dead=False)
        self.study()
        self.assertEqual(self.registry.starts,['genshin-penalty'])

    def test_mode_switch_cleans_pin_and_cancels_downloader(self):
        self.study();self.assertTrue(self.w.is_topmost(6))
        self.config['mode']='study';self.runtime.switch(self.config)
        self.assertFalse(self.w.is_topmost(6));self.assertFalse(self.runtime.presentation.owned)
        self.download.cancel.assert_called()
        self.step(8,gray=True);self.assertEqual(self.registry.starts,['genshin-penalty'])

    def test_switching_learning_targets_waits_next_death(self):
        self.config['mode']='study';self.runtime.switch(self.config);self.step(.5,dead=False)
        self.study(count=19)
        self.config['targets'].append(desktop_spec());self.config['study_target']='custom'
        self.runtime.switch(self.config);self.step(5,count=19,gray=True)
        self.assertEqual(self.registry.starts,['anki'])
        self.w.front_hwnd=9
        self.step(6,dead=False,count=19);self.step(7,count=19,gray=True);self.step(8,count=19,gray=True);self.step(10,count=19,gray=True)
        self.assertEqual(self.registry.starts,['anki','custom'])

    def test_same_match_restart_and_mode_switch_do_not_repeat_penalty(self):
        self.study();self.runtime.cleanup()
        ledger=DeathLedger(self.root/'ledger.json')
        runtime=ModeRuntime(self.w,self.root,self.config,ledger,self.logs.append,registry=self.registry)
        runtime.download_factory=Mock()
        for now,dead in [(5,False),(6,True),(7,True),(9,True)]:
            self.capture.read.return_value=bytes([100,100,100,0] if dead else [40,150,210,0])*100
            runtime.step('本人#123',Snapshot(dead,30,'KogMaw',21),now,100+now,'game:1',1100+now,self.capture)
        runtime.download_factory.assert_not_called()
        self.assertEqual(self.registry.starts.count('genshin-penalty'),1)

    def test_match_end_keeps_page_cleans_state_and_resets_count(self):
        self.study();self.runtime.end_match()
        self.assertIn(6,self.w.rows);self.assertFalse(self.w.is_topmost(6))
        self.assertIsNone(self.ledger.current)
        self.assertFalse(self.runtime.presentation.returning)

    def test_close_entertainment_page_does_not_loop_start(self):
        self.runtime.switch(self.config);self.step(.5,dead=False,count=19)
        self.study(count=19);self.w.rows.pop(5);self.step(5,dead=False,count=19)
        self.step(6,count=19,gray=True);self.step(7,count=19,gray=True);self.step(9,count=19,gray=True)
        self.assertEqual(self.registry.starts,['entertainment-bilibili'])

    def test_api_loss_and_pause_remove_temporary_topmost(self):
        self.study();self.runtime.data_lost();self.runtime.cleanup()
        self.assertFalse(self.w.is_topmost(6));self.assertFalse(self.runtime.presentation.owned)
        self.download.cancel.assert_called()

    def test_mode_switch_hides_late_window_started_by_old_mode(self):
        old=self.runtime.manager
        old.pending_since=1;old.attempted=True
        self.config['mode']='study';self.runtime.switch(self.config)
        old.adapter.start()
        self.runtime.poll_return(5)
        self.assertIn(5,self.w.iconic)
        self.assertFalse(self.runtime.presentation.owned)

    def test_pause_resume_registry_does_not_reset_launch_budget(self):
        self.runtime.switch(self.config);self.step(.5,dead=False,count=19)
        self.study(count=19);self.runtime.cleanup();self.w.rows.pop(5)
        resumed=ModeRuntime(self.w,self.root,self.config,self.ledger,self.logs.append,registry=self.registry)
        for now,dead in [(5,False),(6,True),(7,True),(9,True)]:
            self.w.front_hwnd=9
            self.capture.read.return_value=bytes([100,100,100,0] if dead else [40,150,210,0])*100
            resumed.step('本人#123',Snapshot(dead,30,'Ahri',19),now,100+now,'game:1',1100+now,self.capture)
        self.assertEqual(self.registry.starts,['entertainment-bilibili'])


class DownloadTests(TempCase):
    def setUp(self):
        super().setUp();self.ledger=DeathLedger(self.root/'ledger.json')
        self.ledger.observe('本人',100,'game',21,1100);self.ledger.claim()
        self.fallback=patch('lol_words.modes.official_download.verified_official_entry',return_value=None)
        self.fallback_mock=self.fallback.start();self.addCleanup(self.fallback.stop)

    def test_simulated_official_entry_is_clicked_only_once(self):
        adapter=Mock();adapter.evaluate.side_effect=[{'state':'ready','id':'mock-download'},'submitted']
        download=OfficialDownload(Mock(),approve=lambda cancelled:True);download.start(adapter,self.ledger);download.thread.join(3)
        download.start(adapter,self.ledger)
        self.assertEqual(adapter.evaluate.call_count,2)
        self.assertIn('启动器',self.ledger.current['status'])
        self.assertIn('未验证完成',self.ledger.current['status'])
        adapter.navigate_home.assert_called_once()

    def test_missing_approval_handler_never_downloads_silently(self):
        adapter=Mock();download=OfficialDownload(Mock())
        download.start(adapter,self.ledger);download.thread.join(3)
        adapter.evaluate.assert_not_called();adapter.request_download.assert_not_called()
        self.assertIn('未确认下载',self.ledger.current['status'])

    def test_declined_download_is_not_requested_again_this_match(self):
        adapter=Mock();approval=Mock(return_value=False);download=OfficialDownload(Mock(),approve=approval)
        download.start(adapter,self.ledger);download.thread.join(3);download.start(adapter,self.ledger)
        approval.assert_called_once();adapter.evaluate.assert_not_called();adapter.request_download.assert_not_called()
        self.assertTrue(self.ledger.current['claimed'])

    def test_revive_during_approval_cancels_even_if_user_had_clicked_yes(self):
        adapter=Mock()
        def approve(cancelled):cancelled.set();return True
        download=OfficialDownload(Mock(),approve=approve);download.start(adapter,self.ledger);download.thread.join(3)
        adapter.evaluate.assert_not_called();adapter.request_download.assert_not_called()

    def test_cancelled_before_action_never_clicks(self):
        adapter=Mock();download=OfficialDownload(Mock(),approve=lambda cancelled:True);download.cancel();download.start(adapter,self.ledger);download.thread.join(3)
        adapter.evaluate.assert_not_called()

    def test_site_verification_or_wrong_origin_does_not_loop_click(self):
        adapter=Mock();adapter.evaluate.return_value={'state':'wrong_site'}
        download=OfficialDownload(Mock(),approve=lambda cancelled:True);download.start(adapter,self.ledger);download.thread.join(3)
        adapter.evaluate.assert_called_once_with(INSPECT_PC)
        self.assertIn('官网保留',self.ledger.current['status'])

    def test_unavailable_entry_performs_only_bounded_read_checks(self):
        adapter=Mock();adapter.evaluate.return_value={'state':'waiting'}
        download=OfficialDownload(Mock(),approve=lambda cancelled:True)
        with patch.object(download.cancelled,'wait',return_value=False):
            download.start(adapter,self.ledger);download.thread.join(3)
        self.assertEqual(adapter.evaluate.call_count,60)
        self.assertIn('不可用',self.ledger.current['status'])

    def test_entry_click_failure_is_recorded_without_retries(self):
        adapter=Mock();adapter.evaluate.side_effect=[{'state':'ready','id':'mock'},'entry_unavailable']
        download=OfficialDownload(Mock(),approve=lambda cancelled:True);download.start(adapter,self.ledger);download.thread.join(3)
        self.assertEqual(adapter.evaluate.call_count,2)
        self.assertIn('不自动重试',self.ledger.current['status'])

    def test_verified_official_fallback_submits_one_mock_download(self):
        from lol_words.modes.official_download import OFFICIAL_PC_ENTRY
        self.fallback_mock.return_value=OFFICIAL_PC_ENTRY
        adapter=Mock();adapter.evaluate.return_value={'state':'waiting'}
        download=OfficialDownload(Mock(),approve=lambda cancelled:True)
        with patch.object(download.cancelled,'wait',return_value=False):
            download.start(adapter,self.ledger);download.thread.join(3)
        adapter.request_download.assert_called_once_with(OFFICIAL_PC_ENTRY)
        self.fallback_mock.assert_called_once()
        self.assertIn('未验证完成',self.ledger.current['status'])

    def test_website_verification_does_not_use_download_fallback(self):
        adapter=Mock();adapter.evaluate.return_value={'state':'blocked'}
        download=OfficialDownload(Mock(),approve=lambda cancelled:True);download.start(adapter,self.ledger);download.thread.join(3)
        self.fallback_mock.assert_not_called()
        adapter.request_download.assert_not_called()

    def test_new_match_uses_new_page_deduplication_key(self):
        first=click_expression({'state':'ready','id':'mock'},'match-a')
        second=click_expression({'state':'ready','id':'mock'},'match-b')
        self.assertNotEqual(first,second)
