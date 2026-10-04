import { FaceLandmarker, FilesetResolver } from "@mediapipe/tasks-vision";
import { BlinkDetector, type BlinkParams, type DetectorFrame } from "./blinkDetector";

export type BlinkFrame = DetectorFrame & {
  /** Frames actually processed per second over the last second. */
  fps: number;
};

let landmarkerP: Promise<FaceLandmarker> | null = null;

/** Loads MediaPipe from local files (public/mediapipe) so it works offline. */
function landmarker(): Promise<FaceLandmarker> {
  landmarkerP ??= (async () => {
    const base = "/static/";
    const fileset = await FilesetResolver.forVisionTasks(`${base}mediapipe/wasm`);
    const make = (delegate: "GPU" | "CPU") =>
      FaceLandmarker.createFromOptions(fileset, {
        baseOptions: { modelAssetPath: `${base}mediapipe/face_landmarker.task`, delegate },
        runningMode: "VIDEO",
        numFaces: 1,
        outputFaceBlendshapes: true,
      });
    try {
      return await make("GPU");
    } catch {
      return make("CPU");
    }
  })();
  return landmarkerP;
}

export class BlinkTracker {
  readonly detector: BlinkDetector;
  private stream: MediaStream | null = null;
  private raf = 0;
  private lastVideoTime = -1;
  private frameTimes: number[] = [];

  constructor(
    private video: HTMLVideoElement,
    private onFrame: (f: BlinkFrame) => void,
    params: BlinkParams,
  ) {
    this.detector = new BlinkDetector(params);
  }

  setParams(params: BlinkParams): void {
    this.detector.params = { ...params };
  }

  async start(): Promise<void> {
    const lm = await landmarker();
    this.stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "user", width: 640, height: 480, frameRate: { ideal: 30 } },
    });
    this.video.srcObject = this.stream;
    await this.video.play();
    const loop = () => {
      this.raf = requestAnimationFrame(loop);
      if (this.video.currentTime === this.lastVideoTime) return;
      this.lastVideoTime = this.video.currentTime;
      const t = performance.now();
      const res = lm.detectForVideo(this.video, t);
      this.frameTimes.push(t);
      while (this.frameTimes[0] < t - 1000) this.frameTimes.shift();
      const cats = res.faceBlendshapes?.[0]?.categories;
      const get = (name: string) => cats?.find((c) => c.categoryName === name)?.score ?? 0;
      const frame = cats ? this.detector.push(get("eyeBlinkLeft"), get("eyeBlinkRight"), t) : this.detector.push(null, null, t);
      this.onFrame({ ...frame, fps: this.frameTimes.length });
    };
    loop();
  }

  stop(): void {
    cancelAnimationFrame(this.raf);
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
  }
}
