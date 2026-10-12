"""Authenticated phone controls; garage presence approval is one-use and expires."""
import secrets
import threading
import time
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from server import home_control

router = APIRouter(prefix='/home',tags=['home'])
_tickets = {}
_lock = threading.Lock()


@router.get('')
async def get_home():
    return await home_control.snapshot()


@router.post('/action')
async def home_action(body: home_control.HomeAction):
    if body.action=='garage':
        raise HTTPException(403,'Garage commands require fresh presence confirmation')
    return (await home_control.perform(body)).as_dict()


class GarageRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    target: str = Field(min_length=1,max_length=120)
    open: bool


@router.post('/garage/request')
async def garage_request(body: GarageRequest):
    try:
        await home_control.require_online()
        row = home_control.resolve((await home_control.request('/api/garage')).get('doors',[]),body.target)
    except home_control.HomeError as exc:
        raise HTTPException(409,str(exc)) from None
    now = time.time()
    token = secrets.token_urlsafe(24)
    with _lock:
        for old in list(_tickets):
            if _tickets[old]['expires_at']<=now:
                del _tickets[old]
        if len(_tickets)>=100:
            raise HTTPException(429,'Too many pending garage confirmations')
        _tickets[token]={'target':row['id'],'open':body.open,'expires_at':now+30}
    return {'token':token,'expires_at':now+30,'name':row['name']}


class GarageConfirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    token: str = Field(min_length=16,max_length=100)
    present: bool


@router.post('/garage/confirm')
async def garage_confirm(body: GarageConfirmation):
    with _lock:
        ticket = _tickets.pop(body.token,None)
    # Consume before sending: a timeout or duplicate request must not move the door twice.
    if not ticket or ticket['expires_at']<=time.time():
        raise HTTPException(409,'Confirmation expired or already used; start again at the garage')
    if not body.present:
        raise HTTPException(403,'You must be at the garage and able to watch it')
    result = await home_control.perform(home_control.HomeAction(action='garage',
        target=ticket['target'],state=ticket['open']),garage_confirmed=True)
    return result.as_dict()
