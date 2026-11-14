"""Compare raw bulk results to the previously validated per-record fixture oracle."""
import argparse
import json
from pathlib import Path
import sqlite3


def verify(scan_dir, oracle_path):
    report = json.loads((scan_dir/'report.json').read_text(encoding='utf-8'))
    oracle = json.loads(oracle_path.read_text(encoding='utf-8-sig'))
    db = sqlite3.connect((scan_dir/'index.sqlite').resolve().as_uri()+'?mode=ro',uri=True)
    db.row_factory = sqlite3.Row
    comparisons = []
    try:
        for sample in oracle['results']:
            reference = int(sample['file_reference'],16)
            row = db.execute('SELECT * FROM records WHERE record_number=?',(reference & ((1 << 48)-1),)).fetchone()
            checks = {'record_present':row is not None}
            if row:
                expected = sample['mft']
                checks.update(reference=row['reference'] == sample['file_reference'],
                              logical_bytes=row['logical_bytes'] == expected['logical_bytes'],
                              allocated_bytes=row['allocated_bytes'] == expected['allocated_bytes'],
                              hard_links=row['links'] == expected['hard_link_count'],
                              names=json.loads(row['names_json']) == expected['names'],
                              streams=json.loads(row['streams_json']) == expected['streams'])
            comparisons.append({'path':sample['path'],'checks':checks,'passed':all(checks.values())})
        counts = report['counts']
        invariants = {
            'slots_partition':counts['slots'] == sum(counts[k] for k in ('empty','unused','extensions','base_records','errors')),
            'base_partition':counts['base_records'] == counts['files']+counts['directories'],
            'file_sizes_partition':counts['files'] == counts['sizes_parsed']+counts['sizes_unresolved'],
            'index_count':counts['base_records'] == db.execute('SELECT count(*) FROM records').fetchone()[0],
            'error_count':counts['errors'] == db.execute('SELECT count(*) FROM errors').fetchone()[0],
        }
        errors = [dict(row) for row in db.execute('SELECT * FROM errors LIMIT 20')]
        unresolved = [dict(row) for row in db.execute('SELECT size_status,count(*) AS count FROM records WHERE directory=0 AND size_status != ? GROUP BY size_status',('parsed',))]
        return {'fixture_count':len(comparisons),'fixtures_passed':sum(c['passed'] for c in comparisons),
                'all_passed':all(c['passed'] for c in comparisons) and all(invariants.values()),
                'invariants':invariants,'fixtures':comparisons,'error_sample':errors,'unresolved':unresolved}
    finally:
        db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scan-dir',type=Path,required=True)
    parser.add_argument('--oracle',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    result = verify(args.scan_dir,args.oracle)
    with args.output.open('x',encoding='utf-8') as output:
        json.dump(result,output,ensure_ascii=True,indent=2)
    print(json.dumps(result,ensure_ascii=True,indent=2))
    raise SystemExit(0 if result['all_passed'] else 1)
