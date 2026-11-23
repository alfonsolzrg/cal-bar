"""Synthesized Cal Bar melodies and instrumental excerpts from Sayed Darwish."""
import math
from pathlib import Path
import struct
import wave

# MIDI pitches, one per beat; 0 is a rest. Nine original Cal Bar sequences.
MELODIES = (
    ('daybreak', 'Daybreak · soft piano', (60, 64, 67, 72, 71, 67, 64, 62, 65, 69, 72, 74, 72, 69, 65, 60), 'piano'),
    ('woodland', 'Woodland · marimba', (67, 72, 76, 74, 72, 67, 69, 64, 65, 69, 74, 72, 69, 65, 64, 60), 'wood'),
    ('starlight', 'Starlight · music box', (79, 76, 72, 74, 79, 81, 76, 72, 77, 74, 71, 72, 76, 74, 72, 0), 'bell'),
    ('harbour', 'Harbour · mellow bells', (60, 67, 62, 69, 64, 71, 67, 72, 65, 72, 64, 71, 62, 69, 60, 0), 'bell'),
    ('raindrops', 'Raindrops · plucked keys', (72, 67, 76, 72, 79, 76, 74, 69, 77, 74, 81, 77, 76, 72, 67, 0), 'wood'),
    ('orbit', 'Orbit · gentle synth', (60, 63, 70, 67, 65, 70, 75, 72, 67, 72, 74, 70, 65, 63, 62, 60), 'soft'),
    ('sunroom', 'Sunroom · warm piano', (65, 69, 72, 76, 74, 72, 69, 67, 64, 67, 71, 74, 72, 67, 64, 60), 'piano'),
    ('paperkite', 'Paper Kite · bright marimba', (72, 76, 79, 76, 74, 77, 81, 77, 76, 79, 84, 79, 77, 74, 72, 0), 'wood'),
    ('moonrise', 'Moonrise · slow chimes', (69, 72, 76, 79, 76, 74, 71, 67, 65, 69, 72, 76, 74, 71, 69, 0), 'bell'),
    ('salma', 'Salma Ya Salama', (), 'wood'),
    ('helwa', 'El Helwa Di', (), 'piano'),
)


# Instrumental, single-line excerpts of Darwish's early compositions.
# (MIDI pitch, quarter-note duration). No lyrics, recordings or accompaniment
# from later arrangements are included. See docs/melody-sources.md.
SONG_PHRASES = {
    'helwa': (0.55, (
        (60,.5),(65,.5),(64,.5),(65,.5),(67,1),(68,.5),(67,.5),
        (65,1),(67,.5),(65,.5),(64,1),(65,.5),(64,.5),
        (61,.5),(60,.5),(64,.5),(61,.5),(60,2),
        (60,.5),(65,.5),(64,.5),(65,.5),(67,1),(67,1),
        (67,.5),(67,.5),(72,1),(67,1),(67,1),
        (68,.5),(67,.5),(65,.5),(67,.5),(68,2),
    )),
    'salma': (0.65, (
        (67,.25),(67,.25),(67,.25),(67,.25),(67,.5),(67,.5),
        (65,.375),(65,.125),(67,.25),(67,.25),(67,.5),(77,.25),(75,.25),
        (77,.25),(77,.125),(79,.125),(77,.25),(75,.25),(77,.375),(79,.125),(77,.25),(75,.25),
        (77,.5),(79,.5),(79,.75),(72,.25),
        (67,1),(67,.5),(67,.5),
        (65,.5),(67,.5),(67,.5),(77,.25),(75,.25),
        (77,.25),(79,.25),(77,.25),(75,.25),(77,.25),(79,.25),(77,.25),(75,.25),
        (77,.5),(79,.5),(79,.5),(72,.5),
        (67,.75),(67,.125),(67,.125),(67,.5),(67,.5),
        (65,.5),(67,.25),(67,.25),(67,.5),(77,.25),(75,.25),
    )),
}


def render_melody(key, cache_dir):
    """Render bounded, gently fading mono PCM, with each tune's rhythm."""
    melody = next((item for item in MELODIES if item[0] == key), MELODIES[0])
    key, _, notes, voice = melody
    destination = Path(cache_dir) / 'melodies-v1' / (key + '.wav')
    if destination.is_file():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    beat, phrase = SONG_PHRASES.get(key, (0.75, tuple((note, 1) for note in notes)))
    rate, tail = 22050, 2.0
    samples = [0.0] * int(rate * (sum(length for _, length in phrase) * beat + tail))
    position = 0.0
    for pitch, length in phrase:
        start = int(position * rate)
        position += length * beat
        if not pitch:
            continue
        frequency = 440 * 2 ** ((pitch - 69) / 12)
        for n in range(int(rate * 1.8)):
            t = n / rate
            envelope = min(t / 0.018, 1) * math.exp(-t * (4 if voice == 'wood' else 2.6))
            phase = 2 * math.pi * frequency * t
            overtone = {'wood': 3.0, 'bell': 2.76, 'piano': 2.0, 'soft': 2.0}[voice]
            value = math.sin(phase) + 0.22 * math.sin(phase * overtone)
            samples[start + n] += 0.22 * envelope * value
    pcm = b''.join(struct.pack('<h', int(max(-0.9, min(0.9, value)) * 32767)) for value in samples)
    temporary = destination.with_suffix('.tmp')
    with wave.open(str(temporary), 'wb') as output:
        output.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        output.writeframes(pcm)
    temporary.replace(destination)
    return destination
