"""Bounded parsing of NTFS 3.x FILE records. No disk I/O in this module."""
import struct


def restore_record(raw, sector_size, source='raw'):
    if len(raw) < 48 or raw[:4] != b'FILE':
        raise ValueError('Not an NTFS FILE record')
    offset, count = struct.unpack_from('<HH', raw, 4)
    if sector_size < 512 or sector_size & (sector_size-1) or len(raw) % sector_size:
        raise ValueError('Invalid sector/record size')
    if count != len(raw)//sector_size+1 or offset < 8 or offset+count*2 > len(raw):
        raise ValueError('Invalid update sequence array')
    if source == 'ioctl':
        # NTFS returns an in-memory record, not on-disk sector trailers. Its saved
        # USA can be stale after memory updates. Never apply disk fixups twice.
        return raw, 'ioctl_in_memory_record_no_disk_fixup'
    if source != 'raw':
        raise ValueError('Unknown record source')
    marker = raw[offset:offset+2]
    tails = [raw[i*sector_size-2:i*sector_size] for i in range(1, count)]
    saved = [raw[offset+i*2:offset+i*2+2] for i in range(1, count)]
    if all(tail == marker for tail in tails):
        data = bytearray(raw)
        for i, original in enumerate(saved, 1):
            data[i*sector_size-2:i*sector_size] = original
        return bytes(data), 'usa_restored'
    raise ValueError('Update sequence mismatch: torn or unsupported record')


def parse_runs(data, offset, low_vcn, high_vcn):
    if offset < 0 or offset >= len(data):
        raise ValueError('Invalid mapping pair offset')
    if low_vcn == 0 and high_vcn == (1 << 64)-1:
        if data[offset] != 0:
            raise ValueError('Empty run list has nonempty mapping pairs')
        return 0
    position, vcn, lcn = offset, low_vcn, 0
    allocated_clusters = 0
    while position < len(data):
        tag = data[position]
        position += 1
        if tag == 0:
            if vcn != high_vcn+1:
                raise ValueError('Run list VCN coverage mismatch')
            return allocated_clusters
        width, delta_width = tag & 15, tag >> 4
        if not 1 <= width <= 8 or delta_width > 8 or position+width+delta_width > len(data):
            raise ValueError('Invalid mapping pair widths')
        count = int.from_bytes(data[position:position+width], 'little')
        position += width
        if count == 0 or vcn+count > high_vcn+1:
            raise ValueError('Invalid run length')
        if delta_width:
            lcn += int.from_bytes(data[position:position+delta_width], 'little', signed=True)
            if lcn < 0:
                raise ValueError('Negative LCN')
            allocated_clusters += count
        position += delta_width
        vcn += count
    raise ValueError('Unterminated mapping pairs')


