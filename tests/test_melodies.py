import tempfile
import unittest
import wave
from reminder_melodies import MELODIES, SONG_PHRASES, render_melody


class MelodyTests(unittest.TestCase):
    def test_catalog_renders_distinct_playable_clips(self):
        self.assertEqual(len({item[0] for item in MELODIES}), 11)
        contents = set()
        with tempfile.TemporaryDirectory() as directory:
            for key, *_ in MELODIES:
                path = render_melody(key, directory)
                with wave.open(str(path)) as audio:
                    self.assertEqual(audio.getnchannels(), 1)
                    self.assertEqual(audio.getsampwidth(), 2)
                    beat, phrase = SONG_PHRASES.get(key, (0.75, [(0, 1)] * 16))
                    expected = sum(length for _, length in phrase) * beat * 3 + 2
                    self.assertAlmostEqual(audio.getnframes() / audio.getframerate(), expected, places=4)
                    contents.add(audio.readframes(audio.getnframes()))
                self.assertEqual(render_melody(key, directory), path)
            self.assertEqual(len(contents), 11)
            self.assertEqual(render_melody('unknown', directory), render_melody('daybreak', directory))
