#!/usr/bin/env python3
"""
decklog_tracker.py

Pulls decklists from Bushiroad's "Deck Log" tool by deck code, from either
the English site (decklog-en.bushiroad.com) or the Japanese site
(decklog.bushiroad.com), and stores them in a SQLite database so they can
be queried for card frequency across many decks -- optionally weighted by
tournament placement.

WHY A HEADLESS BROWSER, NOT `requests`:
Deck Log's /view/<code> pages are rendered client-side (the raw HTML is
just a JS app shell). Bushiroad has not published a documented JSON
endpoint for "give me the deck for this code" (only an unrelated card
*search* endpoint). The reliable way to get the card list -- the same way
existing community tools do it (a Cardmarket-export bookmarklet, a
Firefox deck-exporter extension) -- is to render the page in a real
browser and read the card tiles out of the DOM. This script uses
Playwright (headless Chromium) for that.

SETUP
-----
    pip install -r requirements.txt
    playwright install chromium

USAGE
-----
Interactive mode (asks you for a code, EN/JP, event, placement, loops):
    python decklog_tracker.py

Add one deck non-interactively:
    python decklog_tracker.py add 53V7L --site EN --event "Regional Q3" --placement "Top8"

Re-scrape a deck already in the database (updates it in place):
    python decklog_tracker.py add 53V7L --site EN

Run the card-frequency report:
    python decklog_tracker.py query
    python decklog_tracker.py query --top 25 --unweighted

All commands accept --file to point at a different SQLite database
(default: decklog_data.db in the current directory).

IF SCRAPING BREAKS
-------------------
Deck Log's markup could change, or the JP site's DOM could differ from
what the EN site's export tools assume. If `add` comes back with zero
cards, run with --debug: it saves a screenshot and the full page HTML
next to the database so you (or I) can see what changed and fix the
selectors in `fetch_decklist()` below.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
except ImportError:
    print("Missing dependency. Run: pip install -r requirements.txt", file=sys.stderr)
    raise



# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

SITE_URLS = {
    "EN": "https://decklog-en.bushiroad.com/view/{code}",
    "JP": "https://decklog.bushiroad.com/view/{code}",
}

DEFAULT_DATABASE = str(Path(__file__).resolve().parent / "decklog_data.db")
DEFAULT_WEIGHTS = [
    ("1st", 5),
    ("2nd", 4),
    ("Top4", 3),
    ("Top8", 2),
    ("Top16", 1.5),
    ("Top32", 1),
    ("Unknown", 1),
]


# --------------------------------------------------------------------------
# Scraping
# --------------------------------------------------------------------------

@dataclass
class CardEntry:
    name: str
    quantity: int
    card_number: str = ""
    image_ref: str = ""


@dataclass
class DeckResult:
    code: str
    site: str
    url: str
    game_title: str
    deck_title: str = ""
    nation: str = ""
    regulation: str = ""
    cards: list[CardEntry] = field(default_factory=list)


@dataclass
class TournamentEntry:
    rank: str
    deck_code: str
    site: str
    deck_url: str
    player_or_team: str = ""


SUPPORTED_GAMES = ("Cardfight Vanguard", "Weiss Schwarz")


# Selector used by Deck Log's card tiles. This is the same selector the
# christopherkade/cfv-deck-exporter tool and Cardmarket bookmarklet rely on
# for the EN site. If Deck Log changes its markup, or the JP site differs,
# update this list -- fetch_decklist() tries each in order until one
# returns tiles.
CARD_TILE_SELECTORS = [
    ".card-controller-inner",
    ".card_thumb",
    ".deck-card",
]


def normalize_deck_title(title: str) -> str:
    title = re.sub(r"\s+deck$", "", (title or "").strip(), flags=re.IGNORECASE).strip()
    return re.sub(r"^Deck Name\s*\[([^]]*)\]$", r"\1", title, flags=re.IGNORECASE).strip()


def infer_game_from_cards(cards: list[CardEntry]) -> str:
    """Infer the game from Deck Log's card image host/path."""
    image_refs = " ".join(card.image_ref.lower() for card in cards)
    if "cf-vanguard.com" in image_refs or "vanguard" in image_refs:
        return "Cardfight Vanguard"
    if "ws-tcg.com" in image_refs or "weiss" in image_refs:
        return "Weiss Schwarz"
    return ""


