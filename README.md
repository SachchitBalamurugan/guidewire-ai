# Noor Guide: an on-device tour co-pilot

**Hack-Nation × World Bank Youth Summit 2026, Track 4: Tourism (Small AI)**

Noor runs a small olive and herb farm. Visitors who find her arrive with a local guide to translate, and once they leave she has no way of knowing what worked. Noor Guide sits beside her on the tour, on one laptop or tablet, with no cloud needed:

1. **Listens to the whole tour** and finds where guests are most engaged, measured by **where they ask the most questions**, per stop and per topic.
2. **Suggests what to say next**: answers from her own farm facts, follows the guests' curiosity, and invites one natural next step (a bottle to take home, the harvest Saturday, a review, bringing friends). It never invents prices or claims.
3. **Translates automatically.** Guests speak any of 29 languages and Noor reads them in hers. Her words, and the co-pilot's suggestions, are shown and spoken to each guest in their own language.
4. **Includes guests who cannot speak or move.** A paralysed guest can join in by **blinking Morse code** at the guest screen's camera. Their message enters the conversation as a guest turn: it is translated, counted as engagement, and the co-pilot answers it. Quick words (`PRICE`, `TASTE`, `BUY`, `MORE`…) expand into full questions so nobody has to blink a whole sentence.
5. **Turns tours and reviews into business advice.** It reads her tour transcripts plus any reviews (pasted or CSV, in any language) and tells her what visitors keep coming back to, what to fix, the next product to build, listing text, and a follow-up message. Every point cites the reviews or tours behind it.

Built by combining two of our projects:
- **orbit.ai**, a live AI sales teleprompter. It provided the real-time session loop, the suggestion filter, the coaching-prompt discipline, the engagement analytics and the post-call report. These were retargeted from closing a sale to delighting a visitor.
- **Bolne Sathi** (hacknations), an offline communication aid. It provided the blink detector (MediaPipe) and the Morse decoder, tuned on 469 annotated natural blinks so ordinary blinks are ignored.

## Small AI: what runs where

| Job | Model | Size | Where |
|---|---|---|---|
| Speech to text + language ID | Whisper small (faster-whisper, int8) | 486 MB | CPU, on device |
| Translation, 200 languages | NLLB-200 distilled 600M (CTranslate2, int8) | 647 MB | CPU, on device |
| Next-line suggestions, review analysis | Qwen2.5 3B via Ollama | ~1.9 GB | CPU, on device |
| Blink detection | MediaPipe Face Landmarker | 4 MB | in the browser |
| Fallback when no LLM is running | FAQ retrieval + rules from the farm briefing | – | – |

The co-pilot never goes blank. If the small LLM is not running or is slow, a rule layer answers from Noor's FAQ and stop facts. Gemini is an optional cloud fallback (`LLM_BACKEND=gemini`), off by default.

## Run it

```bash
cd noor-guide
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # macOS/Linux: .venv/bin/pip
npm install && npm run build                       # builds static/blink.bundle.js
```

Optional, for model-written suggestions and insights: install [Ollama](https://ollama.com) and pull the small model.

```bash
ollama pull qwen2.5:3b
```

Start the server:

```bash
.venv/Scripts/python -m uvicorn app:app --port 8000
```

- **Guide console:** http://127.0.0.1:8000. Start a tour, or press **Play demo tour**.
- **Guest screen:** http://127.0.0.1:8000/guest. Open it in a second window or on a tablet facing the guests, and tap **Sound** once.
- **Insights:** http://127.0.0.1:8000/insights. Load the sample reviews, then press **Generate insights**.

The first start downloads Whisper and NLLB from Hugging Face (about 1.1 GB, once). After that it runs offline.

> The camera and microphone need a secure origin. `localhost` counts. For a guest tablet on the LAN, serve over HTTPS (`uvicorn ... --ssl-keyfile --ssl-certfile`) or run the guest screen in a second window on the same laptop.

### Phone demo

Every screen is built for phones first (tested at 360 px Android and iPhone sizes, portrait and landscape). To open it on phones with the microphone working, start it through an HTTPS tunnel:

```bash
./share.sh            # macOS / Linux  (needs: brew install cloudflared)
powershell -ExecutionPolicy Bypass -File share.ps1    # Windows
```

It prints two links. Open the first on the guide's phone (the tour console) and `/guest` on each visitor's phone. The tunnel only carries the pages; speech recognition, translation and suggestions still run on the laptop. Without internet, use the laptop plus a tablet on the same Wi-Fi over HTTPS, as above.

## Using it on a tour

- Tap the **stop** you are at. Questions are credited to that stop, and the badge shows the count.
- **Who's speaking:** *Auto* tags anyone not speaking your language as a visitor. With a group that shares your language, hold **Visitor**, or let them use the guest screen's mic.
- **Say it for me** sends the suggestion to the guest screen, translated and spoken, and records it in the conversation.
- **End tour** opens the report: questions by stop, the hot topic, missed sale moments, questions you had no answer for (add them to your briefing), and suggestions used.
- Edit your **farm briefing** (stops, facts, products, FAQ, claims never to make) at the bottom of the Insights page. The co-pilot may only say what is in it.

### Blink input (guest screen → 👁 Blink)
| Eyes closed | Meaning |
|---|---|
| < 0.5 s | natural blink, ignored |
| 0.5–1.2 s | dot `·` |
| 1.2–3 s | dash `−` |
| ≥ 3 s | delete |
| eyes open ~1.5 s | letter done (adapts to the person's pace) |
| `··−−` | space |
| `·−·−·` | send |

Hold **Space** as a switch if no camera is available. **Calibrate** learns the person's open and closed eye levels in 4 seconds.

## How the brief's datasets map to features

| Dataset (Annex C) | Used for |
|---|---|
| FLORES-200 / NLLB-200 | The translation model and its language codes (`languages.py`) |
| MASSIVE | The intent list each guest question is sorted into: price, buy, booking, directions, food, accessibility, history, how it's made (`engagement.py`) |
| Yelp Open Dataset | The style of the review analysis. The sample reviews in `data/sample_reviews.csv` are synthetic, written in that style; no Yelp text is copied |
| Wikivoyage | Source for the stop facts and FAQ in the farm briefing (`data/sample_profile.json`) |
| UN Tourism / WDI / Enterprise Surveys | Problem framing: small operators lack the analysis and the languages, not the hospitality |

## Project layout

```
app.py              FastAPI: pages, REST, /ws (guide + guest roles)
session_manager.py  TourHub + TourSession: VAD → Whisper → NLLB → engagement → coach (forked from orbit.ai)
engines/            vad.py · stt.py (faster-whisper) · translate.py (NLLB) · llm.py (Ollama / Gemini / rules)
engagement.py       Questions per stop/topic, intents, mood, next-step signals, unanswered questions
coach.py            Prompt + small-LLM call + FAQ/rules fallback
insights.py         Tour report, review aspects, map-reduce business advice
storage.py          Local JSON store (data/store/)
prompts/            Tour co-pilot prompt and playbooks
web/blink/          Bolne Sathi's blink detector + Morse decoder (bundled to static/blink.bundle.js)
static/             guide, guest and insights pages
data/               Sample farm briefing, sample reviews, scripted demo tour
tests/              pytest (backend) + tests/web (vitest, Morse decoder)
```

## Tests

```bash
.venv/Scripts/python -m pytest -q
npx vitest run
```
