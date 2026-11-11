from __future__ import annotations
import os
import sys
import copy
import time
import ctypes
import threading
from datetime import datetime
from typing import Optional, List, Tuple
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk
import pyautogui

from models import TargetObject, ClickStep, AutomationProfile, default_click_sequence
from detector import (
    ScreenDetector,
    DetectionResult,
    ScreenSnipperOverlay,
    PointPickerOverlay,
    flash_multiple_boxes,
)
from executor import SequenceExecutor, LiveMouseRecorder, GlobalHotkeyManager


def enable_windows_dpi_awareness() -> None:
    """Ensure 1:1 pixel coordinates across screen capture, Tkinter, and PyAutoGUI on Windows."""
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def is_running_as_admin() -> bool:
    if sys.platform != "win32":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


class StepEditorDialog(tk.Toplevel):
    """Modal dialog to add or edit a ClickStep with advanced click patterns."""

    def __init__(
        self,
        parent: tk.Tk,
        step: Optional[ClickStep] = None,
        title: str = "Edit Sequence Step",
        target_names: Optional[List[str]] = None,
    ) -> None:
        super().__init__(parent)
        self.title(title)
        self.configure(bg="#1e293b")
        self.resizable(False, False)
        self.result: Optional[ClickStep] = None
        self.target_names = target_names or []

        init_step = step or ClickStep()

        pad = {"padx": 12, "pady": 4}
        main = tk.Frame(self, bg="#1e293b", padx=16, pady=12)
        main.pack(fill=tk.BOTH, expand=True)

        row = 0

        # Step Type
        tk.Label(main, text="Step Type:", bg="#1e293b", fg="#e2e8f0", font=("Segoe UI", 10, "bold")).grid(
            row=row, column=0, sticky="w", **pad
        )
        self.type_var = tk.StringVar(value=init_step.step_type)
        type_cb = ttk.Combobox(
            main,
            textvariable=self.type_var,
            values=["click_detected", "fixed_coords", "wait", "keypress"],
            state="readonly",
            width=26,
        )
        type_cb.grid(row=row, column=1, columnspan=2, sticky="ew", **pad)
        type_cb.bind("<<ComboboxSelected>>", self._on_type_or_pattern_change)
        row += 1

        # Target Filter (for click_detected)
        self.target_filt_lbl = tk.Label(main, text="Target Object:", bg="#1e293b", fg="#e2e8f0", font=("Segoe UI", 10))
        self.target_filt_lbl.grid(row=row, column=0, sticky="w", **pad)
        filt_opts = ["any"] + self.target_names
        self.target_filt_var = tk.StringVar(value=init_step.target_filter if init_step.target_filter in filt_opts else "any")
        self.target_filt_cb = ttk.Combobox(main, textvariable=self.target_filt_var, values=filt_opts, state="readonly", width=26)
        self.target_filt_cb.grid(row=row, column=1, columnspan=2, sticky="ew", **pad)
        row += 1

        # Click Pattern
        self.pattern_lbl = tk.Label(main, text="Click Pattern:", bg="#1e293b", fg="#38bdf8", font=("Segoe UI", 10, "bold"))
        self.pattern_lbl.grid(row=row, column=0, sticky="w", **pad)
        self.pattern_var = tk.StringVar(value=init_step.pattern_type)
        self.pattern_cb = ttk.Combobox(
            main,
            textvariable=self.pattern_var,
            values=["point", "all_instances", "jitter", "burst", "long_press", "glide"],
            state="readonly",
            width=26,
        )
        self.pattern_cb.grid(row=row, column=1, columnspan=2, sticky="ew", **pad)
        self.pattern_cb.bind("<<ComboboxSelected>>", self._on_type_or_pattern_change)
        row += 1

        # Pattern Details Subframe
        self.pattern_frame = tk.LabelFrame(main, text=" Pattern Settings ", bg="#1e293b", fg="#38bdf8", padx=8, pady=6)
        self.pattern_frame.grid(row=row, column=0, columnspan=3, sticky="ew", padx=12, pady=6)

        # Pattern Order (for all_instances)
        self.order_lbl = tk.Label(self.pattern_frame, text="Instance Order:", bg="#1e293b", fg="#cbd5e1", font=("Segoe UI", 9))
        self.order_lbl.grid(row=0, column=0, sticky="w", padx=4, pady=2)
        self.order_var = tk.StringVar(value=init_step.pattern_order)
        self.order_cb = ttk.Combobox(
            self.pattern_frame,
            textvariable=self.order_var,
            values=["top_to_bottom", "bottom_to_top", "left_to_right", "right_to_left", "nearest", "random"],
            state="readonly",
            width=16,
        )
        self.order_cb.grid(row=0, column=1, sticky="w", padx=4, pady=2)

        # Jitter Radius
        self.jitter_lbl = tk.Label(self.pattern_frame, text="Jitter Radius (px):", bg="#1e293b", fg="#cbd5e1", font=("Segoe UI", 9))
        self.jitter_lbl.grid(row=1, column=0, sticky="w", padx=4, pady=2)
        self.jitter_var = tk.IntVar(value=init_step.jitter_radius)
        self.jitter_spin = ttk.Spinbox(self.pattern_frame, from_=1, to=100, textvariable=self.jitter_var, width=8)
        self.jitter_spin.grid(row=1, column=1, sticky="w", padx=4, pady=2)

        # Burst Count & Micro Delay
        self.burst_lbl = tk.Label(self.pattern_frame, text="Burst Clicks / Delay:", bg="#1e293b", fg="#cbd5e1", font=("Segoe UI", 9))
        self.burst_lbl.grid(row=2, column=0, sticky="w", padx=4, pady=2)
        burst_sub = tk.Frame(self.pattern_frame, bg="#1e293b")
        burst_sub.grid(row=2, column=1, sticky="w", padx=4, pady=2)
        self.burst_count_var = tk.IntVar(value=init_step.burst_count)
        self.burst_count_spin = ttk.Spinbox(burst_sub, from_=2, to=30, textvariable=self.burst_count_var, width=5)
        self.burst_count_spin.pack(side=tk.LEFT)
        tk.Label(burst_sub, text="x @", bg="#1e293b", fg="#94a3b8").pack(side=tk.LEFT, padx=3)
        self.burst_delay_var = tk.DoubleVar(value=init_step.burst_delay)
        self.burst_delay_spin = ttk.Spinbox(burst_sub, from_=0.01, to=1.0, increment=0.01, textvariable=self.burst_delay_var, width=6)
        self.burst_delay_spin.pack(side=tk.LEFT)
        tk.Label(burst_sub, text="s", bg="#1e293b", fg="#94a3b8").pack(side=tk.LEFT)

        # Hold Duration (long_press)
        self.hold_lbl = tk.Label(self.pattern_frame, text="Hold Duration (s):", bg="#1e293b", fg="#cbd5e1", font=("Segoe UI", 9))
        self.hold_lbl.grid(row=3, column=0, sticky="w", padx=4, pady=2)
        self.hold_var = tk.DoubleVar(value=init_step.hold_duration)
        self.hold_spin = ttk.Spinbox(self.pattern_frame, from_=0.05, to=10.0, increment=0.05, textvariable=self.hold_var, width=8)
        self.hold_spin.grid(row=3, column=1, sticky="w", padx=4, pady=2)

        # Glide Duration
        self.glide_lbl = tk.Label(self.pattern_frame, text="Glide Duration (s):", bg="#1e293b", fg="#cbd5e1", font=("Segoe UI", 9))
        self.glide_lbl.grid(row=4, column=0, sticky="w", padx=4, pady=2)
        self.glide_var = tk.DoubleVar(value=init_step.glide_duration)
        self.glide_spin = ttk.Spinbox(self.pattern_frame, from_=0.05, to=5.0, increment=0.05, textvariable=self.glide_var, width=8)
        self.glide_spin.grid(row=4, column=1, sticky="w", padx=4, pady=2)

        row += 1

        # Coordinates / Offset
        self.coord_label = tk.Label(main, text="Offset (X, Y):", bg="#1e293b", fg="#e2e8f0", font=("Segoe UI", 10))
        self.coord_label.grid(row=row, column=0, sticky="w", **pad)
        coord_frame = tk.Frame(main, bg="#1e293b")
        coord_frame.grid(row=row, column=1, columnspan=2, sticky="w", **pad)
        tk.Label(coord_frame, text="X:", bg="#1e293b", fg="#94a3b8").pack(side=tk.LEFT)
        self.x_var = tk.IntVar(value=init_step.x)
        self.x_spin = ttk.Spinbox(coord_frame, from_=-5000, to=10000, textvariable=self.x_var, width=7)
        self.x_spin.pack(side=tk.LEFT, padx=(4, 10))
        tk.Label(coord_frame, text="Y:", bg="#1e293b", fg="#94a3b8").pack(side=tk.LEFT)
        self.y_var = tk.IntVar(value=init_step.y)
        self.y_spin = ttk.Spinbox(coord_frame, from_=-5000, to=10000, textvariable=self.y_var, width=7)
        self.y_spin.pack(side=tk.LEFT, padx=(4, 10))
        self.pick_btn = tk.Button(
            coord_frame,
            text="Pick on Screen",
            bg="#0284c7",
            fg="#ffffff",
            relief=tk.FLAT,
            padx=8,
            pady=2,
            command=self._pick_screen_point,
        )
        self.pick_btn.pack(side=tk.LEFT)
        row += 1

        # Mouse Button & Clicks
        tk.Label(main, text="Mouse Button:", bg="#1e293b", fg="#e2e8f0", font=("Segoe UI", 10)).grid(
            row=row, column=0, sticky="w", **pad
        )
        self.btn_var = tk.StringVar(value=init_step.button)
        self.btn_cb = ttk.Combobox(
            main,
            textvariable=self.btn_var,
            values=["left", "double", "right", "middle"],
            state="readonly",
            width=14,
        )
        self.btn_cb.grid(row=row, column=1, sticky="w", **pad)
        row += 1

        tk.Label(main, text="Click Count:", bg="#1e293b", fg="#e2e8f0", font=("Segoe UI", 10)).grid(
            row=row, column=0, sticky="w", **pad
        )
        self.clicks_var = tk.IntVar(value=init_step.clicks)
        self.clicks_spin = ttk.Spinbox(main, from_=1, to=20, textvariable=self.clicks_var, width=8)
        self.clicks_spin.grid(row=row, column=1, sticky="w", **pad)
        row += 1

        # Key name (for keypress)
        tk.Label(main, text="Key to Press:", bg="#1e293b", fg="#e2e8f0", font=("Segoe UI", 10)).grid(
            row=row, column=0, sticky="w", **pad
        )
        self.key_var = tk.StringVar(value=init_step.key or "enter")
        self.key_cb = ttk.Combobox(
            main,
            textvariable=self.key_var,
            values=["enter", "space", "esc", "tab", "backspace", "up", "down", "left", "right", "f1", "f5"],
            width=14,
        )
        self.key_cb.grid(row=row, column=1, sticky="w", **pad)
        row += 1

        # Delay After
        tk.Label(main, text="Delay After (sec):", bg="#1e293b", fg="#e2e8f0", font=("Segoe UI", 10)).grid(
            row=row, column=0, sticky="w", **pad
        )
        self.delay_var = tk.DoubleVar(value=init_step.delay_after)
        self.delay_spin = ttk.Spinbox(main, from_=0.0, to=3600.0, increment=0.05, textvariable=self.delay_var, width=10)
        self.delay_spin.grid(row=row, column=1, sticky="w", **pad)
        row += 1

        # Note
        tk.Label(main, text="Note / Label:", bg="#1e293b", fg="#e2e8f0", font=("Segoe UI", 10)).grid(
            row=row, column=0, sticky="w", **pad
        )
        self.note_var = tk.StringVar(value=init_step.note)
        ttk.Entry(main, textvariable=self.note_var, width=28).grid(
            row=row, column=1, columnspan=2, sticky="ew", **pad
        )
        row += 1

        # Buttons
        btn_row = tk.Frame(main, bg="#1e293b")
        btn_row.grid(row=row, column=0, columnspan=3, pady=(12, 2))
        tk.Button(
            btn_row,
            text="Save Step",
            bg="#10b981",
            fg="#ffffff",
            font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT,
            padx=16,
            pady=5,
            command=self._on_save,
        ).pack(side=tk.LEFT, padx=6)
        tk.Button(
            btn_row,
            text="Cancel",
            bg="#475569",
            fg="#ffffff",
            font=("Segoe UI", 10),
            relief=tk.FLAT,
            padx=16,
            pady=5,
            command=self.destroy,
        ).pack(side=tk.LEFT, padx=6)

        self._on_type_or_pattern_change()
        self.transient(parent)
        self.grab_set()

    def _on_type_or_pattern_change(self, _event: Optional[tk.Event] = None) -> None:
        st = self.type_var.get()
        pat = self.pattern_var.get()

        if st == "click_detected":
            self.target_filt_cb.config(state="readonly")
            self.pattern_cb.config(state="readonly")
            self.coord_label.config(text="Offset from Object (dX, dY):")
            self.x_spin.config(state="normal")
            self.y_spin.config(state="normal")
            self.pick_btn.config(state="disabled")
            self.btn_cb.config(state="readonly")
            self.clicks_spin.config(state="normal")
            self.key_cb.config(state="disabled")
        elif st == "fixed_coords":
            self.target_filt_cb.config(state="disabled")
            self.pattern_cb.config(state="readonly")
            self.coord_label.config(text="Screen Coords (X, Y):")
            self.x_spin.config(state="normal")
            self.y_spin.config(state="normal")
            self.pick_btn.config(state="normal")
            self.btn_cb.config(state="readonly")
            self.clicks_spin.config(state="normal")
            self.key_cb.config(state="disabled")
        elif st in ("wait", "keypress"):
            self.target_filt_cb.config(state="disabled")
            self.pattern_cb.config(state="disabled")
            self.x_spin.config(state="disabled")
            self.y_spin.config(state="disabled")
            self.pick_btn.config(state="disabled")
            self.btn_cb.config(state="disabled")
            self.clicks_spin.config(state="disabled")
            self.key_cb.config(state="normal" if st == "keypress" else "disabled")

        # Configure pattern specific controls
        self.order_cb.config(state="readonly" if pat == "all_instances" and st == "click_detected" else "disabled")
        self.jitter_spin.config(state="normal" if pat == "jitter" else "disabled")
        self.burst_count_spin.config(state="normal" if pat == "burst" else "disabled")
        self.burst_delay_spin.config(state="normal" if pat == "burst" else "disabled")
        self.hold_spin.config(state="normal" if pat == "long_press" else "disabled")
        self.glide_spin.config(state="normal" if pat == "glide" else "disabled")

    def _pick_screen_point(self) -> None:
        self.withdraw()
        self.master.withdraw()
        self.master.after(250, lambda: PointPickerOverlay(self.master, self._on_point_picked))

    def _on_point_picked(self, pt: Optional[Tuple[int, int]]) -> None:
        self.master.deiconify()
        self.deiconify()
        self.grab_set()
        if pt is not None:
            self.x_var.set(pt[0])
            self.y_var.set(pt[1])

    def _on_save(self) -> None:
        try:
            self.result = ClickStep(
                step_type=self.type_var.get(),
                x=int(self.x_var.get()),
                y=int(self.y_var.get()),
                button=self.btn_var.get(),
                clicks=max(1, int(self.clicks_var.get())),
                delay_after=max(0.0, float(self.delay_var.get())),
                key=self.key_var.get().strip(),
                note=self.note_var.get().strip(),
                target_filter=self.target_filt_var.get(),
                pattern_type=self.pattern_var.get(),
                pattern_order=self.order_var.get(),
                jitter_radius=max(1, int(self.jitter_var.get())),
                burst_count=max(2, int(self.burst_count_var.get())),
                burst_delay=max(0.01, float(self.burst_delay_var.get())),
                hold_duration=max(0.05, float(self.hold_var.get())),
                glide_duration=max(0.05, float(self.glide_var.get())),
            )
            self.destroy()
        except ValueError as e:
            messagebox.showerror("Invalid Input", f"Please check numeric values:\n{e}", parent=self)


