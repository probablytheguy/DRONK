#!/usr/bin/env python3
"""Scan tracked working-tree text. Emit categories/locations, never matched values.
Optional private exact-match inventory stays outside Git; this is not a full
secret scanner or a history/publication approval tool.
"""
import argparse
import ipaddress
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
DOC_NETS=[ipaddress.ip_network(n) for n in ('192.0.2.0/24','198.51.100.0/24','203.0.113.0/24')]
STANDARD_MACS={'00:00:00:00:00:00','00:00:00:00:00:01','ff:ff:ff:ff:ff:ff','51:6f:9a:01:00:00','88:69:19:9d:92:09'}


def findings(text, private_values=()):
    result=[]
    for number,line in enumerate(text.splitlines(),1):
        categories=set()
        if any(value and value in line for value in private_values):categories.add('private-exact-match')
        if re.search(r'/' + r'home/[^/\s`"\x27]+/',line):categories.add('absolute-personal-home')
        for token in re.findall(r'(?i)(?:[0-9a-f]{2}:){5}[0-9a-f]{2}',line):
            if token.lower() not in STANDARD_MACS and not int(token[:2],16)&3: categories.add('non-synthetic-MAC')
        for token in re.findall(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])',line):
            try:address=ipaddress.ip_address(token)
            except ValueError:continue
            if 'OpenJDK ' in line or 'ISO/IEC 9899:1999 Chapter' in line:continue
            if address.is_loopback or address.is_unspecified or token in ('255.255.255.255','5.5.1.8'):continue
            if not any(address in network for network in DOC_NETS):categories.add('non-example-IP')
        if re.search(r'-----BEGIN (?:[A-Z ]*PRIVATE KEY|CERTIFICATE)-----',line):categories.add('embedded-key-or-certificate')
        for category in sorted(categories):result.append((number,category))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-values-file',help='Protected JSON list, or object with values list; never committed')
    parser.add_argument('--index',action='store_true',help='Scan staged text instead of the working copy')
    args=parser.parse_args()
    values=[]
    if args.private_values_file:
        try:
            data=json.loads(Path(args.private_values_file).read_text())
            values=data['values'] if isinstance(data,dict) else data
            if not isinstance(values,list) or not all(isinstance(v,str) for v in values):raise ValueError()
        except (OSError,ValueError,KeyError):
            print('Cannot read private match inventory; details withheld',file=sys.stderr);return 2
    names=filter(None,subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0'))
    count=0
    for name in names:
        try:text=subprocess.check_output(['git','show',':'+name],cwd=ROOT,stderr=subprocess.DEVNULL).decode() if args.index else (ROOT/name).read_text()
        except (UnicodeError,OSError,subprocess.CalledProcessError):continue
        for line,category in findings(text,values):
            print(f'{name}:{line}: {category}');count+=1
    print(f'Tracked source findings: {count}; matched values withheld. History and external artifacts are not covered.')
    return 1 if count else 0


if __name__=='__main__':raise SystemExit(main())
