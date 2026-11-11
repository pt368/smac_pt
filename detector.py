from __future__ import annotations
import os
import random
from dataclasses import dataclass, field
from typing import Optional, Tuple, Callable, List
import tkinter as tk
import numpy as np
import cv2
from PIL import Image, ImageGrab, ImageTk

try:
    import mss
    HAS_MSS = True
except ImportError:
    HAS_MSS = False

from models import TargetObject, MatchInstance


@dataclass
class DetectionResult:
    found: bool
    confidence: float
    center_x: int = 0
    center_y: int = 0
    bbox: Tuple[int, int, int, int] = (0, 0, 0, 0)  # (left, top, width, height)
    matches: List[MatchInstance] = field(default_factory=list)
    target_name: str = ""
    target_id: str = ""


def non_max_suppression(
    boxes: List[Tuple[int, int, int, int]], scores: List[float], overlap_thresh: float = 0.35
) -> List[int]:
    """Pure numpy Non-Maximum Suppression to remove overlapping duplicate detections."""
    if not boxes:
        return []
    x1 = np.array([b[0] for b in boxes], dtype=float)
    y1 = np.array([b[1] for b in boxes], dtype=float)
    x2 = np.array([b[0] + b[2] for b in boxes], dtype=float)
    y2 = np.array([b[1] + b[3] for b in boxes], dtype=float)
    sc = np.array(scores, dtype=float)
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = sc.argsort()[::-1]

    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])

        w = np.maximum(0.0, xx2 - xx1 + 1)
        h = np.maximum(0.0, yy2 - yy1 + 1)
        inter = w * h
        ovr = inter / (areas[i] + areas[order[1:]] - inter)

        inds = np.where(ovr <= overlap_thresh)[0]
        order = order[inds + 1]

    return keep


def sort_matches(
    matches: List[MatchInstance],
    pattern_order: str,
    reference_point: Optional[Tuple[int, int]] = None,
) -> List[MatchInstance]:
    """Sort detected match instances according to the chosen pattern order."""
    if pattern_order == "top_to_bottom":
        return sorted(matches, key=lambda m: (m.center_y, m.center_x))
    elif pattern_order == "bottom_to_top":
        return sorted(matches, key=lambda m: (-m.center_y, m.center_x))
    elif pattern_order == "left_to_right":
        return sorted(matches, key=lambda m: (m.center_x, m.center_y))
    elif pattern_order == "right_to_left":
        return sorted(matches, key=lambda m: (-m.center_x, m.center_y))
    elif pattern_order == "nearest":
        rx, ry = reference_point if reference_point else (0, 0)
        return sorted(matches, key=lambda m: (m.center_x - rx) ** 2 + (m.center_y - ry) ** 2)
    elif pattern_order == "random":
        shuffled = list(matches)
        random.shuffle(shuffled)
        return shuffled
    return matches


