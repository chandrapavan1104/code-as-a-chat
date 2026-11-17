import asyncio
import json
from unittest.mock import AsyncMock

from server import cli_errors,orchestrator,brain_health
from server.skills.base import SkillResult

PAYLOAD={'is_error':True,'result':'Failed to authenticate: OAuth session expired and could not be refreshed',
 'total_cost_usd':140.38,'duration_ms':57,'usage':{'input_tokens':0,'output_tokens':0}}

def test_error_summary_does_not_leak_session_spend_into_new_request():
 text=cli_errors.detail(json.dumps(PAYLOAD))
 assert 'OAuth' in text and '140.38' not in text
 old='I could not complete it\n[claude error code 1]\n'+json.dumps(PAYLOAD)
 rendered=cli_errors.historical_context(old)
 assert '140.38' not in rendered and 'not a verified bill' in rendered
 assert cli_errors.historical_context('ordinary conversation')=='ordinary conversation'


def test_unsupported_client_is_not_expired_login():
 message='Error authenticating: IneligibleTierError: This client is no longer supported'
 assert cli_errors.kind(message)=='unsupported_client'
 brain_health.failed('gemini',RuntimeError(message))
 assert 'unsupported' in brain_health.snapshot()['gemini']['reason']
 assert 'authentication expired' not in brain_health.fallback_note()


def test_native_claude_auth_status_uses_no_model_prompt(monkeypatch):
 from server.skills import auth
 monkeypatch.setattr(auth.shutil,'which',lambda _: '/fake/claude')
 probe=AsyncMock(return_value=(0,json.dumps({'loggedIn':True,'authMethod':'claude.ai'})))
 monkeypatch.setattr(auth,'_sh',probe)
 brain_health.failed('claude',RuntimeError('OAuth session expired'))
 r=asyncio.run(auth._claude_status())
 assert 'saved sign-in exists' in r and 'failed OAuth refresh' in r
 assert not brain_health.available('claude')
 assert probe.call_args.args==('claude','auth','status')


def test_invalid_auth_response_does_not_claim_logout(monkeypatch):
 from server.skills import auth
 monkeypatch.setattr(auth.shutil,'which',lambda _: '/fake/claude')
 monkeypatch.setattr(auth,'_sh',AsyncMock(return_value=(1,'Keychain unavailable')))
 assert 'no logout inferred' in asyncio.run(auth._claude_status())


def test_exact_slash_command_dispatch_does_not_call_model(monkeypatch):
 from server import prefs
 calls=[]
 class Probe:
  async def run(self,prompt,**kw):calls.append((prompt,kw));return 'actual command response'
 monkeypatch.setattr(orchestrator,'COMMAND_MAP',{'probe':'probe','shell':'shell'})
 monkeypatch.setattr(orchestrator,'get_skill',lambda n:Probe() if n=='probe' else None)
 monkeypatch.setattr(prefs,'is_skill_enabled',lambda n:True)
 assert asyncio.run(orchestrator.route('shell','/probe status',request_id='stable'))=='actual command response'
 assert calls==[('status',{'request_id':'stable'})]
 assert 'Unknown command' in asyncio.run(orchestrator.route('shell','/invented nope'))
 assert len(calls)==1


def test_claude_auth_error_never_retries_same_credentials_on_backup(monkeypatch):
 from server.skills.claude_code import ClaudeCodeSkill
 skill=ClaudeCodeSkill()
 monkeypatch.setattr(skill,'_primary_model',lambda:'sonnet')
 monkeypatch.setattr(skill,'_backup_model',lambda:'opus')
 monkeypatch.setattr(skill,'_sync_context',lambda:None)
 monkeypatch.setattr('server.skills.cli_base.shutil.which',lambda _: '/fake/claude')
 attempt=AsyncMock(return_value=(1,json.dumps(PAYLOAD),'',None,'old-session'))
 monkeypatch.setattr(skill,'_attempt',attempt)
 result=asyncio.run(skill.run('review the code'))
 assert isinstance(result,SkillResult) and result.status=='failed'
 assert result.data['kind']=='authentication' and '140.38' not in result.message
 assert attempt.await_count==1


def test_rc_zero_structured_claude_error_is_still_failure():
 from server.skills.claude_code import ClaudeCodeSkill
 skill=ClaudeCodeSkill()
 assert skill._failed(0,json.dumps(PAYLOAD))
 assert skill.extract_cost(json.dumps(PAYLOAD)) is None


def test_partial_recovery_retains_the_useful_coding_report():
 from server.skills.shell import ShellSkill
 report='Detailed evidence and recommendations: '+('example '*60)
 result=ShellSkill._partial_summary([{'tool':'claude','args':'review','result':report,'status':'unknown'}])
 assert report in result
 assert 'Observed actions:' not in ShellSkill._partial_summary([])


def test_auth_errors_do_not_discard_the_native_session():
 from server.skills.cli_base import CLISubprocessSkill
 assert not CLISubprocessSkill._resume_rejected('Failed to resume: OAuth session expired','')
 assert CLISubprocessSkill._resume_rejected('session not found','')


def test_explicit_provider_verify_clears_failure_only_on_success(monkeypatch):
 from server.skills import auth,shell
 probe=AsyncMock(return_value='OK')
 monkeypatch.setattr(shell,'_claude_cli',probe)
 brain_health.failed('claude',RuntimeError('OAuth session expired'))
 assert 'verified' in asyncio.run(auth.AuthSkill().run('claude verify'))
 assert brain_health.available('claude')
 probe.side_effect=RuntimeError('OAuth session expired')
 assert 'failed' in asyncio.run(auth.AuthSkill().run('claude verify'))
 assert not brain_health.available('claude')


def test_resumed_session_estimate_is_not_added_as_new_turn_cost(monkeypatch):
 from server.skills.claude_code import ClaudeCodeSkill
 from server.db import cli_runs_store
 from server import run_usage
 monkeypatch.setattr(cli_runs_store,'add',lambda **kwargs:None)
 payload=json.dumps({'result':'report','total_cost_usd':140.38,'usage':{'input_tokens':10,'output_tokens':5}})
 run_usage.start()
 ClaudeCodeSkill()._record_attempt('/tmp','server',0,0,payload,'resumed-session')
 assert run_usage.summary()['cost_usd'] is None
 assert run_usage.summary()['input_tokens']==15
 assert 'not an invoice' in ClaudeCodeSkill().parse_output(payload,'')
