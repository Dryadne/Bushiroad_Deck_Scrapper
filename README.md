# Deck Log Tracker

Pulls decklists from Bushiroad's **Deck Log** by deck code — from either
the English site (`decklog-en.bushiroad.com`) or the Japanese site
(`decklog.bushiroad.com`) — and stores them in a SQLite database you can
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

**Desktop GUI** — opens a window for importing decks and browsing the card report:

```bash
python decklog_gui.py
```

The GUI uses the same SQLite database and scraper as the command-line interface.
Choose **Cardfight Vanguard** or **Weiss Schwarz** from the game selector; each
uses its own database file (`cardfight_vanguard.db` or `weiss_schwarz.db`). Enter
a Deck Log code, select EN or JP, optionally enter the tournament date in
`YYYY-MM-DD` format, and click **Fetch and save deck**. The imported decks and
weighted card report refresh automatically.

Deck imports automatically identify Cardfight Vanguard or Weiss Schwarz from
the card image source returned by Deck Log and save to the matching database.
The selected game is used as a fallback when Deck Log returns no recognizable
card image metadata.

To import a complete tournament, paste its results-page URL into **Tournament
page URL**, enter an event name, and click **Import entire tournament**. The GUI
renders the page, follows every Deck Log link it finds, and stores the page's
rank (including team ranks such as `1A`, `1B`, and `1C`) as the deck placement.
An optional tournament date is applied to every imported deck.
Individual deck failures are reported while the remaining entries continue.
VG-Paradox English Singles/Teams pages and Japanese result pages are supported.

Use the filter bar to narrow both the imported-decks list and card report by
tournament name, import date range, and nation. Dates are selected from the
calendar controls. `Energy Generator` is excluded from card reports.

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

**Point at a different database** with `--file`. The GUI's default game files
are `cardfight_vanguard.db` and `weiss_schwarz.db` inside this project folder:

```bash
python decklog_tracker.py --file my_decks.db query
```

## The database

The SQLite database contains `decks`, `cards`, and `placement_weights` tables.
Each deck keeps both its automatic import timestamp (`date_added`) and optional
event date (`tournament_date`).
Deck updates are unique by site and deck code, card reports use indexed SQL
aggregation, and unknown placement labels are added with weight `1`.

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
next to your database. Send those over (or open them yourself) and the
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
  outbound network access here) — the SQLite storage, dedup-on-re-add, and
  weighted-query logic are unit-tested and confirmed working; the DOM
  scraping is implemented against the documented/known markup and should
  work, but flag it via `--debug` if a real code comes back empty and
  I'll adjust the selectors.
