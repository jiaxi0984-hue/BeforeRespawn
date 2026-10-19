"""软件库、名称解析与本机检测；所有程序文件均为临时模拟文件。"""
import copy
import json
from pathlib import Path
import tempfile
import threading
import io
import xml.etree.ElementTree as ET
import unittest
from unittest.mock import Mock,patch
import test_app
from lol_words.ui.legacy_app import App
from lol_words.targets.catalog import LearningCatalog,installer_name,online_candidates
from lol_words.targets.software_catalog import scan_learning_apps,detected_target,split_arguments
from lol_words.modes.mode_config import validate_target
from lol_words.modes.penalty import DeathLedger


class CatalogCase(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.catalog=LearningCatalog(user_path=self.root/'user-catalog.json')

    def exe(self,name='Eudic.exe',folder='installed'):
        directory=self.root/folder;directory.mkdir(exist_ok=True)
        path=directory/name;path.write_bytes(b'mock executable, never launched')
        return str(path.resolve())


class NameSearchTests(CatalogCase):
    def test_first_catalog_has_seventeen_entries_and_eleven_windows_products(self):
        self.assertEqual(len(self.catalog.entries),17)
        self.assertEqual(len(self.catalog.search(windows_only=True)),11)
        self.assertTrue(all(e['sources'] and e['checked_on'] for e in self.catalog.entries))

    def test_chinese_and_english_aliases_resolve_same_product(self):
        for query in ['欧路词典','欧路','EUDIC','Ｅｕｄｉｃ']:
            self.assertEqual(self.catalog.resolve(query)['id'],'eudic')

    def test_youdao_uses_verified_domain_not_lookalike(self):
        entry=self.catalog.resolve('网易有道词典')
        self.assertEqual(entry['official_url'],'https://fanyi.youdao.com/download/')
        self.assertEqual(self.catalog.web_target(entry)['url'],'https://dict.youdao.com/')

    def test_ambiguous_partial_query_does_not_choose_first_result(self):
        self.assertIsNone(self.catalog.resolve('词典'))
        self.assertGreater(len(self.catalog.search('词典')),1)

    def test_mobile_brand_does_not_become_a_fictional_windows_client(self):
        self.assertIsNone(self.catalog.resolve('百词斩')['windows'])
        self.assertNotIn('baicizhan',{e['id'] for e in self.catalog.search(windows_only=True)})
        self.assertEqual(self.catalog.web_target(self.catalog.resolve('百词斩'))['url'],'https://www.baicizhan.com/')

    def test_maimemo_beta_limitation_is_preserved(self):
        entry=self.catalog.resolve('墨墨')
        self.assertIn('公测',entry['note'])
        self.assertEqual(self.catalog.web_target(entry)['url'],'https://www.maimemo.com/')

    def test_unknown_name_only_creates_search_page_not_guessed_official_site(self):
        self.assertIsNone(self.catalog.resolve('未收录学习应用'))
        target=self.catalog.search_target('未收录学习应用')
        self.assertTrue(target['url'].startswith('https://www.bing.com/search?q='))
        self.assertNotIn('catalog_id',target)

    def test_search_query_is_encoded_and_cannot_change_destination(self):
        target=self.catalog.search_target('words & redirect=https://evil.example/')
        self.assertNotIn('&redirect=',target['url'])
        self.assertTrue(target['url'].startswith('https://www.bing.com/'))

    def test_empty_and_overlong_search_rejected(self):
        for query in ['',' '*10,'x'*81]:
            with self.assertRaises(ValueError):self.catalog.search_target(query)

    def test_user_confirmed_official_name_is_persisted_for_next_search(self):
        self.catalog.remember('自选学习站','https://study.example.org/')
        restored=LearningCatalog(user_path=self.root/'user-catalog.json')
        entry=restored.resolve('自选学习站')
        self.assertEqual(entry['verification'],'user_confirmed')
        self.assertEqual(entry['official_url'],'https://study.example.org/')

    def test_builtin_official_mapping_cannot_be_silently_overridden(self):
        with self.assertRaises(ValueError):self.catalog.remember('有道','https://evil.example/')

    def test_binary_and_credential_urls_not_saved_as_official_webpage(self):
        for url in ['https://user:pass@example.org/','http://example.org/','https://example.org/setup.exe']:
            with self.subTest(url=url),self.assertRaises(ValueError):self.catalog.remember('自选学习站',url)

    def test_catalog_target_validates_without_manual_path(self):
        target=self.catalog.web_target(self.catalog.resolve('不背单词'))
        validate_target(target)
        self.assertEqual(target['adapter'],'web')

    def test_explicit_website_open_uses_selected_entry_even_when_entertainment_active(self):
        app=App.__new__(App);app.catalog=self.catalog;app.mode=Mock();app.refresh_mode_visibility=Mock();app.store_target=Mock()
        completed=threading.Event();manager=Mock();manager.ensure.side_effect=lambda **kw:completed.set()
        app.registry=Mock(get=Mock(return_value=manager))
        app.open_catalog_entry(self.catalog.resolve('欧路'))
        self.assertTrue(completed.wait(1))
        app.mode.set.assert_called_once_with('study')
        self.assertEqual(app.registry.get.call_args.args[0]['catalog_id'],'eudic')
        manager.ensure.assert_called_once_with(manual=True)


class DetectionTests(CatalogCase):
    def test_registry_product_matches_existing_executable(self):
        path=self.exe()
        result=scan_learning_apps(self.catalog,records=[dict(name='欧路词典 26.9.1 (x64)',path=path,source='安装登记')])
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['catalog_id'],'eudic')
        self.assertEqual(result[0]['path'],path)

    def test_unknown_non_learning_software_is_filtered(self):
        path=self.exe('game.exe')
        self.assertEqual(scan_learning_apps(self.catalog,records=[dict(name='普通游戏',path=path)]),[])

    def test_known_name_without_existing_executable_is_not_claimed_installed(self):
        self.assertEqual(scan_learning_apps(self.catalog,records=[dict(name='欧路词典',path=str(self.root/'missing.exe'))]),[])

    def test_ankidroid_is_not_matched_as_anki(self):
        path=self.exe('AnkiDroid.exe')
        self.assertIsNone(self.catalog.identify_installation('AnkiDroid'))
        self.assertEqual(scan_learning_apps(self.catalog,records=[dict(name='AnkiDroid',path=path)]),[])

    def test_mobile_only_or_unverified_client_is_not_added(self):
        path=self.exe('baicizhan.exe')
        self.assertEqual(scan_learning_apps(self.catalog,records=[dict(name='百词斩',path=path)]),[])

    def test_shell_shortcut_is_not_misidentified_as_learning_software(self):
        path=self.exe('powershell.exe')
        self.assertEqual(scan_learning_apps(self.catalog,records=[dict(name='欧路词典',path=path,arguments='-Command fake')]),[])

    def test_existing_custom_target_is_detected_even_outside_builtin_library(self):
        path=self.exe('custom-study.exe')
        target=dict(id='custom',name='自选学习应用',adapter='desktop',command=[path],process_path=path,
                    title_contains='主窗口',window_class='',pin=False)
        items=scan_learning_apps(self.catalog,records=[],configured_targets=[target])
        self.assertEqual(len(items),1)
        self.assertEqual(items[0]['source'],'已有学习目标配置')
        self.assertEqual(detected_target(items[0]),target)

    def test_registry_and_shortcut_duplicate_path_deduplicates(self):
        path=self.exe()
        records=[dict(name='欧路词典',path=path,source='安装登记'),dict(name='Eudic',path=path,source='开始菜单')]
        self.assertEqual(len(scan_learning_apps(self.catalog,records=records)),1)

    def test_two_installations_remain_distinct(self):
        paths=[self.exe(folder='old'),self.exe(folder='new')]
        results=scan_learning_apps(self.catalog,records=[dict(name='Eudic',path=p) for p in paths])
        self.assertEqual(len(results),2)
        targets=[]
        for item in results:targets.append(detected_target(item,targets))
        self.assertNotEqual(targets[0]['id'],targets[1]['id'])
        self.assertNotEqual(targets[0]['name'],targets[1]['name'])

    def test_shortcut_arguments_preserved_without_shell_execution(self):
        path=self.exe()
        records=[dict(name='欧路词典',path=path,arguments='--profile "中文 笔记"')]
        result=scan_learning_apps(self.catalog,records=records)
        self.assertEqual(result[0]['command'],[path,'--profile','中文 笔记'])

    def test_uninstaller_is_not_selected_as_learning_program(self):
        path=self.exe('uninstall.exe')
        self.assertEqual(scan_learning_apps(self.catalog,records=[dict(name='欧路词典',path=path)]),[])

    def test_actual_registered_install_location_used_without_guessed_path(self):
        path=self.exe()
        result=scan_learning_apps(self.catalog,records=[dict(name='欧路词典',install_location=str(Path(path).parent))])
        self.assertEqual(result[0]['path'],path)

    def test_ambiguous_installation_executables_are_not_guessed(self):
        first=self.exe();self.exe('欧路词典.exe')
        result=scan_learning_apps(self.catalog,records=[dict(name='欧路词典',install_location=str(Path(first).parent))])
        self.assertEqual(result,[])

    def test_running_product_path_can_be_detected_without_registry(self):
        path=self.exe('Readest.exe');windows=Mock()
        windows.window_inventory.return_value=[dict(path=path,title='阅读中',pid=50)]
        result=scan_learning_apps(self.catalog,windows=windows,records=[])
        self.assertEqual(result[0]['catalog_id'],'readest')

    def test_target_builder_preserves_existing_window_and_pin_preferences(self):
        path=self.exe();item=scan_learning_apps(self.catalog,records=[dict(name='Eudic',path=path)])[0]
        target=detected_target(item);target.update(pin=False,title_contains='主窗口')
        updated=detected_target(item,[target])
        self.assertFalse(updated['pin']);self.assertEqual(updated['title_contains'],'主窗口')
        validate_target(updated)

    def test_anki_detection_reuses_legacy_target_identity(self):
        path=self.exe('anki.exe');item=scan_learning_apps(self.catalog,records=[dict(name='Anki',path=path)])[0]
        target=detected_target(item,[dict(id='anki',name='Anki',pin=False)])
        self.assertEqual(target['id'],'anki');self.assertEqual(target['adapter'],'anki');self.assertFalse(target['pin'])

    def test_installer_version_normalization_is_conservative(self):
        self.assertEqual(installer_name('Readest 0.9.99 (x64)'),'readest')
        self.assertNotEqual(installer_name('AnkiDroid'),'anki')


