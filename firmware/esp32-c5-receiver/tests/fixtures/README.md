# Offline fixture provenance

All `.hex` files are **synthetic**, generated locally by `tests/make_fixtures.c` using the pinned OpenDroneID in-memory encoder plus manually assembled Beacon/NAN envelopes. They are raw 802.11 bytes without radiotap or FCS, never transmitted and never captured from a drone. The Basic ID `SYNTH-C5-TEST` identifies test data. No real Avata reception is represented.

Expected decoded values: aircraft `(38,-90)`, HAE 123.5 m; System type 0/takeoff `(38.1,-90.1)`, HAE 120 m; Location time 123.4 seconds after hour; System raw timestamp 100000 seconds since 2019. These are expected fixture values, not ground truth for any real aircraft.

- `synthetic-beacon.hex`: ASD-STAN OUI, type 0D, counter 7, three-member Basic ID/Location/System pack.
- `synthetic-nan.hex`: supported follow-up service descriptor carrying the same three-member pack.
- `unrelated-beacon.hex`: ordinary non-RID Beacon with SSID IE.

Tests mutate these files in memory for malformed lengths, prefixes, unknown versions, sentinels, invalid positions, unrelated services, duplicate IDs and other rejection cases. The sanitizer harness also enumerates every prefix and all 256 values at every byte position. No over-air compatibility claim follows from locally constructed fixtures.

Regenerate only offline from the `tests` directory:

```bash
cc -std=c11 -DODID_DISABLE_PRINTF -I../components/odid/vendor \
  make_fixtures.c ../components/odid/vendor/opendroneid.c -lm -o .build/make_fixtures
.build/make_fixtures
```

Create `.build` first if tests have not run. Frame bodies contain no variable wall-clock time; regeneration is deterministic.

## Preflight regression fixtures

`tests/make_preflight_fixtures.py` deterministically derives the following from the original **synthetic** fixtures. Run it from any directory using Python 3; no hardware or network access occurs. These are compatible-layout regression cases, not historical drone captures or proof of complete standards conformance.

- `synthetic-{beacon,nan}-v{0,1,2}.hex`: outer/member version headers, with legacy System reserved fields zeroed. Older reserved bytes are also poisoned in a separate in-memory test to ensure they never become timestamps/altitude.
- `synthetic-{beacon,nan}-reserved.hex`: a complete type-6 member appended to the otherwise valid pack; decoder must retain the known ID/Location and skip the slot.
- `synthetic-{beacon,nan}-padding.hex`: eight trailing zero bytes inside the declared transport payload, outside the declared member extent.
- `synthetic-{beacon,nan}-future-member.hex`: System member version 3, safely skipped; operator location stays unavailable, without suppressing the valid Basic ID/Location.
- `unsupported-{beacon,nan}-v3.hex`: unknown outer version; no detection.
- `malformed-{beacon,nan}-padding.hex`: nonzero trailing data; no detection.

All checked-in fixtures feed the ASan/UBSan prefix/byte-mutation harness, which now also exercises production result accounting and JSON serialization. Dedicated host-model tests exercise the production bounded drain helper using an eight-slot queue and real parser, including exact overflow accounting, empty waits, packet/time slice limits and sustained-load yielding. This does not emulate the ESP-IDF driver, FreeRTOS scheduling, USB hardware or real RF reception.
