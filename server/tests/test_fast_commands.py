import asyncio
import json
import time
from unittest.mock import AsyncMock

import pytest
from server import fast_commands as fast, home_control
from server.skills.base import SkillResult

ROWS=[{'id':'mbr-fan','name':'Mbr Fan','room':'Mbr','fan':True,'dimmable':False},
      {'id':'mbr-light','name':'Mbr Light','room':'Mbr','fan':False,'dimmable':True},
      {'id':'kitchen-light','name':'Kitchen Main','room':'Kitchen','fan':False,'dimmable':True},
      {'id':'kitchen-fan','name':'Kitchen Fan','room':'Kitchen','fan':True,'dimmable':False}]

@pytest.mark.parametrize('text,tool,args',[
 ('turn on master bedroom fan','home',{'action':'switch','target':'mbr-fan','state':True}),
 ('turn off all the kitchen lights','home',{'action':'room','room':'Kitchen','kind':'lights','state':False}),
 ('dim the master bedroom light to 40 percent','home',{'action':'switch','target':'mbr-light','state':True,'level':40}),
 ('lock my Mac screen','mac','lock'),('turn on my Mac','mac','wake'),
 ('speak in my Mac come down','mac','say come down'),
 ('increase the volume to max in my Mac','mac','volume 100'),
 ('play AR rehman songs','device','play ar rehman songs on youtube music'),
 ('next song','device','media next'),('turn off bluetooth','device','settings bluetooth'),
 ('set an alarm for 7:30 am','device','alarm 7:30 am'),
])
def test_exact_validated_candidates(text,tool,args):
 c=fast.choose(text,ROWS,phone_stream=True)
 assert c and c.score>.8 and c.tool==tool
 assert (json.loads(c.args) if tool=='home' else c.args)==args

@pytest.mark.parametrize('text',[
 'turn off hall lights','turn off my Mac','call PSS','open the garage',
 'send an SMS to PSS','turn on master bedroom fan then play songs',
 'turn on master bedroom fan when I get home',"don't turn on master bedroom fan",
 'why would I lock my Mac','play devil\'s advocate','lock my mac\ncall PSS',
])
def test_unknown_ambiguous_sensitive_and_compound_use_normal_path(text):
 assert fast.choose(text,ROWS,phone_stream=True) is None


def test_threshold_and_context_are_real_gates():
 assert fast.choose('play AR rehman songs',ROWS,phone_stream=True,threshold=.9) is None
 assert fast.choose('lock my mac',ROWS,threshold=.97) is None
 assert fast.choose('lock my mac',ROWS,reply_context={'text':'an older command'}) is None
 assert fast.choose('play AR rehman songs',ROWS,phone_stream=False) is None
 assert fast.choose('turn off hall fan',ROWS+[{'id':'x','name':'Hall Fan','room':'Hall','fan':True},
     {'id':'y','name':'Hall Fan','room':'Hall','fan':True}]) is None


def test_light_cannot_resolve_to_fan():
 rows=[{'id':'hall-fan','name':'Hall Lights','fan':True,'room':'Hall'}]
 assert fast.choose('turn off hall lights',rows) is None


def test_catalog_timeout_falls_back_before_execution(monkeypatch):
 monkeypatch.setenv('FAST_COMMANDS_ENABLED','1')
 monkeypatch.setattr(fast,'_catalog',None)
 async def unavailable(*a,**k):raise asyncio.TimeoutError()
 monkeypatch.setattr(home_control,'request',unavailable)
 assert asyncio.run(fast.prepare('turn on kitchen lights')) is None


def test_home_execution_revalidates_category_and_room_subset(monkeypatch):
 commands=[]
 async def request(path,*a,**k):
  if path=='/api/status':return {'connected':True,'updated_at':time.time()}
  return {'available':True,'loaded':True,'switches':ROWS}
 async def set_switch(row,state,level=None):
  commands.append(row['id']);return SkillResult('succeeded','verified',changed=True)
 monkeypatch.setattr(home_control,'request',request);monkeypatch.setattr(home_control,'set_switch',set_switch)
 blocked=asyncio.run(home_control.perform(home_control.HomeAction(action='switch',target='mbr-fan',state=False),expected_kind='lights'))
 assert not blocked.ok and commands==[]
 result=asyncio.run(home_control.perform(home_control.HomeAction(action='room',room='Kitchen',state=False),expected_kind='lights'))
 assert result.ok and commands==['kitchen-light']

@pytest.mark.parametrize('result', [SkillResult('succeeded','Mac locked.'), RuntimeError('timeout after write')])
def test_fast_execution_uses_receipts_and_never_retries_in_llm(monkeypatch,result):
 from server.skills import shell
 from server import tool_runtime,prefs
 monkeypatch.setenv('FAST_COMMANDS_ENABLED','1')
 monkeypatch.setattr(prefs,'is_skill_enabled',lambda name:True)
 monkeypatch.setattr(shell,'get_skill',lambda name:object())
 async def execute(*a,**kw):
  assert kw['request_id']=='same-request'
  if isinstance(result,Exception):raise result
  return result
 runner=AsyncMock(side_effect=execute);llm=AsyncMock(side_effect=AssertionError('No LLM on the fast path'))
 monkeypatch.setattr(tool_runtime,'execute',runner);monkeypatch.setattr(shell,'_haiku',llm)
 reply=asyncio.run(shell.ShellSkill().run('lock my mac',session_id='test-fast',request_id='same-request'))
 assert runner.await_count==1 and llm.await_count==0
 assert 'locked' in reply if not isinstance(result,Exception) else 'unverified' in reply


def test_low_confidence_still_uses_regular_shell(monkeypatch):
 from server.skills import shell
 monkeypatch.setenv('FAST_COMMANDS_ENABLED','1')
 llm=AsyncMock(return_value=json.dumps({'action':'done','reply':'regular path'}))
 monkeypatch.setattr(shell,'_haiku',llm)
 reply=asyncio.run(shell.ShellSkill().run('explain how black holes work',session_id='test-regular'))
 assert reply=='regular path' and llm.await_count==1


def test_mac_volume_verifies_value_and_rejects_invalid_input(monkeypatch):
 from server.skills import mac
 runner=AsyncMock(return_value=(0,'40\n',''))
 monkeypatch.setattr(mac,'_run',runner)
 assert asyncio.run(mac.MacSkill().run('volume 40')).status=='succeeded'
 assert asyncio.run(mac.MacSkill().run('volume 101')).status=='blocked'
 assert runner.await_count==1
 runner.return_value=(0,'39\n','')
 assert asyncio.run(mac.MacSkill().run('volume 40')).status=='unverified'


def test_regular_home_all_off_cannot_expand_lights_to_fans(monkeypatch):
 from server.skills.home import HomeSkill
 perform=AsyncMock(side_effect=AssertionError('must not operate the broader set'))
 monkeypatch.setattr(home_control,'perform',perform)
 r=asyncio.run(HomeSkill().run('all-off',source_prompt='turn off kitchen lights'))
 assert r.status=='blocked' and perform.await_count==0
