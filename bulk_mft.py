"""Read-only, bounded-memory raw NTFS MFT scan. Windows, administrator required.

Bootstrap only: a few FSCTL_GET_NTFS_FILE_RECORD calls map $MFT's extents.
Main scan: sector-aligned ReadFile calls, strict disk USA checks, SQLite index.
"""
import argparse
import bisect
import ctypes
from ctypes import wintypes as w
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import struct
import time

from ntfs_record import parse_record, restore_record
from size_demo import Reader

MASK48 = (1 << 48)-1


def decode_runs(attribute):
    """Decode nonresident mapping pairs as (VCN, LCN or None, cluster count)."""
    if len(attribute) < 64 or attribute[8] != 1:
        raise ValueError('Expected nonresident attribute')
    low, high, offset = struct.unpack_from('<QQH', attribute, 16)
    if offset < 64 or offset >= len(attribute) or high < low:
        raise ValueError('Invalid run list header')
    if low == 0 and high == (1 << 64)-1:
        if attribute[offset] or any(struct.unpack_from('<QQQ', attribute, 40)):
            raise ValueError('Empty run list declares nonempty data')
        return []
    vcn, lcn, position, result = low, 0, offset, []
    while position < len(attribute):
        tag = attribute[position]
        position += 1
        if not tag:
            if vcn != high+1:
                raise ValueError('Run list does not cover declared VCNs')
            return result
        size, delta_size = tag & 15, tag >> 4
        if not 1 <= size <= 8 or delta_size > 8 or position+size+delta_size > len(attribute):
            raise ValueError('Invalid run list encoding')
        clusters = int.from_bytes(attribute[position:position+size], 'little')
        position += size
        if not clusters or vcn+clusters > high+1:
            raise ValueError('Invalid run list span')
        physical = None
        if delta_size:
            lcn += int.from_bytes(attribute[position:position+delta_size], 'little', signed=True)
            if lcn < 0:
                raise ValueError('Negative physical cluster')
            physical = lcn
        position += delta_size
        result.append((vcn, physical, clusters))
        vcn += clusters
    raise ValueError('Unterminated run list')


def attributes(raw):
    """Validated attribute headers from an already normalized FILE record."""
    if len(raw) < 48 or raw[:4] != b'FILE':
        raise ValueError('Invalid FILE record')
    start = struct.unpack_from('<H', raw, 20)[0]
    used, allocated = struct.unpack_from('<II', raw, 24)
    if allocated != len(raw) or not 48 <= start < used <= len(raw) or start % 8:
        raise ValueError('Invalid FILE record bounds')
    position = start
    while position+4 <= used:
        kind = struct.unpack_from('<I', raw, position)[0]
        if kind == 0xffffffff:
            return
        if position+24 > used:
            raise ValueError('Truncated attribute')
        length = struct.unpack_from('<I', raw, position+4)[0]
        if length < 24 or length % 8 or position+length > used:
            raise ValueError('Invalid attribute bounds')
        item = raw[position:position+length]
        form, chars, offset = struct.unpack_from('<BBH', item, 8)
        minimum = 64 if form == 1 else 24
        if form not in (0, 1) or len(item) < minimum or (chars and (offset < minimum or offset % 2 or offset+chars*2 > length)):
            raise ValueError('Invalid attribute form/name')
        name = item[offset:offset+chars*2].decode('utf-16-le', errors='surrogatepass') if chars else ''
        yield kind, name, item
        position += length
    raise ValueError('Missing attribute terminator')


