from __future__ import annotations
import json
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Tuple


@dataclass
class MatchInstance:
    """A single matched occurrence on screen."""
    target_id: str
    target_name: str
    confidence: float
    center_x: int
    center_y: int
    bbox: Tuple[int, int, int, int]  # (left, top, width, height)


@dataclass
class ClickStep:
    """A single step in the click sequence executed when targets are detected."""
    # step_type: "click_detected" | "fixed_coords" | "wait" | "keypress"
    step_type: str = "click_detected"
    x: int = 0                  # Fixed X coordinate (for fixed_coords) or X offset (for click_detected)
    y: int = 0                  # Fixed Y coordinate (for fixed_coords) or Y offset (for click_detected)
    button: str = "left"        # "left" | "right" | "middle" | "double"
    clicks: int = 1
    delay_after: float = 0.25   # Seconds to wait after this step completes
    key: str = ""               # Key name if step_type == "keypress"
    note: str = ""

    # Target filter for click_detected: "any" / "current" or target_id / target_name
    target_filter: str = "any"

    # Click pattern types:
    # "point"          -> Standard single click at exact point
    # "all_instances"  -> Click every detected instance on screen in specified pattern order
    # "jitter"         -> Random offset within radius (anti-detection / natural human dispersion)
    # "burst"          -> Rapid-fire burst of N clicks with micro delays
    # "long_press"     -> Hold button down for hold_duration, then release
    # "glide"          -> Smooth curved mouse glide to target before clicking
    pattern_type: str = "point"

    # Pattern parameters
    pattern_order: str = "top_to_bottom"  # "top_to_bottom" | "bottom_to_top" | "left_to_right" | "right_to_left" | "nearest" | "random"
    jitter_radius: int = 8                # Pixels of random dispersion
    burst_count: int = 3                  # Clicks per burst
    burst_delay: float = 0.03             # Micro delay between burst clicks
    hold_duration: float = 0.40           # Seconds to hold button down for long_press
    glide_duration: float = 0.15          # Seconds for smooth cursor glide

    def summary_target(self) -> str:
        if self.step_type == "click_detected":
            filt = f" [{self.target_filter}]" if self.target_filter and self.target_filter != "any" else ""
            if self.pattern_type == "all_instances":
                return f"All Instances{filt} ({self.pattern_order})"
            if self.x == 0 and self.y == 0:
                return f"Detected Center{filt}"
            return f"Detected ({self.x:+d}, {self.y:+d}){filt}"
        elif self.step_type == "fixed_coords":
            return f"Screen ({self.x}, {self.y})"
        elif self.step_type == "wait":
            return f"Wait {self.delay_after:.2f}s"
        elif self.step_type == "keypress":
            return f"Key '{self.key}'"
        return self.step_type

    def summary_action(self) -> str:
        if self.step_type in ("click_detected", "fixed_coords"):
            if self.button == "double":
                base = "Double Click"
            else:
                btn = self.button.capitalize()
                base = f"{btn} Click (x{self.clicks})" if self.clicks > 1 else f"{btn} Click"
            return base
        elif self.step_type == "wait":
            return "Pause"
        elif self.step_type == "keypress":
            return f"Press [{self.key}]"
        return ""

    def summary_pattern(self) -> str:
        if self.step_type == "wait":
            return "-"
        if self.step_type == "keypress":
            return "Key"
        if self.pattern_type == "all_instances":
            return f"Pattern: All ({self.pattern_order})"
        elif self.pattern_type == "jitter":
            return f"Pattern: Jitter (+/-{self.jitter_radius}px)"
        elif self.pattern_type == "burst":
            return f"Pattern: Burst (x{self.burst_count})"
        elif self.pattern_type == "long_press":
            return f"Pattern: Hold ({self.hold_duration:.2f}s)"
        elif self.pattern_type == "glide":
            return f"Pattern: Smooth Glide ({self.glide_duration:.2f}s)"
        return "Pattern: Point"


def default_click_sequence() -> List[ClickStep]:
    """Default single click step for an object."""
    return [
        ClickStep(
            step_type="click_detected",
            x=0,
            y=0,
            button="left",
            clicks=1,
            delay_after=0.35,
            pattern_type="point",
            note="Click detected object",
        )
    ]


