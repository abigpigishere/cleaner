"""Bootstrap the native MFT scanner, then verify mapping and report real timings."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import struct
import shutil
import time
from bulk_mft import RawVolume


def main():
    started=time.perf_counter()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--drive',required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--workers',type=int,choices=range(1,17),default=4)
    args=parser.parse_args()
    drive=args.drive.upper().rstrip('\\/')
    if not re.fullmatch('[A-Z]:',drive):
        parser.error('Expected local drive letter')
    output=args.output_dir.absolute()
    if output.exists():
        parser.error('Output directory must be new')
    output.parent.mkdir(parents=True,exist_ok=True)
    executable=Path(__file__).parent/'native/build/cleaner-scan.exe'
    if not executable.is_file():
        parser.error('Compile native/build.cmd first')
    volume=RawVolume(drive)
    try:
        bootstrap_started=time.perf_counter()
        info,mapping,signature=volume.bootstrap()
        bootstrap_seconds=time.perf_counter()-bootstrap_started
        command=[str(executable),'--drive',drive,'--output-dir',str(output),'--workers',str(args.workers),
                 '--record',str(info['record_bytes']),'--sector',str(info['sector_bytes']),
                 '--cluster',str(info['cluster_bytes']),'--valid',str(info['mft_valid_bytes']),
                 '--volume-bytes',str(info['volume_bytes']),'--serial',info['volume_serial']]
        for vcn,lcn,count in mapping.runs:
            command.extend(['--run',f'{vcn},{lcn},{count}'])
        child_started=time.perf_counter()
        child=subprocess.run(command,capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
        child_wall=time.perf_counter()-child_started
        if not (output/'native-report.json').is_file():
            raise RuntimeError(f'Native scanner failed ({child.returncode}): {child.stderr}')
        report=json.loads((output/'native-report.json').read_text())
        ending_info,ending_map,ending_signature=volume.bootstrap()
        # Independent, read-only accounting audit. This does not change any directory sizes.
        try:
            raw=volume.reader.ioctl(volume.handle,0x90064,b'',128)
            total,free,reserved=struct.unpack_from('<QQQ',raw,16)
            cluster=struct.unpack_from('<I',raw,44)[0]
            usage=shutil.disk_usage(drive+'\\')
            audit=dict(total_clusters=total,free_clusters=free,reserved_clusters=reserved,
                       cluster_bytes=cluster,ntfs_used_bytes=(total-free)*cluster,
                       windows_used_bytes=usage.used,windows_free_bytes=usage.free)
            count=(total+7)//8
            if count<=64*1024*1024:
                bitmap=volume.reader.ioctl(volume.handle,0x9006f,struct.pack('<Q',0),count+65536)
                start,bits=struct.unpack_from('<QQ',bitmap)
                if start!=0 or bits<total or len(bitmap)<16+count:raise ValueError('Incomplete volume bitmap')
                used=(int.from_bytes(bitmap[16:16+count],'little')&((1<<total)-1)).bit_count()
                audit['bitmap_used_bytes']=used*cluster
            report['allocation_audit']=audit
        except Exception as error:
            report['allocation_audit_error']=str(error)

        report.update(drive=drive,mode='native_compact_index_with_directory_aggregation',
                      bootstrap_seconds=bootstrap_seconds,native_process_wall_seconds=child_wall,
                      wrapper_elapsed_seconds=time.perf_counter()-started,
                      volume=info,mft_runs=mapping.runs,mapping_sha256=signature,
                      final_mapping_unchanged=signature==ending_signature,
                      final_mft_length_unchanged=info['mft_valid_bytes']==ending_info['mft_valid_bytes'],
                      native_exit_code=child.returncode,
                      limitations=['Live raw NTFS metadata, not a point-in-time snapshot.',
                                   'ATTRIBUTE_LIST extension DATA extents are merged by base reference and checked for contiguous VCN coverage; unresolved sizes are not zero.',
                                   'Directory totals assign each base record to its first long-name parent; all names are retained.',
                                   'Logical totals cover unnamed DATA; allocated totals include all nonresident attribute runs, named DATA, directory indexes and attribute-list storage.',
                                   'ReadFile timings may include OS-cache reads; export includes file flush, not FlushFileBuffers durability.',
                                   'Scan-ready includes names, sizes, compact arenas and directory aggregation; no GUI rendering.'])
        if not report['final_mapping_unchanged'] or not report['final_mft_length_unchanged']:
            report['status']='metadata_changed_during_scan'
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=True,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=True,indent=2))
        return 0 if child.returncode==0 and report['status']=='completed' else 1
    finally:
        volume.close()


if __name__=='__main__':
    raise SystemExit(main())
