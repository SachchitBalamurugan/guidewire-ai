export const MORSE: Record<string, string> = {
  A: ".-", B: "-...", C: "-.-.", D: "-..", E: ".", F: "..-.", G: "--.", H: "....", I: "..",
  J: ".---", K: "-.-", L: ".-..", M: "--", N: "-.", O: "---", P: ".--.", Q: "--.-", R: ".-.",
  S: "...", T: "-", U: "..-", V: "...-", W: ".--", X: "-..-", Y: "-.--", Z: "--..",
  "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-",
  "5": ".....", "6": "-....", "7": "--...", "8": "---..", "9": "----.",
  ".": ".-.-.-", "?": "..--..", ",": "--..--",
};

/** Prosign AR ("end of message"): speak the transcript. */
export const SEND_CODE = ".-.-.";
/** Word space (··−−, unassigned in ITU letters). Pauses never insert spaces on their own. */
export const SPACE_CODE = "..--";
/** Eight dots (the "error" prosign): clears the pending letter as soon as the 8th dot lands. */
export const CLEAR_CODE = "........";

const DECODE = new Map(Object.entries(MORSE).map(([ch, code]) => [code, ch]));

export function decodeCode(code: string): string | undefined {
  return DECODE.get(code);
}

/** Characters whose code starts with the symbols blinked so far. */
export function candidates(prefix: string): string[] {
  return Object.keys(MORSE).filter((ch) => MORSE[ch].startsWith(prefix));
}

export function prettyCode(code: string): string {
  return code.replace(/\./g, "·").replace(/-/g, "−");
}

/** What the pending sequence would become if committed right now: a character, a command name, or "?". */
export function previewFor(code: string): string {
  if (!code) return "";
  if (code === SEND_CODE) return "SPEAK";
  if (code === SPACE_CODE) return "SPACE";
  if (code.endsWith(CLEAR_CODE)) return "CLEAR";
  return decodeCode(code) ?? "?";
}
