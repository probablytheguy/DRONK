# Synthetic C5 protocol fixture

`observation.json` is generated from the C5-0.1 host decoder's synthetic Beacon fixture (`firmware/esp32-c5-receiver/tests/fixtures/synthetic-beacon.hex`) using its production JSON serializer. Receiver ID and session are replaced with deterministic valid host-test values. It is not a captured RF observation. `SYNTH-C5-TEST` coordinates are test values only. Tests feed it through explicit fixture/PTY inputs; no aircraft data is transmitted over RF or to TAK.
