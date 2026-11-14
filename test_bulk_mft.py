import struct
import unittest

from bulk_mft import RunMap, attribute_list_entries, decode_runs


def attribute(runs, low, high):
    data = bytearray(64+len(runs))
    data[8] = 1
    struct.pack_into('<QQH', data, 16, low, high, 64)
    data[64:] = runs
    return data


class BulkTests(unittest.TestCase):
    def test_fragmented_logical_read_crosses_physical_extents(self):
        mapping = RunMap([(0,2,2),(2,9,2)],16,4,100)
        source = bytes(range(100))
        reads = []
        def read_at(offset, count):
            reads.append((offset,count))
            return source[offset:offset+count]
        self.assertEqual(mapping.read(read_at,6,8),source[14:16]+source[36:42])
        self.assertEqual(reads,[(14,2),(36,6)])

    def test_signed_mapping_delta_and_sparse_extent(self):
        data = attribute(b'\x11\x02\x09\x11\x01\xfd\x01\x02\x00',0,4)
        self.assertEqual(decode_runs(data),[(0,9,2),(2,6,1),(3,None,2)])

    def test_empty_nonresident_mapping(self):
        data = attribute(b'\x00',0,(1 << 64)-1)
        self.assertEqual(decode_runs(data),[])
        struct.pack_into('<Q',data,48,1)
        with self.assertRaises(ValueError):
            decode_runs(data)

    def test_mft_map_rejects_holes_overlaps_and_bad_physical_ranges(self):
        examples = [([(1,2,2)],8), ([(0,2,2),(1,9,2)],16),
                    ([(0,None,2)],8), ([(0,30,2)],8), ([(0,2,1)],8)]
        for runs,length in examples:
            with self.subTest(runs=runs),self.assertRaises(ValueError):
                RunMap(runs,length,4,100)

    def test_out_of_range_logical_read_rejected(self):
        mapping = RunMap([(0,2,2)],8,4,100)
        for offset,length in ((-1,1),(4,5),(0,-1)):
            with self.assertRaises(ValueError):
                list(mapping.segments(offset,length))

    def test_corrupt_run_lists_rejected(self):
        for runs in (b'\x11\x01\x08\x00',b'\x11\x00\x08\x00',b'\x11\x02',b'\x19\x00'):
            with self.subTest(runs=runs),self.assertRaises(ValueError):
                decode_runs(attribute(runs,0,1))

    def test_attribute_list_reference_and_bounds(self):
        data = bytearray(32)
        struct.pack_into('<IHBBQQH',data,0,0x80,32,0,0,16,0x100000000002a,7)
        self.assertEqual(list(attribute_list_entries(data)),[(0x80,'',16,0x100000000002a,7)])
        struct.pack_into('<H',data,4,64)
        with self.assertRaises(ValueError):
            list(attribute_list_entries(data))


if __name__ == '__main__':
    unittest.main()
