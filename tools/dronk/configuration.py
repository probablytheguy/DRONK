"""Private, file-based DRONK settings. Errors deliberately omit supplied values."""
import os
from pathlib import Path
import re
import stat
import tomllib

ENV = {'host':'DRONK_TAK_HOST', 'port':'DRONK_TAK_PORT',
       'connect_address':'DRONK_TAK_CONNECT_ADDRESS', 'client_p12':'DRONK_TAK_CLIENT_P12',
       'ca_file':'DRONK_TAK_CA_FILE', 'password_file':'DRONK_TAK_PASSWORD_FILE', 'forward_pilot_location':'DRONK_TAK_FORWARD_PILOT_LOCATION'}
ALIAS = re.compile(r'[A-Za-z0-9_-]{1,32}')


def load_settings(config_path=None, environ=None):
    env=os.environ if environ is None else environ
    if 'DRONK_TAK_PASSWORD' in env:
        raise ValueError('Plaintext password overrides are unsupported; use DRONK_TAK_PASSWORD_FILE')
    selected=config_path or env.get('DRONK_CONFIG')
    path=Path(selected).expanduser() if selected else Path(env.get('XDG_CONFIG_HOME',str(Path.home()/'.config')))/'dronk/config.toml'
    data={}
    if selected or path.exists():
        if path.resolve().is_relative_to(Path(__file__).resolve().parents[2]):
            raise ValueError("Private DRONK configuration must live outside the repository")
        try:
            if not path.is_file() or stat.S_IMODE(path.stat().st_mode)&0o077:
                raise ValueError('DRONK configuration must be a private regular file (chmod 600)')
            with path.open('rb') as source: data=tomllib.load(source)
        except (OSError,tomllib.TOMLDecodeError):
            raise ValueError('Cannot read DRONK configuration; contents and path withheld') from None
    if set(data)-{'mode','tak','receivers','expected_receivers','receiver_aliases','legacy_namespace','capture_dir','db_path','analysis'}:
        raise ValueError('Unknown DRONK configuration key')
    tak=data.get('tak',{})
    if not isinstance(tak,dict) or set(tak)-set(ENV):
        raise ValueError('Unknown TAK setting; use password_file, never a plaintext password')
    if 'DRONK_TAK_PASSWORD' in env:
        raise ValueError('Plaintext password overrides are unsupported; use DRONK_TAK_PASSWORD_FILE')
    tak=dict(tak)
    for key,name in ENV.items():
        if name in env: tak[key]=env[name]
    for key,value in tak.items():
        if key not in ('port','forward_pilot_location') and not isinstance(value,str):
            raise ValueError('Invalid TAK setting type; values withheld')
        if key=='port' and (isinstance(value,bool) or not isinstance(value,(str,int))):
            raise ValueError('Invalid TAK port type')
    result=dict(data,tak=tak)
    mode=env.get('DRONK_MODE',data.get('mode','CAPTURE'))
    if not isinstance(mode,str): raise ValueError('Invalid DRONK mode')
    result['mode']=mode.upper()
    for name in ('receivers','expected_receivers','receiver_aliases'):
        values=result.setdefault(name,{})
        if not isinstance(values,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in values.items()):
            raise ValueError('Receiver configuration must contain alias/value strings')
    return result


ANALYSIS_KEYS=('sighting_gap_seconds','packet_gap_seconds','edge_seconds','status_silence_seconds')


def validate_runtime(config):
    """Validate before spawning a worker, opening any device, or creating TLS."""
    result=dict(config)
    zone=(result.get('analysis') or {}).get('time_zone')
    if zone is not None:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            if not isinstance(zone,str): raise ValueError
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError,ValueError):
            raise ValueError('Display time zone must be an IANA name such as America/Chicago') from None
    mode=result.get('mode') or ('DEMO' if result.get('demo') else 'LIVE' if result.get('forward_live') else 'CAPTURE')
    if mode not in ('DEMO','CAPTURE','LIVE'): raise ValueError('DRONK mode must be DEMO, CAPTURE or LIVE')
    devices=result.get('devices',{})
    expected=result.get('expected',{})
    aliases=result.get('receiver_aliases',{})
    if len(devices)>8 or set(expected)-set(devices) or set(aliases)-set(devices):
        raise ValueError('Receiver settings must reference configured streams (maximum eight)')
    if any(not ALIAS.fullmatch(k) or not isinstance(v,str) or not v for k,v in devices.items()):
        raise ValueError('Invalid configured receiver stream')
    labels=[aliases.get(k,k) for k in devices]
    if any(not ALIAS.fullmatch(v) for v in labels) or len(set(labels))!=len(labels):
        raise ValueError('Receiver aliases must be distinct public labels')
    # Default public identity is the stream label; a MAC-derived stream is unsafe.
    if any(re.search(r'(?i)(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}|c5-[0-9a-f]{12}',v) for v in [*devices,*aliases.values()]):
        raise ValueError('Public receiver labels must not contain factory identifiers')
    if mode=='DEMO' and (devices or expected or result.get('forward_live')):
        raise ValueError('DEMO cannot access receivers or TAK')
    if mode=='CAPTURE' and result.get('forward_live'):
        raise ValueError('CAPTURE cannot forward to TAK')
    if mode=='LIVE':
        if not devices: raise ValueError('LIVE requires explicit physical receiver configuration')
        for name in ('tak_host','client_p12','password_file','ca_file'):
            value=result.get(name)
            if not isinstance(value,str) or not value.strip():
                raise ValueError('LIVE requires explicit TAK host and protected client, CA and password-file paths')
        host=result['tak_host']
        if any(c.isspace() for c in host) or '/' in host or len(host)>253 or host.endswith('.invalid') or host.startswith('YOUR_'):
            raise ValueError('Invalid TAK certificate hostname')
        try: port=int(result.get('tak_port') or 8089)
        except (ValueError,TypeError): raise ValueError('Invalid TAK port') from None
        if not 1<=port<=65535: raise ValueError('Invalid TAK port')
        result['tak_port']=port
        address=result.get('tak_connect_address') or host
        if not isinstance(address,str) or any(c.isspace() for c in address) or '/' in address or len(address)>253:
            raise ValueError('Invalid TAK connection address')
        result['tak_connect_address']=address
    policy=result.get('forward_pilot_location')
    if policy is None: policy=False
    if isinstance(policy,str) and policy.lower() in ('true','false'): policy=policy.lower()=='true'
    if not isinstance(policy,bool): raise ValueError('Pilot forwarding setting must be true or false')
    result['forward_pilot_location']=policy
    for key,label in (('capture_dir','Capture directory'),('db_path','DRONK database')):
        value=result.get(key)
        if value is not None:
            if not isinstance(value,str) or not value.strip() or not Path(value).expanduser().is_absolute():
                raise ValueError(label+' must be an absolute path')
            value=str(Path(value).expanduser())
            if Path(value).resolve().is_relative_to(Path(__file__).resolve().parents[2]):
                raise ValueError(label+' must live outside the repository')
        result[key]=value
    result.update(mode=mode,demo=mode=='DEMO',forward_live=mode=='LIVE',devices=devices,expected=expected,receiver_aliases=aliases)
    return result


