"""Inspect only metadata framing of failed scan records; no data-stream reads."""
import argparse
import json
from pathlib import Path
import sqlite3
import struct
from bulk_mft import RawVolume, attributes
from ntfs_record import restore_record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scan-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    report = json.loads((args.scan_dir/'report.json').read_text(encoding='utf-8'))
    db = sqlite3.connect((args.scan_dir/'index.sqlite').resolve().as_uri()+'?mode=ro',uri=True)
    volume = RawVolume(report['drive'])
    rows = []
    try:
        info,mapping,signature = volume.bootstrap()
        if signature != report['initial_mapping_sha256']:
            raise ValueError('MFT mapping changed since scan')
        for number,error in db.execute('SELECT * FROM errors'):
            raw = mapping.read(volume.read_at,number*info['record_bytes'],info['record_bytes'])
            data,_ = restore_record(raw,512)
            streams = []
            for kind,name,attribute in attributes(data):
                if kind == 0x80 and attribute[8]:
                    low,high,runs_offset = struct.unpack_from('<QQH',attribute,16)
                    allocated,logical,initialized = struct.unpack_from('<QQQ',attribute,40)
                    streams.append({'name':name,'low_vcn':low,'high_vcn':high,'allocated_header':allocated,
                                    'logical':logical,'initialized':initialized,'flags':struct.unpack_from('<H',attribute,12)[0],
                                    'run_list_prefix_hex':attribute[runs_offset:runs_offset+16].hex()})
            rows.append({'record':number,'error':error,'streams':streams})
    finally:
        volume.close()
        db.close()
    with args.output.open('x',encoding='utf-8') as output:
        json.dump(rows,output,ensure_ascii=True,indent=2)


if __name__ == '__main__':
    main()
