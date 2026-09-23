#!/usr/bin/env python3
"""
decklog_tracker.py

Pulls decklists from Bushiroad's "Deck Log" tool by deck code, from either
the English site (decklog-en.bushiroad.com) or the Japanese site
(decklog.bushiroad.com), and stores them in an Excel workbook so they can
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

Re-scrape a deck already in the workbook (updates it in place):
    python decklog_tracker.py add 53V7L --site EN

Run the card-frequency report:
    python decklog_tracker.py query
    python decklog_tracker.py query --top 25 --unweighted

All commands accept --file to point at a different workbook
(default: decklog_data.xlsx in the current directory).

IF SCRAPING BREAKS
-------------------
Deck Log's markup could change, or the JP site's DOM could differ from
what the EN site's export tools assume. If `add` comes back with zero
cards, run with --debug: it saves a screenshot and the full page HTML
next to the workbook so you (or I) can see what changed and fix the
selectors in `fetch_decklist()` below.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
except ImportError:
    print("Missing dependency. Run: pip install -r requirements.txt", file=sys.stderr)
    raise

try:
    from openpyxl import Workbook, load_workbook
    from openpyxl.worksheet.worksheet import Worksheet
    from openpyxl.utils import get_column_letter
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

DEFAULT_WORKBOOK = "decklog_data.xlsx"

DECKS_SHEET = "Decks"
CARDS_SHEET = "Cards"
WEIGHTS_SHEET = "PlacementWeights"

DECKS_HEADERS = [
    "Deck Code", "Site", "Game", "Deck Title", "Nation", "Regulation", "Date Added", "Deck URL",
    "Event / Tournament", "Placement", "Total Cards", "Unique Cards",
]
CARDS_HEADERS = [
    "Deck Code", "Card Name", "Card Number", "Quantity",
    "Site", "Event / Tournament", "Placement", "Weight", "Image Ref",
]
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
# Excel storage
# --------------------------------------------------------------------------

def open_or_create_workbook(path: Path) -> Workbook:
    if path.exists():
        wb = load_workbook(path)
        decks_ws = wb[DECKS_SHEET]
        existing_headers = [cell.value for cell in decks_ws[1]]
        for header in DECKS_HEADERS:
            if header not in existing_headers:
                decks_ws.cell(row=1, column=decks_ws.max_column + 1, value=header)
        header_to_column = {cell.value: cell.column for cell in decks_ws[1]}
        title_column = header_to_column.get("Deck Title")
        if title_column:
            for row in range(2, decks_ws.max_row + 1):
                cell = decks_ws.cell(row=row, column=title_column)
                if cell.value:
                    cell.value = normalize_deck_title(str(cell.value))
        _autosize(decks_ws)
        return wb

    wb = Workbook()
    default_ws = wb.active
    wb.remove(default_ws)

    decks_ws = wb.create_sheet(DECKS_SHEET)
    decks_ws.append(DECKS_HEADERS)

    cards_ws = wb.create_sheet(CARDS_SHEET)
    cards_ws.append(CARDS_HEADERS)

    weights_ws = wb.create_sheet(WEIGHTS_SHEET)
    weights_ws.append(["Placement Label", "Weight"])
    for label, weight in DEFAULT_WEIGHTS:
        weights_ws.append([label, weight])

    _autosize(decks_ws)
    _autosize(cards_ws)
    _autosize(weights_ws)
    return wb


def _autosize(ws: Worksheet, max_width: int = 40) -> None:
    for i, col_cells in enumerate(ws.columns, start=1):
        length = max((len(str(c.value)) for c in col_cells if c.value is not None), default=8)
        ws.column_dimensions[get_column_letter(i)].width = min(length + 2, max_width)


def get_placement_weight(wb: Workbook, placement: str) -> float:
    ws = wb[WEIGHTS_SHEET]
    placement_norm = (placement or "Unknown").strip().lower()
    for row in ws.iter_rows(min_row=2, values_only=True):
        label, weight = row[0], row[1]
        if label and str(label).strip().lower() == placement_norm:
            return float(weight)
    # Unknown placement label: add it with weight 1 so the user can edit
    # it later in the PlacementWeights sheet, and use 1 for now.
    ws.append([placement or "Unknown", 1])
    return 1.0


def remove_existing_deck(wb: Workbook, code: str, site: str) -> None:
    """If this deck code (for this site) was already imported, strip its
    old rows out of both sheets before re-inserting, so re-running `add`
    on the same code updates it instead of duplicating it."""
    for sheet_name, code_col in ((DECKS_SHEET, 0), (CARDS_SHEET, 0)):
        ws = wb[sheet_name]
        site_col = 1 if sheet_name == DECKS_SHEET else 4
        rows_to_delete = [
            row[0].row for row in ws.iter_rows(min_row=2)
            if row[code_col].value == code and row[site_col].value == site
        ]
        for row_idx in reversed(rows_to_delete):
            ws.delete_rows(row_idx)


def save_deck(wb: Workbook, deck: DeckResult, event: str, placement: str) -> None:
    remove_existing_deck(wb, deck.code, deck.site)
    weight = get_placement_weight(wb, placement) if placement else 1.0

    decks_ws = wb[DECKS_SHEET]
    deck_values = {
        "Deck Code": deck.code,
        "Site": deck.site,
        "Game": deck.game_title,
        "Deck Title": deck.deck_title,
        "Nation": deck.nation,
        "Regulation": deck.regulation,
        "Date Added": dt.datetime.now().isoformat(timespec="seconds"),
        "Deck URL": deck.url,
        "Event / Tournament": event or "",
        "Placement": placement or "",
        "Total Cards": sum(c.quantity for c in deck.cards),
        "Unique Cards": len(deck.cards),
    }
    header_to_column = {cell.value: cell.column for cell in decks_ws[1]}
    row = decks_ws.max_row + 1
    for header, value in deck_values.items():
        decks_ws.cell(row=row, column=header_to_column[header], value=value)

    cards_ws = wb[CARDS_SHEET]
    for c in deck.cards:
        cards_ws.append([
            deck.code, c.name, c.card_number, c.quantity,
            deck.site, event or "", placement or "", weight, c.image_ref,
        ])


# --------------------------------------------------------------------------
# Query
# --------------------------------------------------------------------------

def query_common_cards(wb: Workbook, top: int | None = None, weighted: bool = True):
    """Aggregate the Cards sheet: for each distinct card name, how many
    copies appear across all decks (optionally weighted by each deck's
    placement weight), and in how many distinct decks it shows up."""
    ws = wb[CARDS_SHEET]
    totals: dict[str, dict] = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        deck_code, name, card_number, qty, site, event, placement, weight, image_ref = row
        if not name:
            continue
        bucket = totals.setdefault(name, {"card_number": card_number or "", "score": 0.0, "raw_copies": 0, "decks": set()})
        w = weight if (weighted and weight is not None) else 1
        bucket["score"] += (qty or 0) * w
        bucket["raw_copies"] += qty or 0
        bucket["decks"].add(deck_code)
        if card_number and not bucket["card_number"]:
            bucket["card_number"] = card_number

    rows = [
        {
            "name": name,
            "card_number": b["card_number"],
            "score": round(b["score"], 2),
            "raw_copies": b["raw_copies"],
            "deck_count": len(b["decks"]),
        }
        for name, b in totals.items()
    ]
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows[:top] if top else rows


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
    wb = open_or_create_workbook(wb_path)
    debug_dir = wb_path.parent / "debug" if debug else None

    print(f"Using workbook: {wb_path.resolve()}")
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

        save_deck(wb, deck, event, placement)
        wb.save(wb_path)
        print(f"  Saved. Workbook now has {wb[DECKS_SHEET].max_row - 1} deck(s).\n")

    if wb[DECKS_SHEET].max_row > 1:
        if input("Run the common-cards report now? [Y/n]: ").strip().lower() != "n":
            rows = query_common_cards(wb, top=25, weighted=True)
            print()
            print_report(rows, weighted=True)

    print("\nDone.")


def cmd_add(args: argparse.Namespace) -> None:
    wb_path = Path(args.file)
    wb = open_or_create_workbook(wb_path)
    debug_dir = wb_path.parent / "debug" if args.debug else None

    site = args.site.upper() if args.site else ""
    while site not in ("EN", "JP"):
        site = input("Is this deck on the EN or JP site? [EN/JP]: ").strip().upper()

    print(f"Fetching {args.code} from the {site} site...")
    deck = fetch_decklist(args.code, site, debug_dir=debug_dir)
    print(f"Found {len(deck.cards)} unique cards ({sum(c.quantity for c in deck.cards)} total).")

    save_deck(wb, deck, args.event or "", args.placement or "")
    wb.save(wb_path)
    print(f"Saved to {wb_path.resolve()}")


def cmd_query(args: argparse.Namespace) -> None:
    wb_path = Path(args.file)
    if not wb_path.exists():
        print(f"No workbook found at {wb_path}. Add some decks first.")
        return
    wb = load_workbook(wb_path)
    rows = query_common_cards(wb, top=args.top, weighted=not args.unweighted)
    print_report(rows, weighted=not args.unweighted)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", default=DEFAULT_WORKBOOK, help=f"Excel workbook path (default: {DEFAULT_WORKBOOK})")
    parser.add_argument("--debug", action="store_true", help="Save page HTML + screenshot on fetch for troubleshooting")
    sub = parser.add_subparsers(dest="command")

    p_add = sub.add_parser("add", help="Fetch one deck by code and add/update it in the workbook")
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