def fetch_decklist(code: str, site: str, debug_dir: Path | None = None, headless: bool = True) -> DeckResult:
    """Render a Deck Log page and scrape its card list.

    site must be "EN" or "JP".
    """
    site = site.upper()
    if site not in SITE_URLS:
        raise ValueError(f"site must be one of {list(SITE_URLS)}, got {site!r}")

    url = SITE_URLS[site].format(code=code)
    last_error: Exception | None = None

    for attempt in range(3):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                    locale="en-US",
                    timezone_id="America/New_York",
                    viewport={"width": 1440, "height": 1200},
                )
                context.set_extra_http_headers({
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "Upgrade-Insecure-Requests": "1",
                })
                page = context.new_page()
                try:
                    response = page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                    if response is not None and response.status >= 400:
                        raise RuntimeError(f"HTTP {response.status} while loading {url}")
                    page.wait_for_timeout(2500)
                except PlaywrightTimeoutError:
                    page.wait_for_timeout(3000)

                page_content = page.content()
                if "Request blocked" in page_content or "The request could not be satisfied" in page_content:
                    raise RuntimeError("CloudFront blocked the request (403/anti-bot page)")

                game_title = ""
                try:
                    game_title = page.title().split("|")[-1].strip()
                except Exception:
                    pass

                deck_title = ""
                try:
                    deck_title = normalize_deck_title(page.locator(".views-view h2").first.text_content() or "")
                except Exception:
                    pass

                nation = ""
                try:
                    nation = (page.locator(".preview-top-label-right span").first.text_content() or "").strip()
                except Exception:
                    pass

                regulation = ""
                try:
                    regulation_text = (page.locator(".preview-top-label-left").first.text_content() or "").strip()
                    regulation = re.sub(r"^Regulation\s*:\s*", "", regulation_text, flags=re.IGNORECASE).strip()
                except Exception:
                    pass

                tiles = []
                used_selector = None
                for selector in CARD_TILE_SELECTORS:
                    try:
                        found = page.query_selector_all(selector)
                    except Exception:
                        continue
                    if found:
                        tiles = found
                        used_selector = selector
                        break

                cards: list[CardEntry] = []
                for tile in tiles:
                    entry = _parse_card_tile(tile)
                    if entry is not None:
                        cards.append(entry)

                detected_game = infer_game_from_cards(cards)
                if detected_game:
                    game_title = detected_game

                if debug_dir is not None:
                    debug_dir.mkdir(parents=True, exist_ok=True)
                    (debug_dir / f"{site}_{code}.html").write_text(page_content, encoding="utf-8")
                    page.screenshot(path=str(debug_dir / f"{site}_{code}.png"), full_page=True)
                    print(f"[debug] selector used: {used_selector!r}; wrote HTML + screenshot to {debug_dir}")

                browser.close()
                return DeckResult(
                    code=code,
                    site=site,
                    url=url,
                    game_title=game_title,
                    deck_title=deck_title,
                    nation=nation,
                    regulation=regulation,
                    cards=cards,
                )
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                continue
            raise RuntimeError(f"Failed to fetch deck {code} from {site}: {exc}") from exc

    if last_error is not None:
        raise RuntimeError(f"Failed to fetch deck {code} from {site}: {last_error}") from last_error
    raise RuntimeError(f"Failed to fetch deck {code} from {site}: unknown error")


