from server import config
from server.skills import discover, get_skill, command_map
from server.skills.base import SkillResult

# Built once at init() from skill manifests: {telegram_command: skill_name}
COMMAND_MAP: dict[str, str] = {}


def init() -> None:
    """Auto-discover every skill, then build the command map from manifests."""
    global COMMAND_MAP
    config.ensure_home_base()   # guarantee the default workspace exists
    discover()
    COMMAND_MAP = command_map()


async def route(command: str, prompt: str = "", **kwargs) -> str:
    from server import prefs
    # Mobile sends typed slash commands inside a shell request. These are exact
    # owner commands, not suggestions for an LLM to reinterpret.
    if command in ("shell",config.DEFAULT_SKILL) and prompt.lstrip().startswith('/'):
        token,_,rest=prompt.lstrip().partition(' ')
        explicit=token[1:]
        return await _typed_command(explicit,rest.strip(),prompt,**kwargs)
    skill_name = COMMAND_MAP.get(command, config.DEFAULT_SKILL)
    skill = get_skill(skill_name)
    if skill is None:
        available = ", ".join(f"/{c}" for c in sorted(COMMAND_MAP))
        return f"Unknown command: /{command}\nAvailable: {available}"
    # Check if the skill is enabled
    if not prefs.is_skill_enabled(skill_name):
        return f"Skill '{skill_name}' is currently disabled."
    # A directly-typed /projects switch is the user choosing, so it also moves
    # the default for new threads. The agent calling the same skill mid-turn
    # only rebinds that turn (see server/skills/projects.py).
    # The same holds for a directly-typed `/sessions continue <id>`.
    if skill_name in ("projects", "sessions"):
        kwargs.setdefault("persist", True)
    result = await skill.run(prompt, **kwargs)
    # Direct /run callers still receive the long-standing text contract. The
    # shell consumes SkillResult before this boundary and keeps its semantics.
    return result.message if isinstance(result, SkillResult) else str(result)


async def _typed_command(command,args,original_prompt,**kwargs):
    """An explicit mobile command keeps the same trace, receipt and chat contract."""
    import time
    from server import prefs,tool_runtime,run_usage
    from server.skills import shell
    from server.outcomes import completion_status
    on_event=kwargs.get('on_event')
    sid=kwargs.get('session_id')
    run_usage.start()
    shell._brains_var.set([])
    shell._brains.append('direct:'+command)
    run_id=shell._run_start(sid,original_prompt)
    await shell._emit(on_event,{'type':'run','run_id':run_id,'project':shell.workspace.name()})
    name=COMMAND_MAP.get(command)
    skill=get_skill(name) if name else None
    start=time.monotonic()
    if skill is None:
        raw=SkillResult('failed',f'Unknown command: /{command}')
    elif not prefs.is_skill_enabled(name):
        raw=SkillResult('blocked',f"Skill '{name}' is currently disabled.")
    else:
        await shell._emit(on_event,{'type':'step','n':1,'tool':name,'args':args[:200],
            'project':shell.workspace.name(),'label':f'/{command}'})
        call_kwargs={**kwargs,'session_id':sid,'source_prompt':original_prompt}
        if name in ('projects','sessions'):call_kwargs.setdefault('persist',True)
        try:
            raw=await tool_runtime.execute(skill,name,args,**call_kwargs)
        except Exception as exc:
            raw=SkillResult('failed',f'/{command} failed: {str(exc)[:300]}. It was not retried.')
    result=raw.message if isinstance(raw,SkillResult) else str(raw)
    status=raw.status if isinstance(raw,SkillResult) else shell._result_status(result)
    step={'tool':name or command,'args':args,'result':result,'status':status,
        'structured':raw.as_dict() if isinstance(raw,SkillResult) else None,'charged':True}
    ok=status in ('succeeded','unknown')
    elapsed=int((time.monotonic()-start)*1000)
    shell._run_step(run_id,1,name or command,args,result,charged=True,duration_ms=elapsed,ok=ok)
    await shell._emit(on_event,{'type':'step_result','n':1,'tool':name or command,
        'ok':ok,'status':status,'duration_ms':elapsed,'outcome':step['structured'],
        'project':shell.workspace.name(),'summary':result[:120]})
    reason='done' if ok else 'tool_failed'
    completion=completion_status(result,reason,[step])
    await shell._emit(on_event,{'type':'completion','status':completion,'reason':reason,'brains':'direct:'+command})
    shell._run_finish(run_id,reason,result,brains='direct:'+command,usage=run_usage.summary())
    shell.ShellSkill._remember(sid,original_prompt,result,run_id)
    return result
