"""大厅状态机与真实服务接口边界；不连接客户端、不发送真实游戏操作。"""
import runtime_bootstrap
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock,patch
from lcu_api import ClientSnapshot,Connection,LCUClient,LCUError,Member,OPERATIONS,parse_lobby,parse_lockfile,parse_commandline,stable_id
from lobby_flow import ActionJournal,LobbyFlow
from lobby_config import settings,validate
from mode_config import migrate_config
from client_surface import Evidence


ACCOUNT='puuid:myself0001'
CONNECTION=((10,12345,'credential-digest'),ACCOUNT)


def member(identifier,name='队友',leader=False,**changes):
    value=dict(puuid=identifier,summonerId=1 if identifier=='myself0001' else 2,summonerName=name,
               isLeader=leader,isSpectator=False)
    value.update(changes);return value


def lobby(me_leader=True,others=True,party='party-one'):
    me=member('myself0001','本人',me_leader)
    team=[me]+([member('friend0001','接任队友',not me_leader)] if others else [])
    return dict(partyId=party,localMember=copy.deepcopy(me),members=team)


def sample(phase='EndOfGame',game_id=100,**changes):
    kwargs=dict(connection=CONNECTION,phase=phase,session={'gameData':{'gameId':game_id}})
    if phase=='ReadyCheck':kwargs['ready']=dict(state='InProgress',playerResponse='None',suppressUx=False,timer=10)
    if phase=='Lobby':kwargs['lobby']=lobby()
    kwargs.update(changes);return ClientSnapshot(**kwargs)


class Client:
    def __init__(self):
        self.capabilities=set(OPERATIONS)
        self.latest=sample();self.calls=[];self.error=None
    def snapshot(self):return self.latest
    def write(self,operation,member=None):
        self.calls.append((operation,member))
        if self.error:raise self.error


