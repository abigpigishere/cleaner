"""Create a new, isolated set of NTFS fixtures; never alter existing files."""
import ctypes
import json
import msvcrt
import os
from pathlib import Path
import tempfile
import struct

from size_demo import Reader


def main():
    root = Path(tempfile.mkdtemp(prefix='size-check-', dir=Path(__file__).parent))
    fixtures = []
    for name, content in [('empty.bin', b''), ('resident-128.bin', b'r'*128),
                          ('ordinary-8193.bin', os.urandom(8193)),
                          ('ordinary-1m.bin', os.urandom(1024*1024))]:
        path = root / name
        with path.open('xb') as target:
            target.write(content)
        fixtures.append(str(path))
    linked = root / 'hardlink-to-8193.bin'
    os.link(root/'ordinary-8193.bin', linked)
    fixtures.append(str(linked))
    reader = Reader()
    optional = []
    try:
        sparse = root / 'sparse-16m.bin'
        with sparse.open('xb') as target:
            handle = msvcrt.get_osfhandle(target.fileno())
            reader.ioctl(handle, 0x000900c4, b'', 1)  # FSCTL_SET_SPARSE, new fixture only
            target.seek(8*1024*1024)
            target.write(b's'*4096)
            target.truncate(16*1024*1024+123)
            target.flush()
            # Mark the gap explicitly as a hole; merely seeking then writing may
            # allocate zero-initialization clusters on an NTFS live volume.
            reader.ioctl(handle, 0x000980c8, struct.pack('<qq', 0, 8*1024*1024), 1)
        fixtures.append(str(sparse))
        compressed = root / 'compressed-2m.bin'
        try:
            with compressed.open('x+b') as target:
                handle = msvcrt.get_osfhandle(target.fileno())
                reader.ioctl(handle, 0x0009c040, b'\x02\x00', 1)  # NTFS compression, new fixture only
                target.write(b'compress-me-' * 174762 + b'end')
            fixtures.append(str(compressed))
            optional.append({'type':'compressed','status':'created'})
        except OSError as error:
            optional.append({'type':'compressed','status':'unsupported','error':str(error)})
        ads_base = root / 'with-named-stream.bin'
        with ads_base.open('xb') as target:
            target.write(b'b'*1025)
        with open(str(ads_base)+':demo', 'xb') as target:
            target.write(b'named-stream'*4)
        fixtures.append(str(ads_base))
    finally:
        reader.close()
    manifest = {'root':str(root),'paths':fixtures,'optional':optional,
                'note':'Only these newly created fixtures were written; the analyzer itself is read-only.'}
    (root/'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps(manifest))


if __name__ == '__main__':
    main()
