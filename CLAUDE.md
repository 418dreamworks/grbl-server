# Claude Code Rules for nedjrbrains

## MANDATORY — Do Not Code Without Permission
Do NOT write or suggest code unless the user explicitly asks. Diagnose first, explain the problem, and wait for instructions. If you need to add logging to diagnose, explain what you need and ask permission first.

## MANDATORY — Everything Must Be Logged
All server and HTML events MUST be visible in error.log. If you cannot see what the user sees, add logging so you can. Every button click, step change, unit toggle, jog, block, feed change — everything. If it happens in the UI, it must appear in error.log.

## MANDATORY — Read These Files Before Any Work
You MUST read these two files at the start of EVERY conversation, before making ANY edits:
1. `~/.claude/projects/-home-tzuohann-Documents-grbl-server/memory/feedback_custom_step_safety.md`
2. `~/.claude/projects/-home-tzuohann-Documents-grbl-server/memory/feedback_no_deploy_while_running.md`
These contain safety-critical rules learned from incidents. No exceptions.

## Deployment

**Local deploy** (running on same machine as controller):

```bash
fuser -k 8000/tcp 8001/tcp 2>/dev/null; sleep 2; nohup python3 grbl_server.py --port 8000 > server.log 2>&1 &
```

**IMPORTANT:** Always use `fuser -k` to free sockets before restart. `pkill` alone leaves sockets in TIME_WAIT.

Verify: `pgrep -f grbl_server && ss -tlnp | grep 8000`

## Version Number

- Version is in `jog.html` (search for `v1.`)
- **Increment version on every deploy**
- Current: v1.129

## Key Architecture

### Macros
- All macros in `macros/` directory
- Config values (DOC_RATIO, SPINDLE_RPM, etc.) in `config.py`
- **Current:** `exec()` hot-reload for development (edit macros without restart)
- **Future:** Stable macros will become importable library
- Must use `macro_dir` not `__file__` (undefined in exec context)
- Import pattern: `sys.path.insert(0, os.path.dirname(macro_dir))`

### Macro Methods Available
| Method | Purpose |
|--------|---------|
| `await self._send_and_log(gcode)` | Send G-code, wait for ok, log |
| `await self._wait_idle()` | Wait for Idle state |
| `await self._log(message)` | Log to console |
| `await self._wait_for_continue()` | Pause for user Continue |
| `await self._get_distance_mode()` | Returns 'G90' or 'G91' (for save/restore) |
| `self.grbl.status.wpos` | Work position dict {x,y,z,a} |
| `self.grbl.status.mpos` | Machine position dict |
| `self.grbl.last_probe` | Probe result {x,y,z,a,success} |

## Critical Rules

### Position Safety
1. **Always `await self._wait_idle()` before reading positions**
2. **Return to start position at end of all macros:**
   - Return to Z first (to clear work)
   - Then return to XY
   - Never go above start Z (triggers soft limit errors)
3. Use absolute mode (G90) for final return moves

### G-code Formatting
- Always use `.3f` for all numeric values in G-code
- Bad: `G3 I{-radius} J0` → `I-3.2249999999999996`
- Good: `G3 I{-radius:.3f} J0` → `I-3.225`
- GRBL rejects arcs with floating point ugliness (error:26)

### Coordinate Systems
- `G10 L20 P1 X{val}` sets current position to val (shifts WCO)
- After G10, old WPOS values are invalid (coordinate system changed)
- For probes: track relative displacement, reverse at end
- For milling: record absolute start, return to it at end

## Macro Categories

### Probe Macros
- `probe_x.py`, `probe_y.py` - Edge probes (track displacement, return to start)
- `probe_z.py` - Z probe with safe raise (checks MPos before moving up)

### Milling Macros
All use config.py values, record start XYZ, return at end:
- `milling_drill.py` - Peck drill (peck = tool_dia/2)
- `milling_boring.py` - Helical bore with spiral outward
- `milling_facing.py` - Rectangle pocket (path offset by tool radius)
- `milling_line_contour.py` - Line slot (width=0) or zigzag facing (width>0)
- `milling_od_contour.py` - Circular pocket with helical plunge

### Rotary Macros
- `rotary_chuck.py` - Find chuck center, apply XYZ offsets
- `rotary_tailstock.py` - Check tailstock alignment

### Safety Macros
- `safety_z_check.py` - Plunge to G-code max depth for visual check
- `safety_probe_fixture.py`, `safety_remove_fixture.py` - Fixture management

## UI Notes

### Tool Diameter Modal
- Shows 1/4" and 1/8" buttons for most macros
- Drill uses free-form prompt (not modal)

### Facing Dimensions
- User specifies pocket size (length x width)
- Tool path is offset inward by tool radius
- So actual pocket = specified dimensions

### Status Display
- Raw status lines not shown in console (parsed values shown in UI)
- Input pins (Pn:) parsed and displayed

## Recent Session Summary (v1.127)

### Fixed Issues
1. **Soft limit blocking after probe** - probe_z was pushing MPos past Z home with absolute move. Fixed with safe raise calculation.
2. **GRBL arc errors (error:26)** - Floating point values like `I-3.2249999999999996`. Fixed with `.3f` formatting.
3. **Macros not returning to start** - All milling macros now track start XYZ and return (Z first, then XY).
4. **`__file__` undefined in macros** - Changed to use `macro_dir` for imports.

### Macro Improvements
- All macros wait for idle before reading positions
- All macros return to exact start position
- Facing offsets path by tool radius (dimensions = pocket size)
- Drill uses free-form tool diameter input
- Tailstock output simplified to just result message
