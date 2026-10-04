import { describe, expect, it } from "vitest";
import { MorseDecoder, applyEvent, type MorseEvent } from "../../web/blink/decoder";
import { CLEAR_CODE, candidates, MORSE, previewFor, SEND_CODE, SPACE_CODE } from "../../web/blink/table";
import { demoTimeline } from "../../web/blink/demo";
import { DEFAULT_TIMING, type MorseTiming } from "../../web/blink/decoder";

type Seg = [closed: boolean, ms: number];

/**
 * The decoder-logic tests below use the timing they were written for (400 ms dots, 1200 ms dashes).
 * The shipped defaults are slower (see "default timing" at the end).
 */
const SPEC: MorseTiming = { minDotMs: 350, dotDashBoundaryMs: 900, deleteMs: 3000, letterGapMs: 1500 };

/**
 * Drives the decoder the way the keyboard does: exact close()/open() at segment edges,
 * plus a 30 fps tick() while open (as the Blink page does).
 */
function press(segments: Seg[], d = new MorseDecoder(SPEC), start = 0): { text: string; events: MorseEvent[]; d: MorseDecoder; end: number } {
  let t = start;
  let text = "";
  const events: MorseEvent[] = [];
  const take = (es: MorseEvent[]) => es.forEach((e) => (events.push(e), (text = applyEvent(text, e))));
  for (const [closed, ms] of segments) {
    if (closed) {
      take(d.close(t));
      t += ms;
      take(d.open(t));
    } else {
      const end = t + ms;
      for (; t < end; t += 33) take(d.tick(t));
      t = end;
      take(d.tick(t));
    }
  }
  return { text, events, d, end: t };
}

const of = (events: MorseEvent[], type: MorseEvent["type"]) => events.filter((e) => e.type === type);
const symbols = (events: MorseEvent[]) => of(events, "symbol").map((e) => (e as { symbol: string }).symbol).join("");

/** Spec timings: 400 ms dots, 1200 ms dashes, 300 ms between symbols, 2000 ms between letters. */
const letter = (code: string): Seg[] => [
  ...[...code].flatMap((s, i): Seg[] => [...(i ? [[false, 300] as Seg] : []), [true, s === "." ? 400 : 1200]]),
  [false, 2000],
];

describe("MorseDecoder classification", () => {
  it("ignores a 150 ms closure (natural blink)", () => {
    const { events } = press([[false, 500], [true, 150], [false, 2000]]);
    expect(of(events, "ignored")).toHaveLength(1);
    expect(of(events, "symbol")).toHaveLength(0);
  });

  it("reads a 500 ms closure as one dot", () => {
    expect(symbols(press([[true, 500], [false, 100]]).events)).toBe(".");
  });

  it("reads a 2500 ms closure as one dash, not a delete", () => {
    const { events } = press([[true, 2500], [false, 100]]);
    expect(symbols(events)).toBe("-");
    expect(of(events, "delete")).toHaveLength(0);
  });

  it("a 3200 ms closure deletes (on reopening) the last letter", () => {
    const { text, events } = press([...letter("...."), ...letter(".."), [true, 3200], [false, 100]]);
    expect(of(events, "delete")).toEqual([expect.objectContaining({ target: "letter" })]);
    expect(text).toBe("H");
  });

  it("a delete with symbols pending clears only the pending letter", () => {
    const { text } = press([...letter("...."), [true, 400], [false, 300], [true, 3200], [false, 2000]]);
    expect(text).toBe("H");
  });
});

describe("MorseDecoder segmentation", () => {
  it("decodes SOS with spec timings, with no spaces", () => {
    expect(press(["...", "---", "..."].flatMap(letter)).text).toBe("SOS");
  });

  it("a 6 s pause between letters adds no space", () => {
    const { text, events } = press([[true, 400], [false, 6000], [true, 400], [false, 6000]]);
    expect(text).toBe("EE");
    expect(of(events, "space")).toHaveLength(0);
  });

  it("··−− adds a space", () => {
    const { text, events } = press([...letter("."), ...letter(SPACE_CODE), ...letter(".")]);
    expect(text).toBe("E E");
    expect(of(events, "space")).toHaveLength(1);
  });

  it("········ clears the pending letter without committing anything", () => {
    // "A" is committed, then a dash is pending, then 8 dots clear it.
    const eightDots = Array.from({ length: 8 }, (): Seg[] => [[false, 300], [true, 400]]).flat();
    const { text, events, d } = press([...letter(".-"), [true, 1200], ...eightDots, [false, 3000]]);
    expect(of(events, "clear")).toHaveLength(1);
    expect(text).toBe("A");
    expect(of(events, "letter")).toHaveLength(1);
    expect(of(events, "invalid")).toHaveLength(0);
    expect(d.buffer).toBe("");
  });

  it("does not commit an invalid sequence (seven dots)", () => {
    const { text, events } = press(letter("......."));
    expect(text).toBe("");
    expect(of(events, "invalid")).toEqual([{ type: "invalid", code: "......." }]);
    expect(of(events, "letter")).toHaveLength(0);
  });

  it("a natural blink between symbols neither adds a dot nor resets the letter timer", () => {
    const { text } = press([[true, 400], [false, 600], [true, 150], [false, 2000]]);
    expect(text).toBe("E");
  });

  it("AR prosign (·−·−·) sends without adding text", () => {
    const { text, events } = press([...["---", "-.-"].flatMap(letter), ...letter(SEND_CODE)]);
    expect(text).toBe("OK");
    expect(of(events, "send")).toHaveLength(1);
  });
});

