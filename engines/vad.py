"""Energy-based utterance segmenter for the 16 kHz mono PCM stream.

orbit.ai let Gemini Live decide where a turn ends. Whisper transcribes whole
clips, so the stream has to be cut into utterances first. On a farm the
noise floor moves (wind, animals, a tractor), so the threshold follows it
instead of being fixed.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass, field

SAMPLE_RATE = 16000
FRAME_SAMPLES = 320  # 20 ms
FRAME_MS = 20


@dataclass
class VadEvent:
    kind: str  # "speech_start" | "utterance"
    pcm: bytes = b""
    duration_ms: int = 0


@dataclass
class EnergyVad:
    # Speech must be this many times louder than the running noise floor.
    ratio: float = 3.0
    # ...and above this absolute RMS (int16 scale), so silence never triggers.
    min_rms: float = 300.0
    start_frames: int = 4  # 80 ms of speech opens an utterance
    # Pause that ends a turn. 550 ms keeps a breath mid-sentence inside the
    # turn while handing a finished question over without a noticeable wait.
    end_silence_ms: int = 550
    min_utterance_ms: int = 450
    max_utterance_ms: int = 15000
    pre_roll_frames: int = 10  # keep 200 ms before onset so first syllables survive
    noise_floor: float = 200.0
    _pending: bytearray = field(default_factory=bytearray)
    _pre_roll: list[bytes] = field(default_factory=list)
    _speech: bytearray = field(default_factory=bytearray)
    _in_speech: bool = False
    _voiced_run: int = 0
    _silence_ms: int = 0

    def push(self, pcm: bytes) -> list[VadEvent]:
        self._pending.extend(pcm)
        events: list[VadEvent] = []
        frame_bytes = FRAME_SAMPLES * 2
        while len(self._pending) >= frame_bytes:
            frame = bytes(self._pending[:frame_bytes])
            del self._pending[:frame_bytes]
            events.extend(self._frame(frame))
        return events

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    @property
    def speech_ms(self) -> int:
        return len(self._speech) // 2 * 1000 // SAMPLE_RATE if self._in_speech else 0

    def snapshot(self) -> bytes:
        """The speech so far in the current turn, for a live caption."""

        return bytes(self._speech) if self._in_speech else b""

    def flush(self) -> list[VadEvent]:
        """End of stream: hand over whatever speech is in progress."""

        if self._in_speech:
            return self._close()
        return []

    def _frame(self, frame: bytes) -> list[VadEvent]:
        rms = _rms(frame)
        threshold = max(self.min_rms, self.noise_floor * self.ratio)
        voiced = rms >= threshold
        events: list[VadEvent] = []

        if not self._in_speech:
            # The floor only learns from frames that are not speech.
            if not voiced:
                self.noise_floor = 0.95 * self.noise_floor + 0.05 * max(rms, 50.0)
            self._pre_roll.append(frame)
            if len(self._pre_roll) > self.pre_roll_frames:
                self._pre_roll.pop(0)
            self._voiced_run = self._voiced_run + 1 if voiced else 0
            if self._voiced_run >= self.start_frames:
                self._in_speech = True
                self._silence_ms = 0
                self._speech = bytearray(b"".join(self._pre_roll))
                self._pre_roll.clear()
                events.append(VadEvent("speech_start"))
            return events

        self._speech.extend(frame)
        self._silence_ms = 0 if voiced else self._silence_ms + FRAME_MS
        duration = len(self._speech) // 2 * 1000 // SAMPLE_RATE
        if self._silence_ms >= self.end_silence_ms or duration >= self.max_utterance_ms:
            events.extend(self._close())
        return events

    def _close(self) -> list[VadEvent]:
        pcm = bytes(self._speech)
        self._speech = bytearray()
        self._in_speech = False
        self._voiced_run = 0
        self._silence_ms = 0
        duration = len(pcm) // 2 * 1000 // SAMPLE_RATE
        if duration < self.min_utterance_ms:
            return []
        return [VadEvent("utterance", pcm=pcm, duration_ms=duration)]


def _rms(frame: bytes) -> float:
    samples = array("h")
    samples.frombytes(frame)
    if not samples:
        return 0.0
    total = 0
    for s in samples:
        total += s * s
    return (total / len(samples)) ** 0.5
