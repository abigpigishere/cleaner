"""Read-only NTFS MFT-derived enumeration benchmark, using only Python stdlib.

This is NOT a raw $MFT attribute parser or a space accounting implementation.
FSCTL_ENUM_USN_DATA yields names/IDs/attributes, not stream sizes or all links.
"""
import argparse
import ctypes
from ctypes import wintypes as w
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import struct
import sys
import time

FSCTL_ENUM_USN_DATA = 0x000900B3
ERROR_HANDLE_EOF = 38
DIRECTORY = 0x10
REPARSE_POINT = 0x400


def parse_batch(data):
    """Validate a complete returned batch; yield V2/V3 records, fail closed."""
    if len(data) < 8:
        raise ValueError('Batch lacks the 8-byte continuation reference')
    position = 8
    while position < len(data):
        if len(data) - position < 8:
            raise ValueError('Truncated record header')
        length, major, minor = struct.unpack_from('<IHH', data, position)
        if length < 8 or length % 8 or position + length > len(data):
            raise ValueError('Invalid record length/alignment')
        if major == 2:
            minimum, id_width, parent_offset = 60, 8, 16
            attribute_offset, name_length_offset = 52, 56
        elif major == 3:
            minimum, id_width, parent_offset = 76, 16, 24
            attribute_offset, name_length_offset = 68, 72
        else:
            raise ValueError(f'Unsupported USN version {major}.{minor}')
        if length < minimum:
            raise ValueError('Record shorter than version header')
        name_length, name_offset = struct.unpack_from('<HH', data, position + name_length_offset)
        if name_length % 2 or name_offset < minimum or name_offset % 2 or name_offset + name_length > length:
            raise ValueError('Filename extends beyond record or has invalid alignment')
        file_id = int.from_bytes(data[position+8:position+8+id_width], 'little')
        parent_id = int.from_bytes(data[position+parent_offset:position+parent_offset+id_width], 'little')
        name = data[position+name_offset:position+name_offset+name_length].decode('utf-16-le', errors='surrogatepass')
        attributes = struct.unpack_from('<I', data, position + attribute_offset)[0]
        yield {'version':major, 'file_id':hex(file_id), 'parent_id':hex(parent_id), 'name':name, 'attributes':attributes}
        position += length


def windows_api():
    if sys.platform != 'win32':
        raise RuntimeError('This demo requires Windows')
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
    api.CreateFileW.restype = w.HANDLE
    api.DeviceIoControl.argtypes = [w.HANDLE, w.DWORD, ctypes.c_void_p, w.DWORD, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p]
    api.DeviceIoControl.restype = w.BOOL
    api.CloseHandle.argtypes = [w.HANDLE]
    api.CloseHandle.restype = w.BOOL
    api.GetVolumeInformationW.argtypes = [w.LPCWSTR, w.LPWSTR, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.POINTER(w.DWORD), ctypes.POINTER(w.DWORD), w.LPWSTR, w.DWORD]
    api.GetVolumeInformationW.restype = w.BOOL
    return api


