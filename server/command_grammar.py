"""Compile known operations into proposals. This module has no execution backend."""
import copy
import re
import time
from urllib.parse import urlsplit
from hassil import Intents, recognize_all

CATEGORIES={'fast_action','fast_read'}
KINDS={'devices','dimmers','doors','computers','apps','workflows','contacts','folders','rooms'}


def validate_setup(setup):
    if not isinstance(setup,dict) or set(setup)-{'name','entities','capabilities','catalog_source'}:
        raise ValueError('Setup accepts only name, entities, capabilities and catalog_source')
    entities=setup.get('entities',[])
    capabilities=setup.get('capabilities',[])
    if len(entities)>500 or not 1<=len(capabilities)<=100:
        raise ValueError('Use up to 500 entities and 1–100 capabilities')
    seen=set()
    for entity in entities:
        if not isinstance(entity,dict) or entity.get('kind') not in KINDS or not entity.get('id') or not entity.get('name'):
            raise ValueError('Each entity needs id, kind and name')
        if entity['id'] in seen:
            raise ValueError('Entity IDs must be unique')
        seen.add(entity['id'])
        if not isinstance(entity.get('aliases',[]),list) or len(entity.get('aliases',[]))>30:
            raise ValueError('Aliases must be a list of up to 30 phrases')
        for phrase in [entity['id'],entity['name'],*entity.get('aliases',[])]:
            if not isinstance(phrase,str) or len(phrase)>120 or re.search(r'[{}<>\[\]|;()]',phrase):
                raise ValueError('Entity names/aliases must be plain phrases, up to 120 characters')
    seen=set()
    for cap in capabilities:
        if not isinstance(cap,dict) or set(cap)-{'id','name','category','sentences','required','confirmation','asynchronous','defaults'}:
            raise ValueError('Capability metadata cannot contain executable handlers, URLs or code')
        if not re.fullmatch(r'[a-z][a-z0-9_.-]{0,79}',cap.get('id','')) or cap['id'] in seen:
            raise ValueError('Capability IDs must be unique lowercase names')
        seen.add(cap['id'])
        if cap.get('category') not in CATEGORIES or not isinstance(cap.get('sentences'),list) or not 1<=len(cap['sentences'])<=30:
            raise ValueError('A capability needs fast_action/fast_read and 1–30 sentence templates')
        if any(not isinstance(s,str) or len(s)>500 for s in cap['sentences']):
            raise ValueError('Templates must be text of up to 500 characters')
        defaults=cap.get('defaults',{})
        if not isinstance(defaults,dict) or any(not isinstance(k,str) or not isinstance(v,(str,int,bool)) for k,v in defaults.items()):
            raise ValueError('Defaults must be static scalar parameters')
        if defaults.get('target') and defaults['target'] not in {e['id'] for e in entities}:
            raise ValueError('Default target must exist in the catalog')
        if not isinstance(cap.get('required',[]),list):
            raise ValueError('Required parameters must be a list')
    return copy.deepcopy(setup)