def parse_record(raw, sector_size, cluster_size, source='raw'):
    data, fixup = restore_record(raw, sector_size, source)
    sequence, link_count, start, flags = struct.unpack_from('<HHHH', data, 16)
    used, allocated_record = struct.unpack_from('<II', data, 24)
    base = struct.unpack_from('<Q', data, 32)[0]
    if not flags & 1 or base:
        raise ValueError('Record is not an in-use base record')
    if allocated_record != len(data) or not 48 <= start < used <= len(data) or start % 8:
        raise ValueError('Invalid record attribute bounds')
    result = {'sequence':sequence, 'hard_link_count':link_count, 'is_directory':bool(flags & 2),
              'fixup':fixup, 'names':[], 'streams':[], 'has_attribute_list':False}
    position = start
    while position+4 <= used:
        kind = struct.unpack_from('<I', data, position)[0]
        if kind == 0xffffffff:
            break
        if position+16 > used:
            raise ValueError('Truncated attribute')
        length = struct.unpack_from('<I', data, position+4)[0]
        form, name_chars, name_offset, attribute_flags, instance = struct.unpack_from('<BBHHH', data, position+8)
        if length < 24 or length % 8 or position+length > used or form not in (0, 1):
            raise ValueError('Invalid attribute length/form')
        attribute = data[position:position+length]
        header_size = 64 if form else 24
        if len(attribute) < header_size:
            raise ValueError('Truncated attribute header')
        if name_chars and (name_offset < header_size or name_offset % 2 or name_offset+name_chars*2 > length):
            raise ValueError('Invalid attribute name bounds')
        name = attribute[name_offset:name_offset+name_chars*2].decode('utf-16-le', errors='surrogatepass') if name_chars else ''
        if form == 0:
            value_length, value_offset = struct.unpack_from('<IH', attribute, 16)
            if value_offset < 24 or value_offset+value_length > length:
                raise ValueError('Invalid resident value bounds')
        if kind == 0x20:
            result['has_attribute_list'] = True
        if kind == 0x30 and form == 0:
            if value_length < 66:
                raise ValueError('Short FILE_NAME attribute')
            value = attribute[value_offset:value_offset+value_length]
            chars, namespace = struct.unpack_from('<BB', value, 64)
            if 66+chars*2 > len(value):
                raise ValueError('FILE_NAME extends beyond attribute')
            result['names'].append({'parent_id':hex(struct.unpack_from('<Q', value)[0]), 'namespace':namespace,
                                    'name':value[66:66+chars*2].decode('utf-16-le', errors='surrogatepass')})
        if kind == 0x80:
            stream = {'name':name, 'resident':form == 0, 'attribute_flags':attribute_flags, 'instance':instance}
            if form == 0:
                # Content resides inside the MFT. Do not export or count its bytes again as external clusters.
                stream.update(logical_bytes=value_length, allocated_bytes=0, allocated_header_bytes=0,
                              storage_kind='resident_in_mft', run_allocated_bytes=0)
            else:
                low, high, runs_offset = struct.unpack_from('<QQH', attribute, 16)
                header_allocated, logical, initialized = struct.unpack_from('<QQQ', attribute, 40)
                if low == 0 and high == (1 << 64)-1 and (header_allocated or logical or initialized):
                    raise ValueError('Empty run list declares nonempty data')
                extended = bool(attribute_flags & 0x8001)
                minimum = 72 if extended else 64
                if runs_offset < minimum or runs_offset >= length or initialized > logical:
                    raise ValueError('Invalid nonresident bounds')
                physical = struct.unpack_from('<Q', attribute, 64)[0] if extended else header_allocated
                runs_size = parse_runs(attribute, runs_offset, low, high) * cluster_size
                stream.update(low_vcn=low, high_vcn=high, logical_bytes=logical if low == 0 else None,
                              allocated_bytes=physical if low == 0 else None, allocated_header_bytes=header_allocated,
                              run_allocated_bytes=runs_size, initialized_bytes=initialized,
                              storage_kind='sparse' if attribute_flags & 0x8000 else ('compressed' if attribute_flags & 1 else 'nonresident'))
                if low == 0 and physical != runs_size and not result['has_attribute_list']:
                    # This may be a split attribute; defer full accounting below, never silently guess.
                    stream['allocation_needs_other_extents'] = True
            result['streams'].append(stream)
        position += length
    else:
        raise ValueError('Missing attribute terminator')
    main = [s for s in result['streams'] if not s['name']]
    if result['has_attribute_list']:
        result.update(size_status='unsupported_attribute_list', logical_bytes=None, allocated_bytes=None)
    elif result['is_directory']:
        result.update(size_status='directory_not_file', logical_bytes=None, allocated_bytes=None)
    elif len(main) != 1 or main[0].get('low_vcn', 0) != 0 or main[0].get('allocation_needs_other_extents'):
        result.update(size_status='incomplete_or_missing_stream', logical_bytes=None, allocated_bytes=None)
    else:
        result.update(size_status='parsed', logical_bytes=main[0]['logical_bytes'], allocated_bytes=main[0]['allocated_bytes'])
    return result