class Surface:
    def __init__(self):self.evidence=Evidence();self.actions=[];self.focus=[];self.during=None
    def observe(self,guard,activate=False):
        self.focus.append(activate)
        if self.during:self.during()
        return self.evidence if guard() else Evidence(reason='已取消')
    def act(self,kind,evidence,guard):
        if not guard():return False
        self.actions.append(kind);return True


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.area=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root=Path(self.area.name);self.journal=ActionJournal(self.root/'actions.json')
        self.logs=[];self.states=[];self.surface=Surface();self.client=Client()
        self.flow=LobbyFlow(self.journal,self.logs.append,self.states.append,self.surface)
        self.config=migrate_config();self.config.update(lobby_auto_return=True,lobby_return_delay=0)
        self.stop=False
    def tearDown(self):self.area.cleanup()
    def step(self,now,phase='EndOfGame',**kwargs):
        current=sample(phase,**kwargs);self.client.latest=current
        self.flow.step(self.client,current,self.config,now,False,lambda:self.stop)
    def operations(self):return [name for name,target in self.client.calls]

    def test_normal_return_waits_for_process_exit_and_reads_success(self):
        self.step(0);self.assertEqual(self.operations(),[])
        self.step(2);self.assertEqual(self.operations(),['return'])
        self.assertFalse(any('已确认返回' in text for text in self.logs))
        self.step(3,'Lobby');self.assertTrue(any('已确认返回组队房间' in text for text in self.logs))

    def test_return_delay_and_repeated_poll_do_not_create_duplicate_requests(self):
        self.config['lobby_return_delay']=5
        for t in range(12):self.step(t)
        self.assertEqual(self.operations(),['return'])

    def test_no_lobby_action_during_game_reconnect_or_champion_selection(self):
        self.config.update(lobby_auto_accept=True,lobby_auto_promote=True)
        for phase in ['InProgress','GameStart','Reconnect','ChampSelect']:
            for t in [0,10,60]:self.step(t,phase)
        self.assertEqual(self.operations(),[]);self.assertEqual(self.surface.actions,[])

    def test_existing_game_process_blocks_postgame_actions(self):
        current=sample();self.client.latest=current
        for t in [0,5,40]:self.flow.step(self.client,current,self.config,t,True)
        self.assertEqual(self.operations(),[]);self.assertEqual(self.surface.focus,[])

    def test_new_game_process_between_snapshot_and_action_blocks_write(self):
        self.step(0)
        current=sample();self.client.latest=current
        self.flow.step(self.client,current,self.config,3,False,game_check=lambda:True)
        self.assertEqual(self.operations(),[])

    def test_pause_before_action_cancels_everything(self):
        self.step(0);self.stop=True;self.step(40)
        self.assertEqual(self.operations(),[]);self.assertEqual(self.surface.actions,[])

    def test_phase_changes_during_final_read_prevent_stale_return(self):
        self.step(0);expected=sample();self.client.latest=sample('ChampSelect',game_id=101)
        self.flow.step(self.client,expected,self.config,4,False)
        self.assertEqual(self.operations(),[])

    def test_return_request_timeout_is_not_retried(self):
        self.client.error=LCUError('超时',uncertain=True)
        for t in [0,2,3,4]:self.step(t)
        self.assertEqual(self.operations(),['return']);self.assertTrue(any('不反复提交' in text for text in self.logs))

    def test_restart_in_same_match_does_not_repeat_return(self):
        self.step(0);self.step(2)
        second=LobbyFlow(ActionJournal(self.root/'actions.json'),self.logs.append,self.states.append)
        second.step(self.client,sample(),self.config,5,False)
        second.step(self.client,sample(),self.config,8,False)
        self.assertEqual(self.operations(),['return'])

    def test_next_match_can_return_again(self):
        self.step(0);self.step(2);self.step(3,'Lobby')
        self.step(4,'InProgress',game_id=101);self.step(5,game_id=101);self.step(7,game_id=101)
        self.assertEqual(self.operations(),['return','return'])

    def test_late_game_identifier_does_not_create_an_extra_return(self):
        self.step(0,game_id=0);self.step(2,game_id=0)
        self.step(3,game_id=100);self.step(6,game_id=100)
        self.assertEqual(self.operations(),['return'])

    def test_skip_stats_is_only_requested_after_visible_skip_evidence(self):
        self.step(0,'PreEndOfGame');self.step(2,'PreEndOfGame');self.step(5,'PreEndOfGame')
        self.assertNotIn('skip_stats',self.operations())
        self.surface.evidence=Evidence(skip={'source':'uia'})
        self.step(8,'PreEndOfGame');self.step(11,'PreEndOfGame')
        self.assertEqual(self.operations().count('skip_stats'),1)

    def test_visible_skip_uses_button_if_endpoint_not_confirmed(self):
        self.client.capabilities.discard('skip_stats');self.surface.evidence=Evidence(skip={'source':'ocr'})
        self.step(0,'WaitingForStats');self.step(2,'WaitingForStats');self.step(5,'WaitingForStats');self.step(8,'WaitingForStats')
        self.assertEqual(self.surface.actions,['skip']);self.assertNotIn('skip_stats',self.operations())

    def test_after_skip_phase_change_allows_one_normal_return_in_new_stage(self):
        self.surface.evidence=Evidence(skip={'source':'uia'})
        self.step(0,'WaitingForStats');self.step(2,'WaitingForStats');self.step(5,'WaitingForStats');self.step(6,'EndOfGame');self.step(7,'EndOfGame')
        self.assertEqual(self.operations(),['return','return']);self.assertEqual(self.surface.actions,['skip'])

    def test_waiting_for_stats_does_not_use_pre_end_sequence_endpoint(self):
        self.surface.evidence=Evidence(skip={'source':'uia'})
        self.step(0,'WaitingForStats');self.step(2,'WaitingForStats');self.step(5,'WaitingForStats')
        self.assertNotIn('skip_stats',self.operations());self.assertEqual(self.surface.actions,['skip'])

    def test_stuck_avatar_path_waits_and_clicks_at_most_once(self):
        self.surface.evidence=Evidence(avatar={'source':'template'})
        for t in [0,2,5,10,29]:self.step(t)
        self.assertEqual(self.surface.actions,[])
        for t in [32,36,40]:self.step(t)
        self.assertEqual(self.surface.actions,['avatar'])
        self.assertEqual(sum(self.surface.focus),1)

    def test_missing_or_ambiguous_avatar_never_causes_fixed_coordinate_click(self):
        self.surface.evidence=Evidence(reason='无法唯一识别')
        for t in [0,2,35,40]:self.step(t)
        self.assertEqual(self.surface.actions,[])
        self.assertEqual(self.states[-1].detail,'无法唯一识别')

    def test_pause_during_recognition_prevents_click(self):
        self.surface.evidence=Evidence(avatar={'source':'template'})
        self.step(0);self.step(2);self.surface.during=lambda:setattr(self,'stop',True)
        self.step(35);self.assertEqual(self.surface.actions,[])

    def test_disabling_recovery_keeps_normal_return_only(self):
        self.config['lobby_recover_client']=False;self.surface.evidence=Evidence(avatar={})
        for t in [0,2,40]:self.step(t)
        self.assertEqual(self.operations(),['return']);self.assertEqual(self.surface.focus,[])

    def test_auto_accept_waits_then_submits_only_once(self):
        self.config.update(lobby_auto_accept=True,lobby_accept_delay=2)
        for t in [0,1,2,3,4]:self.step(t,'ReadyCheck')
        self.assertEqual(self.operations(),['accept'])

    def test_manual_decline_or_already_accepted_are_not_overridden(self):
        self.config['lobby_auto_accept']=True
        for response in ['Declined','Accepted']:
            ready=dict(state='InProgress',playerResponse=response,suppressUx=False)
            self.step(0,'ReadyCheck',ready=ready);self.step(3,'ReadyCheck',ready=ready)
        self.assertEqual(self.operations(),[])

    def test_expired_or_hidden_ready_check_does_not_accept(self):
        self.config['lobby_auto_accept']=True
        for ready in [dict(state='Invalid',playerResponse='None'),dict(state='InProgress',playerResponse='None',suppressUx=True)]:
            self.step(0,'ReadyCheck',ready=ready);self.step(3,'ReadyCheck',ready=ready)
        self.assertEqual(self.operations(),[])

    def test_response_changes_during_final_read_prevent_auto_accept(self):
        self.config.update(lobby_auto_accept=True,lobby_accept_delay=0)
        expected=sample('ReadyCheck');self.client.latest=sample('ReadyCheck',ready=dict(state='InProgress',playerResponse='Declined'))
        self.flow.step(self.client,expected,self.config,0,False)
        self.assertEqual(self.operations(),[])

    def test_accept_survives_reconnect_without_repeating_and_next_invite_works(self):
        self.config.update(lobby_auto_accept=True,lobby_accept_delay=0)
        self.step(0,'ReadyCheck');self.flow.cancel();self.step(3,'ReadyCheck')
        self.assertEqual(self.operations(),['accept'])
        self.step(4,'Matchmaking');self.step(5,'ReadyCheck')
        self.assertEqual(self.operations(),['accept','accept'])

    def test_two_instances_claim_ready_check_at_most_once(self):
        self.config.update(lobby_auto_accept=True,lobby_accept_delay=0)
        self.step(0,'ReadyCheck')
        second=LobbyFlow(ActionJournal(self.root/'actions.json'),lambda x:None,lambda x:None)
        second.step(self.client,self.client.latest,self.config,1,False)
        self.assertEqual(self.operations(),['accept'])

    def test_promotion_selects_first_eligible_nonself_member(self):
        self.config['lobby_auto_promote']=True
        self.step(0,'Lobby');self.step(2,'Lobby');self.step(3,'Lobby')
        self.assertEqual(self.operations(),['promote_v3']);self.assertEqual(self.client.calls[0][1].key,'puuid:friend0001')

    def test_not_leader_never_attempts_promotion(self):
        self.config['lobby_auto_promote']=True
        self.step(0,'Lobby',lobby=lobby(False));self.step(5,'Lobby',lobby=lobby(False))
        self.assertEqual(self.operations(),[])

    def test_no_other_player_or_missing_preferred_player_waits(self):
        self.config['lobby_auto_promote']=True
        self.step(0,'Lobby',lobby=lobby(others=False));self.step(5,'Lobby',lobby=lobby(others=False))
        self.config['lobby_preferred_member']='puuid:absent0001'
        self.step(6,'Lobby');self.step(8,'Lobby')
        self.assertEqual(self.operations(),[])

    def test_departing_target_between_snapshot_and_write_cancels_promotion(self):
        self.config['lobby_auto_promote']=True
        self.step(0,'Lobby');self.client.latest=sample('Lobby',lobby=lobby(others=False))
        self.flow.step(self.client,sample('Lobby'),self.config,3,False)
        self.assertEqual(self.operations(),[])

    def test_promotion_v2_fallback_uses_exact_summoner_id(self):
        self.config['lobby_auto_promote']=True;self.client.capabilities.discard('promote_v3')
        self.step(0,'Lobby');self.step(2,'Lobby')
        self.assertEqual(self.operations(),['promote_v2']);self.assertEqual(self.client.calls[0][1].summoner_id,2)

    def test_new_leader_tenure_may_transfer_again_after_success(self):
        self.config['lobby_auto_promote']=True
        self.step(0,'Lobby');self.step(2,'Lobby');self.step(3,'Lobby',lobby=lobby(False));self.step(4,'Lobby')
        self.assertEqual(self.operations(),['promote_v3','promote_v3'])
        self.assertTrue(any('不再是队长' in text for text in self.logs))

    def test_record_and_logs_do_not_contain_names_or_credentials(self):
        self.step(0);self.step(2)
        text=(self.root/'actions.json').read_text(encoding='utf-8')+'\n'.join(self.logs)
        for secret in ['myself0001','接任队友','credential-digest','puuid:']:self.assertNotIn(secret,text)

    def test_corrupt_action_store_prevents_new_actions(self):
        (self.root/'actions.json').write_text('{bad',encoding='utf-8')
        with self.assertRaises(ValueError):self.step(0)
        self.assertEqual(self.operations(),[])