class Router:
    def __init__(self,setup):
        self.setup=validate_setup(setup)
        self.capabilities={c['id']:c for c in self.setup['capabilities']}
        lists={k:{'values':[]} for k in KINDS}
        for entity in self.setup['entities']:
            kinds=[entity['kind']]
            if entity['kind']=='devices' and entity.get('dimmable'):
                kinds.append('dimmers')
            for kind in kinds:
                for phrase in dict.fromkeys([entity['id'],entity['name'],*entity.get('aliases',[])]):
                    lists[kind]['values'].append({'in':phrase.lower(),'out':entity['id']})
        lists.update({'free_text':{'wildcard':True},
                      'settings_panel':{'values':[{'in':p,'out':v} for p,v in [('wifi','wifi'),('wi-fi','wifi'),('bluetooth','bluetooth'),('mobile data','internet'),('internet','internet'),('nfc','nfc')]]},
                      'device_kind':{'values':[{'in':'lights','out':'lights'},{'in':'fans','out':'fans'}]},
                      'meridiem':{'values':[{'in':'am','out':'am'},{'in':'pm','out':'pm'}]},
                      'power':{'values':[{'in':'on','out':'on'},{'in':'off','out':'off'}]},
                      'door_action':{'values':[{'in':'open','out':'open'},{'in':'close','out':'close'}]}})
        self.intents=Intents.from_dict({'language':'en','lists':lists,'intents':{
            cap['id']:{'data':[{'sentences':cap['sentences']}]} for cap in self.setup['capabilities']}})

    def classify(self,text,context=None):
        started=time.perf_counter()
        normalized=re.sub(r'\s+',' ',text.strip().lower().replace('’',"'")).strip(' .!?')
        normalized=re.sub(r'(\d{1,2}):(\d{2})',r'\1 colon \2',normalized)
        normalized=re.sub(r'^(?:could you|can you|please|hey gajala|gajala)[, ]+','',normalized)
        route={'category':'reasoning','capability':None,'parameters':{},
               'reason':'No registered command matched. Full-assistant fallback proposed; no model or tool was called.'}
        if not normalized:
            route.update(category='clarify',reason='Enter a request.')
        elif re.search(r"\b(?:don't|never)\b|\bdo not\b(?! disturb)",normalized):
            route.update(category='clarify',reason='Negated or mixed instructions require clarification; no action proposed.')
        elif re.match(r'^(?:yes|no|stop that|cancel that|turn it (?:on|off)|do that|the other one)$',normalized):
            route.update(category='clarify',reason='A contextual reference needs an unambiguous pending operation; the lab will not guess.',
                         context=context or {})
        elif re.match(r'^(?:call|dial|play|put|turn|switch|set|start|open|lock|unlock|wake|sleep|connect|disconnect|navigate|take|make|say|notify|text|compose|whatsapp)\b',normalized) and (re.search(r'\b(?:and|then|after|tomorrow|later|unless|if)\b|;',normalized) or '\n' in text):
            route.update(category='clarify',reason='Compound, conditional or deferred commands need planning; no single action proposed.')
        else:
            matches=[]
            for match in recognize_all(normalized,self.intents):
                params={k:v.value for k,v in match.entities.items()}
                cap=self.capabilities[match.intent.name]
                explicit_count=len(params)
                if cap['id']=='phone.play' and 'target' not in params and not re.search(r'\b(?:songs?|music|playlist|album|tracks?)\b',str(params.get('query',''))):
                    continue
                params={**cap.get('defaults',{}),**params}
                if 'url' in params:
                    # HassIL strips punctuation; retain the original URL verbatim.
                    raw_url=re.search(r'\bopen\s+(\S+)\s+on\s+',text,re.I)
                    if not raw_url:
                        continue
                    params['url']=raw_url.group(1)
                    parsed=urlsplit(str(params['url']))
                    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
                        continue
                if any(isinstance(v,str) and len(v)>500 for v in params.values()):
                    continue
                if any(k not in params for k in cap.get('required',[])):
                    continue
                item={'category':cap['category'],'capability':cap['id'],'parameters':params,
                      'reason':'Matched a registered sentence and catalog-backed slots.',
                      '_specificity':explicit_count,
                      'confirmation':cap.get('confirmation'),'asynchronous':bool(cap.get('asynchronous'))}
                if item not in matches:
                    matches.append(item)
            # A named player wins over the default-player form of the same intent.
            if matches and len({m['capability'] for m in matches})==1:
                best=max(m['_specificity'] for m in matches)
                matches=[m for m in matches if m['_specificity']==best]
            for item in matches:
                item.pop('_specificity',None)
            if len(matches)==1:
                route=matches[0]
            elif len(matches)>1:
                route.update(category='clarify',reason='Multiple valid capabilities or targets matched; no target selected.',candidates=matches)
            elif re.search(r'\b(?:turn|switch|dim|open|close|lock|wake|timer)\b',normalized) and not re.match(r'^(?:why|how|explain|research|compare|write|build|debug)\b',normalized):
                route.update(category='clarify',reason='Possible command, but its target/parameters did not match the registered setup. No action proposed.')
        decision_ms=round((time.perf_counter()-started)*1000,3)
        if route['category']=='fast_action':
            reply=f"Would propose {route['capability']} with {route['parameters']}. Nothing executed."
            if route.get('confirmation'):
                reply+=' Would require '+route['confirmation']+'.'
        elif route['category']=='fast_read':
            reply=f"Would look up {route['capability']} with {route['parameters']}. No live lookup executed."
        else:
            reply=route['reason']
        return {'route':route,'reply':reply,'decision_ms':decision_ms,'execution_ms':0,
                'state':{},'simulated':True,'executed':False,
                'trace':[{'stage':'hassil_intent_and_slot_matching','ms':decision_ms},
                         {'stage':'execution','status':'disabled'}]}