@dataclass
class TargetObject:
    """Configuration for a screen object to detect via template matching with its own click sequence."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str = "Target Object"
    image_path: str = ""
    confidence: float = 0.85
    search_region: Optional[Tuple[int, int, int, int]] = None  # (left, top, width, height) or None for full screen
    multi_scale: bool = False
    grayscale: bool = False
    enabled: bool = True
    max_instances: int = 1   # 1 for best match only, >1 to detect multiple instances on screen
    color_hex: str = "#10b981"  # Visual highlight color for overlay

    # Per-object click sequence support
    use_custom_sequence: bool = True
    sequence: List[ClickStep] = field(default_factory=default_click_sequence)


@dataclass
class AutomationProfile:
    """Complete profile saving detection targets, click sequences, and engine settings."""
    name: str = "Default Profile"
    targets: List[TargetObject] = field(default_factory=list)
    multi_target_mode: str = "any"  # "any" | "all" | "priority"
    pattern_order: str = "top_to_bottom"  # Default ordering for multi-instance detection
    sequence: List[ClickStep] = field(default_factory=default_click_sequence)  # Shared fallback sequence
    poll_interval: float = 0.30     # Seconds between screen scans
    cooldown: float = 1.00          # Seconds to wait after a full sequence finishes
    trigger_mode: str = "continuous"  # "continuous" | "once" | "count"
    max_triggers: int = 10

    # Backward-compatible property for single target
    @property
    def target(self) -> TargetObject:
        if not self.targets:
            self.targets.append(TargetObject())
        return self.targets[0]

    def get_sequence_for_target(self, target: Optional[TargetObject]) -> List[ClickStep]:
        """Returns the target's custom sequence if enabled and non-empty, otherwise fallback shared sequence."""
        if target and target.use_custom_sequence and target.sequence:
            return target.sequence
        return self.sequence

    def to_dict(self) -> dict:
        data = asdict(self)
        for t in data.get("targets", []):
            if t.get("search_region") is not None:
                t["search_region"] = list(t["search_region"])
        # Provide legacy 'target' key for compatibility
        if self.targets:
            legacy_t = asdict(self.targets[0])
            if legacy_t.get("search_region") is not None:
                legacy_t["search_region"] = list(legacy_t["search_region"])
            data["target"] = legacy_t
        return data

    @classmethod
    def from_dict(cls, data: dict) -> AutomationProfile:
        targets_list: List[TargetObject] = []

        # Read multiple targets if available
        raw_targets = data.get("targets", [])
        if raw_targets:
            for t_data in raw_targets:
                region = t_data.get("search_region")
                if region is not None:
                    t_data["search_region"] = tuple(region)
                if "id" not in t_data or not t_data["id"]:
                    t_data["id"] = uuid.uuid4().hex[:8]
                # Deserialize target sequence if present
                if "sequence" in t_data:
                    t_data["sequence"] = [ClickStep(**step) for step in t_data["sequence"]]
                else:
                    t_data["sequence"] = default_click_sequence()
                if "use_custom_sequence" not in t_data:
                    t_data["use_custom_sequence"] = True
                targets_list.append(TargetObject(**t_data))
        elif "target" in data and isinstance(data["target"], dict):
            # Fallback to legacy single target
            t_data = data["target"]
            region = t_data.get("search_region")
            if region is not None:
                t_data["search_region"] = tuple(region)
            if "id" not in t_data or not t_data["id"]:
                t_data["id"] = uuid.uuid4().hex[:8]
            if "sequence" in t_data:
                t_data["sequence"] = [ClickStep(**step) for step in t_data["sequence"]]
            else:
                t_data["sequence"] = default_click_sequence()
            targets_list.append(TargetObject(**t_data))

        if not targets_list:
            targets_list.append(TargetObject())

        sequence = [ClickStep(**step) for step in data.get("sequence", [])]
        if not sequence:
            sequence = default_click_sequence()

        return cls(
            name=data.get("name", "Profile"),
            targets=targets_list,
            multi_target_mode=data.get("multi_target_mode", "any"),
            pattern_order=data.get("pattern_order", "top_to_bottom"),
            sequence=sequence,
            poll_interval=float(data.get("poll_interval", 0.30)),
            cooldown=float(data.get("cooldown", 1.00)),
            trigger_mode=data.get("trigger_mode", "continuous"),
            max_triggers=int(data.get("max_triggers", 10)),
        )

    def save(self, filepath: str) -> None:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, filepath: str) -> AutomationProfile:
        with open(filepath, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))
