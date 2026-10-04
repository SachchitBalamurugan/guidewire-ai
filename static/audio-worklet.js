class PcmDownsamplerProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.targetRate = 16000;
    this.targetChunkSamples = 640;
    this.inputBuffer = [];
    this.outputBuffer = [];
    this.step = sampleRate / this.targetRate;
    this.readIndex = 0;
    // Keep at most one second of audio queued (25 x 40 ms chunks).
    this.maxChunksBuffered = 25;
    this.port.postMessage({
      type: "ready",
      inputSampleRate: sampleRate,
      targetSampleRate: this.targetRate
    });
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0 || input[0].length === 0) {
      return true;
    }

    const channelCount = input.length;
    const sampleCount = input[0].length;
    for (let i = 0; i < sampleCount; i += 1) {
      let sum = 0;
      for (let channel = 0; channel < channelCount; channel += 1) {
        sum += input[channel][i] || 0;
      }
      this.inputBuffer.push(sum / channelCount);
    }

    while (this.readIndex + 1 < this.inputBuffer.length) {
      const base = Math.floor(this.readIndex);
      const fraction = this.readIndex - base;
      const a = this.inputBuffer[base] || 0;
      const b = this.inputBuffer[base + 1] || a;
      this.outputBuffer.push(a + (b - a) * fraction);
      this.readIndex += this.step;
    }

    const consumed = Math.floor(this.readIndex);
    if (consumed > 0) {
      this.inputBuffer.splice(0, consumed);
      this.readIndex -= consumed;
    }

    // The capture device is opened before this worklet exists, so on the first
    // process() call it can flush several seconds of buffered pre-call audio at
    // once. Sending that burst overruns the server's queue and the audio is
    // stale anyway, so keep only the most recent second and drop the rest.
    const maxBacklog = this.targetChunkSamples * this.maxChunksBuffered;
    if (this.outputBuffer.length > maxBacklog) {
      this.outputBuffer.splice(0, this.outputBuffer.length - maxBacklog);
    }

    while (this.outputBuffer.length >= this.targetChunkSamples) {
      const samples = this.outputBuffer.splice(0, this.targetChunkSamples);
      const pcm = new ArrayBuffer(samples.length * 2);
      const view = new DataView(pcm);
      for (let i = 0; i < samples.length; i += 1) {
        const clamped = Math.max(-1, Math.min(1, samples[i]));
        const int16 = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
        view.setInt16(i * 2, int16, true);
      }
      this.port.postMessage({ type: "audio", bytes: pcm.byteLength, pcm }, [pcm]);
    }

    return true;
  }
}

registerProcessor("pcm-downsampler", PcmDownsamplerProcessor);