def fetch_tournament_entries(url: str, headless: bool = True) -> list[TournamentEntry]:
    """Render a tournament page and return its ranked Deck Log links.

    VG-Paradox tournament pages populate ``#data-output`` in JavaScript, so
    this intentionally uses the same browser-rendered approach as deck fetch.
    Other pages with a table of links can also work when their rows contain a
    Deck Log URL with a ``/view/<code>`` path.
    """
    if not url.strip():
        raise ValueError("tournament URL cannot be blank")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36",
            locale="en-US",
            viewport={"width": 1440, "height": 1200},
        )
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            if response is not None and response.status >= 400:
                raise RuntimeError(f"HTTP {response.status} while loading tournament page")
            page.wait_for_timeout(2500)

            row_selector = "#data-output tr, #data-outputSingles tr, #data-outputTeams tr"
            try:
                page.locator(row_selector).first.wait_for(state="attached", timeout=10_000)
            except PlaywrightTimeoutError:
                pass
            rows = page.locator(row_selector)
            entries: list[TournamentEntry] = []
            for row_index in range(rows.count()):
                row = rows.nth(row_index)
                cells = row.locator("td")
                if cells.count() == 0:
                    continue
                rank = (cells.nth(0).text_content() or "").strip()
                rank = re.sub(r"[A-Za-z]+$", "", rank).strip() or rank
                player_or_team = (cells.nth(1).text_content() or "").strip() if cells.count() > 1 else ""
                links = row.locator("a[href]")
                for link_index in range(links.count()):
                    href = (links.nth(link_index).get_attribute("href") or "").strip()
                    match = re.search(r"decklog(?:-en)?\.bushiroad\.com/view/([^/?#]+)", href, flags=re.IGNORECASE)
                    if not match:
                        continue
                    site = "EN" if "decklog-en" in href.lower() else "JP"
                    entries.append(TournamentEntry(
                        rank=rank or "Unknown",
                        deck_code=match.group(1),
                        site=site,
                        deck_url=href,
                        player_or_team=player_or_team,
                    ))
                    break
            return entries
        finally:
            browser.close()


def _parse_card_tile(tile) -> CardEntry | None:
    """Best-effort extraction of (name, quantity, card number, image ref)
    from one card tile element. Deck Log doesn't expose a clean data-*
    attribute for this, so we pull from whatever's available:

      - name:      the <img title="..."> attribute, format seen in the
                    wild is "<something>: <Card Name>" -- we take the part
                    after the colon if present, else the whole title.
      - quantity:  the tile's last text node (the little "x3" style count
                    badge next to/under the art).
      - card_number: not reliably exposed in the DOM as clean text; left
                    blank unless it can be recovered from the image
                    filename (varies by game, best-effort regex).
      - image_ref: the image src, kept as a stable fallback identifier
                    even if name parsing above is imperfect.
    """
    try:
        title_attr = ""
        title_el = tile.query_selector(".card-ctrl") or tile.query_selector("[title]")
        if title_el is not None:
            title_attr = title_el.get_attribute("title") or ""

        image_ref = tile.evaluate(
            """
            (el) => {
                const container = el.closest('.card-container');
                const img = container ? container.querySelector('img') : null;
                return img ? (img.dataset.src || img.src || '') : '';
            }
            """
        )

        if not title_attr:
            image_title = tile.evaluate(
                """
                (el) => {
                    const container = el.closest('.card-container');
                    const img = container ? container.querySelector('img') : null;
                    return img ? (img.title || img.alt || '') : '';
                }
                """
            )
            title_attr = image_title or ""

        card_number = ""
        name = ""
        if ":" in title_attr:
            left, right = title_attr.split(":", 1)
            card_number = left.strip()
            name = right.strip()
        else:
            name = title_attr.strip()

        if not name:
            alt_name = tile.evaluate(
                """
                (el) => {
                    const container = el.closest('.card-container');
                    const img = container ? container.querySelector('img') : null;
                    return img ? (img.alt || '') : '';
                }
                """
            )
            name = alt_name or "Unknown Card"

        qty_text = ""
        qty_el = tile.query_selector(".num")
        if qty_el is not None:
            qty_text = (qty_el.text_content() or "").strip()
        if not qty_text:
            inner_text = tile.inner_text().strip()
            qty_text = inner_text.splitlines()[-1].strip() if inner_text else ""
        qty_match = re.search(r"\d+", qty_text)
        quantity = int(qty_match.group()) if qty_match else 1

        if not card_number:
            m = re.search(r"([A-Za-z0-9]+_[A-Za-z0-9]+_\d+)", image_ref)
            if m:
                card_number = m.group(1).upper().replace("_", "/", 1).replace("_", "-")

        return CardEntry(name=name, quantity=quantity, card_number=card_number, image_ref=image_ref)
    except Exception:
        return None


