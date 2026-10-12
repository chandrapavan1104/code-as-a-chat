"""Home actions use Gogon's single panel session; acknowledgements need read evidence."""
import asyncio
import hashlib
import json
import os
import re
import time
from typing import Literal
from urllib.parse import quote, urlparse
import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator
from server.skills.base import SkillResult


class HomeAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['status', 'list', 'history', 'automations', 'switch', 'room',
                    'all_off', 'climate', 'garage', 'schedule', 'automation']
    target: str | None = Field(default=None, min_length=1, max_length=120)
    room: str | None = Field(default=None, min_length=1, max_length=120)
    kind: Literal['all', 'lights', 'fans'] = 'all'
    state: bool | None = None
    level: int | None = Field(default=None, ge=1, le=99)
    mode: Literal['off', 'heat', 'cool', 'auto'] | None = None
    heat: float | None = Field(default=None, allow_inf_nan=False)
    cool: float | None = Field(default=None, allow_inf_nan=False)
    fan: Literal['auto', 'on'] | None = None
    at: str | None = None
    days: list[Literal['mon','tue','wed','thu','fri','sat','sun']] = Field(default_factory=lambda: ['mon','tue','wed','thu','fri','sat','sun'])
    enabled: bool | None = None

    @model_validator(mode='after')
    def required(self):
        if self.action in ('switch','climate','garage','automation') and not self.target:
            raise ValueError('Select a device/rule before changing it')
        if self.action in ('switch','room','garage','schedule') and self.state is None:
            raise ValueError('Specify the requested on/off or open/close state')
        if self.action == 'room' and not self.room:
            raise ValueError('Specify a room')
        if self.level is not None and (self.action not in ('switch','room') or not self.state):
            raise ValueError('Brightness is only supported when turning a dimmable light on')
        if self.action == 'climate' and not any(getattr(self,k) is not None for k in ('mode','heat','cool','fan')):
            raise ValueError('Specify mode, fan, heat or cool to change')
        if self.action == 'schedule' and (not self.at or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',self.at) or not self.days):
            raise ValueError('A schedule needs HH:MM local time and at least one day')
        if self.action == 'automation' and self.enabled is None:
            raise ValueError('Specify whether the rule should be enabled')
        return self


class HomeError(Exception):
    def __init__(self,message,*,uncertain=False):
        super().__init__(message)
        self.uncertain=uncertain


async def request(path, payload=None, *, method=None):
    base = os.getenv('GOGON_HOME_URL', 'http://127.0.0.1:8500').rstrip('/')
    parsed = urlparse(base)
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1','localhost','::1') or parsed.username or parsed.password:
        raise HomeError('GOGON_HOME_URL must point to the local Gogon service')
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(35, connect=3), trust_env=False) as client:
            response = await client.request(method or ('POST' if payload is not None else 'GET'), base+path, json=payload)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        try:
            detail = str(exc.response.json().get('detail', 'Home service rejected this action'))[:400]
        except ValueError:
            detail = 'Home service rejected this action'
        raise HomeError(detail, uncertain=payload is not None and exc.response.status_code>=500) from None
    except (httpx.HTTPError, ValueError):
        raise HomeError('Gogon home service did not respond. A timed-out command has an uncertain outcome and is not automatically retried.', uncertain=payload is not None) from None


async def snapshot():
    names = ('status','switches','climate','garage','automations')
    values = await asyncio.gather(*(request('/api/'+n) for n in names), return_exceptions=True)
    return {name: ({'error': str(value), 'connected': False, 'available': False} if isinstance(value,Exception) else value)
            for name,value in zip(names, values)}


def normalized(text):
    text = re.sub(r'[^a-z0-9]', '', text.lower())
    text = re.sub(r'^bedroom(\d)', r'br\1', text)
    return {'masterbedroom':'mbr','powderroom':'pwdr','secondfloor':'level2','thirdfloor':'level3'}.get(text,text)


def resolve(rows, target):
    key = normalized(target)
    exact = [r for r in rows if any(normalized(str(r.get(k,'')))==key for k in ('id','name'))]
    matches = exact or [r for r in rows if key in normalized(str(r.get('name',''))) or key in normalized(str(r.get('id','')))]
    if len(matches) != 1:
        options = '; '.join(f"{r['name']} ({r['id']})" for r in (matches or rows)[:12])
        raise HomeError(f"{'More than one device matches' if matches else 'No device matches'} {target!r}. Choose an exact device: {options}")
    return matches[0]