def attribute_list_entries(data):
    position = 0
    while position < len(data):
        # The resident value excludes padding. Nonresident lists may end in zero padding.
        if len(data)-position < 4 or struct.unpack_from('<I', data, position)[0] == 0:
            if any(data[position:]):
                raise ValueError('Nonzero attribute-list tail')
            return
        if len(data)-position < 26:
            raise ValueError('Truncated attribute-list entry')
        kind, length, chars, name_offset, low, reference, instance = struct.unpack_from('<IHBBQQH', data, position)
        if length < 26 or length % 8 or position+length > len(data):
            raise ValueError('Invalid attribute-list entry bounds')
        if chars and (name_offset < 26 or name_offset % 2 or name_offset+chars*2 > length):
            raise ValueError('Invalid attribute-list name')
        name = data[position+name_offset:position+name_offset+chars*2].decode('utf-16-le', errors='surrogatepass') if chars else ''
        yield kind, name, low, reference, instance
        position += length


class RunMap:
    def __init__(self, runs, length, cluster_size, volume_bytes):
        self.runs = sorted(runs)
        self.length, self.cluster_size = length, cluster_size
        expected = 0
        self.starts = []
        for vcn, lcn, count in self.runs:
            if vcn != expected or lcn is None or count <= 0 or lcn < 0 or (lcn+count)*cluster_size > volume_bytes:
                raise ValueError('Incomplete, overlapping, sparse or out-of-volume metadata map')
            self.starts.append(vcn*cluster_size)
            expected += count
        if length < 0 or expected*cluster_size < length:
            raise ValueError('Metadata map does not cover valid data length')

    def segments(self, start, length):
        if start < 0 or length < 0 or start+length > self.length:
            raise ValueError('Logical read outside metadata stream')
        while length:
            index = bisect.bisect_right(self.starts, start)-1
            if index < 0:
                raise ValueError('Missing stream prefix')
            vcn, lcn, count = self.runs[index]
            within = start-vcn*self.cluster_size
            take = min(length, count*self.cluster_size-within)
            if take <= 0:
                raise ValueError('Missing stream extent')
            yield lcn*self.cluster_size+within, take
            start += take
            length -= take

    def read(self, read_at, start, length):
        return b''.join(read_at(offset, count) for offset, count in self.segments(start, length))