class OnlineCandidateTests(CatalogCase):
    def response(self,items):
        root=ET.Element('rss');channel=ET.SubElement(root,'channel')
        for title,url in items:
            item=ET.SubElement(channel,'item');ET.SubElement(item,'title').text=title;ET.SubElement(item,'link').text=url
        return io.BytesIO(ET.tostring(root,encoding='utf-8'))

    def test_candidates_are_unverified_and_not_automatically_saved(self):
        with patch('lol_words.targets.catalog.urllib.request.urlopen',return_value=self.response([('自选学习器 官方网站','https://study.example.org/')])):
            items=online_candidates('自选学习器')
        self.assertEqual(items[0]['verification'],'unverified')
        self.assertIsNone(self.catalog.resolve('自选学习器'))
        self.assertFalse((self.root/'user-catalog.json').exists())

    def test_unrelated_results_download_links_and_aggregators_are_filtered(self):
        candidates=[('别的应用','https://other.example.org/'),('自选学习器 下载','https://study.example.org/setup.exe'),
                    ('自选学习器 官方','https://www.php.cn/download'),('自选学习器 官网','https://study.example.org/')]
        with patch('lol_words.targets.catalog.urllib.request.urlopen',return_value=self.response(candidates)):
            items=online_candidates('自选学习器')
        self.assertEqual([i['url'] for i in items],['https://study.example.org/'])

    def test_private_addresses_and_embedded_credentials_not_offered(self):
        candidates=[('自选学习器','https://127.0.0.1/'),('自选学习器','https://10.0.0.1/'),
                    ('自选学习器','https://localhost/'),('自选学习器','https://user:pass@study.example.org/')]
        with patch('lol_words.targets.catalog.urllib.request.urlopen',return_value=self.response(candidates)):
            self.assertEqual(online_candidates('自选学习器'),[])

    def test_repeated_domains_deduplicate_and_results_are_bounded(self):
        candidates=[('自选学习器官网','https://study.example.org/a'),('自选学习器官网','https://study.example.org/b')]
        candidates += [('自选学习器官网',f'https://study{i}.example.org/') for i in range(12)]
        with patch('lol_words.targets.catalog.urllib.request.urlopen',return_value=self.response(candidates)):
            self.assertEqual(len(online_candidates('自选学习器')),8)

    def test_oversized_search_response_is_rejected(self):
        with patch('lol_words.targets.catalog.urllib.request.urlopen',return_value=io.BytesIO(b'x'*300001)),self.assertRaises(ValueError):
            online_candidates('自选学习器')

    def test_name_must_be_present_before_network_request(self):
        with patch('lol_words.targets.catalog.urllib.request.urlopen') as request,self.assertRaises(ValueError):online_candidates('')
        request.assert_not_called()


class TwentiethDeathResetTests(CatalogCase):
    def test_new_match_can_trigger_again_at_exactly_twenty(self):
        ledger=DeathLedger(self.root/'penalty.json')
        ledger.observe('本人',100,'pid:1',20,1000)
        self.assertTrue(ledger.eligible('entertainment'));self.assertTrue(ledger.claim());self.assertFalse(ledger.claim())
        first=ledger.current['id'];ledger.end()
        ledger.observe('本人',1,'pid:2',0,1100)
        self.assertEqual(ledger.current['deaths'],0);self.assertFalse(ledger.current['claimed'])
        ledger.observe('本人',100,'pid:2',20,1199)
        self.assertTrue(ledger.eligible('entertainment'));self.assertTrue(ledger.claim())
        self.assertNotEqual(ledger.current['id'],first)