async def require_online():
    status = await request('/api/status')
    age = time.time() - (status.get('updated_at') or 0)
    if not status.get('connected') or age < 0 or age > 45:
        raise HomeError('The home panel is disconnected or its status is stale; no command was sent')
    return status


def observed(row, start, fields=None):
    if fields is not None:
        stamps = row.get('observed_fields') or {}
        return all((stamps.get(key) or 0) >= start and
                   (stamps.get(key) or 0) > (row.get('last_command_at',{}).get(key) or 0)
                   for key in fields)
    return row.get('state_confirmed') is True and (row.get('observed_at') or 0) >= start


async def set_switch(row, state, level=None):
    if row.get('reachable') is False:
        return SkillResult('blocked', f"{row['name']} is not responding; no command was sent", data={'device':row})
    if level is not None and not row.get('dimmable'):
        raise HomeError(f"{row['name']} does not support brightness")
    start = time.time()
    reply = await request('/api/switch', {'target':row['id'], 'state':state, 'level':level})
    current = next((d for d in reply.get('switches',[]) if d['id']==row['id']), {})
    matches = current.get('on') is state and (level is None or current.get('level')==level)
    desired = f'on at {level}%' if state and level else 'on' if state else 'off'
    if matches and observed(current,start):
        return SkillResult('succeeded', f"{row['name']} is {desired} (device read confirmed)", changed=True, data={'device':current})
    return SkillResult('unverified', f"{row['name']}: {desired} command acknowledged, but the matching device state is not verified", changed=True, data={'device':current})


