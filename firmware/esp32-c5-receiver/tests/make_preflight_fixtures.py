"""Deterministic mutations of synthetic fixtures only; no RF or real aircraft."""
from pathlib import Path

FIXTURES = Path(__file__).with_name('fixtures')


def save(name, data):
    (FIXTURES / (name + '.hex')).write_text('\n'.join(data[i:i+24].hex(' ') for i in range(0,len(data),24))+'\n')


def wrap(transport, pack):
    base = bytes.fromhex((FIXTURES / f'synthetic-{transport}.hex').read_text())
    if transport == 'beacon':
        data=bytearray(base[:43]+pack)
        data[37]=5+len(pack)
    else:
        data=bytearray(base[:44]+pack)
        data[31:33]=(11+len(pack)).to_bytes(2,'little')
        data[42]=1+len(pack)
    return data


def main():
    pack=bytearray(bytes.fromhex((FIXTURES/'synthetic-beacon.hex').read_text())[43:])
    for transport in ('beacon','nan'):
        for version in (0,1,2):
            data=bytearray(pack)
            for off in (0,3,28,53): data[off]=(data[off]&0xf0)|version
            # Older System reserved bytes are not newer timestamp/altitude.
            data[73:77]=bytes(4) if version<2 else data[73:77]
            if version==0: data[71:73]=bytes(2)
            save(f'synthetic-{transport}-v{version}',wrap(transport,data))
        data=bytearray(pack);data[2]+=1;data.extend(b'\x62'+bytes(24))
        save(f'synthetic-{transport}-reserved',wrap(transport,data))
        save(f'synthetic-{transport}-padding',wrap(transport,pack+bytes(8)))
        data=bytearray(pack);data[53]=0x43
        save(f'synthetic-{transport}-future-member',wrap(transport,data))
        data=bytearray(pack);data[0]=0xf3
        save(f'unsupported-{transport}-v3',wrap(transport,data))
        save(f'malformed-{transport}-padding',wrap(transport,pack+b'\x00\x01'))


if __name__=='__main__': main()
