from __future__ import annotations
import sys
import time
import math
import random
import ctypes
from ctypes import wintypes
import threading
from typing import List, Callable, Optional, Tuple
import pyautogui
from pynput import mouse, keyboard

from models import ClickStep, MatchInstance
from detector import DetectionResult, sort_matches

# Enable PyAutoGUI fail-safe (moving cursor to top-left corner aborts)
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.01

# Win32 SendInput / mouse_event constants for reliable hardware-like clicking
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000
INPUT_MOUSE = 0


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("mi", MOUSEINPUT),
    ]


def _win32_send_mouse_flag(flags: int, dx: int = 0, dy: int = 0) -> None:
    """Send a low-level Win32 mouse input event via SendInput with mouse_event fallback."""
    inp = INPUT(
        type=INPUT_MOUSE,
        mi=MOUSEINPUT(
            dx=dx,
            dy=dy,
            mouseData=0,
            dwFlags=flags,
            time=0,
            dwExtraInfo=0,
        ),
    )
    sent = ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
    if sent == 0:
        ctypes.windll.user32.mouse_event(flags, dx, dy, 0, 0)


def _to_normalized_abs(x: int, y: int) -> Tuple[int, int]:
    """Convert pixel screen coordinates to Win32 0..65535 normalized absolute coordinates."""
    screen_w = max(1, ctypes.windll.user32.GetSystemMetrics(0) - 1)
    screen_h = max(1, ctypes.windll.user32.GetSystemMetrics(1) - 1)
    abs_x = int(round(x * 65535 / screen_w))
    abs_y = int(round(y * 65535 / screen_h))
    return abs_x, abs_y


def _check_failsafe() -> None:
    """Check if cursor was slammed into the top-left corner (0, 0) to abort."""
    pos = pyautogui.position()
    if pos.x <= 2 and pos.y <= 2:
        raise pyautogui.FailSafeException("PyAutoGUI fail-safe triggered from mouse moving to upper-left corner.")