describe("adaptive letter gap", () => {
  /** Blinks `code` with `gap` ms between its elements, then a clear letter gap. */
  const slowLetter = (code: string, gap: number): Seg[] => [
    ...[...code].flatMap((s, i): Seg[] => [...(i ? [[false, gap] as Seg] : []), [true, s === "." ? 400 : 1200]]),
    [false, 6000],
  ];

  it("learns long pauses inside letters so a 2.5 s hesitation no longer splits a letter", () => {
    const hesitant = slowLetter("...", 2500);
    // Fixed timing: a 2.5 s pause inside S splits it.
    expect(press(hesitant).text).toBe("EEE");

    // Adaptive, from the 1.5 s default. A pause can only be measured as "inside a letter" if it didn't split it,
    // so the user first types 10 letters pausing 1.2 s, which lifts the gap to 3 s ...
    const d = new MorseDecoder(SPEC, { adaptive: true });
    let r = press(Array.from({ length: 10 }, () => slowLetter("..", 1200)).flat(), d);
    expect(r.text).toBe("IIIIIIIIII");
    expect(d.letterGapMs).toBe(3000);
    // ... then 20 letters with 2.5 s pauses inside them, which stay whole and push it to the 5 s cap.
    r = press(Array.from({ length: 20 }, () => slowLetter("..", 2500)).flat(), d, r.end);
    expect(r.text).toBe("I".repeat(20));
    expect(d.letterGapMs).toBe(5000);
    expect(press(hesitant, d, r.end).text).toBe("S");
  });

  it("is clamped to 1.5–5 s and falls back to the fixed gap with too little data", () => {
    const d = new MorseDecoder({ minDotMs: 350, dotDashBoundaryMs: 900, deleteMs: 3000, letterGapMs: 2200 }, { adaptive: true });
    expect(d.letterGapMs).toBe(2200);
    press(Array.from({ length: 5 }, () => slowLetter("....", 300)).flat(), d);
    expect(d.letterGapMs).toBe(1500);
  });
});

describe("pending preview", () => {
  it("shows the right letter for every code in the dictionary, as the decoder builds it", () => {
    for (const [ch, code] of Object.entries(MORSE)) {
      const { d } = press([...code].flatMap((s, i): Seg[] => [...(i ? [[false, 300] as Seg] : []), [true, s === "." ? 400 : 1200]]));
      expect(d.buffer).toBe(code);
      expect(previewFor(d.buffer)).toBe(ch);
    }
    expect(previewFor(SEND_CODE)).toBe("SPEAK");
    expect(previewFor(SPACE_CODE)).toBe("SPACE");
    expect(previewFor("......")).toBe("?");
    expect(previewFor("")).toBe("");
  });

  it("the command codes don't collide with any dictionary code", () => {
    for (const code of [SEND_CODE, SPACE_CODE, CLEAR_CODE]) expect(Object.values(MORSE)).not.toContain(code);
  });
});

describe("candidates", () => {
  it("narrows by prefix", () => {
    expect(candidates("")).toHaveLength(Object.keys(MORSE).length);
    expect(candidates("...")).toEqual(expect.arrayContaining(["S", "H", "V"]));
    expect(candidates("...")).not.toContain("A");
  });
});

describe("default timing (natural-blink study, eval/blink_results.json)", () => {
  it("ignores natural blinks (median and p95 fully-closed durations from Eyeblink8)", () => {
    const { text, events } = press([[false, 500], [true, 100], [false, 1500], [true, 333], [false, 1500], [true, 400], [false, 1500]], new MorseDecoder());
    expect(text).toBe("");
    expect(of(events, "ignored")).toHaveLength(3);
  });

  it("reads 700 ms as a dot and 1600 ms as a dash", () => {
    expect(symbols(press([[true, 700], [false, 300], [true, 1600], [false, 100]], new MorseDecoder()).events)).toBe(".-");
  });
});

describe("demoTimeline", () => {
  it("spells the word, ignores its two natural blinks, then sends", () => {
    const { text, events } = press(demoTimeline("HELP", DEFAULT_TIMING).map((s) => [s.closed, s.ms] as Seg), new MorseDecoder());
    expect(text).toBe("HELP");
    expect(of(events, "ignored")).toHaveLength(2);
    expect(events.at(-1)?.type).toBe("send");
  });
});
