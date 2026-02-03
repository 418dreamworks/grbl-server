# Remove Fixture by Probing
# Position tool roughly at center of fixture to remove, run macro
# Probes to find actual center, then removes matching fixture

import asyncio
import math

PROBE_FEED = 10  # mm/min
PROBE_DIST = 50  # max probe distance
BACKOFF = 5      # mm to back off after contact
MATCH_TOL = 5    # mm tolerance for matching fixture

await self._log('=== REMOVE FIXTURE ===')

if not self.fixtures:
    await self._log('No fixtures to remove')
    return

# Disable hard limits for probing
await self._send_and_log('$21=0')
await self._wait_idle()

start_x = self.grbl.status.wpos['x']
start_y = self.grbl.status.wpos['y']

# Probe directions: 0°, 120°, 240°
angles = [0, 120, 240]
contact_points = []

async def probe_direction(angle_deg):
    """Probe in direction, return contact point (x, y) or None"""
    angle_rad = math.radians(angle_deg)
    dx = math.cos(angle_rad)
    dy = math.sin(angle_rad)

    await self._log(f'Probing {angle_deg}°...')

    await self._send_and_log('G91')
    await self._send_and_log(f'G1 X{dx * PROBE_DIST:.3f} Y{dy * PROBE_DIST:.3f} F{PROBE_FEED}')

    while True:
        await asyncio.sleep(0.05)
        state = self.grbl.status.state
        if 'Alarm' in state:
            px = self.grbl.status.wpos['x']
            py = self.grbl.status.wpos['y']
            await self._send_and_log('$X')
            await asyncio.sleep(0.1)
            await self._send_and_log(f'G0 X{-dx * BACKOFF:.3f} Y{-dy * BACKOFF:.3f}')
            await self._wait_idle()
            await self._send_and_log('G90')
            return (px, py)
        if state == 'Idle':
            await self._send_and_log('G90')
            return None

    return None

# Probe 3 directions
for angle in angles:
    await self._send_and_log(f'G0 X{start_x} Y{start_y}')
    await self._wait_idle()

    point = await probe_direction(angle)
    if point is None:
        await self._log(f'ERROR: No contact at {angle}°')
        await self._send_and_log('$21=1')
        return
    contact_points.append(point)

# Calculate circle center from 3 points
(x1, y1), (x2, y2), (x3, y3) = contact_points

A = x1 * (y2 - y3) - y1 * (x2 - x3) + x2 * y3 - x3 * y2
if abs(A) < 0.0001:
    await self._log('ERROR: Points are collinear')
    await self._send_and_log('$21=1')
    return

B = (x1**2 + y1**2) * (y3 - y2) + (x2**2 + y2**2) * (y1 - y3) + (x3**2 + y3**2) * (y2 - y1)
C = (x1**2 + y1**2) * (x2 - x3) + (x2**2 + y2**2) * (x3 - x1) + (x3**2 + y3**2) * (x1 - x2)

x_center = -B / (2 * A)
y_center = -C / (2 * A)

# Re-enable hard limits
await self._send_and_log('$21=1')

# Convert to machine coords
wco = self.grbl.status.wco
mx_probed = x_center + wco['x']
my_probed = y_center + wco['y']

await self._log(f'Probed center: MX{mx_probed:.1f} MY{my_probed:.1f}')

# Find matching fixture
match_idx = -1
min_dist = float('inf')

for i, f in enumerate(self.fixtures):
    dist = math.sqrt((mx_probed - f['mx'])**2 + (my_probed - f['my'])**2)
    await self._log(f'Fixture #{i+1}: dist={dist:.1f}mm')
    if dist < min_dist:
        min_dist = dist
        match_idx = i

if match_idx < 0 or min_dist > MATCH_TOL:
    await self._log(f'ERROR: No fixture matches within {MATCH_TOL}mm')
    await self._log('This fixture may not be in the list')
    return

# Remove it
removed = self.fixtures.pop(match_idx)
await self._log(f'REMOVED fixture #{match_idx + 1}')
await self._log(f'Was at MX{removed["mx"]:.1f} MY{removed["my"]:.1f} R{removed["radius"]:.1f}')

# Broadcast updated list
await self.broadcast_fixtures()

# Return to center
await self._send_and_log('G91')
move_x = x_center - start_x
move_y = y_center - start_y
await self._send_and_log(f'G0 X{move_x:.3f} Y{move_y:.3f}')
await self._wait_idle()
await self._send_and_log('G90')

await self._log(f'{len(self.fixtures)} fixtures remaining')
await self._log('=== REMOVE FIXTURE COMPLETE ===')
