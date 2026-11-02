#!/usr/bin/env python3
"""Verify receiver-side ATAK Unknown Markers UI dumps before/after an A update.

Usage: python3 tools/verify_atak_rows.py BEFORE.xml AFTER.xml
The test screen must contain exactly the two synthetic fixture rows. This checks
ATAK state, not a UDP send result. Take screenshots too to verify rendering.
"""
import argparse
import json
import xml.etree.ElementTree as ET

parser = argparse.ArgumentParser()
parser.add_argument('before')
parser.add_argument('after')
parser.add_argument('--trails', action='store_true', help='Verify synthetic Drawing Objects rows instead of aircraft rows')
args = parser.parse_args()

def rows(path):
    root = ET.parse(path)
    prefix = 'com.atakmap.app.civ:id/'
    titles = [n.get('text') for n in root.iter('node') if n.get('resource-id') == prefix + 'hierarchy_manager_list_item_title']
    coords = [n.get('text') for n in root.iter('node') if n.get('resource-id') == prefix + 'hierarchy_manager_list_item_desc']
    assert len(titles) == len(coords) == 2, 'Expected exactly two receiver-side aircraft rows'
    label_prefix = 'SIM HISTORY ' if args.trails else ''
    assert sorted(titles) == [label_prefix + 'SIM-DRONE-001', label_prefix + 'SIM-DRONE-002'], 'Unexpected or duplicate callsign'
    titles = [title.removeprefix(label_prefix) for title in titles]
    return dict(zip(titles, coords))

before, after = rows(args.before), rows(args.after)
assert before['SIM-DRONE-001'] != after['SIM-DRONE-001'], 'Aircraft A did not update'
assert before['SIM-DRONE-002'] == after['SIM-DRONE-002'], 'Aircraft B unexpectedly changed'
print(json.dumps({'result': 'PASS', 'receiver_rows_before': 2, 'receiver_rows_after': 2,
                  'before': before, 'after': after}, ensure_ascii=True, indent=2))