def enumerate_volume(drive, sample_limit=20, buffer_mib=1, max_batches=0):
    api = windows_api()
    filesystem = ctypes.create_unicode_buffer(64)
    if not api.GetVolumeInformationW(drive + '\\', None, 0, None, None, None, filesystem, len(filesystem)):
        raise ctypes.WinError(ctypes.get_last_error())
    if filesystem.value != 'NTFS':
        raise RuntimeError(f'{drive} is {filesystem.value}, not NTFS')
    # OPEN_EXISTING, GENERIC_READ only, all share flags. Never open for write.
    handle = api.CreateFileW('\\\\.\\' + drive, 0x80000000, 7, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    result = {
        'drive':drive, 'filesystem':filesystem.value, 'method':'FSCTL_ENUM_USN_DATA',
        'raw_mft_parser':False, 'recursive_directory_walk':False,
        'source_access':'GENERIC_READ', 'status':'running', 'completed':False,
        'started_at':datetime.now(timezone.utc).isoformat(),
        'records':0, 'file_records':0, 'directory_records':0, 'reparse_records':0,
        'batches':0, 'bytes_returned':0, 'samples':[], 'record_versions':{},
        'limitations':[
            'No file sizes or allocated-byte accounting; no content is opened.',
            'One returned name per record does not enumerate every hard-link path.',
            'A live volume is not a point-in-time filesystem snapshot.',
            'USN range is [0, INT64_MAX]; only records admitted by the API are counted.',
            'Throughput includes API calls and Python record parsing, not output writing.',
            'No recursive-walk/WizTree comparison was performed; this is not a speedup claim.'
        ]
    }
    buffer = ctypes.create_string_buffer(buffer_mib * 1024 * 1024)
    returned = w.DWORD()
    cursor = 0
    started = time.perf_counter()
    last_progress = started
    try:
        while True:
            query = ctypes.create_string_buffer(struct.pack('<Qqq', cursor, 0, (1 << 63)-1))
            ok = api.DeviceIoControl(handle, FSCTL_ENUM_USN_DATA, query, 24, buffer, len(buffer), ctypes.byref(returned), None)
            if not ok:
                error = ctypes.get_last_error()
                if error == ERROR_HANDLE_EOF:
                    result.update(status='completed', completed=True)
                    break
                raise ctypes.WinError(error)
            batch = buffer.raw[:returned.value]
            if len(batch) < 8:
                raise ValueError('Driver returned no continuation reference')
            next_cursor = struct.unpack_from('<Q', batch)[0]
            if next_cursor <= cursor:
                raise ValueError('Enumeration continuation did not advance')
            for record in parse_batch(batch):
                result['records'] += 1
                directory = bool(record['attributes'] & DIRECTORY)
                result['directory_records' if directory else 'file_records'] += 1
                result['reparse_records'] += bool(record['attributes'] & REPARSE_POINT)
                version = str(record['version'])
                result['record_versions'][version] = result['record_versions'].get(version, 0) + 1
                if len(result['samples']) < sample_limit:
                    result['samples'].append(record)
            result['batches'] += 1
            result['bytes_returned'] += len(batch)
            cursor = next_cursor
            now = time.perf_counter()
            if now - last_progress >= 1:
                print(json.dumps({'records':result['records'], 'elapsed_seconds':round(now-started, 3)}, ensure_ascii=True), file=sys.stderr, flush=True)
                last_progress = now
            if max_batches and result['batches'] >= max_batches:
                result['status'] = 'limited'
                break
    except KeyboardInterrupt:
        result['status'] = 'cancelled'
    except (OSError, ValueError) as error:
        result['status'] = 'error'
        result['error'] = str(error)
        result['winerror'] = getattr(error, 'winerror', None)
    finally:
        result['elapsed_seconds'] = round(time.perf_counter() - started, 6)
        result['records_per_second'] = round(result['records'] / max(result['elapsed_seconds'], 1e-9))
        result['continuation_reference'] = hex(cursor)
        api.CloseHandle(handle)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--drive', required=True, help='One local NTFS drive, e.g. C:')
    parser.add_argument('--output', type=Path, help='New JSON report file; existing files are never overwritten')
    parser.add_argument('--samples', type=int, default=20)
    parser.add_argument('--buffer-mib', type=int, choices=range(1, 17), default=1)
    parser.add_argument('--max-batches', type=int, default=0, help='0 = enumerate to EOF; otherwise a deliberately partial smoke test')
    args = parser.parse_args()
    drive = args.drive.upper().rstrip('\\/')
    if not re.fullmatch('[A-Z]:', drive):
        parser.error('--drive must be a drive letter such as C:')
    if args.samples < 0 or args.max_batches < 0:
        parser.error('samples and max-batches must be nonnegative')
    # Reserve output before touching the volume. Refuse files, links and existing paths.
    output = None
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        output = args.output.open('x', encoding='utf-8')
    try:
        try:
            result = enumerate_volume(drive, args.samples, args.buffer_mib, args.max_batches)
        except (OSError, RuntimeError) as error:
            result = {'drive':drive, 'method':'FSCTL_ENUM_USN_DATA', 'status':'error', 'completed':False, 'error':str(error), 'winerror':getattr(error, 'winerror', None)}
            if result['winerror'] == 5:
                result['remediation'] = 'Run this same read-only demo from an Administrator terminal; no fallback to directory walking.'
        serialized = json.dumps(result, ensure_ascii=True, indent=2) + '\n'
        if output:
            output.write(serialized)
        print(serialized)
        return 0 if result['status'] in ('completed', 'limited') else 1
    finally:
        if output:
            output.close()


if __name__ == '__main__':
    raise SystemExit(main())
