# XY Check (Collision Analysis)
# Analyzes G-code for potential fixture collisions
# - Finds G1 "cut hull" (bounding box of cutting moves)
# - Identifies G0 rapids outside hull (entry/exit paths)
# - Groups by tool change
# - Traces paths at actual Z heights
# - Checks against stored fixtures

import re
import math

await self._log('=== XY CHECK (COLLISION ANALYSIS) ===')

# Parse G-code - track positions, modes, tool changes
class GCodeAnalyzer:
    def __init__(self):
        self.x, self.y, self.z = 0.0, 0.0, 0.0
        self.absolute = True
        self.g1_points = []  # (x, y) for cut hull
        self.segments = []   # All tool segments
        self.current_segment = {'tool': 0, 'g0_outside': [], 'g1_bounds': None}

    def parse(self, gcode):
        for line in gcode.splitlines():
            line = line.split(';')[0].strip()
            if not line:
                continue

            # Mode tracking
            if re.search(r'\bG90\b', line, re.IGNORECASE):
                self.absolute = True
            if re.search(r'\bG91\b', line, re.IGNORECASE):
                self.absolute = False

            # Tool change - start new segment
            if re.search(r'\bM0*6\b', line, re.IGNORECASE):
                t_match = re.search(r'T(\d+)', line, re.IGNORECASE)
                tool_num = int(t_match.group(1)) if t_match else 0
                self._finish_segment()
                self.current_segment = {'tool': tool_num, 'g0_outside': [], 'g1_bounds': None}
                continue

            # Extract coordinates
            x_match = re.search(r'X([-\d.]+)', line, re.IGNORECASE)
            y_match = re.search(r'Y([-\d.]+)', line, re.IGNORECASE)
            z_match = re.search(r'Z([-\d.]+)', line, re.IGNORECASE)

            old_x, old_y, old_z = self.x, self.y, self.z

            if x_match:
                val = float(x_match.group(1))
                self.x = val if self.absolute else self.x + val
            if y_match:
                val = float(y_match.group(1))
                self.y = val if self.absolute else self.y + val
            if z_match:
                val = float(z_match.group(1))
                self.z = val if self.absolute else self.z + val

            # G1 moves define cut hull
            if re.match(r'G0*1\b', line, re.IGNORECASE):
                if x_match or y_match:
                    self.g1_points.append((self.x, self.y))

            # G0 moves - check if outside hull later
            elif re.match(r'G0+\b', line, re.IGNORECASE):
                if x_match or y_match:
                    self.current_segment['g0_outside'].append({
                        'from': (old_x, old_y, old_z),
                        'to': (self.x, self.y, self.z)
                    })

        self._finish_segment()

    def _finish_segment(self):
        if self.current_segment['g0_outside'] or self.g1_points:
            self.segments.append(self.current_segment)

    def get_g1_hull(self):
        if not self.g1_points:
            return None
        xs = [p[0] for p in self.g1_points]
        ys = [p[1] for p in self.g1_points]
        return {
            'x_min': min(xs), 'x_max': max(xs),
            'y_min': min(ys), 'y_max': max(ys)
        }

analyzer = GCodeAnalyzer()
analyzer.parse(self.loaded_gcode)

hull = analyzer.get_g1_hull()
if not hull:
    await self._log('ERROR: No G1 XY moves found')
    return

await self._log(f'Cut hull: X[{hull["x_min"]:.3f}, {hull["x_max"]:.3f}] Y[{hull["y_min"]:.3f}, {hull["y_max"]:.3f}]')

# Filter G0 moves to only those outside hull
def is_outside_hull(x, y, h, margin=0.1):
    return x < h['x_min'] - margin or x > h['x_max'] + margin or \
           y < h['y_min'] - margin or y > h['y_max'] + margin

def segment_outside_hull(seg, h):
    return is_outside_hull(seg['from'][0], seg['from'][1], h) or \
           is_outside_hull(seg['to'][0], seg['to'][1], h)

# Check fixtures for collisions
wco = self.grbl.status.wco
collisions = []

def check_fixture_collision(x, y, z):
    mx, my, mz = x + wco['x'], y + wco['y'], z + wco['z']
    for i, f in enumerate(self.fixtures):
        dist = math.sqrt((mx - f['mx'])**2 + (my - f['my'])**2)
        if dist < f['radius'] and mz < f['mz']:
            return i + 1  # fixture number (1-indexed)
    return None

# Analyze each tool segment
total_outside = 0
for seg in analyzer.segments:
    outside_moves = [m for m in seg['g0_outside'] if segment_outside_hull(m, hull)]
    if not outside_moves:
        continue

    await self._log(f'--- Tool T{seg["tool"]} ---')
    await self._log(f'{len(outside_moves)} G0 moves outside cut hull:')

    for m in outside_moves[:5]:  # Show first 5
        fx, fy, fz = m['from']
        tx, ty, tz = m['to']
        await self._log(f'  ({fx:.1f},{fy:.1f},Z{fz:.1f}) -> ({tx:.1f},{ty:.1f},Z{tz:.1f})')

        # Check both endpoints against fixtures
        if self.fixtures:
            col = check_fixture_collision(tx, ty, tz)
            if col:
                collisions.append({'fixture': col, 'pos': (tx, ty, tz), 'tool': seg['tool']})

    if len(outside_moves) > 5:
        await self._log(f'  ... and {len(outside_moves) - 5} more')
    total_outside += len(outside_moves)

await self._log(f'Total G0 outside hull: {total_outside}')

# Report fixture collisions
if collisions:
    await self._log('')
    await self._log('!!! FIXTURE COLLISIONS DETECTED !!!')
    for c in collisions:
        await self._log(f'  Fixture #{c["fixture"]} at ({c["pos"][0]:.1f},{c["pos"][1]:.1f},Z{c["pos"][2]:.1f}) T{c["tool"]}')
    await self._log('Review G-code or clear fixtures before running!')
elif self.fixtures:
    await self._log(f'Fixture check OK ({len(self.fixtures)} fixtures)')

# Offer to trace the cut hull
await self._log('')
await self._log('Trace cut hull boundary? (at current Z)')
await self._wait_for_continue()
await self._wait_idle()

current_z = self.grbl.status.wpos['z']
await self._log(f'Tracing at Z={current_z:.3f}mm')

await self._send_and_log('G90')
await self._send_and_log(f'G0 X{hull["x_min"]:.3f} Y{hull["y_min"]:.3f}')
await self._wait_idle()
await self._send_and_log(f'G0 X{hull["x_max"]:.3f} Y{hull["y_min"]:.3f}')
await self._wait_idle()
await self._send_and_log(f'G0 X{hull["x_max"]:.3f} Y{hull["y_max"]:.3f}')
await self._wait_idle()
await self._send_and_log(f'G0 X{hull["x_min"]:.3f} Y{hull["y_max"]:.3f}')
await self._wait_idle()
await self._send_and_log(f'G0 X{hull["x_min"]:.3f} Y{hull["y_min"]:.3f}')
await self._wait_idle()

await self._log('=== XY CHECK COMPLETE ===')
