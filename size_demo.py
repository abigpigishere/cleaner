"""Read individual NTFS FILE records and cross-check sizes; no full-volume scan."""
import argparse
import ctypes
from ctypes import wintypes as w
from datetime import datetime, timezone
import json
from pathlib import Path
import struct
import time

from mft_demo import windows_api
from ntfs_record import parse_record


class StandardInfo(ctypes.Structure):
    _fields_ = [('allocation', ctypes.c_longlong), ('eof', ctypes.c_longlong),
                ('links', w.DWORD), ('delete_pending', ctypes.c_ubyte), ('directory', ctypes.c_ubyte)]


class CompressionInfo(ctypes.Structure):
    _fields_ = [('compressed_bytes', ctypes.c_longlong), ('format', w.WORD),
                ('unit_shift', ctypes.c_ubyte), ('chunk_shift', ctypes.c_ubyte),
                ('cluster_shift', ctypes.c_ubyte), ('reserved', ctypes.c_ubyte*3)]


class FileInfo(ctypes.Structure):
    _fields_ = [('attributes', w.DWORD), ('created', w.FILETIME), ('accessed', w.FILETIME),
                ('written', w.FILETIME), ('serial', w.DWORD), ('size_high', w.DWORD),
                ('size_low', w.DWORD), ('links', w.DWORD), ('id_high', w.DWORD), ('id_low', w.DWORD)]


