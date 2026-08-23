"""
ShiftWork GUI
Run with: python gui.py
"""
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
import queue
import sys
import os
import importlib

# D2: set True during development to hot-reload shiftwork.py on every run.
# Keep False in production — module reload adds unnecessary overhead.
DEV_MODE = False


# ── stdout redirector ────────────────────────────────────────────────────────
class _QueueWriter:
    def __init__(self, q: queue.Queue):
        self._q = q

    def write(self, text: str):
        if text:
            self._q.put(text)

    def flush(self):
        pass


# ── Main application ─────────────────────────────────────────────────────────
class ShiftWorkApp(tk.Tk):

    # Default weekends from SettingLoader (0-based day indices)
    _DEFAULT_WEEKENDS = {5, 6, 12, 13, 19, 20, 26, 27}

    def __init__(self):
        super().__init__()
        self.title("ShiftWork Scheduler")
        self.geometry("960x680")
        self.minsize(820, 560)
        self.configure(bg="#f5f5f5")

        # Working directory = folder containing this script
        self._base_dir = os.path.dirname(os.path.abspath(__file__))
        os.chdir(self._base_dir)

        # Ensure output folder exists
        os.makedirs(os.path.join(self._base_dir, "output"), exist_ok=True)

        # State
        self._log_queue: queue.Queue = queue.Queue()
        self._is_running = False
        self._weekend_days: set = set(self._DEFAULT_WEEKENDS)
        self._file_vars: dict[str, tk.BooleanVar] = {}
        self._day_btns: dict[int, tk.Button] = {}

        # Tk variables
        self._num_days = tk.IntVar(value=31)
        self._allow_en = tk.BooleanVar(value=False)
        self._allow_nd = tk.BooleanVar(value=False)

        self._build_styles()
        self._build_ui()
        self._refresh_files()
        self._draw_calendar()
        self._poll_log()

    # ── Styles ────────────────────────────────────────────────────────────────
    def _build_styles(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("TFrame",        background="#f5f5f5")
        s.configure("TLabel",        background="#f5f5f5", font=("Segoe UI", 9))
        s.configure("TCheckbutton",  background="#f5f5f5", font=("Segoe UI", 9))
        s.configure("TButton",       font=("Segoe UI", 9))
        s.configure("TSpinbox",      font=("Segoe UI", 9))
        s.configure("TSeparator",    background="#cccccc")
        s.configure("Title.TLabel",  font=("Segoe UI", 15, "bold"), background="#f5f5f5")
        s.configure("Sub.TLabel",    font=("Segoe UI", 9),  foreground="#777777", background="#f5f5f5")
        s.configure("Head.TLabel",   font=("Segoe UI", 8, "bold"), foreground="#555555", background="#f5f5f5")
        s.configure("Run.TButton",   font=("Segoe UI", 10, "bold"))
        s.map("Run.TButton",
              background=[("disabled", "#cccccc"), ("active", "#005a9e"), ("!disabled", "#0078d7")],
              foreground=[("disabled", "#888888"), ("!disabled", "white")])

    # ── UI layout ─────────────────────────────────────────────────────────────
    def _build_ui(self):
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)

        # ── Left column (fixed 300px) ─────────────────────────────────────────
        left = ttk.Frame(outer, width=300)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 12))
        left.pack_propagate(False)

        ttk.Label(left, text="ShiftWork", style="Title.TLabel").pack(anchor="w")
        ttk.Label(left, text="Nurse Schedule Generator", style="Sub.TLabel").pack(anchor="w")
        ttk.Separator(left).pack(fill="x", pady=8)

        # Settings
        ttk.Label(left, text="SETTINGS", style="Head.TLabel").pack(anchor="w")
        row = ttk.Frame(left)
        row.pack(anchor="w", pady=4)
        ttk.Label(row, text="Days in month:").pack(side=tk.LEFT)
        sp = ttk.Spinbox(row, from_=28, to=31, textvariable=self._num_days,
                         width=4, command=self._draw_calendar)
        sp.pack(side=tk.LEFT, padx=6)

        ttk.Checkbutton(left, text="Allow Evening → Night (consecutive days)",
                        variable=self._allow_en).pack(anchor="w", pady=1)
        ttk.Checkbutton(left, text="Allow Night → Day (consecutive days)",
                        variable=self._allow_nd).pack(anchor="w", pady=1)
        ttk.Separator(left).pack(fill="x", pady=8)

        # Weekend picker
        ttk.Label(left, text="WEEKENDS", style="Head.TLabel").pack(anchor="w")
        ttk.Label(left, text="Click to toggle  ■ = weekend", style="Sub.TLabel").pack(anchor="w", pady=(0, 4))
        self._cal_frame = ttk.Frame(left)
        self._cal_frame.pack(anchor="w")
        ttk.Separator(left).pack(fill="x", pady=8)

        # Input files
        ttk.Label(left, text="INPUT FILES", style="Head.TLabel").pack(anchor="w")
        self._files_frame = ttk.Frame(left)
        self._files_frame.pack(fill=tk.X, pady=4)

        btn_row = ttk.Frame(left)
        btn_row.pack(anchor="w", pady=(0, 4))
        ttk.Button(btn_row, text="↻ Refresh",  command=self._refresh_files, width=9).pack(side=tk.LEFT)
        ttk.Button(btn_row, text="☑ All",       command=self._select_all,    width=6).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_row, text="☐ None",      command=self._select_none,   width=6).pack(side=tk.LEFT)
        ttk.Separator(left).pack(fill="x", pady=8)

        # Run button + status
        self._run_btn = ttk.Button(left, text="▶   Run Solver",
                                   style="Run.TButton", command=self._run)
        self._run_btn.pack(fill=tk.X, ipady=7)
        self._status = ttk.Label(left, text="Ready", style="Sub.TLabel")
        self._status.pack(anchor="w", pady=(4, 0))

        # ── Right column (log) ────────────────────────────────────────────────
        right = ttk.Frame(outer)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        header_row = ttk.Frame(right)
        header_row.pack(fill=tk.X)
        ttk.Label(header_row, text="OUTPUT LOG", style="Head.TLabel").pack(side=tk.LEFT)
        ttk.Button(header_row, text="Clear", command=self._clear_log, width=6).pack(side=tk.RIGHT)

        self._log = scrolledtext.ScrolledText(
            right, font=("Consolas", 9),
            bg="#1e1e1e", fg="#d4d4d4",
            insertbackground="white",
            wrap=tk.WORD, state=tk.DISABLED,
            relief=tk.FLAT, borderwidth=1,
        )
        self._log.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self._log.tag_config("info",    foreground="#9cdcfe")
        self._log.tag_config("ok",      foreground="#4ec9b0")
        self._log.tag_config("error",   foreground="#f44747")
        self._log.tag_config("section", foreground="#c586c0", font=("Consolas", 9, "bold"))

    # ── Calendar ──────────────────────────────────────────────────────────────
    def _draw_calendar(self, *_):
        for w in self._cal_frame.winfo_children():
            w.destroy()
        self._day_btns.clear()

        # Day-of-week headers
        for col, hdr in enumerate(["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]):
            color = "#e07b00" if col >= 5 else "#555555"
            tk.Label(self._cal_frame, text=hdr, width=3,
                     fg=color, bg="#f5f5f5",
                     font=("Segoe UI", 7, "bold")).grid(row=0, column=col, padx=1)

        n = self._num_days.get()
        # Remove out-of-range weekend days
        self._weekend_days = {d for d in self._weekend_days if d < n}

        for i in range(n):
            d0 = i
            is_wknd = d0 in self._weekend_days
            btn = tk.Button(
                self._cal_frame,
                text=str(i + 1),
                width=3,
                bg="#e07b00" if is_wknd else "#e0e0e0",
                fg="white"  if is_wknd else "#333333",
                activebackground="#c06800",
                relief=tk.FLAT,
                font=("Segoe UI", 8),
                cursor="hand2",
                command=lambda d=d0: self._toggle_weekend(d),
            )
            btn.grid(row=(i // 7) + 1, column=i % 7, padx=1, pady=1)
            self._day_btns[d0] = btn

    def _toggle_weekend(self, d0: int):
        if d0 in self._weekend_days:
            self._weekend_days.discard(d0)
        else:
            self._weekend_days.add(d0)
        is_wknd = d0 in self._weekend_days
        btn = self._day_btns[d0]
        btn.configure(bg="#e07b00" if is_wknd else "#e0e0e0",
                      fg="white"  if is_wknd else "#333333")

    # ── Files ─────────────────────────────────────────────────────────────────
    def _refresh_files(self):
        for w in self._files_frame.winfo_children():
            w.destroy()
        self._file_vars.clear()

        input_dir = os.path.join(self._base_dir, "input")
        if not os.path.isdir(input_dir):
            ttk.Label(self._files_frame, text="'input/' folder not found",
                      foreground="red").pack(anchor="w")
            return

        files = sorted(f for f in os.listdir(input_dir) if f.lower().endswith(".csv"))
        if not files:
            ttk.Label(self._files_frame, text="No CSV files in input/",
                      style="Sub.TLabel").pack(anchor="w")
            return

        for fname in files:
            var = tk.BooleanVar(value=True)
            self._file_vars[fname] = var
            ttk.Checkbutton(self._files_frame, text=fname, variable=var).pack(anchor="w")

    def _select_all(self):
        for v in self._file_vars.values():
            v.set(True)

    def _select_none(self):
        for v in self._file_vars.values():
            v.set(False)

    # ── Log ───────────────────────────────────────────────────────────────────
    def _append_log(self, text: str):
        self._log.config(state=tk.NORMAL)
        tl = text.lower()
        if text.startswith("===") or text.startswith("\n==="):
            tag = "section"
        elif "[data]" in tl or "detected" in tl or "init" in tl or "loaded" in tl:
            tag = "info"
        elif "solution found" in tl or "total execution" in tl or "generated" in tl:
            tag = "ok"
        elif "no solution" in tl or "error" in tl or "traceback" in tl:
            tag = "error"
        else:
            tag = None
        self._log.insert(tk.END, text, tag or "")
        self._log.see(tk.END)
        self._log.config(state=tk.DISABLED)

    def _clear_log(self):
        self._log.config(state=tk.NORMAL)
        self._log.delete("1.0", tk.END)
        self._log.config(state=tk.DISABLED)

    def _poll_log(self):
        try:
            while True:
                self._append_log(self._log_queue.get_nowait())
        except queue.Empty:
            pass
        self.after(80, self._poll_log)

    # ── Solver thread ─────────────────────────────────────────────────────────
    def _run(self):
        selected = [f for f, v in self._file_vars.items() if v.get()]
        if not selected:
            messagebox.showwarning("No files selected",
                                   "Please select at least one input file.")
            return
        if self._is_running:
            return

        self._is_running = True
        self._run_btn.config(state=tk.DISABLED)
        self._status.config(text="Running…", foreground="#e07b00")

        snap = {
            "num_days":  self._num_days.get(),
            "weekends":  sorted(self._weekend_days),
            "allow_e_n": self._allow_en.get(),
            "allow_n_d": self._allow_nd.get(),
            "files":     selected,
        }
        threading.Thread(target=self._solver_thread, args=(snap,), daemon=True).start()

    def _solver_thread(self, snap: dict):
        old_stdout = sys.stdout
        sys.stdout = _QueueWriter(self._log_queue)
        success, failed = [], []
        try:
            import shiftwork as _sw
            if DEV_MODE:
                importlib.reload(_sw)       # D2: hot-reload only in dev mode
            os.chdir(self._base_dir)

            input_dir = os.path.join(self._base_dir, "input")
            for fname in snap["files"]:
                fpath = os.path.join(input_dir, fname)
                print(f"\n{'='*52}\nProcessing: {fname}\n{'='*52}\n")
                try:
                    _sw.Solver(
                        fpath,
                        snap["num_days"],
                        snap["weekends"],
                        allow_shift_e_n=snap["allow_e_n"],
                        allow_shift_n_d=snap["allow_n_d"],
                    ).run()
                    success.append(fname)
                except Exception as exc:
                    import traceback
                    print(f"ERROR — {fname}:\n{traceback.format_exc()}")
                    failed.append(fname)

            print(f"\n{'='*52}")
            print(f"Done.  ✓ {len(success)} succeeded   ✗ {len(failed)} failed")
            if failed:
                print("Failed: " + ", ".join(failed))
            print(f"{'='*52}\n")
        finally:
            sys.stdout = old_stdout
            self._is_running = False
            self.after(0, lambda: self._on_done(success, failed))

    def _on_done(self, success: list, failed: list):
        self._run_btn.config(state=tk.NORMAL)
        if failed:
            self._status.config(
                text=f"Done — {len(success)} OK, {len(failed)} failed", foreground="#c0392b")
        else:
            self._status.config(
                text=f"Done — {len(success)} file(s) completed ✓", foreground="#27ae60")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = ShiftWorkApp()
    app.mainloop()
