/**
 * Browser entry for the guest screen. Bundled by esbuild into static/blink.bundle.js and exposed as
 * window.NoorBlink, so the plain-JS guest page can use Bolne Sathi's blink → Morse pipeline unchanged.
 */
import { BlinkSession } from "./blinkSession";
import { applyEvent, DEFAULT_TIMING, type MorseEvent } from "./decoder";
import { demoTimeline } from "./demo";
import { prettyCode, previewFor, MORSE, SEND_CODE, SPACE_CODE } from "./table";
import { median, thresholdsFrom } from "./calibration";
import { loadSettings, saveSettings } from "./settings";

/**
 * Morse is slow, so a blinked word that matches one of these is sent as the full question. Letters
 * are what the decoder can type (A–Z, 0–9), so the keys are upper case.
 */
export const SHORTCUTS: Record<string, string> = {
  HI: "Hello, nice to meet you.",
  YES: "Yes.",
  NO: "No.",
  WHAT: "What is this?",
  HOW: "How is this made?",
  WHY: "Why do you do it this way?",
  PRICE: "How much does it cost?",
  BUY: "Can I buy some of this?",
  TASTE: "Can I taste it?",
  MORE: "Can you tell me more about this?",
  AGAIN: "Can you say that again, please?",
  SLOW: "Can you speak more slowly, please?",
  PHOTO: "Can I take a photo here?",
  WC: "Where is the toilet?",
  WATER: "Can I have some water, please?",
  REST: "Can we stop and rest for a moment?",
  LOVE: "I really love this.",
  BACK: "I would like to come back again.",
  BOOK: "How can I book another visit?",
  HELP: "I need help, please.",
};

export function expandShortcut(text: string): { text: string; expanded: boolean } {
  const key = text.trim().toUpperCase();
  const hit = SHORTCUTS[key];
  return hit ? { text: hit, expanded: true } : { text: text.trim(), expanded: false };
}

/** 2 s eyes open then 2 s eyes closed → personal close/open thresholds from the blendshape signal. */
export async function quickCalibrate(
  session: BlinkSession,
  onStep: (step: "open" | "closed" | "done", message: string) => void,
): Promise<{ closeThreshold: number; openThreshold: number }> {
  const collect = (ms: number) =>
    new Promise<number[]>((resolve) => {
      const xs: number[] = [];
      session.listen((f) => {
        if (f.faceFound) xs.push(f.signal);
      });
      setTimeout(() => resolve(xs), ms);
    });
  try {
    onStep("open", "Keep your eyes open and look at the screen");
    const open = await collect(2000);
    onStep("closed", "Now close both eyes until you hear the tone");
    const closed = await collect(2000);
    const t = thresholdsFrom(median(open), median(closed));
    const settings = loadSettings();
    settings.blink = { ...settings.blink, ...t };
    saveSettings(settings);
    session.tracker?.setParams(settings.blink);
    onStep("done", "Calibrated");
    return t;
  } finally {
    session.listen(null);
  }
}

export type { MorseEvent };

const api = {
  BlinkSession,
  applyEvent,
  demoTimeline,
  prettyCode,
  previewFor,
  expandShortcut,
  quickCalibrate,
  SHORTCUTS,
  MORSE,
  SEND_CODE,
  SPACE_CODE,
  DEFAULT_TIMING,
};

declare global {
  interface Window {
    NoorBlink: typeof api;
  }
}

window.NoorBlink = api;
