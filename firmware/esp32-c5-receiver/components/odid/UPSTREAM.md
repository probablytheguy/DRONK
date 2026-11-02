# OpenDroneID core-c provenance

Repository: https://github.com/opendroneid/opendroneid-core-c

Pinned commit: `849c03adbe0cdd68997a462fafab397edcfe53f3`.

`vendor/opendroneid.c`, `vendor/opendroneid.h`, and `vendor/LICENSE` are copied unchanged from that revision (source paths `libopendroneid/` and root LICENSE). Apache-2.0; source copyright/SPDX notices retained. No root NOTICE file was present in this revision. This import does not include the upstream Wi-Fi builders, Linux transmitter programs, submodules, or upstream dependencies.

The core contains pure memory encoders as well as decoders. Firmware calls only decoding/initialization; offline tests may encode fixtures in memory. No RF transmission API is included in this component. `ODID_DISABLE_PRINTF` removes upstream diagnostic printing. Our bounds-checked transport adapter is separate, not a modification of vendored code.
