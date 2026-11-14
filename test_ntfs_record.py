import struct
import unittest
from ntfs_record import parse_record


def resident(value_length=128, kind=0x80):
    size = (24+value_length+7) & ~7
    data = bytearray(size)
    struct.pack_into('<IIBBHHH', data, 0, kind, size, 0, 0, 0, 0, 1)
    struct.pack_into('<IH', data, 16, value_length, 24)
    return data


def nonresident(sparse=False):
    runs = b'\x11\x01\x08\x01\x03\x00' if sparse else b'\x11\x02\x08\x00'
    start = 72 if sparse else 64
    data = bytearray((start+len(runs)+7) & ~7)
    struct.pack_into('<IIBBHHH', data, 0, 0x80, len(data), 1, 0, 0, 0x8000 if sparse else 0, 1)
    struct.pack_into('<QQH', data, 16, 0, 3 if sparse else 1, start)
    struct.pack_into('<QQQ', data, 40, 16384 if sparse else 8192, 15000 if sparse else 8191, 0)
    if sparse:
        struct.pack_into('<Q', data, 64, 4096)
    data[start:start+len(runs)] = runs
    return data


def file_record(*attributes, restored=False):
    data = bytearray(1024)
    data[:4] = b'FILE'
    struct.pack_into('<HH', data, 4, 48, 3)
    struct.pack_into('<HHHHIIQ', data, 16, 7, 1, 56, 1, 60+sum(map(len, attributes)), 1024, 0)
    position = 56
    for attribute in attributes:
        data[position:position+len(attribute)] = attribute
        position += len(attribute)
    struct.pack_into('<I', data, position, 0xffffffff)
    data[48:54] = b'\xaa\xbb\x00\x00\x00\x00'
    if not restored:
        data[510:512] = data[1022:1024] = b'\xaa\xbb'
    return data


class RecordTests(unittest.TestCase):
    def test_resident_and_driver_restored(self):
        for restored in (True, False):
            parsed = parse_record(file_record(resident(), restored=restored), 512, 4096, source='ioctl' if restored else 'raw')
            self.assertEqual((parsed['logical_bytes'], parsed['allocated_bytes']), (128, 0))
            self.assertEqual(parsed['size_status'], 'parsed')

    def test_nonresident_cluster_rounding(self):
        parsed = parse_record(file_record(nonresident()), 512, 4096)
        self.assertEqual((parsed['logical_bytes'], parsed['allocated_bytes']), (8191, 8192))

    def test_empty_nonresident_stream_sentinel(self):
        attribute = nonresident()
        struct.pack_into('<QQ',attribute,16,0,(1 << 64)-1)
        struct.pack_into('<QQQ',attribute,40,0,0,0)
        attribute[64:] = b'\x00'*(len(attribute)-64)
        parsed = parse_record(file_record(attribute),512,4096)
        self.assertEqual((parsed['logical_bytes'],parsed['allocated_bytes']),(0,0))
        struct.pack_into('<Q',attribute,48,1)
        with self.assertRaises(ValueError):
            parse_record(file_record(attribute),512,4096)

    def test_sparse_holes_do_not_count_as_storage(self):
        parsed = parse_record(file_record(nonresident(True)), 512, 4096)
        self.assertEqual((parsed['logical_bytes'], parsed['allocated_bytes']), (15000, 4096))

    def test_attribute_list_is_explicitly_unresolved(self):
        parsed = parse_record(file_record(resident(), resident(0, 0x20)), 512, 4096)
        self.assertEqual(parsed['size_status'], 'unsupported_attribute_list')
        self.assertIsNone(parsed['allocated_bytes'])

    def test_torn_record_rejected(self):
        data = file_record(resident())
        data[510:512] = b'XY'
        with self.assertRaises(ValueError):
            parse_record(data, 512, 4096)

    def test_ioctl_memory_layout_does_not_reapply_stale_usa(self):
        data = file_record(resident(600), restored=True)
        data[510:512] = b'XY'
        parsed = parse_record(data, 512, 4096, source='ioctl')
        self.assertEqual(parsed['logical_bytes'], 600)
        with self.assertRaises(ValueError):
            parse_record(data, 512, 4096, source='raw')

    def test_attribute_bounds_rejected(self):
        data = file_record(resident())
        struct.pack_into('<I', data, 60, 4096)
        with self.assertRaises(ValueError):
            parse_record(data, 512, 4096)

    def test_run_coverage_rejected(self):
        attribute = nonresident()
        attribute[65] = 3
        with self.assertRaises(ValueError):
            parse_record(file_record(attribute), 512, 4096)


if __name__ == '__main__':
    unittest.main()
