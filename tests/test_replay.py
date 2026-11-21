"""Offline behavioral tests for preservation and command-by-command playback."""
import importlib.util
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('archive_tools', ROOT / 'tools/archive.py')
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)


def drawing(commands, version=1002):
    header = [0x436f6c21, version] + [0] * 13 + [len(commands)]
    return struct.pack('<16I', *header) + b''.join(struct.pack('<I', c) for c in commands)


def point(x, y, pressure=255):
    return pressure << 2 | (512 + x * 16) << 10 | (512 + y * 16) << 21


class ReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binary = archive.build()

    def replay(self, data, frames):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'test.drw'
            path.write_bytes(data)
            return subprocess.run([str(self.binary), str(path), '64', '64', str(frames), '0'], capture_output=True)

    def test_incremental_stroke_and_final_point(self):
        red = 2 | 255 << 18
        brush = 3 | 4096 << 2 | 1 << 18 | 255 << 22
        commands = [red, brush, point(16,16), point(48,16), 1]
        result = self.replay(drawing(commands), len(commands) + 1)
        self.assertEqual(result.returncode, 0, result.stderr)
        size = 64 * 64 * 3
        frames = [result.stdout[i:i+size] for i in range(0, len(result.stdout), size)]
        self.assertEqual(len(frames), len(commands) + 1)
        self.assertEqual(frames[0], b'\xff' * size)
        pixel = lambda frame, x, y: frame[(y*64+x)*3:(y*64+x)*3+3]
        self.assertEqual(pixel(frames[3],16,16), b'\xff\0\0')
        self.assertEqual(pixel(frames[3],48,16), b'\xff\xff\xff')
        self.assertEqual(pixel(frames[4],48,16), b'\xff\0\0')
        self.assertEqual(frames[4], frames[5])

    def test_pressure_controls_opacity(self):
        brush = 3 | 4096 << 2 | 1 << 18 | 255 << 22
        result = self.replay(drawing([2,brush,point(16,16,128),1]), 2)
        self.assertEqual(result.returncode, 0, result.stderr)
        final = result.stdout[64*64*3:]
        pixel = final[(16*64+16)*3:(16*64+16)*3+3]
        self.assertTrue(all(120 <= value <= 135 for value in pixel), pixel)

    def test_reject_invalid_input_before_any_frame(self):
        for data in [b'html instead of a drawing', drawing([1])[:-1], drawing([],1300),
                     drawing([3 | 2 << 20 | 255 << 22])]:
            with self.subTest(data=data[:8]):
                result = self.replay(data, 2)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b'')

    def test_final_pixels_independent_of_frame_sampling(self):
        source = (ROOT / 'archive/fra/1633/drawing.drw').read_bytes()
        coarse, fine = self.replay(source, 2), self.replay(source, 31)
        self.assertEqual(coarse.returncode, 0, coarse.stderr)
        self.assertEqual(fine.returncode, 0, fine.stderr)
        self.assertEqual(coarse.stdout[-64*64*3:], fine.stdout[-64*64*3:])

    def test_all_archived_variants_and_manifest(self):
        archive.verify()
        for path in sorted((ROOT / 'archive/fra').glob('*/drawing.drw')):
            with self.subTest(id=path.parent.name):
                result = self.replay(path.read_bytes(),2)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(len(result.stdout),2*64*64*3)
                self.assertNotEqual(result.stdout[-64*64*3:],b'\xff'*(64*64*3))


if __name__ == '__main__':
    unittest.main()
