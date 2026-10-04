import { DEFAULT_TIMING, type MorseTiming } from "./decoder";
import { DEFAULT_BLINK, type BlinkParams } from "./blinkDetector";

/**
 * The slice of Bolne Sathi's settings that the blink path needs. The original also carried the
 * voice-matcher thresholds and speaker profiles, which Noor Guide does not ship.
 */
export type Settings = {
  morse: MorseTiming;
  blink: BlinkParams;
  adaptiveTiming: boolean;
};

const KEY = "noor-guide.blink";

export const DEFAULT_SETTINGS: Settings = {
  morse: DEFAULT_TIMING,
  blink: DEFAULT_BLINK,
  adaptiveTiming: true,
};

export function loadSettings(): Settings {
  try {
    const raw = localStorage.getItem(KEY);
    const s = raw ? (JSON.parse(raw) as Partial<Settings>) : {};
    return {
      morse: { ...DEFAULT_SETTINGS.morse, ...s.morse },
      blink: { ...DEFAULT_SETTINGS.blink, ...s.blink },
      adaptiveTiming: s.adaptiveTiming ?? DEFAULT_SETTINGS.adaptiveTiming,
    };
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

export function saveSettings(s: Settings): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    /* storage unavailable: calibration lasts for this page only */
  }
}
