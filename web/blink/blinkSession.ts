import { BlinkTracker, type BlinkFrame } from "./blink";
import { MorseDecoder, type MorseEvent } from "./decoder";
import type { DemoSegment } from "./demo";
import { loadSettings, type Settings } from "./settings";

export type InputSource = "camera" | "keyboard" | "demo";

/**
 * Camera + keyboard → MorseDecoder wiring, moved out of the old Blink page (src/ui/blink.ts) unchanged:
 * camera edges and SPACE (hold = eyes closed) both go through decoder.close()/open(), and a 30 fps
 * tick commits letters when the letter gap elapses. Screens subscribe to events, frames and ticks.
 */
export class BlinkSession {
  readonly settings: Settings = loadSettings();
  readonly decoder = new MorseDecoder(this.settings.morse, { adaptive: this.settings.adaptiveTiming });
  tracker: BlinkTracker | null = null;
  cameraStartedAt = 0;
  private keyDown = false;
  private calibrationListener: ((f: BlinkFrame) => void) | null = null;
  private timer: ReturnType<typeof setInterval>;
  private demoTimers: ReturnType<typeof setTimeout>[] = [];
  private demoClosed = false;

  /** Decoder events from either input. */
  onEvents: (events: MorseEvent[], source: InputSource) => void = () => {};
  /** Every camera frame (status, debug plot), including during calibration. */
  onFrame: (f: BlinkFrame) => void = () => {};
  /** ~30 fps, after the decoder tick. */
  onTick: (now: number) => void = () => {};

  constructor(readonly video: HTMLVideoElement) {
    this.timer = setInterval(() => {
      const now = performance.now();
      this.feed(this.decoder.tick(now), "camera");
      this.onTick(now);
    }, 33);
    window.addEventListener("keydown", this.onKey);
    window.addEventListener("keyup", this.onKey);
  }

  get cameraOn(): boolean {
    return !!this.tracker;
  }

  get calibrating(): boolean {
    return !!this.calibrationListener;
  }

  private feed(events: MorseEvent[], source: InputSource): void {
    if (events.length) this.onEvents(events, source);
  }

  private handleFrame = (f: BlinkFrame): void => {
    this.onFrame(f);
    if (this.calibrationListener) return this.calibrationListener(f);
    for (const e of f.edges) this.feed(e.type === "close" ? this.decoder.close(e.t) : this.decoder.open(e.t, e.merged), "camera");
  };

  async startCamera(): Promise<void> {
    if (this.tracker) return;
    const tracker = new BlinkTracker(this.video, this.handleFrame, this.settings.blink);
    this.tracker = tracker;
    this.cameraStartedAt = performance.now();
    try {
      await tracker.start();
    } catch (err) {
      tracker.stop();
      if (this.tracker === tracker) this.tracker = null;
      throw err;
    }
  }

  stopCamera(): void {
    if (!this.tracker) return;
    this.tracker.stop();
    this.tracker = null;
    if (this.decoder.closed) this.decoder.reset();
  }

  /** Routes camera frames to a calibration instead of the decoder (null = back to normal). */
  listen(l: ((f: BlinkFrame) => void) | null): void {
    this.calibrationListener = l;
  }

  // Keyboard fallback: hold SPACE = eyes closed, through the same decoder as the camera.
  private onKey = (e: KeyboardEvent): void => {
    const tag = (e.target as HTMLElement)?.tagName;
    if (e.code !== "Space" || tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    e.preventDefault();
    if (e.repeat || this.calibrationListener) return;
    const down = e.type === "keydown";
    if (down === this.keyDown) return;
    this.keyDown = down;
    this.feed(down ? this.decoder.close(e.timeStamp) : this.decoder.open(e.timeStamp), "keyboard");
  };

  get demoPlaying(): boolean {
    return this.demoTimers.length > 0;
  }

  /**
   * "Watch a demo": scripted eye closures (morse/demo.ts) through the same decoder as the camera
   * and keyboard, in real time. `onNote` receives the script's captions.
   */
  playDemo(segments: DemoSegment[], onNote: (note: string) => void, onEnd: () => void): void {
    this.stopDemo();
    let at = 0;
    for (const seg of segments) {
      this.demoTimers.push(
        setTimeout(() => {
          const t = performance.now();
          if (seg.closed && !this.demoClosed) this.feed(this.decoder.close(t), "demo");
          if (!seg.closed && this.demoClosed) this.feed(this.decoder.open(t), "demo");
          this.demoClosed = seg.closed;
          if (seg.note) onNote(seg.note);
        }, at),
      );
      at += seg.ms;
    }
    this.demoTimers.push(
      setTimeout(() => {
        if (this.demoClosed) this.feed(this.decoder.open(performance.now()), "demo");
        this.demoClosed = false;
        this.demoTimers = [];
        onEnd();
      }, at),
    );
  }

  stopDemo(): void {
    this.demoTimers.forEach(clearTimeout);
    this.demoTimers = [];
    if (this.demoClosed) this.feed(this.decoder.open(performance.now()), "demo");
    this.demoClosed = false;
  }

  dispose(): void {
    this.stopDemo();
    clearInterval(this.timer);
    this.tracker?.stop();
    this.tracker = null;
    window.removeEventListener("keydown", this.onKey);
    window.removeEventListener("keyup", this.onKey);
  }
}