class ProtocolTests(unittest.TestCase):
    def test_old_configuration_preserves_values_and_defaults_automation_off(self):
        config=migrate_config({'death_switch_delay':5.25,'my_setting':1})
        self.assertEqual(config['death_switch_delay'],5.25);self.assertEqual(config['my_setting'],1)
        self.assertFalse(any(config[k] for k in ['lobby_auto_return','lobby_auto_accept','lobby_auto_promote']))
        validate(config)

    def test_invalid_lobby_options_are_rejected(self):
        for key,value in [('lobby_auto_accept','yes'),('lobby_stuck_seconds',1),('lobby_accept_delay',float('nan')),('lobby_return_delay',31),('lobby_preferred_member',False)]:
            config=migrate_config();config[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):validate(config)

    def test_live_participant_schema_without_isbot_is_supported(self):
        parsed=parse_lobby(lobby(),ACCOUNT);self.assertTrue(parsed.me.leader);self.assertEqual(len(parsed.members),2)

    def test_duplicate_members_or_multiple_leaders_are_rejected(self):
        for data in [lobby(),lobby()]:
            if data is not None:
                bad=copy.deepcopy(data);bad['members'][1]['isLeader']=True
                with self.assertRaises(LCUError):parse_lobby(bad,ACCOUNT)
                bad=copy.deepcopy(data);bad['members'].append(bad['members'][0])
                with self.assertRaises(LCUError):parse_lobby(bad,ACCOUNT)

    def test_bot_spectator_and_explicit_nonleader_candidate_are_ineligible(self):
        for change in [dict(isBot=True),dict(isSpectator=True),dict(canBeLeader=False)]:
            data=lobby();data['members'][1].update(change)
            self.assertFalse(parse_lobby(data,ACCOUNT).members[1].eligible)

    def test_invalid_or_recycled_lockfile_parameters_are_rejected(self):
        self.assertIsNone(parse_lockfile('LeagueClient:10:12345:secret:https',{11}))
        self.assertIsNone(parse_lockfile('LeagueClient:10:0:secret:https',{10}))
        self.assertIsNone(parse_commandline(10,'--app-port=12345 --remoting-auth-token=secret',{11}))
        self.assertNotIn('secret',repr(Connection(10,12345,'secret')))

    def test_client_refuses_nonlocal_routes_and_hero_or_match_start_operations(self):
        client=LCUClient(Connection(10,12345,'secret'))
        for path in ['https://example.org/','/lol-champ-select/v1/session/actions/1','/lol-lobby/v2/lobby/matchmaking/search']:
            with self.subTest(path=path),self.assertRaises(LCUError):client.request('POST',path)

    def test_capability_probe_checks_method_route_and_argument_contract(self):
        client=LCUClient(Connection(10,12345,'secret'))
        defs={name:dict(url=url,http_method='POST',arguments=[] if op in {'return','accept','skip_stats'} else [{'puuid' if op=='promote_v3' else 'summonerId':{}}]) for op,(name,url) in OPERATIONS.items()}
        client.request=lambda method,path,**kw:{'functions':{name:'' for name in defs}} if path=='/help' else {path.split('=')[1]:defs[path.split('=')[1]]}
        self.assertEqual(client.inspect(),set(OPERATIONS))
        defs[OPERATIONS['skip_stats'][0]]['http_method']='DELETE'
        self.assertNotIn('skip_stats',client.inspect())

    def test_unconfirmed_operations_do_not_send_requests(self):
        client=LCUClient(Connection(10,12345,'secret'));client.request=Mock()
        with self.assertRaises(LCUError):client.write('accept')
        client.request.assert_not_called()

    def test_malformed_optional_snapshot_cancels_instead_of_guessing(self):
        client=LCUClient(Connection(10,12345,'secret'))
        client.request=Mock(side_effect=lambda method,path: 'ReadyCheck' if path.endswith('gameflow-phase') else {'puuid':'myself0001'})
        client.optional=Mock(return_value=[])
        with self.assertRaises(LCUError):client.snapshot()
