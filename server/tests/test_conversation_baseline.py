import asyncio
import json
from pathlib import Path
import pytest
from server import context_resolver, context_index, conversation_api, domain_skills, tool_runtime, workspace
from server.db import store
from server.skills.base import SkillResult

@pytest.fixture
def anyio_backend():
    return "asyncio"

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB_PATH', tmp_path/'messages.db')
    monkeypatch.setattr(tool_runtime, 'DB_PATH', tmp_path/'operations.db')
    store._init()


def test_exact_reply_survives_intervening_topics():
    sid = 'app:alice:chat:one'
    original = 'My exact original draft: '+ 'x'*10000
    parent = store.append_turn(sid, 'user', original)
    for _ in range(30):
        store.append_turn(sid, 'user', 'unrelated music request')
    resolved = context_resolver.resolve('Rewrite this calmly', sid, {'message_id': parent})
    assert original in resolved
    assert 'unrelated music request' not in resolved


def test_scoped_index_does_not_return_other_installations():
    mine = store.append_turn('app:alice::general', 'user', 'Budget privacy diary preferences')
    store.append_turn('app:bob::general', 'user', 'Budget privacy diary secrets')
    rows = context_index.search('app:alice:chat:new', 'privacy diary', all_conversations=True)
    assert [r['id'] for r in rows] == [mine]
    store.clear('app:alice::general')
    assert context_index.search('app:alice:chat:new', 'privacy', all_conversations=True) == []


def test_new_conversations_are_independent_and_legacy_survives():
    store.append_turn('app:alice::general', 'user', 'old history')
    new = conversation_api.create_conversation(conversation_api.NewConversation(client_id='app:alice',project='general'))
    assert new['session_id'].startswith('app:alice:chat:')
    items = conversation_api.conversations('app:alice')['items']
    assert {i['session_id'] for i in items} == {'app:alice::general', new['session_id']}
    assert conversation_api.conversations('app:bob')['items'] == []

@pytest.mark.anyio
async def test_typed_tools_validate_and_do_not_leak():
    other = store.append_turn('app:bob::general', 'user', 'private')
    tool = tool_runtime.adapter('context.read_message')
    assert (await tool.run(json.dumps({'message_id':other}), session_id='app:alice::general')).status == 'failed'
    assert (await tool.run('{"message_id":1,"invented":true}', session_id='app:alice::general')).status == 'failed'

@pytest.mark.anyio
async def test_durable_operation_replays_observation_without_mutation():
    class Fake:
        calls = 0
        async def run(self, args, **kwargs):
            self.calls += 1
            return SkillResult('succeeded', 'observed', data={'count':self.calls})
    fake = Fake()
    for _ in range(2):
        result = await tool_runtime.execute(fake, 'fake', '{}', session_id='app:alice', request_id='same')
        assert result.data['count'] == 1
    assert fake.calls == 1
    assert tool_runtime.operations('app:alice')[0]['state'] == 'finished'

@pytest.mark.anyio
async def test_cancelled_operation_is_not_reexecuted():
    class Fake:
        calls = 0
        async def run(self, args, **kwargs):
            self.calls += 1
            raise asyncio.CancelledError()
    fake = Fake()
    with pytest.raises(asyncio.CancelledError):
        await tool_runtime.execute(fake, 'mutate', '', session_id='app:alice', request_id='same')
    assert (await tool_runtime.execute(fake, 'mutate', '', session_id='app:alice', request_id='same')).status == 'blocked'
    assert fake.calls == 1


def test_project_context_read_stays_inside_target(tmp_path):
    root=tmp_path/'project'; root.mkdir(); (root/'draft.txt').write_text('original')
    (tmp_path/'secret.txt').write_text('outside')
    with workspace.bound(root):
        assert context_resolver.read_project_file('draft.txt')['content'] == 'original'
        with pytest.raises(ValueError): context_resolver.read_project_file('../secret.txt')
        with pytest.raises(ValueError): context_resolver.read_project_file('.env')


def test_domain_procedures_can_compose():
    assert {s.name for s in domain_skills.select('research providers and implement project')} >= {'research','coding'}


def test_tool_dictionary_keeps_typed_query_fields():
    from server.skills.shell import _coerce_args
    value, repaired = _coerce_args({'query':'old draft','all_conversations':True}, 'context.search')
    assert json.loads(value) == {'query':'old draft','all_conversations':True}
    assert not repaired


def test_api_conversation_and_capability_routes():
    from fastapi.testclient import TestClient
    from server import main, config
    client=TestClient(main.app)
    headers={'X-API-Token':config.API_TOKEN}
    created=client.post('/api/conversations',headers=headers,json={'client_id':'app:alice','title':'Mixed topics'})
    assert created.status_code==200
    assert client.get('/api/conversations',headers=headers,params={'client_id':'app:alice'}).json()['items'][0]['title']=='Mixed topics'
    assert client.get('/api/assistant/capabilities',headers=headers).json()['tools'][0]['input_schema']
    assert client.get('/api/conversations',params={'client_id':'app:alice'}).status_code==401

@pytest.mark.anyio
async def test_orchestrator_uses_typed_tool_and_exact_reply(monkeypatch, tmp_path):
    from server.skills import shell
    from server import jev
    parent=store.append_turn('app:alice:chat:topic','user','Original calm draft about privacy')
    inputs=[]
    async def brain(system, message, **kwargs):
        inputs.append(message)
        if len(inputs)==1:
            return json.dumps({'action':'call','tool':'context.read_message','args':{'message_id':parent}})
        return json.dumps({'action':'done','reply':'Here is the revised draft.'})
    async def no_review(*args, **kwargs): return False
    async def no_continuity(*args, **kwargs): return None
    monkeypatch.setattr(shell,'_haiku',brain)
    monkeypatch.setattr(shell,'_run_start',lambda *args:None)
    monkeypatch.setattr(jev,'unsupported_success',no_review)
    monkeypatch.setattr(jev,'continuity',no_continuity)
    with workspace.bound(tmp_path):
        answer=await shell.ShellSkill().run('Rewrite this calmly',session_id='app:alice:chat:topic',
            request_id='rewrite',reply_context={'message_id':parent})
    assert answer=='Here is the revised draft.'
    assert 'Original calm draft about privacy' in inputs[0]
    assert 'source_type' in inputs[1]
    assert tool_runtime.operations('app:alice:chat:topic')[0]['state']=='finished'

@pytest.mark.anyio
async def test_mutating_typed_tool_needs_authorization():
    calls=[]
    spec=tool_runtime.ToolSpec('fake.write','test',tool_runtime.EmptyInput,
        lambda args,session:calls.append('changed'),effect='write')
    result=await tool_runtime.ToolAdapter(spec).run('{}',session_id='app:alice')
    assert result.status=='blocked'
    assert calls==[]