class SequenceExecutor:
    """Executes a sequence of ClickSteps and advanced click patterns when targets are detected."""

    def __init__(self, log_callback: Optional[Callable[[str], None]] = None) -> None:
        self.log_callback = log_callback
        self._pynput_mouse = mouse.Controller()

    def _log(self, message: str) -> None:
        if self.log_callback:
            self.log_callback(message)

    @staticmethod
    def _interruptible_sleep(seconds: float, stop_event: threading.Event) -> bool:
        """Sleep for `seconds` while checking `stop_event`. Returns False if interrupted."""
        if seconds <= 0:
            return not stop_event.is_set()
        return not stop_event.wait(timeout=seconds)

    def execute_sequence(
        self,
        sequence: List[ClickStep],
        detection: DetectionResult,
        stop_event: threading.Event,
    ) -> bool:
        """
        Execute all steps in `sequence`.
        Supports multi-object targeting and click patterns (All instances, Jitter, Burst, Hold, Glide).
        """
        for idx, step in enumerate(sequence, start=1):
            if stop_event.is_set():
                return False

            _check_failsafe()

            # Handle step types
            if step.step_type == "click_detected":
                completed = self._execute_detected_click_step(idx, step, detection, stop_event)
                if not completed or stop_event.is_set():
                    return False

            elif step.step_type == "fixed_coords":
                completed = self._execute_point_click(step.x, step.y, step, stop_event)
                self._log(f"  [Step {idx}] {step.summary_action()} ({step.summary_pattern()}) at Screen ({step.x}, {step.y})")
                if not completed or stop_event.is_set():
                    return False

            elif step.step_type == "wait":
                self._log(f"  [Step {idx}] Waiting {step.delay_after:.2f}s...")
                if not self._interruptible_sleep(step.delay_after, stop_event):
                    return False
                continue

            elif step.step_type == "keypress":
                if step.key:
                    pyautogui.press(step.key)
                    self._log(f"  [Step {idx}] Pressed key '{step.key}'")

            if step.delay_after > 0:
                if not self._interruptible_sleep(step.delay_after, stop_event):
                    return False

        return True

    def _execute_detected_click_step(
        self,
        idx: int,
        step: ClickStep,
        detection: DetectionResult,
        stop_event: threading.Event,
    ) -> bool:
        """Executes a click_detected step taking into account target filters and patterns."""
        # 1. Filter matching instances
        matches = detection.matches
        if step.target_filter and step.target_filter != "any":
            filtered = [m for m in matches if m.target_id == step.target_filter or m.target_name.lower() == step.target_filter.lower()]
            if filtered:
                matches = filtered

        if not matches:
            # Fallback to single primary detection point
            matches = [
                MatchInstance(
                    target_id="",
                    target_name=detection.target_name or "Detected Object",
                    confidence=detection.confidence,
                    center_x=detection.center_x,
                    center_y=detection.center_y,
                    bbox=detection.bbox,
                )
            ]

        # 2. Check if clicking ALL instances pattern
        if step.pattern_type == "all_instances":
            cur_pos = pyautogui.position()
            ordered_matches = sort_matches(matches, step.pattern_order, reference_point=(cur_pos.x, cur_pos.y))
            self._log(f"  [Step {idx}] Pattern: Clicking ALL {len(ordered_matches)} instances in '{step.pattern_order}' order")

            for sub_i, match in enumerate(ordered_matches, start=1):
                if stop_event.is_set():
                    return False
                tx = match.center_x + step.x
                ty = match.center_y + step.y
                self._click_single_target(tx, ty, step, stop_event)
                self._log(f"    -> Instance #{sub_i} [{match.target_name}] at ({tx}, {ty})")
                if sub_i < len(ordered_matches):
                    # Micro delay between instances
                    delay = max(0.05, min(step.delay_after, 0.25))
                    if not self._interruptible_sleep(delay, stop_event):
                        return False
            return True

        # 3. Single target click with chosen click pattern
        primary_match = matches[0]
        tx = primary_match.center_x + step.x
        ty = primary_match.center_y + step.y
        self._execute_point_click(tx, ty, step, stop_event)
        self._log(f"  [Step {idx}] {step.summary_action()} ({step.summary_pattern()}) on [{primary_match.target_name}] at ({tx}, {ty})")
        return True

    def _execute_point_click(
        self,
        x: int,
        y: int,
        step: ClickStep,
        stop_event: threading.Event,
    ) -> bool:
        """Executes a click at (x, y) with the specific ClickPattern (jitter, burst, long_press, glide, point)."""
        target_x, target_y = x, y

        # Apply random Jitter pattern
        if step.pattern_type == "jitter" and step.jitter_radius > 0:
            rad = step.jitter_radius
            target_x += random.randint(-rad, rad)
            target_y += random.randint(-rad, rad)

        # Smooth Human Glide pattern
        if step.pattern_type == "glide" and step.glide_duration > 0:
            self._human_glide_cursor(target_x, target_y, step.glide_duration, stop_event)
            if stop_event.is_set():
                return False

        # Rapid Burst pattern
        if step.pattern_type == "burst":
            burst_count = max(1, step.burst_count)
            burst_delay = max(0.01, step.burst_delay)
            for b in range(burst_count):
                if stop_event.is_set():
                    return False
                self._click_single_target(target_x, target_y, step, stop_event, hold_time=0.02)
                if b + 1 < burst_count:
                    time.sleep(burst_delay)
            return True

        # Long Press pattern
        if step.pattern_type == "long_press":
            hold = max(0.05, step.hold_duration)
            self._click_single_target(target_x, target_y, step, stop_event, hold_time=hold)
            return True

        # Standard precision click
        self._click_single_target(target_x, target_y, step, stop_event, hold_time=0.045)
        return True

    def _human_glide_cursor(
        self,
        target_x: int,
        target_y: int,
        duration: float,
        stop_event: threading.Event,
    ) -> None:
        """Smoothly glides mouse cursor using an eased cubic trajectory for human-like movement."""
        start_pos = pyautogui.position()
        sx, sy = start_pos.x, start_pos.y
        dist = math.hypot(target_x - sx, target_y - sy)
        if dist < 5:
            return

        steps = max(6, int(duration * 60))
        step_delay = duration / steps

        # Subtle perpendicular arc curve
        curve_intensity = min(30.0, dist * 0.10) * (random.choice([-1, 1]))
        mid_x = (sx + target_x) / 2.0
        mid_y = (sy + target_y) / 2.0
        dx = target_x - sx
        dy = target_y - sy
        len_d = max(1.0, math.hypot(dx, dy))
        ctrl_x = mid_x - (dy / len_d) * curve_intensity
        ctrl_y = mid_y + (dx / len_d) * curve_intensity

        for i in range(1, steps + 1):
            if stop_event.is_set():
                break
            _check_failsafe()
            t = i / steps
            # Smooth cubic ease
            ease_t = 3 * (t ** 2) - 2 * (t ** 3)
            # Quadratic Bezier
            u = 1 - ease_t
            curr_x = int(u * u * sx + 2 * u * ease_t * ctrl_x + ease_t * ease_t * target_x)
            curr_y = int(u * u * sy + 2 * u * ease_t * ctrl_y + ease_t * ease_t * target_y)

            if sys.platform == "win32":
                ctypes.windll.user32.SetCursorPos(curr_x, curr_y)
            else:
                self._pynput_mouse.position = (curr_x, curr_y)
            time.sleep(step_delay)

    def _click_single_target(
        self,
        x: int,
        y: int,
        step: ClickStep,
        stop_event: threading.Event,
        hold_time: float = 0.045,
    ) -> None:
        """Low-level hardware click at (x, y) with reliable button down/up hold time."""
        ix, iy = int(x), int(y)
        num_clicks = 2 if step.button == "double" else max(1, int(step.clicks))
        btn_type = "left" if step.button == "double" else (step.button if step.button in ("left", "right", "middle") else "left")

        if sys.platform == "win32":
            abs_x, abs_y = _to_normalized_abs(ix, iy)

            # 1. Move cursor & emit absolute move + relative nudge
            ctypes.windll.user32.SetCursorPos(ix, iy)
            _win32_send_mouse_flag(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, abs_x, abs_y)
            _win32_send_mouse_flag(MOUSEEVENTF_MOVE, 1, 0)
            ctypes.windll.user32.SetCursorPos(ix, iy)

            # 2. Settle time for target window hover
            time.sleep(0.035)

            flag_map = {
                "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
                "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
                "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
            }
            down_flag, up_flag = flag_map[btn_type]

            # 3. Execute click(s)
            for i in range(num_clicks):
                if stop_event.is_set():
                    break
                _check_failsafe()
                ctypes.windll.user32.SetCursorPos(ix, iy)
                _win32_send_mouse_flag(down_flag | MOUSEEVENTF_ABSOLUTE, abs_x, abs_y)
                time.sleep(hold_time)
                _win32_send_mouse_flag(up_flag | MOUSEEVENTF_ABSOLUTE, abs_x, abs_y)
                if i + 1 < num_clicks:
                    time.sleep(0.065)
        else:
            self._pynput_mouse.position = (ix, iy)
            time.sleep(0.035)
            btn_map = {
                "left": mouse.Button.left,
                "right": mouse.Button.right,
                "middle": mouse.Button.middle,
            }
            pbtn = btn_map[btn_type]
            for i in range(num_clicks):
                if stop_event.is_set():
                    break
                self._pynput_mouse.press(pbtn)
                time.sleep(hold_time)
                self._pynput_mouse.release(pbtn)
                if i + 1 < num_clicks:
                    time.sleep(0.065)