class SmartAutoClickerApp:
    """Main GUI Application for Multi-Object Detection with Per-Object Click Sequences."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Smart Object Auto Clicker — Multi-Object Detection & Per-Object Sequences")
        self.root.geometry("1200x800")
        self.root.minsize(1060, 720)
        self.root.configure(bg="#0f172a")

        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.templates_dir = os.path.join(self.base_dir, "templates")
        self.profiles_dir = os.path.join(self.base_dir, "profiles")
        os.makedirs(self.templates_dir, exist_ok=True)
        os.makedirs(self.profiles_dir, exist_ok=True)

        self.profile = AutomationProfile()
        self.detector = ScreenDetector()
        self.executor = SequenceExecutor(log_callback=self.log_threadsafe)
        self.recorder = LiveMouseRecorder(on_finished=self._on_recording_finished)

        self.selected_target_idx = 0
        self.stop_event = threading.Event()
        self.worker_thread: Optional[threading.Thread] = None
        self.is_running = False
        self.trigger_count = 0
        self.last_detection: Optional[DetectionResult] = None
        self._preview_photo: Optional[ImageTk.PhotoImage] = None

        self._configure_styles()
        self._build_ui()
        self._refresh_targets_table()
        self._refresh_sequence_table()

        self.hotkeys = GlobalHotkeyManager(
            on_toggle_engine=lambda: self.root.after(0, self.toggle_engine),
            on_toggle_record=lambda: self.root.after(0, self.toggle_recording),
            on_emergency_stop=lambda: self.root.after(0, self.emergency_stop),
        )
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.log("Ready. Each object can have its own custom click sequence & patterns. Press F6 to start.")

    def _configure_styles(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            "Treeview",
            background="#1e293b",
            foreground="#f1f5f9",
            fieldbackground="#1e293b",
            rowheight=26,
            font=("Segoe UI", 9),
        )
        style.configure(
            "Treeview.Heading",
            background="#334155",
            foreground="#38bdf8",
            font=("Segoe UI", 9, "bold"),
        )
        style.map("Treeview", background=[("selected", "#0284c7")], foreground=[("selected", "#ffffff")])

    def _build_ui(self) -> None:
        # Top Header Bar
        header = tk.Frame(self.root, bg="#1e293b", padx=16, pady=8)
        header.pack(fill=tk.X)

        tk.Label(
            header,
            text="🎯 Smart Object Auto Clicker",
            bg="#1e293b",
            fg="#38bdf8",
            font=("Segoe UI", 13, "bold"),
        ).pack(side=tk.LEFT)

        tk.Label(
            header,
            text="Hotkeys: [F6] Start/Stop   |   [F7] Record Clicks   |   [F8] Emergency Stop",
            bg="#1e293b",
            fg="#94a3b8",
            font=("Segoe UI", 9),
        ).pack(side=tk.LEFT, padx=20)

        prof_frame = tk.Frame(header, bg="#1e293b")
        prof_frame.pack(side=tk.RIGHT)
        if not is_running_as_admin():
            tk.Button(
                prof_frame,
                text="🛡 Restart as Admin",
                bg="#d97706",
                fg="#ffffff",
                font=("Segoe UI", 9, "bold"),
                relief=tk.FLAT,
                padx=8,
                pady=3,
                command=self.restart_as_admin,
            ).pack(side=tk.LEFT, padx=4)
        else:
            tk.Label(
                prof_frame,
                text="🛡 Admin Mode",
                bg="#1e293b",
                fg="#10b981",
                font=("Segoe UI", 9, "bold"),
            ).pack(side=tk.LEFT, padx=6)
        tk.Button(
            prof_frame,
            text="💾 Save Profile",
            bg="#334155",
            fg="#f8fafc",
            relief=tk.FLAT,
            padx=9,
            pady=3,
            command=self.save_profile_dialog,
        ).pack(side=tk.LEFT, padx=4)
        tk.Button(
            prof_frame,
            text="📂 Load Profile",
            bg="#334155",
            fg="#f8fafc",
            relief=tk.FLAT,
            padx=9,
            pady=3,
            command=self.load_profile_dialog,
        ).pack(side=tk.LEFT, padx=4)

        # Main Split Container
        body = tk.Frame(self.root, bg="#0f172a", padx=10, pady=6)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=4)
        body.columnconfigure(1, weight=6)
        body.rowconfigure(0, weight=1)

        self._build_left_panel(body)
        self._build_right_panel(body)
        self._build_bottom_panel()

    def _build_left_panel(self, parent: tk.Frame) -> None:
        left = tk.Frame(parent, bg="#1e293b", padx=12, pady=10, highlightbackground="#334155", highlightthickness=1)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 5))

        # Title & Targets list
        t_header = tk.Frame(left, bg="#1e293b")
        t_header.pack(fill=tk.X)
        self.targets_badge = tk.Label(
            t_header,
            text="1. Target Objects (Click to Select)",
            bg="#1e293b",
            fg="#38bdf8",
            font=("Segoe UI", 11, "bold"),
        )
        self.targets_badge.pack(side=tk.LEFT)

        # Target Toolbar
        tb = tk.Frame(left, bg="#1e293b")
        tb.pack(fill=tk.X, pady=(6, 4))
        tk.Button(
            tb,
            text="✂ Snip New",
            bg="#0284c7",
            fg="#ffffff",
            font=("Segoe UI", 8, "bold"),
            relief=tk.FLAT,
            padx=6,
            pady=3,
            command=self.add_target_snipped,
        ).pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(
            tb,
            text="🖼 Load File",
            bg="#334155",
            fg="#f8fafc",
            font=("Segoe UI", 8),
            relief=tk.FLAT,
            padx=6,
            pady=3,
            command=self.add_target_from_file,
        ).pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(
            tb,
            text="✓ Toggle",
            bg="#334155",
            fg="#10b981",
            font=("Segoe UI", 8, "bold"),
            relief=tk.FLAT,
            padx=6,
            pady=3,
            command=self.toggle_target_active,
        ).pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(
            tb,
            text="🗑 Remove",
            bg="#334155",
            fg="#f87171",
            font=("Segoe UI", 8),
            relief=tk.FLAT,
            padx=6,
            pady=3,
            command=self.remove_target,
        ).pack(side=tk.RIGHT)

        # Targets Table (Shows Active, Name, Conf, Max Inst, Sequence Steps count)
        tt_frame = tk.Frame(left, bg="#1e293b")
        tt_frame.pack(fill=tk.X, pady=2)
        columns = ("active", "name", "conf", "instances", "steps")
        self.targets_tree = ttk.Treeview(tt_frame, columns=columns, show="headings", height=4, selectmode="browse")
        self.targets_tree.heading("active", text="Active")
        self.targets_tree.heading("name", text="Target Name")
        self.targets_tree.heading("conf", text="Confidence")
        self.targets_tree.heading("instances", text="Max Inst")
        self.targets_tree.heading("steps", text="Sequence")

        self.targets_tree.column("active", width=42, anchor="center", stretch=False)
        self.targets_tree.column("name", width=125, anchor="w")
        self.targets_tree.column("conf", width=68, anchor="center", stretch=False)
        self.targets_tree.column("instances", width=60, anchor="center", stretch=False)
        self.targets_tree.column("steps", width=80, anchor="center", stretch=False)

        self.targets_tree.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.targets_tree.bind("<<TreeviewSelect>>", self._on_target_selected)

        # Selected Target Editor Frame
        self.edit_frame = tk.LabelFrame(left, text=" Selected Target Settings ", bg="#1e293b", fg="#38bdf8", padx=8, pady=4)
        self.edit_frame.pack(fill=tk.X, pady=(6, 4))

        # Target Name & Preview
        prev_row = tk.Frame(self.edit_frame, bg="#1e293b")
        prev_row.pack(fill=tk.X, pady=2)

        self.preview_canvas = tk.Canvas(
            prev_row, width=130, height=75, bg="#0f172a", highlightbackground="#334155", highlightthickness=1
        )
        self.preview_canvas.pack(side=tk.LEFT, padx=(0, 8))

        detail_col = tk.Frame(prev_row, bg="#1e293b")
        detail_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tk.Label(detail_col, text="Name:", bg="#1e293b", fg="#94a3b8", font=("Segoe UI", 8)).pack(anchor="w")
        self.target_name_var = tk.StringVar(value="")
        self.target_name_entry = ttk.Entry(detail_col, textvariable=self.target_name_var)
        self.target_name_entry.pack(fill=tk.X, pady=(0, 4))
        self.target_name_entry.bind("<FocusOut>", lambda _e: self._on_target_field_updated())

        self.target_info_lbl = tk.Label(detail_col, text="Template: None", bg="#1e293b", fg="#64748b", font=("Consolas", 8), anchor="w")
        self.target_info_lbl.pack(fill=tk.X)

        # Confidence & Multi-Instance Count
        conf_row = tk.Frame(self.edit_frame, bg="#1e293b")
        conf_row.pack(fill=tk.X, pady=3)
        tk.Label(conf_row, text="Confidence:", bg="#1e293b", fg="#cbd5e1", font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self.conf_val_lbl = tk.Label(conf_row, text="85%", bg="#1e293b", fg="#38bdf8", font=("Consolas", 9, "bold"))
        self.conf_val_lbl.pack(side=tk.LEFT, padx=6)
        self.conf_var = tk.DoubleVar(value=0.85)
        conf_scale = ttk.Scale(
            conf_row,
            from_=0.50,
            to=0.99,
            variable=self.conf_var,
            command=self._on_conf_scale_moved,
        )
        conf_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 8))

        tk.Label(conf_row, text="Max Instances:", bg="#1e293b", fg="#cbd5e1", font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self.max_inst_var = tk.IntVar(value=1)
        inst_spin = ttk.Spinbox(conf_row, from_=1, to=100, textvariable=self.max_inst_var, width=4, command=self._on_target_field_updated)
        inst_spin.pack(side=tk.LEFT, padx=(4, 0))
        inst_spin.bind("<FocusOut>", lambda _e: self._on_target_field_updated())

        # Checkboxes
        chk_row = tk.Frame(self.edit_frame, bg="#1e293b")
        chk_row.pack(fill=tk.X, pady=2)
        self.grayscale_var = tk.BooleanVar(value=False)
        self.multiscale_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            chk_row,
            text="Grayscale",
            variable=self.grayscale_var,
            command=self._on_target_field_updated,
            bg="#1e293b",
            fg="#cbd5e1",
            selectcolor="#0f172a",
        ).pack(side=tk.LEFT, padx=(0, 10))
        tk.Checkbutton(
            chk_row,
            text="Multi-Scale (0.8x-1.2x)",
            variable=self.multiscale_var,
            command=self._on_target_field_updated,
            bg="#1e293b",
            fg="#cbd5e1",
            selectcolor="#0f172a",
        ).pack(side=tk.LEFT)

        # Search Region
        reg_row = tk.Frame(self.edit_frame, bg="#1e293b")
        reg_row.pack(fill=tk.X, pady=2)
        self.region_lbl = tk.Label(reg_row, text="Area: Full Screen", bg="#1e293b", fg="#94a3b8", font=("Segoe UI", 8))
        self.region_lbl.pack(side=tk.LEFT)
        tk.Button(
            reg_row,
            text="Reset Area",
            bg="#334155",
            fg="#e2e8f0",
            relief=tk.FLAT,
            padx=4,
            pady=1,
            font=("Segoe UI", 8),
            command=self.reset_search_region,
        ).pack(side=tk.RIGHT, padx=(3, 0))
        tk.Button(
            reg_row,
            text="Select Area",
            bg="#334155",
            fg="#38bdf8",
            relief=tk.FLAT,
            padx=4,
            pady=1,
            font=("Segoe UI", 8),
            command=self.select_search_region,
        ).pack(side=tk.RIGHT)

        # Multi-Target & Pattern Rules Section
        rules_frame = tk.LabelFrame(left, text=" Detection Strategy & Pattern Order ", bg="#1e293b", fg="#38bdf8", padx=8, pady=4)
        rules_frame.pack(fill=tk.X, pady=(6, 4))

        tk.Label(rules_frame, text="Trigger Condition:", bg="#1e293b", fg="#cbd5e1", font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", pady=2)
        self.multi_rule_var = tk.StringVar(value="any")
        rule_cb = ttk.Combobox(
            rules_frame,
            textvariable=self.multi_rule_var,
            values=["any", "all", "priority"],
            state="readonly",
            width=14,
        )
        rule_cb.grid(row=0, column=1, sticky="w", padx=6, pady=2)

        tk.Label(rules_frame, text="Multi-Instance Order:", bg="#1e293b", fg="#cbd5e1", font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", pady=2)
        self.pattern_order_var = tk.StringVar(value="top_to_bottom")
        order_cb = ttk.Combobox(
            rules_frame,
            textvariable=self.pattern_order_var,
            values=["top_to_bottom", "bottom_to_top", "left_to_right", "right_to_left", "nearest", "random"],
            state="readonly",
            width=14,
        )
        order_cb.grid(row=1, column=1, sticky="w", padx=6, pady=2)

        # Test Detection Button
        tk.Button(
            left,
            text="🔍 Test Detect ALL Objects on Screen",
            bg="#4f46e5",
            fg="#ffffff",
            font=("Segoe UI", 9, "bold"),
            relief=tk.FLAT,
            pady=6,
            command=self.test_detection_now,
        ).pack(fill=tk.X, pady=(6, 6))

        # Timing controls
        time_f = tk.Frame(left, bg="#1e293b")
        time_f.pack(fill=tk.X)
        tk.Label(time_f, text="Scan (s):", bg="#1e293b", fg="#cbd5e1", font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self.poll_var = tk.DoubleVar(value=0.30)
        ttk.Spinbox(time_f, from_=0.05, to=60.0, increment=0.05, textvariable=self.poll_var, width=5).pack(side=tk.LEFT, padx=(2, 8))

        tk.Label(time_f, text="Cooldown:", bg="#1e293b", fg="#cbd5e1", font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self.cooldown_var = tk.DoubleVar(value=1.00)
        ttk.Spinbox(time_f, from_=0.0, to=3600.0, increment=0.25, textvariable=self.cooldown_var, width=5).pack(side=tk.LEFT, padx=(2, 8))

        tk.Label(time_f, text="Mode:", bg="#1e293b", fg="#cbd5e1", font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self.mode_var = tk.StringVar(value="continuous")
        ttk.Combobox(time_f, textvariable=self.mode_var, values=["continuous", "once", "count"], state="readonly", width=8).pack(side=tk.LEFT)

    def _build_right_panel(self, parent: tk.Frame) -> None:
        right = tk.Frame(parent, bg="#1e293b", padx=12, pady=10, highlightbackground="#334155", highlightthickness=1)
        right.grid(row=0, column=1, sticky="nsew", padx=(5, 0))

        # Header indicating which object's sequence is being edited
        top_bar = tk.Frame(right, bg="#1e293b")
        top_bar.pack(fill=tk.X)
        self.seq_header_lbl = tk.Label(
            top_bar,
            text="2. Click Sequence for: [Target Object]",
            bg="#1e293b",
            fg="#38bdf8",
            font=("Segoe UI", 11, "bold"),
        )
        self.seq_header_lbl.pack(side=tk.LEFT)

        # Toggle: Use custom sequence for this object
        self.custom_seq_var = tk.BooleanVar(value=True)
        self.custom_seq_chk = tk.Checkbutton(
            top_bar,
            text="Custom Sequence for this Target",
            variable=self.custom_seq_var,
            command=self._on_custom_seq_toggle,
            bg="#1e293b",
            fg="#cbd5e1",
            selectcolor="#0f172a",
            font=("Segoe UI", 8, "bold"),
        )
        self.custom_seq_chk.pack(side=tk.RIGHT)

        # Quick Add Toolbar
        add_bar = tk.Frame(right, bg="#1e293b")
        add_bar.pack(fill=tk.X, pady=(6, 4))

        tk.Button(
            add_bar,
            text="+ Click Detected Object",
            bg="#0284c7",
            fg="#ffffff",
            font=("Segoe UI", 8, "bold"),
            relief=tk.FLAT,
            padx=6,
            pady=4,
            command=self.add_click_detected_step,
        ).pack(side=tk.LEFT, padx=(0, 4))

        tk.Button(
            add_bar,
            text="+ Click ALL Instances",
            bg="#0369a1",
            fg="#ffffff",
            font=("Segoe UI", 8, "bold"),
            relief=tk.FLAT,
            padx=6,
            pady=4,
            command=self.add_click_all_instances_step,
        ).pack(side=tk.LEFT, padx=(0, 4))

        tk.Button(
            add_bar,
            text="+ Custom Step / Pattern...",
            bg="#334155",
            fg="#f8fafc",
            font=("Segoe UI", 8),
            relief=tk.FLAT,
            padx=6,
            pady=4,
            command=self.add_custom_step_dialog,
        ).pack(side=tk.LEFT, padx=(0, 4))

        tk.Button(
            add_bar,
            text="📋 Copy to All",
            bg="#334155",
            fg="#38bdf8",
            font=("Segoe UI", 8),
            relief=tk.FLAT,
            padx=6,
            pady=4,
            command=self.copy_sequence_to_all_targets,
        ).pack(side=tk.LEFT, padx=(0, 4))

        self.record_btn = tk.Button(
            add_bar,
            text="🔴 Record Clicks (F7)",
            bg="#dc2626",
            fg="#ffffff",
            font=("Segoe UI", 8, "bold"),
            relief=tk.FLAT,
            padx=8,
            pady=4,
            command=self.toggle_recording,
        )
        self.record_btn.pack(side=tk.RIGHT)

        # Sequence Treeview Table
        table_frame = tk.Frame(right, bg="#1e293b")
        table_frame.pack(fill=tk.BOTH, expand=True, pady=2)

        columns = ("idx", "target", "action", "pattern", "delay", "note")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("idx", text="#")
        self.tree.heading("target", text="Target / Location")
        self.tree.heading("action", text="Action")
        self.tree.heading("pattern", text="Click Pattern")
        self.tree.heading("delay", text="Delay")
        self.tree.heading("note", text="Note")

        self.tree.column("idx", width=30, anchor="center", stretch=False)
        self.tree.column("target", width=170, anchor="w")
        self.tree.column("action", width=110, anchor="w")
        self.tree.column("pattern", width=145, anchor="w")
        self.tree.column("delay", width=65, anchor="center", stretch=False)
        self.tree.column("note", width=110, anchor="w")

        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<Double-1>", lambda _e: self.edit_selected_step())

        # Step Manipulation Bar
        ctrl_bar = tk.Frame(right, bg="#1e293b")
        ctrl_bar.pack(fill=tk.X, pady=(6, 0))

        for label, cmd in [
            ("✏ Edit", self.edit_selected_step),
            ("▲ Up", lambda: self.move_selected_step(-1)),
            ("▼ Down", lambda: self.move_selected_step(1)),
            ("🗑 Delete", self.delete_selected_step),
            ("Clear", self.clear_all_steps),
        ]:
            tk.Button(
                ctrl_bar,
                text=label,
                bg="#334155",
                fg="#e2e8f0",
                relief=tk.FLAT,
                padx=6,
                pady=3,
                font=("Segoe UI", 8),
                command=cmd,
            ).pack(side=tk.LEFT, padx=(0, 4))

        tk.Button(
            ctrl_bar,
            text="▶ Dry-Run Sequence",
            bg="#0d9488",
            fg="#ffffff",
            font=("Segoe UI", 8, "bold"),
            relief=tk.FLAT,
            padx=8,
            pady=3,
            command=self.dry_run_sequence,
        ).pack(side=tk.RIGHT)

    def _build_bottom_panel(self) -> None:
        bottom = tk.Frame(self.root, bg="#1e293b", padx=12, pady=8, highlightbackground="#334155", highlightthickness=1)
        bottom.pack(fill=tk.X, padx=10, pady=(0, 8))

        ctrl_left = tk.Frame(bottom, bg="#1e293b")
        ctrl_left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 12))

        self.start_btn = tk.Button(
            ctrl_left,
            text="▶ START AUTO CLICKER (F6)",
            bg="#10b981",
            fg="#ffffff",
            font=("Segoe UI", 11, "bold"),
            relief=tk.FLAT,
            width=26,
            pady=8,
            command=self.toggle_engine,
        )
        self.start_btn.pack(pady=(0, 4))

        status_row = tk.Frame(ctrl_left, bg="#1e293b")
        status_row.pack(fill=tk.X)
        self.status_badge = tk.Label(status_row, text="● IDLE", bg="#1e293b", fg="#94a3b8", font=("Segoe UI", 10, "bold"))
        self.status_badge.pack(side=tk.LEFT)

        self.counter_lbl = tk.Label(status_row, text="Triggers: 0", bg="#1e293b", fg="#38bdf8", font=("Consolas", 10, "bold"))
        self.counter_lbl.pack(side=tk.RIGHT)

        # Activity Log
        log_frame = tk.Frame(bottom, bg="#0f172a")
        log_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(
            log_frame,
            height=4,
            bg="#0f172a",
            fg="#cbd5e1",
            font=("Consolas", 9),
            relief=tk.FLAT,
            state=tk.DISABLED,
            padx=8,
            pady=4,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

    # -------------------------------------------------------------------------
    # Logging & Helpers
    # -------------------------------------------------------------------------

    def log(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{ts}] {message}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def log_threadsafe(self, message: str) -> None:
        self.root.after(0, lambda: self.log(message))

    def _sync_ui_to_profile(self) -> None:
        self.profile.multi_target_mode = self.multi_rule_var.get()
        self.profile.pattern_order = self.pattern_order_var.get()
        self.profile.poll_interval = max(0.05, float(self.poll_var.get()))
        self.profile.cooldown = max(0.0, float(self.cooldown_var.get()))
        self.profile.trigger_mode = self.mode_var.get()

    def _sync_profile_to_ui(self) -> None:
        self.multi_rule_var.set(self.profile.multi_target_mode)
        self.pattern_order_var.set(self.profile.pattern_order)
        self.poll_var.set(self.profile.poll_interval)
        self.cooldown_var.set(self.profile.cooldown)
        self.mode_var.set(self.profile.trigger_mode)
        self._refresh_targets_table()
        self._refresh_sequence_table()

    # -------------------------------------------------------------------------
    # Target Objects Management (Multiple Objects Support)
    # -------------------------------------------------------------------------

    def _get_current_target(self) -> Optional[TargetObject]:
        if 0 <= self.selected_target_idx < len(self.profile.targets):
            return self.profile.targets[self.selected_target_idx]
        return None

    def _get_active_sequence(self) -> List[ClickStep]:
        """Returns the click sequence currently active for editing (selected target's custom or profile shared)."""
        t = self._get_current_target()
        if t is not None and t.use_custom_sequence:
            return t.sequence
        return self.profile.sequence

    def _refresh_targets_table(self) -> None:
        for item in self.targets_tree.get_children():
            self.targets_tree.delete(item)

        for i, t in enumerate(self.profile.targets):
            act_str = "✓" if t.enabled else "✗"
            steps_str = f"{len(t.sequence)} step(s)" if t.use_custom_sequence else "Shared"
            self.targets_tree.insert(
                "",
                tk.END,
                iid=str(i),
                values=(act_str, t.name, f"{t.confidence * 100:.0f}%", t.max_instances, steps_str),
            )

        self.targets_badge.config(text=f"1. Target Objects ({len(self.profile.targets)} configured)")

        if self.profile.targets:
            self.selected_target_idx = max(0, min(self.selected_target_idx, len(self.profile.targets) - 1))
            self.targets_tree.selection_set(str(self.selected_target_idx))
            self._display_target(self.profile.targets[self.selected_target_idx])
        else:
            self._display_target(None)

    def _on_target_selected(self, _event: Optional[tk.Event] = None) -> None:
        sel = self.targets_tree.selection()
        if not sel:
            return
        self.selected_target_idx = int(sel[0])
        self._display_target(self.profile.targets[self.selected_target_idx])
        self._refresh_sequence_table()

    def _display_target(self, target: Optional[TargetObject]) -> None:
        if target is None:
            self.target_name_var.set("")
            self.target_info_lbl.config(text="Template: None")
            self.preview_canvas.delete("all")
            self.seq_header_lbl.config(text="2. Click Sequence (Default Profile)")
            return

        self.target_name_var.set(target.name)
        self.conf_var.set(target.confidence)
        self.conf_val_lbl.config(text=f"{target.confidence * 100:.0f}%")
        self.max_inst_var.set(target.max_instances)
        self.grayscale_var.set(target.grayscale)
        self.multiscale_var.set(target.multi_scale)
        self.custom_seq_var.set(target.use_custom_sequence)

        if target.search_region:
            l, top, w, h = target.search_region
            self.region_lbl.config(text=f"Area: ({l}, {top}, {w}x{h})", fg="#38bdf8")
        else:
            self.region_lbl.config(text="Area: Full Screen", fg="#94a3b8")

        self._update_template_preview(target.image_path)
        self._update_seq_header()

    def _update_seq_header(self) -> None:
        t = self._get_current_target()
        if t:
            seq_type = "Custom Sequence" if t.use_custom_sequence else "Shared Profile Sequence"
            self.seq_header_lbl.config(text=f"2. Click Sequence for: [{t.name}] ({seq_type})")
        else:
            self.seq_header_lbl.config(text="2. Click Sequence (Default Profile)")

    def _on_custom_seq_toggle(self) -> None:
        t = self._get_current_target()
        if not t:
            return
        t.use_custom_sequence = bool(self.custom_seq_var.get())
        if t.use_custom_sequence and not t.sequence:
            t.sequence = default_click_sequence()
        self._update_seq_header()
        self._refresh_sequence_table()
        self._update_target_tree_row()
        self.log(f"Target '{t.name}' set to {'Custom Sequence' if t.use_custom_sequence else 'Shared Sequence'}.")

    def _update_target_tree_row(self) -> None:
        t = self._get_current_target()
        if not t:
            return
        iid = str(self.selected_target_idx)
        if self.targets_tree.exists(iid):
            act_str = "✓" if t.enabled else "✗"
            steps_str = f"{len(t.sequence)} step(s)" if t.use_custom_sequence else "Shared"
            self.targets_tree.item(iid, values=(act_str, t.name, f"{t.confidence * 100:.0f}%", t.max_instances, steps_str))

    def _on_target_field_updated(self) -> None:
        t = self._get_current_target()
        if t is None:
            return
        t.name = self.target_name_var.get().strip() or "Target Object"
        t.confidence = float(self.conf_var.get())
        t.max_instances = max(1, int(self.max_inst_var.get()))
        t.grayscale = bool(self.grayscale_var.get())
        t.multi_scale = bool(self.multiscale_var.get())
        self._update_target_tree_row()
        self._update_seq_header()

    def _on_conf_scale_moved(self, val: str) -> None:
        fval = float(val)
        self.conf_val_lbl.config(text=f"{fval * 100:.0f}%")
        t = self._get_current_target()
        if t:
            t.confidence = fval
            self._update_target_tree_row()

    def _update_template_preview(self, path: str) -> None:
        self.preview_canvas.delete("all")
        if not path or not os.path.isfile(path):
            self.preview_canvas.create_text(65, 38, text="No Image", fill="#64748b", font=("Segoe UI", 9))
            self.target_info_lbl.config(text="Template: None")
            return
        try:
            pil_img = Image.open(path)
            ow, oh = pil_img.size
            scale = min(120 / max(1, ow), 70 / max(1, oh), 2.0)
            dw = max(1, int(ow * scale))
            dh = max(1, int(oh * scale))
            resized = pil_img.resize((dw, dh), Image.Resampling.LANCZOS)
            self._preview_photo = ImageTk.PhotoImage(resized)
            self.preview_canvas.create_image(65, 38, anchor=tk.CENTER, image=self._preview_photo)
            self.target_info_lbl.config(text=f"{os.path.basename(path)} ({ow}x{oh})")
        except Exception as e:
            self.log(f"Preview error: {e}")

    def add_target_snipped(self) -> None:
        if self.is_running:
            self.stop_engine()
        self.root.withdraw()
        self.root.after(280, lambda: ScreenSnipperOverlay(self.root, self._on_new_target_snipped))

    def _on_new_target_snipped(self, img: Optional[Image.Image], bbox: Optional[Tuple[int, int, int, int]]) -> None:
        self.root.deiconify()
        if img is None or bbox is None:
            self.log("Target snip cancelled.")
            return

        idx = len(self.profile.targets) + 1
        filename = f"target_{idx}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        save_path = os.path.join(self.templates_dir, filename)
        img.save(save_path)

        new_t = TargetObject(
            name=f"Object {idx}",
            image_path=save_path,
            confidence=0.85,
            max_instances=1,
            use_custom_sequence=True,
            sequence=default_click_sequence(),
        )
        self.profile.targets.append(new_t)
        self.selected_target_idx = len(self.profile.targets) - 1
        self._refresh_targets_table()
        self._refresh_sequence_table()
        self.log(f"Added Target #{idx} '{new_t.name}' with its own click sequence.")

    def add_target_from_file(self) -> None:
        filepath = filedialog.askopenfilename(
            title="Select Target Template Image",
            initialdir=self.templates_dir,
            filetypes=[("Image Files", "*.png;*.jpg;*.jpeg;*.bmp"), ("All Files", "*.*")],
        )
        if filepath:
            idx = len(self.profile.targets) + 1
            base_name = os.path.splitext(os.path.basename(filepath))[0]
            new_t = TargetObject(
                name=base_name,
                image_path=filepath,
                confidence=0.85,
                max_instances=1,
                use_custom_sequence=True,
                sequence=default_click_sequence(),
            )
            self.profile.targets.append(new_t)
            self.selected_target_idx = len(self.profile.targets) - 1
            self._refresh_targets_table()
            self._refresh_sequence_table()
            self.log(f"Added Target #{idx} '{base_name}' from file with custom sequence.")

    def toggle_target_active(self) -> None:
        t = self._get_current_target()
        if not t:
            return
        t.enabled = not t.enabled
        self._update_target_tree_row()
        self.log(f"Target '{t.name}' set to {'Active' if t.enabled else 'Disabled'}.")

    def remove_target(self) -> None:
        if not self.profile.targets:
            return
        t = self.profile.targets[self.selected_target_idx]
        if messagebox.askyesno("Remove Target", f"Remove target '{t.name}'?"):
            del self.profile.targets[self.selected_target_idx]
            self.selected_target_idx = max(0, self.selected_target_idx - 1)
            self._refresh_targets_table()
            self._refresh_sequence_table()

    def select_search_region(self) -> None:
        t = self._get_current_target()
        if not t:
            return
        self.root.withdraw()
        self.root.after(
            280,
            lambda: ScreenSnipperOverlay(
                self.root,
                self._on_region_selected,
                prompt_text=f"Drag a box to restrict search area for '{t.name}' (ESC to cancel)",
            ),
        )

    def _on_region_selected(self, _img: Optional[Image.Image], bbox: Optional[Tuple[int, int, int, int]]) -> None:
        self.root.deiconify()
        t = self._get_current_target()
        if t and bbox:
            t.search_region = bbox
            l, top, w, h = bbox
            self.region_lbl.config(text=f"Area: ({l}, {top}, {w}x{h})", fg="#38bdf8")
            self.log(f"Search area for '{t.name}' set to ({l}, {top}, {w}x{h}).")

    def reset_search_region(self) -> None:
        t = self._get_current_target()
        if t:
            t.search_region = None
            self.region_lbl.config(text="Area: Full Screen", fg="#94a3b8")
            self.log(f"Search area for '{t.name}' reset to Full Screen.")

    def test_detection_now(self) -> None:
        self._sync_ui_to_profile()
        enabled = [t for t in self.profile.targets if t.enabled and t.image_path and os.path.isfile(t.image_path)]
        if not enabled:
            messagebox.showwarning("No Active Targets", "Please configure and enable at least one target object image.")
            return

        cur_pos = pyautogui.position()
        res = self.detector.detect_multiple_targets(
            self.profile.targets,
            multi_target_mode=self.profile.multi_target_mode,
            pattern_order=self.profile.pattern_order,
            cursor_pos=(cur_pos.x, cur_pos.y),
        )
        self.last_detection = res

        if res.found:
            self.log(f"✅ Found {len(res.matches)} match(es) across {len(enabled)} target(s)!")
            for idx, m in enumerate(res.matches, start=1):
                # find target sequence count
                t_obj = next((t for t in self.profile.targets if t.id == m.target_id or t.name == m.target_name), None)
                seq_len = len(self.profile.get_sequence_for_target(t_obj))
                self.log(f"   #{idx} [{m.target_name}] ({m.confidence * 100:.1f}%) at ({m.center_x}, {m.center_y}) -> Sequence: {seq_len} step(s)")
            flash_multiple_boxes(self.root, res.matches)
        else:
            self.log(f"❌ No matching targets detected on screen (Strategy: '{self.profile.multi_target_mode}').")

    # -------------------------------------------------------------------------
    # Per-Object Click Sequence & Patterns
    # -------------------------------------------------------------------------

    def _refresh_sequence_table(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)

        seq = self._get_active_sequence()
        for i, step in enumerate(seq, start=1):
            self.tree.insert(
                "",
                tk.END,
                iid=str(i - 1),
                values=(
                    i,
                    step.summary_target(),
                    step.summary_action(),
                    step.summary_pattern(),
                    f"{step.delay_after:.2f}s",
                    step.note,
                ),
            )
        self._update_target_tree_row()

    def copy_sequence_to_all_targets(self) -> None:
        t = self._get_current_target()
        if not t:
            return
        if not messagebox.askyesno(
            "Copy Sequence",
            f"Copy '{t.name}'s click sequence ({len(t.sequence)} steps) to all other target objects?",
        ):
            return
        for other in self.profile.targets:
            if other.id != t.id:
                other.use_custom_sequence = True
                other.sequence = copy.deepcopy(t.sequence)
        self._refresh_targets_table()
        self.log(f"Copied '{t.name}'s sequence to all other targets.")

    def add_click_detected_step(self) -> None:
        seq = self._get_active_sequence()
        step = ClickStep(
            step_type="click_detected",
            x=0,
            y=0,
            button="left",
            clicks=1,
            delay_after=0.35,
            pattern_type="point",
            note="Click detected object",
        )
        seq.append(step)
        self._refresh_sequence_table()
        self.log("Added 'Click Detected Object (Point)' step.")

    def add_click_all_instances_step(self) -> None:
        seq = self._get_active_sequence()
        step = ClickStep(
            step_type="click_detected",
            x=0,
            y=0,
            button="left",
            clicks=1,
            delay_after=0.25,
            pattern_type="all_instances",
            pattern_order=self.pattern_order_var.get(),
            note="Click all detected instances in pattern order",
        )
        seq.append(step)
        self._refresh_sequence_table()
        self.log(f"Added 'Click ALL Instances' step with pattern order '{step.pattern_order}'.")

    def add_custom_step_dialog(self) -> None:
        seq = self._get_active_sequence()
        t_names = [t.name for t in self.profile.targets]
        dlg = StepEditorDialog(self.root, title="Add Custom Sequence Step / Click Pattern", target_names=t_names)
        self.root.wait_window(dlg)
        if dlg.result:
            seq.append(dlg.result)
            self._refresh_sequence_table()
            self.log(f"Added step: {dlg.result.summary_target()} -> {dlg.result.summary_pattern()}")

    def edit_selected_step(self) -> None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Select a Step", "Please select a step in the sequence table to edit.")
            return
        idx = int(sel[0])
        seq = self._get_active_sequence()
        if idx >= len(seq):
            return
        t_names = [t.name for t in self.profile.targets]
        dlg = StepEditorDialog(self.root, step=seq[idx], title=f"Edit Step #{idx + 1}", target_names=t_names)
        self.root.wait_window(dlg)
        if dlg.result:
            seq[idx] = dlg.result
            self._refresh_sequence_table()
            self.tree.selection_set(str(idx))

    def move_selected_step(self, delta: int) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        seq = self._get_active_sequence()
        new_idx = idx + delta
        if 0 <= new_idx < len(seq):
            seq[idx], seq[new_idx] = seq[new_idx], seq[idx]
            self._refresh_sequence_table()
            self.tree.selection_set(str(new_idx))

    def delete_selected_step(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        seq = self._get_active_sequence()
        if idx < len(seq):
            del seq[idx]
            self._refresh_sequence_table()

    def clear_all_steps(self) -> None:
        t = self._get_current_target()
        name = t.name if t else "Active Profile"
        if messagebox.askyesno("Clear Sequence", f"Remove all steps from the sequence for '{name}'?"):
            seq = self._get_active_sequence()
            seq.clear()
            self._refresh_sequence_table()

    def toggle_recording(self) -> None:
        if self.recorder.is_recording:
            self.recorder.stop()
            return
        if self.is_running:
            self.stop_engine()

        self.status_badge.config(text="● RECORDING CLICKS (ESC or F7 to finish)", fg="#f87171")
        self.record_btn.config(text="⏹ Stop Recording (F7)")
        t = self._get_current_target()
        name = t.name if t else "Profile"
        self.log(f"Recording live clicks for '{name}'... Press ESC or F7 when finished.")
        self.root.iconify()
        self.root.after(250, self.recorder.start)

    def _on_recording_finished(self, recorded_steps: List[ClickStep]) -> None:
        def _finish_ui() -> None:
            self.root.deiconify()
            self.status_badge.config(text="● IDLE", fg="#94a3b8")
            self.record_btn.config(text="🔴 Record Clicks (F7)")
            if recorded_steps:
                seq = self._get_active_sequence()
                seq.extend(recorded_steps)
                self._refresh_sequence_table()
                self.log(f"Appended {len(recorded_steps)} recorded click step(s).")
            else:
                self.log("Recording stopped (no clicks captured).")

        self.root.after(0, _finish_ui)

    def dry_run_sequence(self) -> None:
        seq = self._get_active_sequence()
        if not seq:
            messagebox.showwarning("Empty Sequence", "Add at least one step to this sequence first.")
            return
        t = self._get_current_target()
        name = t.name if t else "Object"
        det = self.last_detection or DetectionResult(
            found=True,
            confidence=1.0,
            center_x=self.root.winfo_screenwidth() // 2,
            center_y=self.root.winfo_screenheight() // 2,
            target_name=name,
        )
        self.log(f"Dry-running sequence for '{name}' ({len(seq)} steps) at ({det.center_x}, {det.center_y})...")
        self.stop_event.clear()
        threading.Thread(
            target=lambda: self.executor.execute_sequence(seq, det, self.stop_event),
            daemon=True,
        ).start()

    # -------------------------------------------------------------------------
    # Detection & Execution Engine Loop
    # -------------------------------------------------------------------------

    def toggle_engine(self) -> None:
        if self.is_running:
            self.stop_engine()
        else:
            self.start_engine()

    def start_engine(self) -> None:
        self._sync_ui_to_profile()
        enabled = [t for t in self.profile.targets if t.enabled and t.image_path and os.path.isfile(t.image_path)]
        if not enabled:
            messagebox.showwarning("Missing Targets", "Please configure and enable at least one target object image.")
            return

        self.is_running = True
        self.trigger_count = 0
        self.stop_event.clear()
        self.start_btn.config(text="⏹ STOP AUTO CLICKER (F6)", bg="#ef4444")
        self.status_badge.config(text="● MONITORING SCREEN", fg="#10b981")
        self.counter_lbl.config(text="Triggers: 0")
        self.log(f"Started monitoring {len(enabled)} target(s) with per-object click sequences.")

        self.worker_thread = threading.Thread(target=self._engine_loop, daemon=True)
        self.worker_thread.start()

    def stop_engine(self) -> None:
        if not self.is_running:
            return
        self.stop_event.set()
        self.is_running = False
        self.start_btn.config(text="▶ START AUTO CLICKER (F6)", bg="#10b981")
        self.status_badge.config(text="● IDLE", fg="#94a3b8")
        self.log("Stopped monitoring.")

    def emergency_stop(self) -> None:
        self.stop_event.set()
        if self.recorder.is_recording:
            self.recorder.stop()
        if self.is_running:
            self.stop_engine()
        self.log("🚨 EMERGENCY STOP (F8) triggered.")

    def _engine_loop(self) -> None:
        try:
            while not self.stop_event.is_set():
                cur_pos = pyautogui.position()
                result = self.detector.detect_multiple_targets(
                    self.profile.targets,
                    multi_target_mode=self.profile.multi_target_mode,
                    pattern_order=self.profile.pattern_order,
                    cursor_pos=(cur_pos.x, cur_pos.y),
                )

                if result.found and result.matches:
                    self.last_detection = result
                    self.trigger_count += 1
                    trig_num = self.trigger_count

                    # Group matches by target_id so each distinct object executes its own sequence
                    matches_by_target = {}
                    for m in result.matches:
                        matches_by_target.setdefault(m.target_id, []).append(m)

                    self.root.after(
                        0,
                        lambda n=trig_num, targets=list(matches_by_target.keys()), tot=len(result.matches): (
                            self.counter_lbl.config(text=f"Triggers: {n}"),
                            self.status_badge.config(text="● EXECUTING SEQUENCES", fg="#f59e0b"),
                            self.log(f"🎯 Trigger #{n}: Detected {len(targets)} object type(s) ({tot} total matches)."),
                        ),
                    )

                    all_completed = True
                    for tid, t_matches in matches_by_target.items():
                        if self.stop_event.is_set():
                            all_completed = False
                            break

                        t_obj = next((t for t in self.profile.targets if t.id == tid), None)
                        target_seq = self.profile.get_sequence_for_target(t_obj)
                        target_name = t_obj.name if t_obj else (t_matches[0].target_name if t_matches else "Object")

                        self.log_threadsafe(
                            f"  -> Running custom sequence for '{target_name}' ({len(target_seq)} step(s), {len(t_matches)} instance(s))..."
                        )

                        # Sub-detection result specific to this target's matches
                        sub_det = DetectionResult(
                            found=True,
                            confidence=max(m.confidence for m in t_matches),
                            center_x=t_matches[0].center_x,
                            center_y=t_matches[0].center_y,
                            bbox=t_matches[0].bbox,
                            matches=t_matches,
                            target_name=target_name,
                            target_id=tid,
                        )

                        completed = self.executor.execute_sequence(target_seq, sub_det, self.stop_event)
                        if not completed:
                            all_completed = False
                            break

                    if not all_completed or self.stop_event.is_set():
                        break

                    if self.profile.trigger_mode == "once":
                        self.log_threadsafe("Trigger mode 'once' finished. Stopping engine.")
                        self.root.after(0, self.stop_engine)
                        break
                    elif self.profile.trigger_mode == "count" and self.trigger_count >= self.profile.max_triggers:
                        self.log_threadsafe(f"Reached max triggers limit ({self.profile.max_triggers}). Stopping.")
                        self.root.after(0, self.stop_engine)
                        break

                    self.root.after(0, lambda: self.status_badge.config(text="● MONITORING SCREEN", fg="#10b981"))
                    if self.profile.cooldown > 0:
                        if self.stop_event.wait(timeout=self.profile.cooldown):
                            break
                else:
                    if self.stop_event.wait(timeout=self.profile.poll_interval):
                        break
        except pyautogui.FailSafeException:
            self.log_threadsafe("🚨 PyAutoGUI Fail-Safe triggered (mouse in screen corner). Stopping.")
            self.root.after(0, self.stop_engine)
        except Exception as exc:
            self.log_threadsafe(f"Engine error: {exc}")
            self.root.after(0, self.stop_engine)

    # -------------------------------------------------------------------------
    # Profile Save / Load / Admin Elevation
    # -------------------------------------------------------------------------

    def save_profile_dialog(self) -> None:
        self._sync_ui_to_profile()
        filepath = filedialog.asksaveasfilename(
            title="Save Automation Profile",
            initialdir=self.profiles_dir,
            defaultextension=".json",
            filetypes=[("JSON Profile", "*.json")],
        )
        if filepath:
            self.profile.name = os.path.splitext(os.path.basename(filepath))[0]
            self.profile.save(filepath)
            self.log(f"Saved profile: {os.path.basename(filepath)}")

    def load_profile_dialog(self) -> None:
        filepath = filedialog.askopenfilename(
            title="Load Automation Profile",
            initialdir=self.profiles_dir,
            filetypes=[("JSON Profile", "*.json")],
        )
        if filepath:
            try:
                self.profile = AutomationProfile.load(filepath)
                self.selected_target_idx = 0
                self._sync_profile_to_ui()
                self.log(f"Loaded profile: {os.path.basename(filepath)}")
            except Exception as e:
                messagebox.showerror("Load Error", f"Failed to load profile:\n{e}")

    def restart_as_admin(self) -> None:
        if sys.platform != "win32":
            return
        script_path = os.path.abspath(__file__)
        params = f'"{script_path}"'
        ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, self.base_dir, 1)
        if int(ret) > 32:
            self._on_close()
        else:
            self.log("Administrator elevation was cancelled or failed.")

    def _on_close(self) -> None:
        self.stop_event.set()
        self.hotkeys.stop()
        self.root.destroy()


def main() -> None:
    enable_windows_dpi_awareness()
    root = tk.Tk()
    SmartAutoClickerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
