#!/usr/bin/env python3
import sys
import time
import json
import select
import tty
import termios
from datetime import datetime, timedelta, date
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.live import Live
from rich.text import Text
from rich.table import Table
from rich.align import Align
from rich import box

# --- Configuration ---
BASE_DIR = Path("/home/yassir/focusTimer")
DATA_FILE = BASE_DIR / "data.json"
TAGS_FILE = BASE_DIR / "tags.txt"

# Ensure the directory exists immediately
BASE_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_POMODORO_MIN = 25
CONSOLE = Console()

# --- Input Handling ---
class KeyInput:
    def __init__(self):
        self.fd = sys.stdin.fileno()
        self.old_settings = termios.tcgetattr(self.fd)

    def __enter__(self):
        tty.setcbreak(self.fd)
        return self

    def __exit__(self, type, value, traceback):
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old_settings)

    def get_key(self):
        if select.select([sys.stdin], [], [], 0)[0]:
            return sys.stdin.read(1).lower()
        return None

# --- Data Management ---
class DataManager:
    def __init__(self):
        self.ensure_tags_file()

    def ensure_tags_file(self):
        if not TAGS_FILE.exists():
            defaults = ["coding", "reading", "meeting", "writing", "german"]
            with open(TAGS_FILE, "w") as f:
                f.write("\n".join(defaults))

    def load_known_tags(self):
        if not TAGS_FILE.exists(): return []
        with open(TAGS_FILE, "r") as f:
            return sorted([line.strip() for line in f if line.strip()])

    def add_new_tag(self, new_tag):
        with open(TAGS_FILE, "a") as f:
            f.write(f"\n{new_tag}")

    def save_session(self, mode, tag, duration_sec, start_time):
        data = []
        if DATA_FILE.exists():
            try:
                with open(DATA_FILE, 'r') as f:
                    data = json.load(f)
            except: pass

        entry = {
            "timestamp": start_time.isoformat(),
            "mode": mode,
            "tag": tag, 
            "duration_seconds": round(duration_sec, 2)
        }
        data.append(entry)
        with open(DATA_FILE, 'w') as f:
            json.dump(data, f, indent=2)

    def get_dashboard_stats(self):
        if not DATA_FILE.exists(): return {}, {}, {}
        with open(DATA_FILE, 'r') as f:
            data = json.load(f)

        now = datetime.now()
        today_date = now.date()
        week_start = (now - timedelta(days=now.weekday())).date()
        month_start = now.replace(day=1).date()

        stats_today = {}
        stats_week = {}
        stats_month = {}

        for entry in data:
            try:
                ts = datetime.fromisoformat(entry["timestamp"])
                dur = entry.get("duration_seconds", 0)
                tag_raw = entry.get("tag") or entry.get("tags")
                tag = tag_raw if isinstance(tag_raw, str) else (tag_raw[0] if tag_raw else "unspecified")
                
                entry_date = ts.date()

                if entry_date == today_date:
                    stats_today[tag] = stats_today.get(tag, 0) + dur
                
                if entry_date >= week_start:
                    stats_week[tag] = stats_week.get(tag, 0) + dur

                if entry_date >= month_start:
                    stats_month[tag] = stats_month.get(tag, 0) + dur

            except Exception:
                continue

        return stats_today, stats_week, stats_month