class RawVolume:
    def __init__(self, drive):
        self.reader = Reader()
        self.handle, self.geometry = self.reader.volume(drive)
        api = self.reader.api
        api.SetFilePointerEx.argtypes = [w.HANDLE, ctypes.c_longlong, ctypes.POINTER(ctypes.c_longlong), w.DWORD]
        api.SetFilePointerEx.restype = w.BOOL
        api.ReadFile.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p]
        api.ReadFile.restype = w.BOOL
        self.raw_calls = self.raw_bytes = self.bootstrap_record_calls = 0
        self.io_seconds = 0.0
        self.buffer = ctypes.create_string_buffer(8*1024*1024+65536)

    def information(self):
        data = self.reader.ioctl(self.handle, 0x90064, b'', 128)
        if len(data) < 104:
            raise ValueError('Short NTFS volume metadata')
        serial, sectors, clusters = struct.unpack_from('<QQQ', data)
        valid_length, mft_start = struct.unpack_from('<QQ', data, 56)
        return {**self.geometry,'volume_serial':hex(serial),'volume_bytes':sectors*self.geometry['sector_bytes'],
                'total_clusters':clusters,'mft_valid_bytes':valid_length,'mft_start_lcn':mft_start}

    def read_at(self, offset, length):
        sector = self.geometry['sector_bytes']
        aligned = offset//sector*sector
        skip = offset-aligned
        requested = (skip+length+sector-1)//sector*sector
        if offset < 0 or requested > 32*1024*1024:
            raise ValueError('Raw read exceeds bounded read size')
        if requested > len(self.buffer):
            self.buffer = ctypes.create_string_buffer(requested)
        api = self.reader.api
        start = time.perf_counter()
        if not api.SetFilePointerEx(self.handle, aligned, None, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        received = w.DWORD()
        if not api.ReadFile(self.handle, self.buffer, requested, ctypes.byref(received), None):
            raise ctypes.WinError(ctypes.get_last_error())
        self.io_seconds += time.perf_counter()-start
        self.raw_calls += 1
        self.raw_bytes += received.value
        if received.value != requested:
            raise ValueError('Short raw volume read')
        return self.buffer.raw[skip:skip+length]

    def file_record(self, reference):
        reply = self.reader.ioctl(self.handle, 0x90068, struct.pack('<Q', reference), self.geometry['record_bytes']+16)
        self.bootstrap_record_calls += 1
        if len(reply) < 12:
            raise ValueError('Short bootstrap FILE reply')
        returned, length = struct.unpack_from('<QI', reply)
        if returned & MASK48 != reference & MASK48 or length != self.geometry['record_bytes'] or length+12 > len(reply):
            raise ValueError('Bootstrap returned a different or truncated FILE record')
        raw = reply[12:12+length]
        if reference >> 48 and struct.unpack_from('<H', raw, 16)[0] != reference >> 48:
            raise ValueError('Stale extension FILE reference')
        return raw

    def bootstrap(self):
        info = self.information()
        base = self.file_record(0)
        base_sequence = struct.unpack_from('<H', base, 16)[0]
        seen, pending = {0}, [(0, base)]
        data_runs, data_length = [], None
        while pending:
            number, raw = pending.pop()
            if not struct.unpack_from('<H', raw, 22)[0] & 1:
                raise ValueError('Inactive bootstrap record')
            if number and struct.unpack_from('<Q', raw, 32)[0] != base_sequence << 48:
                raise ValueError('Extension belongs to a different base record')
            for kind, name, attribute in attributes(raw):
                if kind == 0x80 and not name:
                    if not attribute[8] or struct.unpack_from('<H', attribute, 12)[0] & 0x8001:
                        raise ValueError('$MFT stream cannot be resident, compressed or sparse')
                    runs = decode_runs(attribute)
                    data_runs.extend(runs)
                    if runs[0][0] == 0:
                        if data_length is not None:
                            raise ValueError('Duplicate base $MFT DATA attribute')
                        data_length = struct.unpack_from('<Q', attribute, 48)[0]
                elif kind == 0x20:
                    if attribute[8] == 0:
                        size, offset = struct.unpack_from('<IH', attribute, 16)
                        if offset < 24 or offset+size > len(attribute):
                            raise ValueError('Invalid resident bootstrap attribute list')
                        listing = attribute[offset:offset+size]
                    else:
                        size = struct.unpack_from('<Q', attribute, 48)[0]
                        if size > 16*1024*1024:
                            raise ValueError('Bootstrap attribute list exceeds 16 MiB')
                        mapping = RunMap(decode_runs(attribute), size, info['cluster_bytes'], info['volume_bytes'])
                        listing = mapping.read(self.read_at, 0, size)
                    for entry_kind, entry_name, low, reference, instance in attribute_list_entries(listing):
                        record_number = reference & MASK48
                        if entry_kind == 0x80 and not entry_name and record_number not in seen:
                            if len(seen) >= 4096:
                                raise ValueError('Too many bootstrap extension records')
                            seen.add(record_number)
                            pending.append((record_number, self.file_record(reference)))
        if data_length is None or info['mft_valid_bytes'] > data_length or info['mft_valid_bytes'] % info['record_bytes']:
            raise ValueError('Invalid $MFT valid data length')
        mapping = RunMap(data_runs, info['mft_valid_bytes'], info['cluster_bytes'], info['volume_bytes'])
        if mapping.runs[0][1] != info['mft_start_lcn']:
            raise ValueError('$MFT mapping differs from volume start LCN')
        signature = hashlib.sha256(json.dumps(mapping.runs).encode()).hexdigest()
        return info, mapping, signature

    def close(self):
        self.reader.close()


def scan(drive, output_dir, block_mib=8, max_records=0):
    output_dir.mkdir(parents=True, exist_ok=False)
    report = {'drive':drive,'started_at':datetime.now(timezone.utc).isoformat(),
              'method':'raw_volume_ReadFile_over_MFT_run_map','source_access':'GENERIC_READ',
              'status':'starting','read_complete':False,'point_in_time_snapshot':False,
              'counts':{'slots':0,'empty':0,'unused':0,'extensions':0,'base_records':0,'files':0,'directories':0,
                        'sizes_parsed':0,'sizes_unresolved':0,'errors':0},
              'known_main_stream_totals':{'logical_bytes':0,'external_allocated_bytes':0},
              'limitations':['Live volume, not a snapshot; dirty memory records can differ from raw disk records.',
                             'Per-file ATTRIBUTE_LIST merging is not implemented; unresolved sizes remain NULL.',
                             'Totals count each base record once and cover unnamed DATA only; they are not disk used/free accounting.',
                             'No deletion, permission changes, journal changes, or recursive directory walking.']}
    db = sqlite3.connect(output_dir/'index.sqlite')
    db.execute('PRAGMA journal_mode=DELETE')
    db.execute('PRAGMA cache_size=-16384')
    db.execute('CREATE TABLE records(record_number INTEGER PRIMARY KEY, reference TEXT, directory INTEGER, links INTEGER, names_json TEXT, logical_bytes INTEGER, allocated_bytes INTEGER, size_status TEXT, streams_json TEXT)')
    db.execute('CREATE TABLE errors(record_number INTEGER, error TEXT)')
    db.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY, value_json TEXT)')
    volume = None
    started = time.perf_counter()
    parse_seconds = write_seconds = 0.0
    try:
        volume = RawVolume(drive)
        info, mapping, signature = volume.bootstrap()
        report.update(volume=info, mft_extents=len(mapping.runs), initial_mapping_sha256=signature,
                      mft_runs=mapping.runs)
        record_size = info['record_bytes']
        if record_size < 512 or record_size & (record_size-1):
            raise ValueError('Unsupported FILE record size')
        total = info['mft_valid_bytes']//record_size
        limit = min(total, max_records) if max_records else total
        report['initial_record_slots'] = total
        report['requested_record_slots'] = limit
        block_bytes = block_mib*1024*1024
        report['status'] = 'scanning'
        last_progress = time.perf_counter()
        for block_start in range(0, limit*record_size, block_bytes):
            block = mapping.read(volume.read_at, block_start, min(block_bytes, limit*record_size-block_start))
            rows, errors = [], []
            parse_start = time.perf_counter()
            counts = report['counts']
            for within in range(0, len(block), record_size):
                number = (block_start+within)//record_size
                raw = block[within:within+record_size]
                counts['slots'] += 1
                if raw[:4] == b'\x00'*4:
                    counts['empty'] += 1
                    continue
                try:
                    if raw[:4] != b'FILE':
                        raise ValueError('Unexpected record signature')
                    flags = struct.unpack_from('<H', raw, 22)[0]
                    if not flags & 1:
                        counts['unused'] += 1
                        continue
                    # USA stride is 512 bytes, independent of physical sector size.
                    if struct.unpack_from('<Q', raw, 32)[0]:
                        restore_record(raw, 512, source='raw')
                        counts['extensions'] += 1
                        continue
                    parsed = parse_record(raw, 512, info['cluster_bytes'], source='raw')
                    counts['base_records'] += 1
                    counts['directories' if parsed['is_directory'] else 'files'] += 1
                    if not parsed['is_directory']:
                        if parsed['size_status'] == 'parsed':
                            counts['sizes_parsed'] += 1
                            report['known_main_stream_totals']['logical_bytes'] += parsed['logical_bytes']
                            report['known_main_stream_totals']['external_allocated_bytes'] += parsed['allocated_bytes']
                        else:
                            counts['sizes_unresolved'] += 1
                    reference = hex(number | parsed['sequence'] << 48)
                    rows.append((number,reference,int(parsed['is_directory']),parsed['hard_link_count'],
                                 json.dumps(parsed['names'],ensure_ascii=True,separators=(',',':')),
                                 parsed['logical_bytes'],parsed['allocated_bytes'],parsed['size_status'],
                                 json.dumps(parsed['streams'],ensure_ascii=True,separators=(',',':'))))
                except (ValueError, struct.error) as error:
                    counts['errors'] += 1
                    errors.append((number,str(error)))
            parse_seconds += time.perf_counter()-parse_start
            write_start = time.perf_counter()
            db.executemany('INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?)', rows)
            db.executemany('INSERT INTO errors VALUES(?,?)', errors)
            db.commit()
            write_seconds += time.perf_counter()-write_start
            if time.perf_counter()-last_progress >= 1:
                progress = {'slots':counts['slots'],'total_slots':limit,'files':counts['files'],'errors':counts['errors'],
                            'elapsed_seconds':round(time.perf_counter()-started,3)}
                (output_dir/'progress.json').write_text(json.dumps(progress),encoding='utf-8')
                print(json.dumps(progress),flush=True)
                last_progress = time.perf_counter()
        end_info, end_map, end_signature = volume.bootstrap()
        report['final_mft_valid_bytes'] = end_info['mft_valid_bytes']
        report['mapping_unchanged_at_end'] = signature == end_signature
        report['mft_length_unchanged_at_end'] = info['mft_valid_bytes'] == end_info['mft_valid_bytes']
        report['read_complete'] = limit == total
        report['status'] = ('mapping_changed' if signature != end_signature else
                            'limited' if limit != total else
                            'completed_with_errors' if report['counts']['errors'] else 'completed')
    except KeyboardInterrupt:
        report['status'] = 'cancelled'
    except (OSError, ValueError, sqlite3.Error, struct.error) as error:
        report.update(status='error',error=str(error),winerror=getattr(error,'winerror',None))
    finally:
        report['elapsed_seconds'] = round(time.perf_counter()-started,6)
        report['parse_and_encode_seconds'] = round(parse_seconds,6)
        report['sqlite_write_seconds'] = round(write_seconds,6)
        if volume:
            report['raw_read_calls'] = volume.raw_calls
            report['raw_bytes_requested_and_returned'] = volume.raw_bytes
            report['raw_io_seconds'] = round(volume.io_seconds,6)
            report['bootstrap_record_calls'] = volume.bootstrap_record_calls
            volume.close()
        report['slots_per_second'] = round(report['counts']['slots']/max(report['elapsed_seconds'],1e-9))
        db.execute('INSERT INTO metadata VALUES(?,?)', ('report',json.dumps(report,ensure_ascii=True)))
        db.commit()
        db.close()
        (output_dir/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=True),encoding='utf-8')
        (output_dir/'progress.json').write_text(json.dumps({'status':report['status'],
            'slots':report['counts']['slots'],'total_slots':report.get('requested_record_slots'),
            'files':report['counts']['files'],'errors':report['counts']['errors'],
            'elapsed_seconds':report['elapsed_seconds']}),encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--drive',required=True)
    parser.add_argument('--output-dir',type=Path,required=True,help='A new directory; never overwrite an old scan')
    parser.add_argument('--block-mib',type=int,choices=(1,2,4,8,16),default=8)
    parser.add_argument('--max-records',type=int,default=0,help='0 = all initial valid MFT slots')
    args = parser.parse_args()
    drive = args.drive.upper().rstrip('\\/')
    if not re.fullmatch('[A-Z]:',drive) or args.max_records < 0:
        parser.error('Expected drive letter and nonnegative max-records')
    report = scan(drive,args.output_dir,args.block_mib,args.max_records)
    print(json.dumps(report,ensure_ascii=True,indent=2))
    return 0 if report['status'] in ('completed','limited') else 1


if __name__ == '__main__':
    raise SystemExit(main())
