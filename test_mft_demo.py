"""Binary-layout and corruption checks; do not touch any volume."""
import struct
import unittest
from mft_demo import parse_batch


def record(name, version=2, attrs=0):
    header_size = 60 if version == 2 else 76
    encoded = name.encode('utf-16-le', errors='surrogatepass')
    length = (header_size + len(encoded) + 7) & ~7
    data = bytearray(length)
    struct.pack_into('<IHH', data, 0, length, version, 0)
    width = 8 if version == 2 else 16
    data[8:8+width] = (123).to_bytes(width, 'little')
    data[8+width:8+width*2] = (5).to_bytes(width, 'little')
    struct.pack_into('<IHH', data, header_size-8, attrs, len(encoded), header_size)
    data[header_size:header_size+len(encoded)] = encoded
    return data


class ParserTests(unittest.TestCase):
    def test_mixed_versions_unicode_and_parent_identity(self):
        batch = struct.pack('<Q', 999) + record('测试😀.txt') + record('folder', 3, 0x10)
        rows = list(parse_batch(batch))
        self.assertEqual([r['name'] for r in rows], ['测试😀.txt', 'folder'])
        self.assertEqual(rows[0]['file_id'], '0x7b')
        self.assertEqual(rows[1]['parent_id'], '0x5')
        self.assertEqual(rows[1]['attributes'], 0x10)

    def test_truncated_batch_and_bad_length_rejected(self):
        for batch in (b'', b'1234567', struct.pack('<Q', 1)+b'x', struct.pack('<Q', 1)+struct.pack('<IHH', 0, 2, 0), (struct.pack('<Q', 1)+record('abc'))[:-1]):
            with self.subTest(batch=batch), self.assertRaises(ValueError):
                list(parse_batch(batch))

    def test_filename_bounds_and_version_rejected(self):
        for field, value in ((56, 5000), (58, 2), (58, 61), (4, 4)):
            data = record('abc')
            struct.pack_into('<H', data, field, value)
            with self.subTest(field=field), self.assertRaises(ValueError):
                list(parse_batch(struct.pack('<Q', 1)+data))


if __name__ == '__main__':
    unittest.main()