class ScreenDetector:
    """Captures the screen and locates single or multiple template images using OpenCV."""

    def __init__(self) -> None:
        self._cache = {}  # path -> (mtime, bgr_image)

    def _load_template(self, image_path: str) -> Optional[np.ndarray]:
        if not image_path or not os.path.isfile(image_path):
            return None
        try:
            mtime = os.path.getmtime(image_path)
            cached = self._cache.get(image_path)
            if cached and cached[0] == mtime:
                return cached[1]

            # Support Unicode file paths on Windows
            raw = np.fromfile(image_path, dtype=np.uint8)
            img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
            if img is not None:
                self._cache[image_path] = (mtime, img)
            return img
        except Exception:
            return None

    @staticmethod
    def capture_screen_bgr(region: Optional[Tuple[int, int, int, int]] = None) -> Tuple[np.ndarray, int, int]:
        """
        Capture the screen or a (left, top, width, height) region.
        Returns (bgr_image, offset_left, offset_top).
        """
        if HAS_MSS:
            with mss.mss() as sct:
                if region is not None:
                    left, top, width, height = region
                    monitor = {"left": int(left), "top": int(top), "width": int(width), "height": int(height)}
                else:
                    monitor = sct.monitors[1]
                shot = sct.grab(monitor)
                img_bgra = np.array(shot)
                img_bgr = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2BGR)
                return img_bgr, monitor["left"], monitor["top"]
        else:
            if region is not None:
                left, top, width, height = region
                bbox = (left, top, left + width, top + height)
                pil_img = ImageGrab.grab(bbox=bbox)
                offset_x, offset_y = left, top
            else:
                pil_img = ImageGrab.grab()
                offset_x, offset_y = 0, 0
            img_rgb = np.array(pil_img)
            img_bgr = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
            return img_bgr, offset_x, offset_y

    def find_all_instances(
        self,
        target: TargetObject,
        screen_bgr: Optional[np.ndarray] = None,
        screen_offset: Tuple[int, int] = (0, 0),
    ) -> List[MatchInstance]:
        """
        Locates all instances of `target` on screen matching its confidence threshold.
        Applies Non-Maximum Suppression (NMS) to eliminate duplicate overlapping boxes.
        """
        template_bgr = self._load_template(target.image_path)
        if template_bgr is None:
            return []

        if screen_bgr is None or target.search_region is not None:
            screen_bgr, off_x, off_y = self.capture_screen_bgr(target.search_region)
        else:
            off_x, off_y = screen_offset

        if target.grayscale:
            screen_proc = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)
            template_proc = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)
        else:
            screen_proc = screen_bgr
            template_proc = template_bgr

        sh, sw = screen_proc.shape[:2]
        th, tw = template_proc.shape[:2]

        if th > sh or tw > sw or th < 4 or tw < 4:
            return []

        scales = (0.80, 0.90, 0.95, 1.00, 1.05, 1.10, 1.20) if target.multi_scale else (1.00,)

        candidate_boxes: List[Tuple[int, int, int, int]] = []
        candidate_scores: List[float] = []

        max_instances = max(1, target.max_instances)

        for scale in scales:
            if scale == 1.00:
                cur_tmpl = template_proc
                bw, bh = tw, th
            else:
                bw = max(4, int(tw * scale))
                bh = max(4, int(th * scale))
                if bh > sh or bw > sw:
                    continue
                cur_tmpl = cv2.resize(template_proc, (bw, bh), interpolation=cv2.INTER_AREA)

            res = cv2.matchTemplate(screen_proc, cur_tmpl, cv2.TM_CCOEFF_NORMED)

            if max_instances == 1:
                # Single best instance
                _, max_val, _, max_loc = cv2.minMaxLoc(res)
                if float(max_val) >= target.confidence:
                    candidate_boxes.append((off_x + int(max_loc[0]), off_y + int(max_loc[1]), bw, bh))
                    candidate_scores.append(float(max_val))
            else:
                # Multi-instance thresholding
                y_locs, x_locs = np.where(res >= target.confidence)
                for y, x in zip(y_locs, x_locs):
                    score = float(res[y, x])
                    candidate_boxes.append((off_x + int(x), off_y + int(y), bw, bh))
                    candidate_scores.append(score)

        if not candidate_boxes:
            return []

        # Run NMS to remove duplicates
        keep_indices = non_max_suppression(candidate_boxes, candidate_scores, overlap_thresh=0.35)
        keep_indices = keep_indices[:max_instances]

        instances: List[MatchInstance] = []
        for idx in keep_indices:
            bx, by, bw, bh = candidate_boxes[idx]
            sc = candidate_scores[idx]
            instances.append(
                MatchInstance(
                    target_id=target.id,
                    target_name=target.name,
                    confidence=sc,
                    center_x=bx + bw // 2,
                    center_y=by + bh // 2,
                    bbox=(bx, by, bw, bh),
                )
            )

        return instances

    def find_target(self, target: TargetObject) -> DetectionResult:
        """Single-target search (backward compatibility wrapper)."""
        instances = self.find_all_instances(target)
        if not instances:
            return DetectionResult(found=False, confidence=0.0)

        best = max(instances, key=lambda m: m.confidence)
        return DetectionResult(
            found=True,
            confidence=best.confidence,
            center_x=best.center_x,
            center_y=best.center_y,
            bbox=best.bbox,
            matches=instances,
            target_name=target.name,
            target_id=target.id,
        )

    def detect_multiple_targets(
        self,
        targets: List[TargetObject],
        multi_target_mode: str = "any",
        pattern_order: str = "top_to_bottom",
        cursor_pos: Optional[Tuple[int, int]] = None,
    ) -> DetectionResult:
        """
        Scans screen for all enabled targets in `targets`.
        multi_target_mode:
            "any"      -> triggers if ANY enabled target is found. Matches combined & sorted by pattern_order.
            "all"      -> triggers only if ALL enabled targets have at least 1 match.
            "priority" -> scans in list order; triggers on the first target with a match.
        """
        enabled = [t for t in targets if t.enabled and t.image_path and os.path.isfile(t.image_path)]
        if not enabled:
            return DetectionResult(found=False, confidence=0.0)

        has_custom_regions = any(t.search_region is not None for t in enabled)
        shared_screen = None
        shared_offset = [0, 0]
        if not has_custom_regions:
            shared_screen, shared_offset[0], shared_offset[1] = self.capture_screen_bgr(None)

        all_matches: List[MatchInstance] = []
        targets_with_matches = set()

        for target in enabled:
            matches = self.find_all_instances(
                target,
                screen_bgr=shared_screen if target.search_region is None else None,
                screen_offset=(shared_offset[0], shared_offset[1]),
            )
            if matches:
                targets_with_matches.add(target.id)
                all_matches.extend(matches)
                if multi_target_mode == "priority":
                    # Priority mode: first target found wins
                    sorted_p = sort_matches(matches, pattern_order, reference_point=cursor_pos)
                    best = max(sorted_p, key=lambda m: m.confidence)
                    return DetectionResult(
                        found=True,
                        confidence=best.confidence,
                        center_x=best.center_x,
                        center_y=best.center_y,
                        bbox=best.bbox,
                        matches=sorted_p,
                        target_name=target.name,
                        target_id=target.id,
                    )

        if multi_target_mode == "all":
            if len(targets_with_matches) < len(enabled):
                return DetectionResult(found=False, confidence=0.0)

        if not all_matches:
            return DetectionResult(found=False, confidence=0.0)

        sorted_matches = sort_matches(all_matches, pattern_order, reference_point=cursor_pos)
        best = max(sorted_matches, key=lambda m: m.confidence)

        return DetectionResult(
            found=True,
            confidence=best.confidence,
            center_x=best.center_x,
            center_y=best.center_y,
            bbox=best.bbox,
            matches=sorted_matches,
            target_name=best.target_name,
            target_id=best.target_id,
        )