# --------------------------------------------------------------------------
# SQLite storage
# --------------------------------------------------------------------------

def open_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS decks (
            id INTEGER PRIMARY KEY,
            deck_code TEXT NOT NULL,
            site TEXT NOT NULL,
            game TEXT NOT NULL DEFAULT '',
            deck_title TEXT NOT NULL DEFAULT '',
            nation TEXT NOT NULL DEFAULT '',
            regulation TEXT NOT NULL DEFAULT '',
            date_added TEXT NOT NULL,
            tournament_date TEXT NOT NULL DEFAULT '',
            deck_url TEXT NOT NULL,
            event TEXT NOT NULL DEFAULT '',
            placement TEXT NOT NULL DEFAULT '',
            total_cards INTEGER NOT NULL DEFAULT 0,
            unique_cards INTEGER NOT NULL DEFAULT 0,
            UNIQUE(deck_code, site)
        );
        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY,
            deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
            deck_code TEXT NOT NULL,
            card_name TEXT NOT NULL,
            card_number TEXT NOT NULL DEFAULT '',
            quantity INTEGER NOT NULL,
            site TEXT NOT NULL,
            event TEXT NOT NULL DEFAULT '',
            placement TEXT NOT NULL DEFAULT '',
            weight REAL NOT NULL DEFAULT 1,
            image_ref TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS placement_weights (
            placement TEXT PRIMARY KEY,
            weight REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS cards_name_idx ON cards(card_name);
        CREATE INDEX IF NOT EXISTS cards_deck_idx ON cards(deck_id);
        CREATE INDEX IF NOT EXISTS decks_event_idx ON decks(event);
    """)
    deck_columns = {row[1] for row in db.execute("PRAGMA table_info(decks)")}
    if "tournament_date" not in deck_columns:
        db.execute("ALTER TABLE decks ADD COLUMN tournament_date TEXT NOT NULL DEFAULT ''")
    db.execute("CREATE INDEX IF NOT EXISTS decks_tournament_date_idx ON decks(tournament_date)")
    db.executemany(
        "INSERT OR IGNORE INTO placement_weights (placement, weight) VALUES (?, ?)",
        DEFAULT_WEIGHTS,
    )
    db.commit()
    return db


def get_placement_weight(db: sqlite3.Connection, placement: str) -> float:
    placement_norm = (placement or "Unknown").strip().lower()
    numeric_rank = re.fullmatch(r"\d+", placement_norm)
    if numeric_rank:
        rank = int(numeric_rank.group())
        if rank == 1:
            placement_norm = "1st"
        elif rank == 2:
            placement_norm = "2nd"
        elif rank in (3, 4):
            placement_norm = "top4"
        elif 5 <= rank <= 8:
            placement_norm = "top8"
    row = db.execute(
        "SELECT weight FROM placement_weights WHERE lower(placement) = ?",
        (placement_norm,),
    ).fetchone()
    if row is not None:
        return float(row["weight"])
    db.execute(
        "INSERT OR IGNORE INTO placement_weights (placement, weight) VALUES (?, 1)",
        (placement or "Unknown",),
    )
    db.commit()
    return 1.0


def save_deck(
    db: sqlite3.Connection,
    deck: DeckResult,
    event: str,
    placement: str,
    tournament_date: str = "",
) -> None:
    event = event or ""
    placement = placement or ""
    tournament_date = (tournament_date or "").strip()
    if tournament_date:
        try:
            dt.date.fromisoformat(tournament_date)
        except ValueError as exc:
            raise ValueError("tournament date must use YYYY-MM-DD format") from exc
    weight = get_placement_weight(db, placement) if placement else 1.0
    db.execute("DELETE FROM decks WHERE deck_code = ? AND site = ?", (deck.code, deck.site))
    cursor = db.execute(
        """INSERT INTO decks
        (deck_code, site, game, deck_title, nation, regulation, date_added,
         tournament_date, deck_url, event, placement, total_cards, unique_cards)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (deck.code, deck.site, deck.game_title, deck.deck_title, deck.nation,
         deck.regulation, dt.datetime.now().isoformat(timespec="seconds"),
         tournament_date, deck.url, event, placement, sum(c.quantity for c in deck.cards), len(deck.cards)),
    )
    deck_id = cursor.lastrowid
    db.executemany(
        """INSERT INTO cards
        (deck_id, deck_code, card_name, card_number, quantity, site, event,
         placement, weight, image_ref)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [(deck_id, deck.code, c.name, c.card_number, c.quantity, deck.site,
          event, placement, weight, c.image_ref) for c in deck.cards],
    )
    db.commit()


# --------------------------------------------------------------------------
# Query
# --------------------------------------------------------------------------

def query_common_cards(
    db: sqlite3.Connection,
    top: int | None = None,
    weighted: bool = True,
    event: str = "",
    date_from: str = "",
    date_to: str = "",
    nation: str = "",
):
    """Aggregate cards directly in SQLite for fast report generation."""
    score = "SUM(quantity * weight)" if weighted else "SUM(quantity)"
    limit_sql = " LIMIT ?" if top else ""
    filters = ["cards.card_name <> ''", "lower(cards.card_name) <> lower('Energy Generator')"]
    params: list = []
    if event.strip():
        filters.append("lower(decks.event) LIKE lower(?)")
        params.append(f"%{event.strip()}%")
    if date_from.strip():
        filters.append("substr(decks.date_added, 1, 10) >= ?")
        params.append(date_from.strip())
    if date_to.strip():
        filters.append("substr(decks.date_added, 1, 10) <= ?")
        params.append(date_to.strip())
    if nation.strip() and nation.strip().lower() != "all nations":
        filters.append("lower(decks.nation) = lower(?)")
        params.append(nation.strip())
    if top:
        params.append(top)
    rows = db.execute(
        f"""SELECT card_name AS name,
                   COALESCE(MIN(NULLIF(card_number, '')), '') AS card_number,
                   COALESCE(MIN(NULLIF(image_ref, '')), '') AS image_ref,
                   ROUND({score}, 2) AS score,
                   SUM(quantity) AS raw_copies,
                   COUNT(DISTINCT cards.deck_code) AS deck_count
            FROM cards
            JOIN decks ON decks.id = cards.deck_id
            WHERE {' AND '.join(filters)}
            GROUP BY card_name
            ORDER BY score DESC{limit_sql}""",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def print_report(rows, weighted: bool) -> None:
    if not rows:
        print("No cards found. Add some decks first with the 'add' command.")
        return
    score_label = "Weighted Score" if weighted else "Total Copies"
    print(f"{'Card Name':40} {'Card #':12} {score_label:>14} {'Raw Copies':>11} {'# Decks':>8}")
    print("-" * 90)
    for r in rows:
        print(f"{r['name'][:40]:40} {r['card_number'][:12]:12} {r['score']:>14} {r['raw_copies']:>11} {r['deck_count']:>8}")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def prompt(text: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{text}{suffix}: ").strip()
    return val or default


def interactive_loop(wb_path: Path, debug: bool) -> None:
    db = open_database(wb_path)
    debug_dir = wb_path.parent / "debug" if debug else None

    print(f"Using database: {wb_path.resolve()}")
    print("Enter a Deck Log code to add it. Leave the code blank to stop.\n")

    while True:
        code = input("Deck code (blank to finish): ").strip()
        if not code:
            break

        site = ""
        while site not in ("EN", "JP"):
            site = input("Is this deck on the EN or JP site? [EN/JP]: ").strip().upper()

        print(f"Fetching {code} from the {site} site...")
        try:
            deck = fetch_decklist(code, site, debug_dir=debug_dir)
        except Exception as exc:
            print(f"  Failed to fetch {code}: {exc}")
            continue

        if not deck.cards:
            print("  Got 0 cards back -- the page may not have loaded fully, or the "
                  "code may be wrong. Re-run with --debug to inspect the page.")
            if input("  Save it anyway (empty deck)? [y/N]: ").strip().lower() != "y":
                continue
        else:
            print(f"  Found {len(deck.cards)} unique cards ({sum(c.quantity for c in deck.cards)} total).")

        event = prompt("  Event / tournament name (blank to skip)")
        placement = ""
        if event:
            placement = prompt("  Placement (e.g. 1st, Top4, Top8; blank = Unknown)")

        save_deck(db, deck, event, placement)
        deck_count = db.execute("SELECT COUNT(*) FROM decks").fetchone()[0]
        print(f"  Saved. Database now has {deck_count} deck(s).\n")

    if db.execute("SELECT EXISTS(SELECT 1 FROM decks)").fetchone()[0]:
        if input("Run the common-cards report now? [Y/n]: ").strip().lower() != "n":
            rows = query_common_cards(db, top=25, weighted=True)
            print()
            print_report(rows, weighted=True)

    print("\nDone.")


def cmd_add(args: argparse.Namespace) -> None:
    db_path = Path(args.file)
    db = open_database(db_path)
    debug_dir = db_path.parent / "debug" if args.debug else None

    site = args.site.upper() if args.site else ""
    while site not in ("EN", "JP"):
        site = input("Is this deck on the EN or JP site? [EN/JP]: ").strip().upper()

    print(f"Fetching {args.code} from the {site} site...")
    deck = fetch_decklist(args.code, site, debug_dir=debug_dir)
    print(f"Found {len(deck.cards)} unique cards ({sum(c.quantity for c in deck.cards)} total).")

    save_deck(db, deck, args.event or "", args.placement or "")
    print(f"Saved to {db_path.resolve()}")


def cmd_query(args: argparse.Namespace) -> None:
    db_path = Path(args.file)
    if not db_path.exists():
        print(f"No database found at {db_path}. Add some decks first.")
        return
    db = open_database(db_path)
    rows = query_common_cards(db, top=args.top, weighted=not args.unweighted)
    print_report(rows, weighted=not args.unweighted)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", default=DEFAULT_DATABASE, help=f"SQLite database path (default: {DEFAULT_DATABASE})")
    parser.add_argument("--debug", action="store_true", help="Save page HTML + screenshot on fetch for troubleshooting")
    sub = parser.add_subparsers(dest="command")

    p_add = sub.add_parser("add", help="Fetch one deck by code and add/update it in the database")
    p_add.add_argument("code")
    p_add.add_argument("--site", choices=["EN", "en", "JP", "jp"], help="Which Deck Log site the code is from")
    p_add.add_argument("--event", help="Tournament / event name")
    p_add.add_argument("--placement", help="Placement label, e.g. 1st, Top4, Top8")
    p_add.set_defaults(func=cmd_add)

    p_query = sub.add_parser("query", help="Print the common-cards report")
    p_query.add_argument("--top", type=int, default=None, help="Only show the top N cards")
    p_query.add_argument("--unweighted", action="store_true", help="Ignore placement weighting, just sum raw copies")
    p_query.set_defaults(func=cmd_query)

    args = parser.parse_args()

    if args.command is None:
        interactive_loop(Path(args.file), debug=args.debug)
        return

    args.func(args)


if __name__ == "__main__":
    main()
