# Offline serial fixtures

captured-boot.sanitized.txt is the real 1,282-byte discovery transcript, reviewed for secrets/identifiers; contains no drone detections. Original LF/CRLF bytes retained. See docs/milestone-0.5/SANITIZATION.md.

synthetic-source-detections.jsonl is SYNTHETIC, never captured hardware data. Keys/number types derive from Unified S3 src/raw/skyspy.cpp send_json_fast at 6e5e282 and pre-fix ce6e231. MACs are locally administered synthetic values; IDs and positions are invented. These objects deliberately omit typed IDs/times/validity because upstream omits them. They produce candidates, not verified aircraft. Source SIMULATION is supplied outside the JSON.