class Reader:
    def __init__(self):
        self.api = windows_api()
        self.api.GetFileInformationByHandle.argtypes = [w.HANDLE, ctypes.POINTER(FileInfo)]
        self.api.GetFileInformationByHandle.restype = w.BOOL
        self.api.GetFileInformationByHandleEx.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        self.api.GetFileInformationByHandleEx.restype = w.BOOL
        self.volumes = {}

    def open(self, path, access=0):
        handle = self.api.CreateFileW(path, access, 7, None, 3, 0x02200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle

    def ioctl(self, handle, code, input_bytes, output_size):
        source = ctypes.create_string_buffer(input_bytes) if input_bytes else None
        output = ctypes.create_string_buffer(output_size)
        returned = w.DWORD()
        if not self.api.DeviceIoControl(handle, code, source, len(input_bytes), output, len(output), ctypes.byref(returned), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return output.raw[:returned.value]

    def volume(self, drive):
        if drive not in self.volumes:
            fs = ctypes.create_unicode_buffer(64)
            if not self.api.GetVolumeInformationW(drive+'\\', None, 0, None, None, None, fs, 64):
                raise ctypes.WinError(ctypes.get_last_error())
            if fs.value != 'NTFS':
                raise ValueError('Only local NTFS volumes are supported')
            handle = self.open('\\\\.\\'+drive, 0x80000000)
            try:
                volume = self.ioctl(handle, 0x00090064, b'', 128)  # FSCTL_GET_NTFS_VOLUME_DATA
                if len(volume) < 104:
                    raise ValueError('Volume reply lacks NTFS version')
                sector, cluster, record_size = struct.unpack_from('<III', volume, 40)
                major, minor = struct.unpack_from('<HH', volume, 100)
                if (major, minor) not in ((3, 0), (3, 1)) or not 512 <= record_size <= 65536:
                    raise ValueError('Unsupported NTFS version/record size')
                self.volumes[drive] = (handle, {'sector_bytes':sector,'cluster_bytes':cluster,
                                               'record_bytes':record_size,'ntfs_version':f'{major}.{minor}'})
            except Exception:
                self.api.CloseHandle(handle)
                raise
        return self.volumes[drive]

    def standard(self, handle):
        info, compression = StandardInfo(), CompressionInfo()
        for kind, value in ((1, info), (8, compression)):
            if not self.api.GetFileInformationByHandleEx(handle, kind, ctypes.byref(value), ctypes.sizeof(value)):
                raise ctypes.WinError(ctypes.get_last_error())
        return {'logical_bytes':info.eof, 'allocation_size':info.allocation,
                'storage_bytes':compression.compressed_bytes, 'hard_link_count':info.links,
                'compression_format':compression.format}

    def inspect(self, path):
        path = str(Path(path).absolute())
        drive = Path(path).drive.upper()
        if len(drive) != 2 or drive[1] != ':' or ':' in path[2:]:
            raise ValueError('Supply a local base file path, not UNC or an ADS path')
        handle = self.open('\\\\?\\'+path)
        try:
            identity = FileInfo()
            if not self.api.GetFileInformationByHandle(handle, ctypes.byref(identity)):
                raise ctypes.WinError(ctypes.get_last_error())
            if identity.attributes & 0x410:  # directory or reparse point
                raise ValueError('Directories and reparse points are outside this size demo')
            reference = identity.id_low | identity.id_high << 32
            before = self.standard(handle)
            volume, geometry = self.volume(drive)
            start = time.perf_counter()
            reply = self.ioctl(volume, 0x00090068, struct.pack('<Q', reference), geometry['record_bytes']+16)
            read_seconds = time.perf_counter()-start
            if len(reply) < 12:
                raise ValueError('Short file record reply')
            returned_reference, length = struct.unpack_from('<QI', reply)
            if returned_reference & ((1 << 48)-1) != reference & ((1 << 48)-1):
                raise ValueError('Driver returned a different record (backward lookup)')
            if length != geometry['record_bytes'] or 12+length > len(reply):
                raise ValueError('Invalid file record reply length')
            start = time.perf_counter()
            parsed = parse_record(reply[12:12+length], geometry['sector_bytes'], geometry['cluster_bytes'], source='ioctl')
            parse_seconds = time.perf_counter()-start
            if parsed['sequence'] != reference >> 48:
                raise ValueError('Record sequence differs from open file identity')
            after = self.standard(handle)
            stable = before == after
            main = next((s for s in parsed['streams'] if not s['name']), None)
            resident = main is not None and main['resident']
            special = main is not None and bool(main['attribute_flags'] & 0x8001)
            # For uncompressed files CompressionInfo reports logical length, not
            # rounded cluster allocation. Resident AllocationSize can be the
            # resident value length: it does not represent external clusters.
            if resident:
                allocation_match = before['allocation_size'] == parsed['logical_bytes']
                allocation_check = 'resident_value_length_vs_windows_allocation; zero_external_clusters_is_MFT_layout_fact'
            elif special:
                allocation_match = parsed['allocated_bytes'] == before['storage_bytes']
                allocation_check = 'MFT_TotalAllocated_vs_Windows_CompressionInfo'
            else:
                allocation_match = parsed['allocated_bytes'] == before['allocation_size']
                allocation_check = 'MFT_AllocatedLength_vs_Windows_StandardInfo'
            comparison = {
                'logical_match':parsed['logical_bytes'] == before['logical_bytes'],
                'allocation_representation_match':allocation_match,
                'hard_link_count_match':parsed['hard_link_count'] == before['hard_link_count'],
            }
            matched = stable and parsed['size_status'] == 'parsed' and all(comparison.values())
            return {'path':path,'file_reference':hex(reference),'volume':geometry,'mft':parsed,'windows':before,
                    'stable_during_check':stable,'comparison':comparison,'allocation_check':allocation_check,
                    'status':'matched' if matched else 'unresolved',
                    'read_seconds':read_seconds,'parse_seconds':parse_seconds}
        finally:
            self.api.CloseHandle(handle)

    def close(self):
        for handle, _ in self.volumes.values():
            self.api.CloseHandle(handle)
        self.volumes.clear()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('paths', nargs='+', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as output:
        report = {'started_at':datetime.now(timezone.utc).isoformat(), 'method':'FSCTL_GET_NTFS_FILE_RECORD',
                  'mode':'per_record_correctness_probe', 'source_access':'read_only', 'results':[],
                  'limitations':['Not a bulk MFT reader or full-volume benchmark.',
                                 'Attribute-list/extension-record streams are explicitly unresolved.',
                                 'Sizes cover the unnamed DATA stream; named streams are listed separately.',
                                 'Resident data has zero external allocation; shared MFT storage is not free space.',
                                 'Live comparisons are not snapshots; matching before/after values are a stability check only.']}
        reader = Reader()
        try:
            for path in args.paths:
                try:
                    result = reader.inspect(path)
                except (OSError, ValueError) as error:
                    result = {'path':str(path),'status':'error','error':str(error),'winerror':getattr(error,'winerror',None)}
                report['results'].append(result)
        finally:
            reader.close()
        report['matched'] = sum(r['status'] == 'matched' for r in report['results'])
        report['total'] = len(report['results'])
        json.dump(report, output, ensure_ascii=True, indent=2)
        print(json.dumps({'matched':report['matched'],'total':report['total'],'output':str(args.output)}))
    return 0 if report['matched'] == report['total'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
