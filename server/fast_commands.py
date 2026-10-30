"""Conservative routing scores choose a bounded executor, never authorize an action.

Scores are policy tiers, not calibrated statistical probabilities. Live catalogs,
permissions, confirmations and operation receipts remain independent gates.
"""
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import asyncio
import copy
import json
import os
import re
import time

from server.command_grammar import Router

VERSION = 'hassil-fast-v1'
_catalog = None
_catalog_at = 0.0

@dataclass(frozen=True)
class Candidate:
    capability: str
    tool: str
    args: str
    score: float
    reason: str

@lru_cache(maxsize=1)
def patterns():
    return json.loads(Path(__file__).with_name('command_patterns.json').read_text())


def requested_kind(prompt):
    """A fan light is a light; a plain fan must not substitute for a light."""
    text = re.sub(r'\bfan lights?\b', 'light', prompt.lower())
    light = bool(re.search(r'\blights?\b', text))
    fan = bool(re.search(r'\bfans?\b', text))
    return 'lights' if light and not fan else 'fans' if fan and not light else None


def setup_for(rows):
    setup = copy.deepcopy(patterns())
    rooms = set()
    for row in rows:
        name = row.get('name', '')
        aliases = []
        if name.lower().startswith('mbr '):
            aliases = [prefix+name[4:] for prefix in ('main bedroom ', 'master bedroom ')]
        setup['entities'].append({'id':row['id'],'kind':'devices','name':name,
            'aliases':aliases,'dimmable':bool(row.get('dimmable'))})
        if row.get('room'):rooms.add(row['room'])
    for index,room in enumerate(sorted(rooms)):
        aliases = ['main bedroom','master bedroom'] if room.lower()=='mbr' else []
        setup['entities'].append({'id':room,'name':room,'kind':'rooms','aliases':aliases})
    return setup


def choose(prompt, rows=(), *, phone_stream=False, reply_context=None, threshold=.8):
    if reply_context or len(prompt)>1000 or '\n' in prompt:
        return None
    # No commands embedded in questions, quotations, conditions, schedules or plans.
    if re.search(r'[;"`]|\b(?:and|then|when|unless|tomorrow|later|instead|except|without)\b',prompt,re.I):
        return None
    try:
        route=Router(setup_for(rows)).classify(prompt)['route']
    except (ValueError,KeyError,TypeError):
        return None
    cap=route.get('capability');p={k:(int(v) if isinstance(v,float) and v.is_integer() else v) for k,v in route.get('parameters',{}).items()}
    if route['category'] not in ('fast_action','fast_read'):
        return None
    tool=None;args=None;score=.97
    if cap in ('home.power','home.brightness','home.room_power'):
        state=p.get('power')=='on' if cap!='home.brightness' else True
        if cap=='home.room_power':
            body={'action':'room','room':p['room'],'kind':p['kind'],'state':state}
        else:
            matches=[row for row in rows if row['id']==p['target']]
            if len(matches)!=1:return None
            row=matches[0];kind=requested_kind(prompt)
            if kind and (not isinstance(row.get('fan'),bool) or bool(row.get('fan')) != (kind=='fans')):return None
            body={'action':'switch','target':p['target'],'state':state}
            if cap=='home.brightness':
                if not row.get('dimmable') or not 1<=p['level']<=99:return None
                body['level']=p['level']
        tool='home';args=json.dumps(body)
    elif cap in ('computer.lock','computer.wake','computer.sleep','computer.screenshot','computer.photo'):
        tool='mac';args={'computer.lock':'lock','computer.wake':'wake','computer.sleep':'sleep',
                        'computer.screenshot':'screenshot','computer.photo':'photo'}[cap]
    elif cap in ('computer.say','computer.notify','computer.open_url'):
        tool='mac';args={'computer.say':'say','computer.notify':'notify','computer.open_url':'open'}[cap]+' '+str(p.get('text',p.get('url','')));score=.88
    elif cap in ('computer.bluetooth','computer.bluetooth_list'):
        tool='mac';args='bluetooth '+(p['power'] if cap=='computer.bluetooth' else 'list')
    elif cap=='computer.volume':
        tool='mac';args='volume '+str(p['level'])
    elif phone_stream:
        tool='device'
        simple={'phone.volume':'volume up','phone.volume_down':'volume down',
                'phone.mute':'volume mute','phone.unmute':'volume unmute',
                'phone.media_pause':'media pause','phone.media_resume':'media play',
                'phone.media_next':'media next','phone.media_previous':'media previous',
                'phone.camera':'camera','phone.selfie':'camera selfie','phone.notifications':'notifications'}
        if cap in simple:args=simple[cap]
        elif cap in ('phone.flashlight','phone.dnd'):args=('flashlight' if cap=='phone.flashlight' else 'dnd')+' '+p['power']
        elif cap=='phone.settings':args='settings '+p['panel']
        elif cap=='timer.start':args=f"timer {p['minutes']}m"
        elif cap=='phone.timer_seconds':args=f"timer {p['seconds']}s"
        elif cap in ('phone.alarm','phone.alarm_12h'):
            args=f"alarm {p['hour']}:{p.get('minute',0):02d}"+(' '+p['period'] if 'period' in p else '')
        elif cap=='phone.play':
            players={'music':'youtube music','spotify':'spotify','youtube':'youtube'}
            if p['target'] not in players:return None
            args='play '+p['query']+' on '+players[p['target']];score=.88
        elif cap=='phone.navigate':args='navigate '+p['to'];score=.86
        elif cap=='app.open':
            entity=next((e for e in patterns()['entities'] if e['id']==p['target']),None)
            if entity:args='open '+entity['name']
        # Calls/messages/garage never bypass their existing confirmation flow.
        if args is None:return None
        from server.skills.device import parse, BadCommand
        try:parse(args)
        except (BadCommand,ValueError):return None
    if tool is None or args is None or score<=max(.8,threshold):return None
    return Candidate(cap,tool,args,score,'unique full-command match; bounded parameters; target and executor validated')


async def prepare(prompt, *, phone_stream=False, reply_context=None):
    if os.getenv('FAST_COMMANDS_ENABLED','1').lower() not in ('1','true','yes'):return None
    try:threshold=float(os.getenv('FAST_COMMAND_CONFIDENCE','0.80'))
    except ValueError:return None
    if not .8<=threshold<=1:return None
    rows=[]
    if re.search(r'\b(?:lights?|fans?|dim|brightness)\b',prompt,re.I) and re.match(r'^(?:please |gajala[, ]+)?(?:turn|switch|set|dim)\b',prompt,re.I):
        from server import home_control
        global _catalog,_catalog_at
        try:
            if _catalog is None or time.monotonic()-_catalog_at>15:
                data=await asyncio.wait_for(home_control.request('/api/switches'),timeout=.75)
                if not data.get('available') or not data.get('loaded'):return None
                _catalog=data['switches'];_catalog_at=time.monotonic()
            rows=_catalog
        except (asyncio.TimeoutError,home_control.HomeError,KeyError):return None
    try:return choose(prompt,rows,phone_stream=phone_stream,reply_context=reply_context,threshold=threshold)
    except Exception:
        # Parsing/catalog failure is safe to fall back: no executor has run yet.
        return None