async def perform(body: HomeAction, *, garage_confirmed=False):
    try:
        action = body.action
        if action in ('status','list'):
            data = await snapshot()
            status = data['status']
            if not status.get('connected'):
                return SkillResult('blocked', status.get('error') or 'The home panel is not connected',data=data)
            message = f"Home panel connected. {len(data['switches'].get('switches',[]))} lights/fans."
            if not status.get('zones'):
                message += ' No intrusion sensors are enrolled; the alarm cannot confirm the house is secure.'
            if status.get('troubles'):
                message += ' Panel trouble: '+', '.join(status['troubles'])
            return SkillResult('succeeded',message,data=data)
        if action in ('history','automations'):
            return SkillResult('succeeded',f'Home {action}',data=await request('/api/'+action))
        await require_online()
        if action in ('switch','room','all_off'):
            data = await request('/api/switches')
            if not data.get('available') or not data.get('loaded'):
                raise HomeError('The home device catalog is still loading; no command was sent')
            rows = data.get('switches',[])
            if action=='switch':
                return await set_switch(resolve(rows,body.target),body.state,body.level)
            if action=='room':
                rooms = sorted({r['room'] for r in rows})
                key = normalized(body.room)
                matches = [r for r in rooms if normalized(r)==key] or [r for r in rooms if key in normalized(r)]
                if len(matches)!=1:
                    raise HomeError('Choose an exact room: '+', '.join(rooms))
                rows = [r for r in rows if r['room']==matches[0] and
                        (body.kind=='all' or (bool(r.get('fan')) == (body.kind=='fans')))]
                state,level = body.state,body.level
            else:
                # Explicit per-device off also reaches unknown state devices;
                # Gogon's all-off only includes devices its cache thinks are on.
                state,level = False,None
            if not rows:
                raise HomeError('No devices match the requested room/type')
            results = []
            deadline = time.monotonic()+90
            for row in rows:
                if time.monotonic()>deadline:
                    results.append({'id':row['id'],**SkillResult('blocked',f"{row['name']}: not attempted; home action time budget reached").as_dict()})
                    continue
                try:
                    result = await set_switch(row,state,level)
                except HomeError as exc:
                    result = SkillResult('unverified',str(exc))
                results.append({'id':row['id'],**result.as_dict()})
            good = sum(r['status']=='succeeded' for r in results)
            return SkillResult('succeeded' if good==len(results) else 'unverified',
                f'{good}/{len(results)} home device states verified. '+ '\n'.join(r['message'] for r in results if r['status']!='succeeded'),
                changed=any(r['changed'] for r in results),data={'results':results})
        if action=='climate':
            rows = (await request('/api/climate')).get('thermostats',[])
            row = resolve(rows,body.target)
            values = {k:getattr(body,k) for k in ('mode','heat','cool','fan') if getattr(body,k) is not None}
            if any(k in values for k in ('heat','cool')) and row.get('unit') not in ('F','C'):
                raise HomeError('Thermostat temperature units are unknown; no setpoint command was sent')
            lo,hi = (13,29) if row.get('unit')=='C' else (55,85)
            for key in ('heat','cool'):
                if key in values and not lo<=values[key]<=hi:
                    raise HomeError(f'Temperature must be {lo}–{hi}°{row.get("unit","F")}')
            heat,cool = values.get('heat',row.get('heat')),values.get('cool',row.get('cool'))
            if heat is not None and cool is not None and heat>=cool:
                raise HomeError('Heat setpoint must be below the cooling setpoint')
            start = time.time()
            reply = await request('/api/climate', {'target':row['id'],**values})
            current = next((r for r in reply.get('thermostats',[]) if r['id']==row['id']),{})
            verified = all(current.get(k)==v for k,v in values.items()) and observed(current,start,values)
            return SkillResult('succeeded' if verified else 'unverified',
                f"{row['name']} thermostat settings {'confirmed by device read' if verified else 'acknowledged; matching settings not verified'}",
                changed=True,data={'thermostat':current})
        if action=='garage':
            if not garage_confirmed:
                return SkillResult('needs_permission','Garage movement requires fresh confirmation that you are at the door and can watch it')
            rows = (await request('/api/garage')).get('doors',[])
            row = resolve(rows,body.target)
            desired = 'open' if body.state else 'closed'
            if row.get('state') in ('opening','closing','stopped','unknown',None) or time.time()-(row.get('observed_at') or row.get('at') or 0)>90:
                raise HomeError('Garage state is stale, unknown or moving; no command was sent')
            if row['state']==desired:
                return SkillResult('succeeded',f"{row['name']} is already {desired}; no command sent",data={'door':row})
            start = time.time()
            reply = await request('/api/garage',{'target':row['id'],'open':body.state})
            deadline = time.monotonic()+25
            while time.monotonic()<deadline:
                await asyncio.sleep(2)
                try:
                    reply = await request('/api/garage')
                except HomeError:
                    break
                current = next((d for d in reply.get('doors',[]) if d['id']==row['id']),{})
                if current.get('state')==desired and observed(current,start):
                    return SkillResult('succeeded',f"{row['name']} is {desired} (door sensor confirmed)",changed=True,data=reply)
            return SkillResult('unverified',f"{row['name']} {'open' if body.state else 'close'} command acknowledged; final position is not yet verified. Check the door in person before another command.",changed=True,data=reply)
        rules = (await request('/api/automations')).get('rules',[])
        if action=='automation':
            rule = next((r for r in rules if r.get('id')==body.target),None)
            if not rule or rule.get('action',{}).get('type') not in ('switch','all_off'):
                raise HomeError('Only existing lighting/fan automation rules can be changed through Gajala')
            data = await request('/api/automations/'+quote(body.target,safe='')+'/enabled',{'enabled':body.enabled})
            return SkillResult('succeeded','Home automation '+('enabled' if body.enabled else 'disabled'),changed=True,data=data)
        if action=='schedule':
            target = resolve((await request('/api/switches')).get('switches',[]),body.target)['id'] if body.target else None
            task = {'type':'switch','target':target,'state':body.state} if target else {'type':'all_off'}
            if not target and body.state:
                raise HomeError('A whole-home schedule can only turn everything off')
            trigger = {'type':'time','at':body.at,'days':sorted(set(body.days))}
            identity = hashlib.sha256(json.dumps([task,trigger],sort_keys=True).encode()).hexdigest()[:16]
            rid = 'gajala-'+identity
            from server.skills.reminders import _local_timezone
            if any(r.get('id')==rid for r in rules):
                return SkillResult('succeeded','Equivalent home automation already exists',data={'id':rid})
            rule = {'id':rid,'name':f'Gajala: {target or "all lights/fans"} {"on" if body.state else "off"} at {body.at}',
                    'enabled':True,'trigger':trigger,'action':task}
            data = await request('/api/automations/'+rid,rule,method='PUT')
            return SkillResult('succeeded',f'Home schedule saved for {body.at} ({_local_timezone()})',changed=True,data=data)
    except HomeError as exc:
        return SkillResult('unverified' if exc.uncertain else 'blocked',str(exc))
