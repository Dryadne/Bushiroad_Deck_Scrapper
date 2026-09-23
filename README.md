# Deck Log Tracker

Pulls decklists from Bushiroad's **Deck Log** by deck code — from either
the English site (`decklog-en.bushiroad.com`) or the Japanese site
(`decklog.bushiroad.com`) — and stores them in an Excel workbook you can
query for card frequency across many decks, optionally weighted by
tournament placement.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

(Playwright needs to download a headless Chromium once; that's what the
second command does.)

## Usage

**Interactive mode** — asks for a code, asks EN or JP, asks for an event
name and placement, saves it, and loops until you leave the code blank:

```bash
python decklog_tracker.py
```

**Add one deck non-interactively:**

```bash
python decklog_tracker.py add 53V7L --site EN --event "Regional Q3" --placement Top8
```

Re-running `add` on a code you've already imported **updates** that deck
in place rather than duplicating it — useful if a decklist gets edited
after a tournament, or if you want to attach a placement after the fact.

**Run the common-cards report:**

```bash
python decklog_tracker.py query
python decklog_tracker.py query --top 25          # only the top 25
python decklog_tracker.py query --unweighted       # raw copy counts, ignore placement
```

**Point at a different workbook** (default is `decklog_data.xlsx` in the
current folder):

```bash
python decklog_tracker.py --file my_decks.xlsx query
```

## The workbook

- **Decks** — one row per deck you've added: code, site, deck title, Nation,
  regulation, event, placement, date added, total/unique card counts, and URL.
- **Cards** — one row per card per deck (long/normalized format). This is
  what the query aggregates over.
- **PlacementWeights** — editable table mapping a placement label (e.g.
  `1st`, `Top4`, `Top8`) to a numeric weight used by the weighted query.
  Edit this sheet directly in Excel to change how much a 1st-place finish
  should count versus a Top32. If you type a placement that isn't in this
  table yet, it gets added automatically with weight `1` so you can go
  back and adjust it later.

## If scraping comes back empty

Deck Log's pages are rendered client-side — there's no documented public
API for "give me the deck for this code," so this script renders the page
in a real (headless) browser and reads the card tiles out of the DOM,
the same approach existing community tools (a Cardmarket export
bookmarklet, a Firefox deck-exporter extension) use for the EN site.

If a fetch returns 0 cards — which can happen if Bushiroad tweaks their
markup, or if the JP site's DOM turns out to differ from the EN site's —
run with `--debug`:

```bash
python decklog_tracker.py --debug add 53V7L --site EN
```

This saves a screenshot and the full rendered HTML to a `debug/` folder
next to your workbook. Send those over (or open them yourself) and the
selectors in `fetch_decklist()` / `_parse_card_tile()` in
`decklog_tracker.py` can be adjusted — that logic is deliberately kept in
one small, isolated function for exactly this reason.

## Notes / limitations

- Card **number/set code** extraction is best-effort (parsed from the
  card image filename) and may come back blank for some games — card
  **name** and **quantity** are the reliable fields and are what the
  report groups by.
- Only decks that Deck Log will actually render for an anonymous visitor
  can be fetched (i.e. the same as opening the `/view/<code>` link
  yourself in a browser).
- This hasn't been run against the live site from this environment (no
  outbound network access here) — the Excel storage, dedup-on-re-add, and
  weighted-query logic are unit-tested and confirmed working; the DOM
  scraping is implemented against the documented/known markup and should
  work, but flag it via `--debug` if a real code comes back empty and
  I'll adjust the selectors.
