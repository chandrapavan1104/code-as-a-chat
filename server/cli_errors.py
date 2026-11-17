"""Separate actionable provider failures from session telemetry and billing claims."""
import json
import re


def detail(stdout, stderr=''):
    try:
        data=json.loads(stdout)
        if isinstance(data,dict):
            value=data.get('result') or data.get('message')
            if isinstance(value,str) and value.strip():return value.strip()[:1200]
            if data.get('errors'):return str(data['errors'])[:1200]
    except (ValueError,TypeError):pass
    return (stderr.strip() or stdout.strip() or 'No diagnostic was returned')[:1200]


def kind(text):
    low=text.lower()
    if any(x in low for x in ('unsupported_client','client is no longer supported','ineligibletiererror')):
        return 'unsupported_client'
    if any(x in low for x in ('oauth session expired','failed to authenticate','not logged in',
            'invalid_grant','authentication_error','refresh token','unauthorized','401')):
        return 'authentication'
    return None


def historical_context(text):
    """Old saved failures stay readable, but cumulative telemetry is not new spend."""
    marker=re.search(r'\[claude error code \d+\]\s*',text)
    if not marker:return text
    try:
        data,end=json.JSONDecoder().raw_decode(text[marker.end():])
        if isinstance(data,dict) and data.get('is_error'):
            return (text[:marker.start()]+'Claude request failed: '+detail(json.dumps(data))+
                '\nThe discarded CLI telemetry contains session totals, not a verified bill for this request.'+
                text[marker.end()+end:])
    except (ValueError,TypeError):pass
    return text
