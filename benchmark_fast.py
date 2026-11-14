"""Serial native benchmarks. Preserve reports; keep only final generated binary index."""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
from native_index import Index


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--drive',required=True)
    p.add_argument('--output-dir',required=True,type=Path)
    p.add_argument('--oracle',required=True,type=Path)
    p.add_argument('--runs',type=int,choices=range(1,6),default=3)
    p.add_argument('--workers',type=int,choices=range(1,17),default=4)
    args=p.parse_args()
    root=args.output_dir.absolute()
    root.mkdir(parents=True,exist_ok=False)
    rows=[]
    for number in range(1,args.runs+1):
        output=root/f'run-{number}'
        started=time.perf_counter()
        process=subprocess.run([sys.executable,str(Path(__file__).parent/'fast_scan.py'),
                                '--drive',args.drive,'--workers',str(args.workers),'--output-dir',str(output)],
                               capture_output=True,text=True)
        wall=time.perf_counter()-started
        if process.returncode:
            (root/'failure.txt').write_text(process.stdout+'\n'+process.stderr,encoding='utf-8')
            raise RuntimeError(f'Benchmark child {number} failed; see failure.txt')
        report=json.loads((output/'report.json').read_text())
        index=Index(output/'index.bin')
        try:
            verification=index.verify_oracle(args.oracle)
        finally:
            index.close()
        (output/'verification.json').write_text(json.dumps(verification,indent=2),encoding='utf-8')
        if not verification['all_passed']:
            raise RuntimeError('Native size/name/stream validation failed')
        rows.append({'run':number,'scan_ready_seconds':report['scan_ready_seconds'],
                     'native_total_seconds':report['total_seconds'],'cli_wall_seconds':wall,
                     'read_call_seconds':report['read_call_seconds'],'files':report['files'],
                     'errors':report['errors'],'sizes_unresolved':report['sizes_unresolved'],
                     'fixtures_passed':verification['fixtures_passed']})
        print(json.dumps(rows[-1]),flush=True)
        if number<args.runs:
            # Exact artifact in the new, exclusive benchmark directory. No user files.
            artifact=output/'index.bin'
            if artifact.is_symlink() or artifact.resolve().parent!=output.resolve():
                raise RuntimeError('Unexpected generated index path')
            artifact.unlink()
    result={'runs':rows,'workers':args.workers,
            'median_scan_ready_seconds':statistics.median(r['scan_ready_seconds'] for r in rows),
            'median_native_total_seconds':statistics.median(r['native_total_seconds'] for r in rows),
            'median_cli_wall_seconds':statistics.median(r['cli_wall_seconds'] for r in rows),
            'retained_index':str(root/f'run-{args.runs}'/'index.bin'),
            'cache_control':'OS cache not flushed; live volume, serial repeated scans; not a cold-disk benchmark.',
            'scope':'Scan-ready retains names, sizes, all indexed streams and canonical-parent directory aggregates; no GUI rendering.'}
    (root/'benchmark.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
