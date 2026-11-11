# Smart Object Auto Clicker

A desktop auto-clicker application that monitors your screen for one or multiple target objects using **OpenCV template matching** and automatically executes **different, customizable click sequences and patterns for each object**.

---

## Key Features

### 1. Different Click Sequence for Each Object
- **Target-Specific Sequences**: Each target object has its own dedicated click sequence.
  - When **Object A** (*e.g., "Confirm Button"*) appears ➔ Executes Sequence A (*e.g., Precision Click ➔ Wait 0.5s ➔ Press Enter*).
  - When **Object B** (*e.g., "Reward Chest"*) appears ➔ Executes Sequence B (*e.g., 5x Rapid Burst Clicks*).
  - When **Object C** (*e.g., "Close Ad"*) appears ➔ Executes Sequence C (*e.g., Human Jitter Click*).
- **Intuitive UI Sync**: Clicking any target in the left panel's target list immediately switches the right panel to view and edit that target's own sequence.
- **Overview at a Glance**: The left target list displays the exact step count for each object (`2 step(s)`, `3 step(s)`).
- **Copy to All**: Quickly replicate a sequence from one object to all other targets with one click (**📋 Copy to All**).

### 2. Multi-Object Detection
- **Target Management**: Snip or load any number of target images with individual confidence thresholds and search areas.
- **Multiple Instances per Object**: Set `Max Instances` (> 1) to detect all occurrences on screen with Non-Maximum Suppression (NMS).
- **Trigger Strategy**:
  - `ANY`: Fires whenever any enabled target appears (each target runs its own respective sequence).
  - `ALL`: Triggers only when all enabled targets are present on screen simultaneously.
  - `PRIORITY`: Scans in list order and fires on the highest priority target detected.
- **Visual Screen Test**: Click **🔍 Test Detect ALL Objects on Screen** to flash labeled bounding boxes over all detected items.

### 3. Advanced Click Patterns
Each step in an object's sequence can use a specialized click pattern:
- **Click ALL Instances**: Clicks every detected instance on screen in your chosen order (`Top ➔ Bottom`, `Left ➔ Right`, `Nearest to Cursor`, `Random`, etc.).
- **Human Jitter / Dispersion**: Clicks with randomized pixel offsets (`+/- N px`) around the target to mimic natural human variation and evade bot detection.
- **Rapid Fire Burst**: Rapid successive clicks (e.g., 3 to 10 clicks) with micro-delays (`10ms`–`500ms`).
- **Long Press / Hold & Release**: Holds the mouse button down for a configurable duration before releasing.
- **Smooth Human Glide**: Moves the mouse cursor along an eased curved trajectory to the target rather than teleporting instantly.
- **Precision Point**: Direct click at object center or offset `(dX, dY)`.

---

## Safety & Controls

- `F6` — Global Start / Stop auto-clicker monitoring
- `F7` — Global Start / Stop live click recording
- `F8` — Global Emergency Stop (or move cursor to screen corner)
- `🛡 Restart as Admin` — Runs the app with Windows Administrator rights if clicking into elevated games or programs.

---

## Quick Start

Launch the app by double-clicking [`run.bat`](file:///C:/Users/Phong%20Tran/.gemini/antigravity/scratch/smart-auto-clicker/run.bat) or running:
```powershell
& "C:\Users\Phong Tran\.gemini\antigravity\scratch\smart-auto-clicker\.venv\Scripts\python.exe" "C:\Users\Phong Tran\.gemini\antigravity\scratch\smart-auto-clicker\app.py"
```