# --- UI & Logic ---
class FocusApp:
    def __init__(self):
        self.db = DataManager()
        self.running = True

    def clear_screen(self):
        CONSOLE.clear()

    def get_input(self, prompt, default=None):
        text = CONSOLE.input(f"[bold cyan]{prompt}[/bold cyan] ")
        return text.strip() if text.strip() else default

    def format_time(self, seconds):
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        return f"{h}h {m}m" if h > 0 else f"{m}m {s}s"

    def select_single_tag(self):
        while True:
            known = self.db.load_known_tags()
            
            CONSOLE.print("\n[bold underline]Select Tag:[/bold underline]")
            
            grid = Table.grid(padding=(0, 4))
            grid.add_column()
            grid.add_column()
            half = (len(known) + 1) // 2
            for i in range(half):
                col1 = f"[bold green]{i+1}.[/bold green] {known[i]}"
                col2 = ""
                if i + half < len(known):
                    col2 = f"[bold green]{i + half + 1}.[/bold green] {known[i + half]}"
                grid.add_row(col1, col2)
            CONSOLE.print(grid)

            choice = self.get_input("\nEnter number or name (default: unspecified): ", "unspecified")
            
            if choice == "unspecified":
                return "unspecified"

            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(known):
                    return known[idx]
                else:
                    CONSOLE.print("[red]Invalid number.[/red]")
                    continue

            if choice in known:
                return choice

            confirm = self.get_input(f"[yellow]Tag '{choice}' is new. Create it? (y/n): [/yellow]", "n")
            if confirm.lower().startswith('y'):
                self.db.add_new_tag(choice)
                CONSOLE.print(f"[green]Tag '{choice}' added![/green]")
                time.sleep(1)
                return choice
            else:
                CONSOLE.print("[dim]Tag creation cancelled. Please try again.[/dim]")

    def display_menu(self):
        self.clear_screen()
        title = Text("FOCUS TIMER", style="bold magenta", justify="center")
        menu_text = """
[bold green][T] Timer Mode[/bold green]
[bold blue][W] Stopwatch Mode[/bold blue]
[bold yellow][D] Dashboard (Stats)[/bold yellow]
[bold red][Q] Quit[/bold red]
        """
        panel = Panel(Align.center(menu_text), title=title, border_style="white", padding=(1, 5))
        CONSOLE.print(panel)

    def run_session(self, mode):
        self.clear_screen()
        tag = self.select_single_tag()
        
        duration_limit = 0
        if mode == "timer":
            dur_in = self.get_input(f"Minutes (default {DEFAULT_POMODORO_MIN}): ")
            try:
                duration_limit = int(dur_in) * 60 if dur_in else DEFAULT_POMODORO_MIN * 60
            except:
                duration_limit = DEFAULT_POMODORO_MIN * 60

        start_time = datetime.now()
        start_monotonic = time.monotonic()
        paused_duration = 0
        is_paused = False
        pause_start = 0
        session_running = True
        
        while session_running:
            # We use refresh_per_second to let Rich handle the draw rate,
            # but we also need to sleep in our loop to save CPU.
            with KeyInput() as key_listener, Live(refresh_per_second=10) as live:
                while session_running:
                    key = key_listener.get_key()
                    if key == 'q': return 
                    if key == 'x': session_running = False
                    if key == 'p':
                        if is_paused:
                            paused_duration += time.monotonic() - pause_start
                            is_paused = False
                        else:
                            pause_start = time.monotonic()
                            is_paused = True

                    now = time.monotonic()
                    if not is_paused:
                        elapsed = now - start_monotonic - paused_duration
                    else:
                        elapsed = pause_start - start_monotonic - paused_duration

                    remaining = 0
                    if mode == "timer":
                        remaining = max(0, duration_limit - elapsed)
                        display_time = remaining
                        if remaining == 0 and not is_paused:
                            CONSOLE.bell()
                            session_running = False
                    else:
                        display_time = elapsed

                    color = "green" if mode == "timer" else "blue"
                    status = "PAUSED" if is_paused else "RUNNING"
                    
                    m, s = divmod(int(display_time), 60)
                    h, m = divmod(m, 60)
                    time_str = f"{h:02d}:{m:02d}:{s:02d}"

                    content = Table.grid(expand=True)
                    content.add_column(justify="center")
                    
                    content.add_row(Text(f"🏷️  {tag}", style="bold yellow"))
                    content.add_row(Text("\n" + time_str, style=f"bold {color}", size=3) if hasattr(Text, "size") else Text("\n" + time_str, style=f"bold {color}"))

                    if mode == "timer":
                        pct = min(elapsed / duration_limit, 1.0)
                        bar = "█" * int(pct * 40) + "░" * (40 - int(pct * 40))
                        content.add_row(Text(f"\n[{bar}]", style="cyan"))
                    
                    content.add_row(Text(f"\n[{status}]", style="bold red" if is_paused else "dim"))
                    content.add_row(Text("\n[P]ause  [X]Save  [Q]uit", style="dim"))
                    
                    live.update(Panel(content, border_style=color))

                    # === CRITICAL FIX: Prevent 100% CPU usage ===
                    time.sleep(0.1) 
            
            final_dur = duration_limit if (mode == "timer" and remaining == 0) else elapsed
            self.db.save_session(mode, tag, final_dur, start_time)
            time.sleep(0.5)

    def show_dashboard(self):
        self.clear_screen()
        today, week, month = self.db.get_dashboard_stats()

        def create_table(title, data):
            t = Table(title=title, box=box.SIMPLE, expand=True)
            t.add_column("Tag", style="cyan")
            t.add_column("Time", justify="right", style="magenta")
            if not data:
                t.add_row("-", "-")
            else:
                for tag, sec in sorted(data.items(), key=lambda x: x[1], reverse=True):
                    t.add_row(tag, self.format_time(sec))
            return t

        CONSOLE.print(Panel("[bold]Productivity Dashboard[/bold]", style="white"))
        CONSOLE.print(create_table("Today", today))
        CONSOLE.print(create_table("This Week", week))
        CONSOLE.print(create_table("This Month", month))

        input("\n[dim]Press Enter to return...[/dim]")

    def main(self):
        while self.running:
            self.display_menu()
            c = CONSOLE.input("Select: ").strip().lower()
            if c == 't': self.run_session("timer")
            elif c == 'w': self.run_session("stopwatch")
            elif c == 'd': self.show_dashboard()
            elif c == 'q': self.running = False

if __name__ == "__main__":
    try:
        app = FocusApp()
        app.main()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, termios.tcgetattr(sys.stdin))
        except: pass
