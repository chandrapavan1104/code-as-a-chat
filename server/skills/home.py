"""The home's device catalog resolves targets; phone confirmations guard garage movement."""
import json
import re
import shlex
from pydantic import ValidationError
from server import home_control, phone_bridge
from server.skills import register
from server.skills.base import Skill, SkillResult


def parse(prompt):
    text=prompt.strip()
    if text.startswith('{'):
        return home_control.HomeAction.model_validate_json(text)
    words=shlex.split(text)
    if not words:
        return home_control.HomeAction(action='status')
    action=words[0].lower()
    if action in ('status','list','history','automations','all-off') and len(words)==1:
        return home_control.HomeAction(action=action.replace('-','_'))
    if action in ('climate','garage') and len(words)==1:
        return home_control.HomeAction(action='list')
    if action in ('switch','room') and len(words) in (3,4):
        if words[2].lower() not in ('on','off'):
            raise ValueError('Choose on or off')
        data={'action':action,'state':words[2].lower()=='on'}
        data['target' if action=='switch' else 'room']=words[1]
        if len(words)==4:
            data['level']=int(words[3].rstrip('%'))
        return home_control.HomeAction(**data)
    if action=='garage' and len(words)==2 and words[1] in ('open','close'):
        return home_control.HomeAction(action='garage',target='garage',state=words[1]=='open')
    raise ValueError('Use home status | list | switch "device name" on|off [1–99] | room "room name" on|off | all-off | garage open|close, or a JSON home action')


class HomeSkill(Skill):
    name='home'
    description='Control Gogon home lights, fans, thermostats and garage; read home status and schedules.'
    agent_doc='''HOME AUTOMATION, not phone controls. For household lights/fans, dimming,
thermostats, garage, panel status and home schedules use home. First call home list
(or status) to retrieve exact devices/rooms/states; never invent an id or silently
pick between similar devices. Bedroom 3 is Br3; master bedroom is Mbr. JSON args:
{"action":"switch","target":"exact-id","state":true,"level":50},
{"action":"room","room":"exact room","kind":"lights","state":false},
{"action":"climate","target":"level-2","cool":74,"mode":"cool"},
{"action":"garage","target":"garage","state":true} (open) or false (close).
Garage ALWAYS asks the owner's phone for fresh presence confirmation; never infer
presence or confirmation, never bypass this through another tool/CLI. Garage ACK
is not final position. Unknown or stale state is not off or secure. All-off is
lights/fans only. Alarm arm/disarm, sensor bypass and door locks are not exposed.
Home schedules use {"action":"schedule","target":"exact-switch-id","state":false,
"at":"22:00","days":["mon","tue","wed","thu","fri","sat","sun"]} in Mac's
local timezone; omit target only for all lights/fans OFF. Read rules with automations;
enable/disable only lighting rules via {"action":"automation","target":"rule-id",
"enabled":false}. Home schedules execute in Gogon, not reminder notifications.
Report partial, blocked and unverified outcomes honestly; never auto-retry uncertain
physical actions. Status/list data has no enrolled intrusion sensors currently;
therefore connected/Ready does not mean the house is protected.'''

    async def run(self,prompt='',**kwargs):
        try:
            body=parse(prompt)
        except (ValueError,ValidationError) as exc:
            return SkillResult('blocked',str(exc))
        source=kwargs.get('source_prompt') or ''
        if body.action not in ('status','list','history','automations') and re.search(
                r'\bread[- ]only\b|\b(?:do not|don\'t|without)\b.{0,100}\b(?:change|operate|control|move)\b',source,re.I):
            return SkillResult('blocked','The owner requested read-only home access; no command was sent')
        if body.action=='garage':
            try:
                await home_control.require_online()
                door=home_control.resolve((await home_control.request('/api/garage')).get('doors',[]),body.target)
                reply=await phone_bridge.request(kwargs.get('on_event') if kwargs.get('phone_stream') else None,'home.confirm',
                    {'target':door['id'],'name':door['name'],'open':body.state},timeout=45)
            except (home_control.HomeError,phone_bridge.PhoneUnavailable) as exc:
                return SkillResult('needs_permission',str(exc)+' Use Gajala in the app while at the garage.')
            approval=reply.get('data') or {}
            if reply.get('ok') is not True or approval.get('confirmed') is not True or approval.get('present') is not True:
                return SkillResult('blocked','Garage command cancelled; no command was sent')
            return await home_control.perform(body,garage_confirmed=True)
        result=await home_control.perform(body)
        # Tool observations must carry the catalog for routing, not just a count.
        if body.action in ('status','list','history','automations'):
            return SkillResult(result.status,result.message+'\n'+json.dumps(result.data,ensure_ascii=False),data=result.data)
        return result

register(HomeSkill())
