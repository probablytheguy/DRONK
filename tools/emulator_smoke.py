#!/usr/bin/env python3
"""Exercise foreground simulation in airplane mode; restore the prior mode afterward.

Usage: python3 tools/emulator_smoke.py --adb /path/to/adb --serial emulator-5554
Uses only Python stdlib and an already-running emulator. Does not install the APK.
"""
import argparse
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--adb', default='adb')
parser.add_argument('--serial', required=True)
parser.add_argument('--cot-output', type=Path, help='Optional synthetic XML output; keep outside source control')
args = parser.parse_args()
assert args.serial.startswith('emulator-'), 'This smoke test changes connectivity; use an emulator.'

def adb(*cmd):
    return subprocess.check_output([args.adb, '-s', args.serial, *cmd], text=True, timeout=30)

def dump():
    adb('shell', 'uiautomator', 'dump', '/data/local/tmp/ouitak-smoke.xml')
    return ET.fromstring(adb('shell', 'cat', '/data/local/tmp/ouitak-smoke.xml'))

def text(root):
    return '\n'.join(n.get('text', '') for n in root.iter('node'))

def tap(label):
    for node in dump().iter('node'):
        if node.get('text', '').casefold() == label.casefold():
            x1, y1, x2, y2 = map(int, re.findall(r'\d+', node.get('bounds')))
            adb('shell', 'input', 'tap', str((x1+x2)//2), str((y1+y2)//2))
            return
    raise AssertionError(f'Control not visible: {label}')

screen = list(map(int, re.search(r'(\d+)x(\d+)', adb('shell', 'wm', 'size')).groups()))

def swipe(up):
    w, h = screen
    first, last = (0.8, 0.3) if up else (0.3, 0.8)
    adb('shell', 'input', 'swipe', str(w//2), str(int(h*first)), str(w//2), str(int(h*last)), '400')

prior_mode = adb('shell', 'settings', 'get', 'global', 'airplane_mode_on').strip()
try:
    adb('shell', 'cmd', 'connectivity', 'airplane-mode', 'enable')
    assert adb('shell', 'settings', 'get', 'global', 'airplane_mode_on').strip() == '1'
    adb('shell', 'am', 'force-stop', 'org.ouitak.app')
    adb('shell', 'am', 'start', '-W', '-n', 'org.ouitak.app/.MainActivity')
    initial = text(dump())
    assert '0 observations' in initial and 'MARKER EXPIRY: 60S' in initial
    tap('Generate detection')
    root = dump()
    assert 'SIM-DRONE-001' in text(root)
    assert 'SIMULATION' in text(root)
    # A single observation keeps the entire XML preview reachable.
    cot = next((n.get('text') for n in root.iter('node') if n.get('text', '').startswith('<?xml')), None)
    for _ in range(6):
        if cot is not None:
            break
        swipe(True)
        cot = next((n.get('text') for n in dump().iter('node') if n.get('text', '').startswith('<?xml')), None)
    assert cot is not None, 'XML preview not found'
    for _ in range(4):
        swipe(False)
    event = ET.fromstring(cot)
    assert event.tag == 'event' and event.get('type') == 'a-u-A'
    assert event.get('how') == 'm-s'
    if args.cot_output:
        args.cot_output.write_text(cot)
    assert '1 active aircraft' in text(dump())
    tap('Generate detection')
    assert '1 active aircraft' in text(dump()), 'Repeated identity created a duplicate'
    tap('Aircraft: SIM-DRONE-001 (tap to switch)')
    tap('Generate detection')
    assert '2 active aircraft' in text(dump())
    tap('Aircraft: SIM-DRONE-002 (tap to switch)')
    tap('Start simulation')
    time.sleep(4)
    assert 'Simulation: running' in text(dump())
    tap('Stop simulation')
    stopped = text(dump())
    count = re.search(r'(\d+) observations', stopped).group(1)
    assert int(count) >= 3
    time.sleep(3)
    assert f'{count} observations' in text(dump())
    tap('Clear history')
    cleared = text(dump())
    assert '0 observations' in cleared
    # Additional monitoring/history controls can place the XML preview below
    # the viewport. Verify its cleared value by scrolling to it explicitly.
    for _ in range(8):
        if 'No event generated.' in cleared:
            break
        swipe(True)
        cleared = text(dump())
    assert 'No event generated.' in cleared, 'Cleared XML preview not found'
    print('PASS: airplane mode, launch, XML, stable identity, two aircraft, simulation, stop, clear')
finally:
    adb('shell', 'cmd', 'connectivity', 'airplane-mode', 'enable' if prior_mode == '1' else 'disable')