def resolve(args, devices, expected):
    """Precedence: explicit CLI > environment > private TOML > safe defaults."""
    data=load_settings(getattr(args,'config',None))
    selected=getattr(args,'mode',None)
    if getattr(args,'demo',False):
        if selected and selected!='DEMO': raise ValueError('Conflicting DRONK modes')
        selected='DEMO'
    if getattr(args,'forward_live',False):
        if selected and selected!='LIVE':
            raise ValueError('Conflicting DRONK forwarding mode')
        selected='LIVE'
    mode=selected or data['mode']
    if mode=='DEMO':
        # Explicit hardware arguments still reject. Stored hardware settings are
        # ignored so the same private TOML can safely launch a standalone demo.
        if devices or expected or getattr(args,'receiver_alias',[]):
            raise ValueError('DEMO cannot use explicit receiver arguments')
        configured_devices,configured_expected,configured_aliases={},{},{}
    else:
        configured_devices=devices or data['receivers']
        configured_expected=expected or data['expected_receivers']
        configured_aliases=data['receiver_aliases']
    result=dict(vars(args),mode=mode,devices=configured_devices,expected=configured_expected,receiver_aliases=configured_aliases)
    result['legacy_namespace']=data.get('legacy_namespace','')
    # Analysis: display time zone CLI > DRONK_TIME_ZONE > TOML > America/Chicago; thresholds from TOML only
    # (unset ones keep what the database already uses). Stored timestamps are always UTC.
    analysis=data.get('analysis',{})
    if not isinstance(analysis,dict) or set(analysis)-{'time_zone',*ANALYSIS_KEYS}:
        raise ValueError('Unknown DRONK analysis setting')
    for key in ANALYSIS_KEYS:
        if key in analysis and (isinstance(analysis[key],bool) or not isinstance(analysis[key],int)):
            raise ValueError('DRONK analysis thresholds must be whole seconds')
    result['analysis']=dict({key:analysis.get(key) for key in ANALYSIS_KEYS},
                            time_zone=getattr(args,'time_zone',None) or os.environ.get('DRONK_TIME_ZONE') or analysis.get('time_zone') or 'America/Chicago')
    # Capture: CLI > DRONK_CAPTURE_DIR > TOML > default state directory. DEMO captures only when a
    # directory is given explicitly, so synthetic sessions never land beside field captures by default.
    explicit=getattr(args,'capture_dir',None) or os.environ.get('DRONK_CAPTURE_DIR')
    if getattr(args,'no_capture',False):
        result['capture_dir']=None
    elif mode=='DEMO':
        result['capture_dir']=explicit
    else:
        from capture_log import default_directory
        result['capture_dir']=explicit or data.get('capture_dir') or str(default_directory())
    # Database: same precedence and the same DEMO rule; it indexes captures, so no capture means no live index.
    explicit_db=getattr(args,'db',None) or os.environ.get('DRONK_DB')
    if getattr(args,'no_db',False):
        result['db_path']=None
    elif mode=='DEMO':
        result['db_path']=explicit_db
    else:
        from dronk_db import default_path
        result['db_path']=explicit_db or data.get('db_path') or str(default_path())
    for key in ENV:
        target={'host':'tak_host','port':'tak_port','connect_address':'tak_connect_address'}.get(key,key)
        value=getattr(args,target,None)
        result[target]=value if value is not None else data['tak'].get(key)
        if key in ('client_p12','ca_file','password_file') and result[target]:
            result[target]=str(Path(result[target]).expanduser())
    return validate_runtime(result)


def validate_credentials(config):
    """Read metadata only, before LIVE can open a receiver or start TLS."""
    if config.get('mode')!='LIVE': return
    for key in ('client_p12','password_file','ca_file'):
        try:
            path=Path(config[key]).expanduser()
            metadata=path.stat()
            if not stat.S_ISREG(metadata.st_mode) or not os.access(path,os.R_OK): raise OSError()
            if key!='ca_file' and stat.S_IMODE(metadata.st_mode)&0o077: raise OSError()
        except (OSError,KeyError,TypeError):
            raise ValueError('LIVE requires readable protected credential files; paths/details withheld') from None