class ScreenSnipperOverlay:
    """Fullscreen interactive overlay allowing the user to drag a rectangle on screen."""

    def __init__(
        self,
        parent: tk.Tk,
        on_complete: Callable[[Optional[Image.Image], Optional[Tuple[int, int, int, int]]], None],
        prompt_text: str = "Drag a box around the target object on screen (Press ESC to cancel)",
    ) -> None:
        self.parent = parent
        self.on_complete = on_complete
        self.prompt_text = prompt_text

        self.full_screenshot = ImageGrab.grab()
        self.screen_w, self.screen_h = self.full_screenshot.size
        dimmed = self.full_screenshot.point(lambda p: int(p * 0.55))

        self.top = tk.Toplevel(parent)
        self.top.attributes("-fullscreen", True)
        self.top.attributes("-topmost", True)
        self.top.config(cursor="cross")

        self.tk_bg = ImageTk.PhotoImage(dimmed)

        self.canvas = tk.Canvas(
            self.top,
            width=self.screen_w,
            height=self.screen_h,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.tk_bg)

        # Banner instructions
        self.canvas.create_rectangle(
            self.screen_w // 2 - 340,
            18,
            self.screen_w // 2 + 340,
            58,
            fill="#111827",
            outline="#38bdf8",
            width=2,
        )
        self.canvas.create_text(
            self.screen_w // 2,
            38,
            text=self.prompt_text,
            fill="#f8fafc",
            font=("Segoe UI", 11, "bold"),
        )

        self.start_x: Optional[int] = None
        self.start_y: Optional[int] = None
        self.rect_id: Optional[int] = None
        self.size_label_id: Optional[int] = None

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.top.bind("<Escape>", self._on_cancel)
        self.top.focus_force()

    def _on_press(self, event: tk.Event) -> None:
        self.start_x = event.x
        self.start_y = event.y
        if self.rect_id:
            self.canvas.delete(self.rect_id)
        if self.size_label_id:
            self.canvas.delete(self.size_label_id)
        self.rect_id = self.canvas.create_rectangle(
            self.start_x,
            self.start_y,
            self.start_x,
            self.start_y,
            outline="#22d3ee",
            width=2,
        )
        self.size_label_id = self.canvas.create_text(
            self.start_x,
            max(15, self.start_y - 12),
            text="",
            fill="#22d3ee",
            anchor=tk.SW,
            font=("Consolas", 10, "bold"),
        )

    def _on_drag(self, event: tk.Event) -> None:
        if self.start_x is None or self.start_y is None or self.rect_id is None:
            return
        x1, y1 = min(self.start_x, event.x), min(self.start_y, event.y)
        x2, y2 = max(self.start_x, event.x), max(self.start_y, event.y)
        self.canvas.coords(self.rect_id, x1, y1, x2, y2)
        if self.size_label_id:
            self.canvas.coords(self.size_label_id, x1, max(16, y1 - 6))
            self.canvas.itemconfig(self.size_label_id, text=f"{x2 - x1} x {y2 - y1} px")

    def _on_release(self, event: tk.Event) -> None:
        if self.start_x is None or self.start_y is None:
            return
        x1, y1 = min(self.start_x, event.x), min(self.start_y, event.y)
        x2, y2 = max(self.start_x, event.x), max(self.start_y, event.y)
        w, h = x2 - x1, y2 - y1
        self.top.destroy()

        if w < 6 or h < 6:
            self.on_complete(None, None)
            return

        cropped = self.full_screenshot.crop((x1, y1, x2, y2))
        self.on_complete(cropped, (x1, y1, w, h))

    def _on_cancel(self, _event: Optional[tk.Event] = None) -> None:
        self.top.destroy()
        self.on_complete(None, None)


