"""One-time diagnostics of our own compressed fixture's record framing, no payload dump."""
import ctypes
import json
from pathlib import Path
import struct
from size_demo import Reader, FileInfo

root = Path(__file__).parent
manifest = json.loads((root/'results/size-fixture-manifest-v2.json').read_text(encoding='utf-8-sig'))
path = next(p for p in manifest['paths'] if Path(p).name == 'compressed-2m.bin')
reader = Reader()
handle = reader.open(path)
try:
    info = FileInfo()
    if not reader.api.GetFileInformationByHandle(handle, ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    reference = info.id_low | info.id_high << 32
    volume, geometry = reader.volume('E:')
    reply = reader.ioctl(volume, 0x90068, struct.pack('<Q', reference), geometry['record_bytes']+16)
    raw = reply[12:12+geometry['record_bytes']]
    offset, count = struct.unpack_from('<HH', raw, 4)
    sector = geometry['sector_bytes']
    result = {'geometry':geometry,'record_used':struct.unpack_from('<I', raw, 24)[0],
              'usa_offset':offset,'usa_count':count,'marker':raw[offset:offset+2].hex(),
              'tails':[raw[i*sector-2:i*sector].hex() for i in range(1,count)],
              'saved':[raw[offset+i*2:offset+i*2+2].hex() for i in range(1,count)],
              'windows':reader.standard(handle)}
    (root/'results/fixture-header.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
finally:
    reader.api.CloseHandle(handle)
    reader.close()