class LiveMouseRecorder:
    """Records live user mouse clicks on screen and converts them into ClickStep objects."""

    def __init__(self, on_finished: Callable[[List[ClickStep]], None]) -> None:
        self.on_finished = on_finished
        self._recording = False
        self._recorded_events: List[Tuple[float, int, int, str]] = []
        self._mouse_listener: Optional[mouse.Listener] = None
        self._key_listener: Optional[keyboard.Listener] = None
        self._lock = threading.Lock()

    @property
    def is_recording(self) -> bool:
        return self._recording

    def start(self) -> None:
        with self._lock:
            if self._recording:
                return
            self._recording = True
            self._recorded_events.clear()

        self._mouse_listener = mouse.Listener(on_click=self._on_click)
        self._key_listener = keyboard.Listener(on_press=self._on_key_press)
        self._mouse_listener.start()
        self._key_listener.start()

    def stop(self) -> None:
        with self._lock:
            if not self._recording:
                return
            self._recording = False

        if self._mouse_listener:
            self._mouse_listener.stop()
            self._mouse_listener = None
        if self._key_listener:
            self._key_listener.stop()
            self._key_listener = None

        steps = self._build_steps()
        self.on_finished(steps)

    def _on_click(self, x: int, y: int, button: mouse.Button, pressed: bool) -> None:
        if not pressed or not self._recording:
            return
        btn_map = {
            mouse.Button.left: "left",
            mouse.Button.right: "right",
            mouse.Button.middle: "middle",
        }
        btn_name = btn_map.get(button, "left")
        self._recorded_events.append((time.perf_counter(), int(x), int(y), btn_name))

    def _on_key_press(self, key: keyboard.Key | keyboard.KeyCode) -> None:
        if key in (keyboard.Key.esc, keyboard.Key.f7):
            threading.Thread(target=self.stop, daemon=True).start()

    def _build_steps(self) -> List[ClickStep]:
        steps: List[ClickStep] = []
        n = len(self._recorded_events)
        for i, (ts, x, y, btn) in enumerate(self._recorded_events):
            if i + 1 < n:
                next_ts = self._recorded_events[i + 1][0]
                delay = round(max(0.05, min(10.0, next_ts - ts)), 2)
            else:
                delay = 0.30
            steps.append(
                ClickStep(
                    step_type="fixed_coords",
                    x=x,
                    y=y,
                    button=btn,
                    clicks=1,
                    delay_after=delay,
                    pattern_type="point",
                    note=f"Recorded {btn} click #{i + 1}",
                )
            )
        return steps


class GlobalHotkeyManager:
    """Listens globally for F6 (Toggle Start/Stop), F7 (Record Clicks), F8 (Emergency Stop)."""

    def __init__(
        self,
        on_toggle_engine: Callable[[], None],
        on_toggle_record: Callable[[], None],
        on_emergency_stop: Callable[[], None],
    ) -> None:
        self.on_toggle_engine = on_toggle_engine
        self.on_toggle_record = on_toggle_record
        self.on_emergency_stop = on_emergency_stop
        self._listener = keyboard.Listener(on_press=self._on_press)
        self._listener.daemon = True
        self._listener.start()

    def _on_press(self, key: keyboard.Key | keyboard.KeyCode) -> None:
        try:
            if key == keyboard.Key.f6:
                self.on_toggle_engine()
            elif key == keyboard.Key.f7:
                self.on_toggle_record()
            elif key == keyboard.Key.f8:
                self.on_emergency_stop()
        except Exception:
            pass

    def stop(self) -> None:
        try:
            self._listener.stop()
        except Exception:
            pass
