# Guidewire: a tour co-pilot for small tourism operators

**Hack-Nation × World Bank Youth Summit 2026 · Small AI for Development · Track 04: Tourism**

> **Problem statement.** Because of Guidewire, Noor will answer every visitor in their own language and learn what they value, *during* each tour, when otherwise she would depend on a go-between to translate and never find out what worked; we know because small operators like her run on instinct, without the languages or the analysis to turn a good visit into a sale, a review or a return booking (Annex C, Jordan).

Noor runs a small olive and herb farm in the Jordan Valley. She speaks Arabic; her visitors speak French, German, Korean, Spanish. The things that would grow her business (an interpreter on every tour, someone to analyse what guests liked, a coach for when to mention the harvest Saturday) cost money she doesn't have. **Guidewire puts all three on one device, with three small AI models that run on an ordinary processor and need no internet once installed.**

## How it helps Noor earn more

| What Guidewire does | What it changes for the business |
|---|---|
| Translates live between Noor and each guest, both ways | She talks to guests directly, without a go-between, in 200 languages (29 in the app's menus) |
| Suggests her next line from her own farm facts and prices | Exact prices at the moment a guest wants to buy, and never an invented discount |
| Flags the natural next step: a bottle to take home, the harvest Saturday, a review | Direct sales and repeat bookings |
| Counts guest questions per stop and topic | She learns which parts of the tour are worth building on |
| End-of-tour report: missed sale moments, questions she couldn't answer | Every tour makes the next one better |
| Results: tips to keep, fix and try, every tip citing the reviews or tours behind it | The analysis small operators can't buy |
| Writes a listing description and a thank-you message (she pastes it into WhatsApp herself) | More visibility online, and more reviews |
| "Ask about your business" in any language, by typing or voice | Answers from her own data, e.g. *"What do French guests ask about most?"* |
| Blink-to-speak for guests who can't talk or type | A tour that welcomes more people |

## What's on each screen

- **Tour** (`/`): Noor's phone or laptop. Pick her language, start the tour, tap the stop she's at. The conversation shows each guest's words in her language; **Suggested reply** offers what to say next, and **Say this to guests** sends it, translated and spoken, to the guest screens.
- **Guest screen** (`/guest`): each visitor opens it on their own phone and picks a language. Noor's words appear and are read aloud in that language. Guests can **Talk**, **Write** (or tap a ready-made question), or **Blink** in Morse code.
- **Results** (`/insights`): tips, charts of questions and review themes, ready-made messages, and **Ask about your business**.
- **My farm** (`/farm`): the farm briefing. Stops, facts, products, prices, offers, FAQ, and the claims the helper must never make. **The co-pilot may only say what's in it.**

## Small AI: what runs where

| Job | Model | Size | Where |
|---|---|---|---|
| Speech to text + language ID | Whisper small (faster-whisper, 8-bit) | 486 MB | CPU, on device |
| Translation, 200 languages | NLLB-200 distilled 600M (CTranslate2, 8-bit) | 647 MB | CPU, on device |
| Next-line suggestions, tips, Q&A | Qwen2.5 3B via Ollama | ~1.9 GB | CPU, on device |
| Blink detection | MediaPipe Face Landmarker | 4 MB | in the browser |
| Backup when the LLM is slow or off | Rules: FAQ retrieval + farm facts | – | – |

About **3 GB** in total, no GPU. Speech recognition also identifies the language, so anyone not speaking the guide's language is tagged as a visitor automatically. The co-pilot never goes blank: if the small LLM isn't running, the rule layer answers from Noor's FAQ and stop facts. Gemini is an optional cloud fallback (`LLM_BACKEND=gemini`), **off by default**.

**Why AI and not something simpler?** SMS, a phrasebook or a spreadsheet can't follow a live, multilingual conversation, translate it both ways in real time, or find patterns across many tours and reviews. Those are the three jobs the models do. Everything else (prices, offers, the never-say list) stays in a plain briefing that Noor controls.

**Local language.** Noor's interactions are in **Arabic** (Modern Standard Arabic, NLLB code `arb_Arab`). See *Limitations* for how it fares on Levantine dialect.

## Responsible AI and guardrails

- **A person decides.** Suggestions are only suggestions; nothing reaches guests until Noor taps.
- **Grounded.** The co-pilot only uses the farm briefing and what guests said. It never invents prices, discounts, dates, awards or certifications, and it obeys the *claims never to make* list.
- **"Not sure" instead of guessing.** If the briefing can't answer, the suggestion says she'll check, marked *Not sure* on her screen. The question is saved under *Questions you had no answer for* so she can add the answer in **My farm**.
- **Reads the room.** Tired or uncomfortable guests get a break, not a sales pitch.
- **Private by default.** Tours, reviews and tips are stored as local JSON in `data/store/` (git-ignored). No accounts and no cloud calls unless Gemini is switched on. Guidewire never sends messages: Noor copies them herself.

## How the brief's datasets map to features

| Dataset (Annex C) | Used for |
|---|---|
| FLORES-200 / NLLB-200 | The translation model and its language codes (`languages.py`) |
| MASSIVE | The intent list each guest question is sorted into: price, buy, booking, directions, food, accessibility, history, how it's made (`engagement.py`) |
| Yelp Open Dataset | The style of the review analysis. The sample reviews in `data/sample_reviews.csv` are **synthetic**, written in that style; no Yelp text is copied |
| Wikivoyage | Source for the stop facts and FAQ in the sample farm briefing (`data/sample_profile.json`) |
| UN Tourism / WDI / Enterprise Surveys | Problem framing: small operators lack the analysis and the languages, not the hospitality |

**What the data doesn't cover:** dialect speech (Whisper and NLLB are strongest on standard Arabic), noisy outdoor audio, real reviews for this farm (the samples are synthetic), and blink patterns beyond the 469 annotated natural blinks the detector was tuned on.

## Run it

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # macOS/Linux: .venv/bin/pip
npm install && npm run build                       # builds static/blink.bundle.js
```

Optional, for model-written suggestions and tips: install [Ollama](https://ollama.com) and pull the small model.

```bash
ollama pull qwen2.5:3b
```

Start the server:

```bash
.venv/Scripts/python -m uvicorn app:app --port 8000
```

- **Tour (guide):** http://127.0.0.1:8000. Start a tour, or try **Watch a demo tour**.
- **Guest screen:** http://127.0.0.1:8000/guest, in a second window or on a phone or tablet.
- **Results:** http://127.0.0.1:8000/insights. Try the sample reviews, then **Get my tips**.
- **My farm:** http://127.0.0.1:8000/farm.

The first start downloads Whisper and NLLB from Hugging Face (about 1.1 GB, once). After that it runs offline. Settings are in `.env.example` (model sizes, LLM backend, timeouts).

> The camera and microphone need a secure origin. `localhost` counts. For phones on the same Wi-Fi, serve over HTTPS (`uvicorn ... --ssl-keyfile --ssl-certfile`).

### Phone demo

Every screen is built for phones first (tested at 360 px, portrait and landscape). To open it on phones with the microphone working, start it through an HTTPS tunnel:

```bash
./share.sh                                              # macOS / Linux (needs cloudflared)
powershell -ExecutionPolicy Bypass -File share.ps1      # Windows
```

It prints two links: the first for the guide's phone, `/guest` for each visitor. The tunnel only carries the pages. Speech recognition, translation and suggestions still run on the laptop. Without internet, use the laptop and phones on the same local Wi-Fi over HTTPS, as above.

## Using it on a tour

- Tap the **stop** you're at. Questions are credited to that stop.
- **Who's speaking:** anyone not speaking your language is tagged as a visitor automatically. Guests can also use their own screen's mic.
- **Say this to guests** sends the suggestion, translated from its original wording, to every guest screen in each guest's language.
- **End tour** opens the report: questions by stop, the hot topic, missed sale moments, questions with no answer, and suggestions used.

### Blink input (guest screen → Blink)

| Eyes closed | Meaning |
|---|---|
| < 0.5 s | natural blink, ignored |
| 0.5–1.2 s | dot `·` |
| 1.2–3 s | dash `−` |
| ≥ 3 s | delete |
| eyes open ~1.5 s | letter done (adapts to the person's pace) |
| `··−−` | space |
| `·−·−·` | send |

Quick words (`PRICE`, `TASTE`, `BUY`, `BOOK`, `MORE`…) expand into full questions, so nobody has to blink a whole sentence. Hold **Space** as a switch if there's no camera. **Tune to my eyes** calibrates in about 4 seconds.

## Tests

```bash
.venv/Scripts/python -m pytest -q    # 63 backend tests
npm test                             # 20 web tests (Morse decoder)
```

## Project layout

```
app.py              FastAPI: pages, REST, /ws (guide + guest roles)
session_manager.py  TourHub + TourSession: VAD → Whisper → NLLB → engagement → coach
engines/            vad.py · stt.py (faster-whisper) · translate.py (NLLB) · llm.py (Ollama / Gemini / rules)
engagement.py       Questions per stop/topic, intents, mood, next-step signals, unanswered questions
coach.py            Prompt + small-LLM call + FAQ/rules fallback
insights.py         Tour report, review themes, business tips
ask.py              "Ask about your business": answers from tours and reviews, with evidence
storage.py          Local JSON store (data/store/)
prompts/            Co-pilot prompt and tour playbooks (farm walk, food tasting, workshop)
web/blink/          Blink detector + Morse decoder (bundled to static/blink.bundle.js)
static/             Tour, guest, Results and My farm pages
data/               Sample farm briefing, synthetic sample reviews, scripted demo tour
tests/              pytest (backend) + tests/web (vitest)
share.sh, share.ps1 HTTPS tunnel for the phone demo
```

## Limitations and next steps

- **Dialect.** Translation uses standard Arabic. Levantine speech and slang will be weaker; collecting Mozilla Common Voice recordings from Jordan would let us measure and fine-tune.
- **Translation slips.** NLLB sometimes mistranslates short phrases and currency (we have seen "JOD" rendered wrongly in Arabic). Guests' copies are translated directly from the co-pilot's English original to avoid a second hop.
- **Phones need the laptop.** The models run on one laptop or mini-PC; phones connect to it. A phone-only version would need smaller models.
- **Consent.** Tours are transcribed on the device. A short notice on the guest screen, with an opt-out, is the next thing to add.

## Credits

Built by combining two of our earlier projects:
- **orbit.ai**, a live AI sales teleprompter: the real-time session loop, suggestion filter, coaching-prompt discipline, engagement analytics and post-call report, retargeted from closing a sale to delighting a visitor.
- **Bolne Sathi**, an offline communication aid: the blink detector (MediaPipe) and the Morse decoder.
