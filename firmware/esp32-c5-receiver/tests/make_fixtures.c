/* Offline synthetic fixture generator. Never linked into firmware; no RF APIs. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "opendroneid.h"

static void save(const char *name, const unsigned char *p, size_t n) {
    FILE *f = fopen(name, "w"); assert(f);
    for (size_t i = 0; i < n; i++) fprintf(f, "%02x%s", p[i],
        (i + 1 == n || (i + 1) % 24 == 0) ? "\n" : " ");
    fclose(f);
}
int main(void) {
    ODID_MessagePack_data data = {.SingleMessageSize = ODID_MESSAGE_SIZE, .MsgPackSize = 3};
    ODID_BasicID_data id; odid_initBasicIDData(&id);
    id.IDType = ODID_IDTYPE_SERIAL_NUMBER; id.UAType = ODID_UATYPE_HELICOPTER_OR_MULTIROTOR;
    strcpy(id.UASID, "SYNTH-C5-TEST");
    ODID_Location_data loc; odid_initLocationData(&loc);
    loc.Latitude = 38.0; loc.Longitude = -90.0; loc.AltitudeGeo = 123.5; loc.TimeStamp = 123.4f;
    ODID_System_data sys; odid_initSystemData(&sys);
    sys.OperatorLocationType = ODID_OPERATOR_LOCATION_TYPE_TAKEOFF;
    sys.OperatorLatitude = 38.1; sys.OperatorLongitude = -90.1; sys.OperatorAltitudeGeo = 120;
    sys.Timestamp = 100000;
    assert(encodeBasicIDMessage(&data.Messages[0].basicId, &id) == ODID_SUCCESS);
    assert(encodeLocationMessage(&data.Messages[1].location, &loc) == ODID_SUCCESS);
    assert(encodeSystemMessage(&data.Messages[2].system, &sys) == ODID_SUCCESS);
    ODID_MessagePack_encoded pack = {0};
    assert(encodeMessagePack(&pack, &data) == ODID_SUCCESS);
    const size_t plen = 3 + data.MsgPackSize * ODID_MESSAGE_SIZE;
    unsigned char beacon[256] = {0x80};
    memset(beacon + 4, 0xff, 6); beacon[10] = 2; beacon[15] = 1; memcpy(beacon + 16, beacon + 10, 6);
    const unsigned char ie[] = {221, 5 + 78, 0xfa,0x0b,0xbc,0x0d,7};
    memcpy(beacon + 36, ie, sizeof(ie)); memcpy(beacon + 43, &pack, plen);
    save("fixtures/synthetic-beacon.hex", beacon, 43 + plen);
    unsigned char nan[256] = {0xd0};
    const unsigned char dest[] = {0x51,0x6f,0x9a,1,0,0}; memcpy(nan + 4, dest, 6);
    memcpy(nan + 10, beacon + 10, 6);
    const unsigned char nsd[] = {4,9,0x50,0x6f,0x9a,0x13}; memcpy(nan + 24, nsd, 6);
    const unsigned char attr[] = {3, 11 + 78, 0, 0x88,0x69,0x19,0x9d,0x92,9, 1,0,0x10,1 + 78,7};
    memcpy(nan + 30, attr, sizeof(attr)); memcpy(nan + 44, &pack, plen);
    save("fixtures/synthetic-nan.hex", nan, 44 + plen);
    unsigned char ordinary[39] = {0x80}; ordinary[36] = 0; ordinary[37] = 1; ordinary[38] = 'x';
    save("fixtures/unrelated-beacon.hex", ordinary, sizeof(ordinary));
    return 0;
}