class PointPickerOverlay:
    """Fullscreen crosshair overlay to pick an (X, Y) coordinate on screen."""

    def __init__(
        self,
        parent: tk.Tk,
        on_picked: Callable[[Optional[Tuple[int, int]]], None],
    ) -> None:
        self.on_picked = on_picked
        screenshot = ImageGrab.grab()
        screen_w, screen_h = screenshot.size
        dimmed = screenshot.point(lambda p: int(p * 0.7))

        self.top = tk.Toplevel(parent)
        self.top.attributes("-fullscreen", True)
        self.top.attributes("-topmost", True)
        self.top.config(cursor="tcross")

        self.tk_bg = ImageTk.PhotoImage(dimmed)
        self.canvas = tk.Canvas(self.top, width=screen_w, height=screen_h, highlightthickness=0, bd=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.tk_bg)

        self.canvas.create_rectangle(
            screen_w // 2 - 280,
            18,
            screen_w // 2 + 280,
            58,
            fill="#111827",
            outline="#f59e0b",
            width=2,
        )
        self.canvas.create_text(
            screen_w // 2,
            38,
            text="Click anywhere on screen to pick (X, Y) coordinates (ESC to cancel)",
            fill="#f8fafc",
            font=("Segoe UI", 11, "bold"),
        )

        self.coord_text = self.canvas.create_text(
            0, 0, text="", fill="#fbbf24", font=("Consolas", 10, "bold"), anchor=tk.NW
        )

        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<ButtonPress-1>", self._on_click)
        self.top.bind("<Escape>", self._on_cancel)
        self.top.focus_force()

    def _on_motion(self, event: tk.Event) -> None:
        self.canvas.coords(self.coord_text, event.x + 14, event.y + 14)
        self.canvas.itemconfig(self.coord_text, text=f"({event.x}, {event.y})")

    def _on_click(self, event: tk.Event) -> None:
        x, y = int(event.x), int(event.y)
        self.top.destroy()
        self.on_picked((x, y))

    def _on_cancel(self, _event: Optional[tk.Event] = None) -> None:
        self.top.destroy()
        self.on_picked(None)


def flash_multiple_boxes(
    parent: tk.Tk,
    matches: List[MatchInstance],
    duration_ms: int = 1500,
) -> None:
    """Highlight multiple detected objects/instances on screen simultaneously with labels."""
    if not matches:
        return

    windows: List[tk.Toplevel] = []
    palette = ["#10b981", "#38bdf8", "#f59e0b", "#ec4899", "#8b5cf6", "#06b6d4"]

    for i, m in enumerate(matches, start=1):
        left, top, width, height = m.bbox
        pad = 4
        color = palette[(i - 1) % len(palette)]

        win = tk.Toplevel(parent)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.geometry(f"{width + pad * 2}x{height + pad * 2 + 20}+{max(0, left - pad)}+{max(0, top - pad - 20)}")
        win.config(bg="#ff00ff")
        try:
            win.attributes("-transparentcolor", "#ff00ff")
        except tk.TclError:
            pass

        canvas = tk.Canvas(
            win,
            width=width + pad * 2,
            height=height + pad * 2 + 20,
            bg="#ff00ff",
            highlightthickness=0,
            bd=0,
        )
        canvas.pack(fill=tk.BOTH, expand=True)

        # Label tag above box
        label_text = f"#{i} {m.target_name} ({m.confidence * 100:.0f}%)"
        canvas.create_rectangle(2, 2, max(80, len(label_text) * 8 + 8), 18, fill=color, outline=color)
        canvas.create_text(6, 10, text=label_text, fill="#ffffff", font=("Segoe UI", 8, "bold"), anchor=tk.W)

        # Bounding box
        canvas.create_rectangle(
            2, 22, width + pad * 2 - 2, height + pad * 2 + 18,
            outline=color,
            width=3,
        )
        windows.append(win)

    def _destroy_all():
        for w in windows:
            try:
                w.destroy()
            except Exception:
                pass

    parent.after(duration_ms, _destroy_all)
