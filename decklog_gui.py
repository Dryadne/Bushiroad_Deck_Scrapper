#!/usr/bin/env python3
"""Desktop interface for the Deck Log tracker."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
import webbrowser
import calendar
import datetime as dt
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from decklog_tracker import (
    DEFAULT_DATABASE,
    fetch_decklist,
    open_database,
    query_common_cards,
    save_deck,
    fetch_tournament_entries,
)


GAME_DATABASES = {
    "Cardfight Vanguard": str(Path(__file__).resolve().parent / "cardfight_vanguard.db"),
    "Weiss Schwarz": str(Path(__file__).resolve().parent / "weiss_schwarz.db"),
}


class CalendarPopup(tk.Toplevel):
    def __init__(self, parent: tk.Misc, variable: tk.StringVar) -> None:
        super().__init__(parent)
        self.variable = variable
        self.title("Choose date")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        today = dt.date.today()
        try:
            selected = dt.date.fromisoformat(variable.get())
        except ValueError:
            selected = today
        self.year = selected.year
        self.month = selected.month
        self._draw()

    def _draw(self) -> None:
        for child in self.winfo_children():
            child.destroy()
        header = ttk.Frame(self, padding=8)
        header.pack(fill="x")
        ttk.Button(header, text="<", width=3, command=lambda: self._move_month(-1)).pack(side="left")
        ttk.Label(header, text=f"{calendar.month_name[self.month]} {self.year}", anchor="center").pack(side="left", fill="x", expand=True)
        ttk.Button(header, text=">", width=3, command=lambda: self._move_month(1)).pack(side="right")
        grid = ttk.Frame(self, padding=(8, 0, 8, 8))
        grid.pack()
        for column, name in enumerate(("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")):
            ttk.Label(grid, text=name, width=4, anchor="center").grid(row=0, column=column, pady=(0, 4))
        for row_index, week in enumerate(calendar.monthcalendar(self.year, self.month), start=1):
            for column, day in enumerate(week):
                if day:
                    ttk.Button(grid, text=str(day), width=4, command=lambda day=day: self._select(day)).grid(row=row_index, column=column, padx=1, pady=1)
        ttk.Button(self, text="Clear date", command=self._clear).pack(fill="x", padx=8, pady=(0, 8))
        self.update_idletasks()
        x = self.winfo_toplevel().winfo_rootx() + 80
        y = self.winfo_toplevel().winfo_rooty() + 120
        self.geometry(f"+{x}+{y}")

    def _move_month(self, amount: int) -> None:
        self.month += amount
        if self.month == 0:
            self.month, self.year = 12, self.year - 1
        elif self.month == 13:
            self.month, self.year = 1, self.year + 1
        self._draw()

    def _select(self, day: int) -> None:
        self.variable.set(f"{self.year:04d}-{self.month:02d}-{day:02d}")
        self.destroy()

    def _clear(self) -> None:
        self.variable.set("")
        self.destroy()


BG = "#eef3f1"
INK = "#17211f"
MUTED = "#687873"
PANEL = "#ffffff"
TEAL = "#087f73"
TEAL_DARK = "#07564f"
CORAL = "#dc7057"
LINE = "#d5e0dc"


class DeckLogApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Deck Log / Tracker")
        self.geometry("1180x845")
        self.minsize(980, 650)
        self.configure(bg=BG)

        self.database_path = tk.StringVar(value=GAME_DATABASES["Cardfight Vanguard"])
        self.game = tk.StringVar(value="Cardfight Vanguard")
        self.site = tk.StringVar(value="EN")
        self.code = tk.StringVar()
        self.event = tk.StringVar()
        self.tournament_event = tk.StringVar()
        self.tournament_date = tk.StringVar()
        self.deck_tournament_date = tk.StringVar()
        self.placement = tk.StringVar()
        self.tournament_url = tk.StringVar()
        self.debug = tk.BooleanVar(value=False)
        self.weighted = tk.BooleanVar(value=True)
        self.top_count = tk.StringVar(value="25")
        self.status = tk.StringVar(value="Ready")
        self.deck_count = tk.StringVar(value="0 decks")
        self.card_count = tk.StringVar(value="0 card rows")
        self.filter_event = tk.StringVar()
        self.filter_date_from = tk.StringVar()
        self.filter_date_to = tk.StringVar()
        self.filter_nation = tk.StringVar(value="All nations")
        self.filter_archetype = tk.StringVar(value="All archetypes")
        self.nation_box: ttk.Combobox | None = None
        self.archetype_box: ttk.Combobox | None = None
        self.jobs: queue.Queue = queue.Queue()

        self._setup_styles()
        self._build_header()
        self._build_body()
        self.after_idle(self._fit_window_to_screen)
        self.after(100, self._drain_jobs)
        self.refresh_views()

    def _fit_window_to_screen(self) -> None:
        """Use a centered comfortable size, or maximize when the screen is tight."""
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        preferred_width = 1500
        preferred_height = 845

        if screen_width < preferred_width + 80 or screen_height < preferred_height + 100:
            self.state("zoomed")
            return

        left = (screen_width - preferred_width) // 2
        top = (screen_height - preferred_height) // 2
        self.geometry(f"{preferred_width}x{preferred_height}+{left}+{top}")

    def _setup_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL, relief="flat", borderwidth=1)
        style.configure("TLabel", background=BG, foreground=INK, font=("Segoe UI", 9))
        style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 8))
        style.configure("Title.TLabel", background=BG, foreground=INK, font=("Georgia", 22, "bold"))
        style.configure("Section.TLabel", background=PANEL, foreground=INK, font=("Segoe UI", 11, "bold"))
        style.configure("Metric.TLabel", background=PANEL, foreground=TEAL, font=("Segoe UI", 14, "bold"))
        style.configure("MetricCaption.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 8))
        style.configure("TEntry", fieldbackground="#ffffff", foreground=INK, padding=6, borderwidth=1, relief="solid")
        style.configure("TCombobox", fieldbackground="#ffffff", foreground=INK, padding=4)
        style.configure("TButton", background="#e2eeea", foreground=TEAL_DARK, padding=(11, 7), font=("Segoe UI", 9, "bold"), relief="flat", borderwidth=0)
        style.map("TButton", background=[("active", "#cfe5df")])
        style.configure("Accent.TButton", background=TEAL, foreground="white", padding=(14, 8), font=("Segoe UI", 9, "bold"), relief="flat", borderwidth=0)
        style.map("Accent.TButton", background=[("active", TEAL_DARK)])
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=INK, rowheight=28, borderwidth=0, font=("Segoe UI", 8))
        style.configure("Treeview.Heading", background="#dcebe6", foreground=TEAL_DARK, relief="flat", padding=(6, 6), font=("Segoe UI", 8, "bold"))
        style.map("Treeview", background=[("selected", "#cde6df")], foreground=[("selected", INK)])
        style.configure("Horizontal.TProgressbar", troughcolor="#e4e0d6", background=TEAL, borderwidth=0, lightcolor=TEAL, darkcolor=TEAL)

    def _build_header(self) -> None:
        header = ttk.Frame(self, padding=(24, 16, 24, 10))
        header.pack(fill="x")
        ttk.Label(header, text="DECK LOG", style="Muted.TLabel").pack(anchor="w")
        ttk.Label(header, text="Track the field. Find the staples.", style="Title.TLabel").pack(anchor="w", pady=(1, 4))
        ttk.Label(header, text="Import Bushiroad decklists, then compare card frequency across your collection.", style="Muted.TLabel").pack(anchor="w")

        database_bar = ttk.Frame(header)
        database_bar.pack(fill="x", pady=(12, 0))
        ttk.Label(database_bar, text="GAME", style="Muted.TLabel").pack(side="left", padx=(0, 8))
        game_box = ttk.Combobox(database_bar, textvariable=self.game, values=tuple(GAME_DATABASES), state="readonly", width=22)
        game_box.pack(side="left")
        game_box.bind("<<ComboboxSelected>>", lambda _event: self.select_game_database())
        ttk.Label(database_bar, text="DATABASE", style="Muted.TLabel").pack(side="left", padx=(20, 8))
        ttk.Entry(database_bar, textvariable=self.database_path).pack(side="left", fill="x", expand=True)
        ttk.Button(database_bar, text="Browse", command=self.choose_database).pack(side="left", padx=(8, 0))
        ttk.Button(database_bar, text="Open", command=self.refresh_views).pack(side="left", padx=(8, 0))

    def _build_body(self) -> None:
        body = ttk.Frame(self, padding=(24, 0, 24, 16))
        body.pack(fill="both", expand=True)

        body_pane = ttk.Panedwindow(body, orient="horizontal")
        body_pane.pack(fill="both", expand=True)

        import_panel = ttk.Frame(body_pane, style="Panel.TFrame", padding=14, width=340)
        import_panel.pack_propagate(False)
        body_pane.add(import_panel, weight=0)
        import_tabs = ttk.Notebook(import_panel, width=340)
        import_tabs.pack(fill="x")
        deck_page = ttk.Frame(import_tabs, style="Panel.TFrame", padding=(2, 8, 2, 0))
        tournament_page = ttk.Frame(import_tabs, style="Panel.TFrame", padding=(2, 8, 2, 0))
        import_tabs.add(deck_page, text=" Deck ")
        import_tabs.add(tournament_page, text=" Tournament ")

        ttk.Label(deck_page, text="Import a deck", style="Section.TLabel").pack(anchor="w")
        ttk.Label(deck_page, text="Paste a Deck Log code to fetch or update it.", style="Muted.TLabel", wraplength=315).pack(anchor="w", pady=(2, 12))
        self._field(deck_page, "DECK CODE", self.code, "e.g. 53V7L")
        ttk.Label(deck_page, text="SITE", style="Muted.TLabel").pack(anchor="w", pady=(14, 5))
        site_row = ttk.Frame(deck_page, style="Panel.TFrame")
        site_row.pack(fill="x")
        ttk.Radiobutton(site_row, text="English", value="EN", variable=self.site).pack(side="left")
        ttk.Radiobutton(site_row, text="Japanese", value="JP", variable=self.site).pack(side="left", padx=(16, 0))
        self._field(deck_page, "EVENT / TOURNAMENT", self.event, "optional")
        self._date_field(deck_page, "TOURNAMENT DATE", self.deck_tournament_date, "optional")
        self._field(deck_page, "PLACEMENT", self.placement, "e.g. Top8")
        ttk.Checkbutton(deck_page, text="Save debug HTML + screenshot", variable=self.debug).pack(anchor="w", pady=(16, 0))
        self.import_button = ttk.Button(deck_page, text="Fetch and save deck", style="Accent.TButton", command=self.import_deck)
        self.import_button.pack(fill="x", pady=(20, 0))
        ttk.Label(deck_page, text="Re-importing the same code updates it in place.", style="Muted.TLabel", wraplength=305).pack(anchor="w", pady=(10, 0))

        ttk.Label(tournament_page, text="Import a tournament", style="Section.TLabel").pack(anchor="w")
        ttk.Label(tournament_page, text="Load every ranked Deck Log link from a results page.", style="Muted.TLabel", wraplength=315).pack(anchor="w", pady=(2, 12))
        self._field(tournament_page, "TOURNAMENT PAGE URL", self.tournament_url, "paste the results page URL")
        self._field(tournament_page, "EVENT / TOURNAMENT", self.tournament_event, "applied to every imported deck")
        self._date_field(tournament_page, "TOURNAMENT DATE", self.tournament_date, "optional")
        self.tournament_button = ttk.Button(tournament_page, text="Import entire tournament", style="Accent.TButton", command=self.import_tournament)
        self.tournament_button.pack(fill="x", pady=(18, 0))
        ttk.Label(tournament_page, text="The page rank is saved as the placement for each imported deck.", style="Muted.TLabel", wraplength=315).pack(anchor="w", pady=(10, 0))

        right = ttk.Frame(body_pane)
        body_pane.add(right, weight=1)
        metrics = ttk.Frame(right, style="Panel.TFrame", padding=(18, 13))
        metrics.pack(fill="x", pady=(0, 12))
        self._metric(metrics, "DECKS", self.deck_count).pack(side="left", padx=(0, 45))
        self._metric(metrics, "CARD ROWS", self.card_count).pack(side="left")

        filters = ttk.Frame(right, style="Panel.TFrame", padding=(10, 7))
        filters.pack(fill="x", pady=(0, 12))
        filter_row = ttk.Frame(filters, style="Panel.TFrame")
        filter_row.pack(fill="x", pady=(0, 6))
        ttk.Label(filter_row, text="FILTER", style="Muted.TLabel").pack(side="left", padx=(0, 8))
        ttk.Entry(filter_row, textvariable=self.filter_event).pack(side="left", fill="x", expand=True)
        ttk.Label(filter_row, text="Tournament", style="Muted.TLabel").pack(side="left", padx=(10, 8))
        self._date_filter(filter_row, self.filter_date_from)
        ttk.Label(filter_row, text="to", style="Muted.TLabel").pack(side="left", padx=5)
        self._date_filter(filter_row, self.filter_date_to)

        filter_row_two = ttk.Frame(filters, style="Panel.TFrame")
        filter_row_two.pack(fill="x")
        ttk.Label(filter_row_two, text="NATION", style="Muted.TLabel").pack(side="left", padx=(0, 5))
        self.nation_box = ttk.Combobox(filter_row_two, textvariable=self.filter_nation, values=("All nations",), state="readonly", width=16)
        self.nation_box.pack(side="left")
        ttk.Label(filter_row_two, text="ARCHETYPE", style="Muted.TLabel").pack(side="left", padx=(12, 5))
        self.archetype_box = ttk.Combobox(filter_row_two, textvariable=self.filter_archetype, values=("All archetypes",), state="readonly", width=24)
        self.archetype_box.pack(side="left", fill="x", expand=True)
        ttk.Button(filter_row_two, text="Apply", command=self.refresh_views).pack(side="left", padx=(8, 0))
        ttk.Button(filter_row_two, text="Clear", command=self.clear_filters).pack(side="left", padx=(6, 0))

        tabs = ttk.Notebook(right)
        tabs.pack(fill="both", expand=True)
        decks_tab = ttk.Frame(tabs, style="Panel.TFrame", padding=14)
        report_tab = ttk.Frame(tabs, style="Panel.TFrame", padding=14)
        tabs.add(decks_tab, text="  Imported decks  ")
        tabs.add(report_tab, text="  Card report  ")
        self._build_decks_tab(decks_tab)
        self._build_report_tab(report_tab)

        footer = ttk.Frame(self, padding=(24, 0, 24, 10))
        footer.pack(fill="x")
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=220)
        self.progress.pack(side="left")
        ttk.Label(footer, textvariable=self.status, style="Muted.TLabel").pack(side="left", padx=(10, 0))

    def _build_decks_tab(self, parent: ttk.Frame) -> None:
        columns = ("code", "site", "game", "title", "archetype", "nation", "event", "tournament_date", "placement", "cards")
        self.decks_tree = ttk.Treeview(parent, columns=columns, show="headings")
        self.decks_tree.bind("<Double-1>", self.open_selected_deck)
        headings = {"code": "Code", "site": "Site", "game": "Game", "title": "Deck title", "archetype": "Archetype", "nation": "Nation", "event": "Event", "tournament_date": "Tournament date", "placement": "Place", "cards": "Cards"}
        widths = {"code": 85, "site": 45, "game": 125, "title": 155, "archetype": 190, "nation": 105, "event": 145, "tournament_date": 110, "placement": 75, "cards": 55}
        for column in columns:
            self.decks_tree.heading(column, text=headings[column])
            self.decks_tree.column(column, width=widths[column], anchor="w" if column == "code" else "center")
        self.decks_tree.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=self.decks_tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.decks_tree.configure(yscrollcommand=scrollbar.set)

    def _build_report_tab(self, parent: ttk.Frame) -> None:
        toolbar = ttk.Frame(parent, style="Panel.TFrame")
        toolbar.pack(fill="x", pady=(0, 12))
        ttk.Checkbutton(toolbar, text="Weight by placement", variable=self.weighted, command=self.refresh_report).pack(side="left")
        ttk.Label(toolbar, text="Show top", style="Muted.TLabel").pack(side="left", padx=(20, 6))
        top_box = ttk.Combobox(toolbar, textvariable=self.top_count, values=("10", "25", "50", "100", "All"), width=7, state="readonly")
        top_box.pack(side="left")
        top_box.bind("<<ComboboxSelected>>", lambda _event: self.refresh_report())
        ttk.Button(toolbar, text="Refresh report", command=self.refresh_report).pack(side="right")

        columns = ("rank", "name", "number", "score", "copies", "decks")
        self.report_tree = ttk.Treeview(parent, columns=columns, show="headings")
        self.report_tree.bind("<Double-1>", self.open_selected_card)
        headings = {"rank": "#", "name": "Card name", "number": "Card #", "score": "Weighted score", "copies": "Raw copies", "decks": "Decks"}
        widths = {"rank": 40, "name": 260, "number": 110, "score": 115, "copies": 95, "decks": 70}
        for column in columns:
            self.report_tree.heading(column, text=headings[column])
            self.report_tree.column(column, width=widths[column], anchor="w" if column in ("name", "number") else "center")
        self.report_tree.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=self.report_tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.report_tree.configure(yscrollcommand=scrollbar.set)

    def _field(self, parent: ttk.Frame, label: str, variable: tk.StringVar, placeholder: str) -> None:
        ttk.Label(parent, text=label, style="Muted.TLabel").pack(anchor="w", pady=(7, 3))
        entry = ttk.Entry(parent, textvariable=variable)
        entry.pack(fill="x")
        entry.insert(0, "")
        entry.bind("<Return>", lambda _event: self.import_deck())
        ttk.Label(parent, text=placeholder, style="Muted.TLabel").pack(anchor="w", pady=(2, 0))

    def _date_field(self, parent: ttk.Frame, label: str, variable: tk.StringVar, placeholder: str) -> None:
        ttk.Label(parent, text=label, style="Muted.TLabel").pack(anchor="w", pady=(7, 3))
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill="x")
        ttk.Entry(row, textvariable=variable, state="readonly", width=15).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Calendar", command=lambda: CalendarPopup(self, variable)).pack(side="left", padx=(5, 0))
        ttk.Label(parent, text=placeholder, style="Muted.TLabel").pack(anchor="w", pady=(2, 0))

    def _date_filter(self, parent: ttk.Frame, variable: tk.StringVar) -> None:
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(side="left")
        ttk.Entry(row, textvariable=variable, state="readonly", width=10).pack(side="left")
        ttk.Button(row, text="...", width=3, command=lambda: CalendarPopup(self, variable)).pack(side="left", padx=(2, 0))

    def _metric(self, parent: ttk.Frame, caption: str, variable: tk.StringVar) -> ttk.Frame:
        frame = ttk.Frame(parent, style="Panel.TFrame")
        ttk.Label(frame, textvariable=variable, style="Metric.TLabel").pack(anchor="w")
        ttk.Label(frame, text=caption, style="MetricCaption.TLabel").pack(anchor="w")
        return frame

    def choose_database(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Choose SQLite database",
            defaultextension=".db",
            filetypes=(("SQLite database", "*.db"), ("All files", "*.*")),
            initialfile=Path(self.database_path.get()).name,
        )
        if path:
            self.database_path.set(path)
            self.refresh_views()

    def select_game_database(self) -> None:
        self.database_path.set(GAME_DATABASES[self.game.get()])
        self.refresh_views()

    def clear_filters(self) -> None:
        self.filter_event.set("")
        self.filter_date_from.set("")
        self.filter_date_to.set("")
        self.filter_archetype.set("All archetypes")
        self.refresh_views()

    def import_deck(self) -> None:
        code = self.code.get().strip()
        if not code:
            messagebox.showwarning("Deck code needed", "Enter a Deck Log code before fetching.")
            return
        self.import_button.configure(state="disabled")
        self.progress.start(12)
        self.status.set(f"Fetching {code} from the {self.site.get()} site...")
        args = (code, self.site.get(), self.event.get().strip(), self.deck_tournament_date.get().strip(), self.placement.get().strip(), self.debug.get(), self.database_path.get().strip())
        threading.Thread(target=self._import_worker, args=args, daemon=True).start()

    def import_tournament(self) -> None:
        url = self.tournament_url.get().strip()
        if not url:
            messagebox.showwarning("Tournament URL needed", "Paste a tournament results page URL before importing.")
            return
        self.import_button.configure(state="disabled")
        self.tournament_button.configure(state="disabled")
        self.progress.configure(mode="indeterminate", maximum=1, value=0)
        self.progress.start(12)
        self.status.set("Reading tournament results page...")
        args = (url, self.tournament_event.get().strip(), self.tournament_date.get().strip(), self.debug.get(), self.database_path.get().strip())
        threading.Thread(target=self._import_tournament_worker, args=args, daemon=True).start()

    def _import_worker(self, code: str, site: str, event: str, tournament_date: str, placement: str, debug: bool, database_name: str) -> None:
        try:
            fallback_path = Path(database_name or DEFAULT_DATABASE)
            debug_dir = fallback_path.parent / "debug" if debug else None
            deck = fetch_decklist(code, site, debug_dir=debug_dir)
            path = self._database_path_for_game(deck.game_title, fallback_path)
            db = open_database(path)
            debug_dir = path.parent / "debug" if debug else None
            save_deck(db, deck, event, placement, tournament_date)
            self.jobs.put(("success", f"Import complete. Saved {code} to {path.name}: {len(deck.cards)} unique cards, {sum(c.quantity for c in deck.cards)} total.", str(path)))
        except Exception as exc:
            self.jobs.put(("error", str(exc)))

    def _import_tournament_worker(self, url: str, event: str, tournament_date: str, debug: bool, database_name: str) -> None:
        try:
            entries = fetch_tournament_entries(url)
            if not entries:
                raise RuntimeError("No Deck Log links were found on that tournament page.")
            self.jobs.put(("progress_start", len(entries)))
            fallback_path = Path(database_name or DEFAULT_DATABASE)
            imported = 0
            failures: list[str] = []
            for entry in entries:
                try:
                    self.jobs.put(("progress", imported, len(entries), entry.deck_code))
                    deck = fetch_decklist(entry.deck_code, entry.site)
                    path = self._database_path_for_game(deck.game_title, fallback_path)
                    db = open_database(path)
                    save_deck(db, deck, event, entry.rank, tournament_date)
                    imported += 1
                except Exception as exc:
                    failures.append(f"{entry.rank} / {entry.deck_code}: {exc}")
                self.jobs.put(("progress", imported + len(failures), len(entries), entry.deck_code))
            result = f"Tournament import complete: {imported} of {len(entries)} decks imported."
            if failures:
                result += " Failed: " + "; ".join(failures[:3])
            self.jobs.put(("success", result))
        except Exception as exc:
            self.jobs.put(("error", str(exc)))

    def _database_path_for_game(self, game_title: str, fallback: Path) -> Path:
        for game_name, database_name in GAME_DATABASES.items():
            if game_title.strip().lower() == game_name.lower():
                return Path(database_name)
        return fallback

    def _drain_jobs(self) -> None:
        try:
            while True:
                message = self.jobs.get_nowait()
                kind = message[0]
                if kind == "progress_start":
                    self.progress.stop()
                    self.progress.configure(mode="determinate", maximum=message[1], value=0)
                    self.status.set(f"Found {message[1]} decks. Starting import...")
                    continue
                if kind == "progress":
                    completed, total, code = message[1:]
                    self.progress.configure(value=completed)
                    self.status.set(f"Importing deck {completed} of {total}: {code}")
                    continue

                text = message[1]
                if kind == "success" and len(message) > 2:
                    self.database_path.set(message[2])
                    for game_name, database_name in GAME_DATABASES.items():
                        if Path(database_name).resolve() == Path(message[2]).resolve():
                            self.game.set(game_name)
                            break
                self.progress.stop()
                self.progress.configure(mode="determinate", maximum=1, value=1)
                self.import_button.configure(state="normal")
                self.tournament_button.configure(state="normal")
                if kind == "success":
                    self.status.set(text)
                    self.refresh_views()
                else:
                    self.status.set("Import failed")
                    messagebox.showerror("Could not import deck", text)
        except queue.Empty:
            pass
        self.after(100, self._drain_jobs)

    def refresh_views(self) -> None:
        path = Path(self.database_path.get().strip() or DEFAULT_DATABASE)
        self.database_path.set(str(path))
        try:
            db = open_database(path)
            filter_sql, filter_params = self._deck_filters()
            deck_count = db.execute(f"SELECT COUNT(*) FROM decks {filter_sql}", filter_params).fetchone()[0]
            card_count = db.execute(
                f"SELECT COUNT(*) FROM cards JOIN decks ON decks.id = cards.deck_id {filter_sql}",
                filter_params,
            ).fetchone()[0]
            self.deck_count.set(f"{deck_count} decks")
            self.card_count.set(f"{card_count} card rows")
            for item in self.decks_tree.get_children():
                self.decks_tree.delete(item)
            nations = [row[0] for row in db.execute("SELECT DISTINCT nation FROM decks WHERE nation <> '' ORDER BY nation").fetchall()]
            if self.nation_box is not None:
                self.nation_box.configure(values=("All nations", *nations))
                if self.filter_nation.get() not in ("All nations", *nations):
                    self.filter_nation.set("All nations")
            archetypes = [row[0] for row in db.execute("SELECT DISTINCT archetype FROM decks WHERE archetype <> '' ORDER BY archetype").fetchall()]
            if self.archetype_box is not None:
                self.archetype_box.configure(values=("All archetypes", *archetypes))
                if self.filter_archetype.get() not in ("All archetypes", *archetypes):
                    self.filter_archetype.set("All archetypes")
            rows = db.execute(
                f"""SELECT deck_code, site, game, deck_title, archetype, nation, event, tournament_date, placement, total_cards
                   FROM decks {filter_sql} ORDER BY id DESC""",
                filter_params,
            ).fetchall()
            for row in rows:
                code = row["deck_code"]
                site = row["site"]
                self.decks_tree.insert("", "end", iid=f"{site}:{code}", values=(code, site, row["game"], row["deck_title"], row["archetype"], row["nation"], row["event"], row["tournament_date"], row["placement"], row["total_cards"]))
            self.refresh_report()
            self.status.set(f"Opened {path.name}")
        except Exception as exc:
            self.status.set("Database unavailable")
            messagebox.showerror("Could not open database", str(exc))

    def refresh_report(self) -> None:
        path = Path(self.database_path.get().strip() or DEFAULT_DATABASE)
        for item in self.report_tree.get_children():
            self.report_tree.delete(item)
        if not path.exists():
            return
        try:
            db = open_database(path)
            top_value = self.top_count.get().strip()
            top = None if top_value.lower() == "all" else int(top_value or "25")
            rows = query_common_cards(
                db,
                top=top,
                weighted=self.weighted.get(),
                event=self.filter_event.get(),
                date_from=self.filter_date_from.get(),
                date_to=self.filter_date_to.get(),
                nation=self.filter_nation.get(),
                archetype=self.filter_archetype.get(),
            )
            for rank, row in enumerate(rows, start=1):
                self.report_tree.insert("", "end", values=(rank, row["name"], row["card_number"], row["score"], row["raw_copies"], row["deck_count"]), tags=(row["image_ref"],))
        except (ValueError, KeyError, OSError):
            return

    def _deck_filters(self) -> tuple[str, list[str]]:
        filters: list[str] = []
        params: list[str] = []
        if self.filter_event.get().strip():
            filters.append("WHERE lower(decks.event) LIKE lower(?)")
            params.append(f"%{self.filter_event.get().strip()}%")
        if self.filter_nation.get().strip() and self.filter_nation.get() != "All nations":
            filters.append("AND lower(decks.nation) = lower(?)" if filters else "WHERE lower(decks.nation) = lower(?)")
            params.append(self.filter_nation.get().strip())
        if self.filter_archetype.get().strip() and self.filter_archetype.get() != "All archetypes":
            filters.append("AND lower(decks.archetype) = lower(?)" if filters else "WHERE lower(decks.archetype) = lower(?)")
            params.append(self.filter_archetype.get().strip())
        if self.filter_date_from.get().strip():
            filters.append("AND substr(decks.date_added, 1, 10) >= ?" if filters else "WHERE substr(decks.date_added, 1, 10) >= ?")
            params.append(self.filter_date_from.get().strip())
        if self.filter_date_to.get().strip():
            filters.append("AND substr(decks.date_added, 1, 10) <= ?" if filters else "WHERE substr(decks.date_added, 1, 10) <= ?")
            params.append(self.filter_date_to.get().strip())
        return " ".join(filters), params

    def open_selected_deck(self, _event) -> None:
        selection = self.decks_tree.selection()
        if not selection:
            return
        values = self.decks_tree.item(selection[0], "values")
        if len(values) < 2:
            return
        code, site = values[0], values[1]
        host = "decklog-en.bushiroad.com" if site == "EN" else "decklog.bushiroad.com"
        webbrowser.open(f"https://{host}/view/{code}")

    def open_selected_card(self, _event) -> None:
        selection = self.report_tree.selection()
        if not selection:
            return
        tags = self.report_tree.item(selection[0], "tags")
        if tags and tags[0]:
            webbrowser.open(tags[0])


if __name__ == "__main__":
    app = DeckLogApp()
    app.mainloop()
