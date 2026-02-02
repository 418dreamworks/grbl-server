# Probe Fixture (Circle)
# User positions tool roughly at fixture center
# Probes 3 directions (120° apart) to find true center
# Uses hard limit alarm detection (fixture wired to limit pin)

import asyncio
import math

PROBE_FEED = 10  # mm/min - slow for accuracy with 200ms polling
PROBE_DIST = 50  # max probe distance
BACKOFF = 5      # mm to back off after contact

await self._log('=== PROBE FIXTURE ===')

# Disable hard limits for probing
await self._send_and_log('$21=0')
await asyncio.sleep(0.1)

start_x = self.grbl.status['wpos']['x']
start_y = self.grbl.status['wpos']['y']

# Probe directions: 0°, 120°, 240°
angles = [0, 120, 240]
contact_points = []

async def probe_direction(angle_deg):
    """Probe in direction, return contact point (x, y) or None"""
    angle_rad = math.radians(angle_deg)
    dx = math.cos(angle_rad)
    dy = math.sin(angle_rad)

    await self._log(f'Probing {angle_deg}°...')

    # Move in probe direction
    await self._send_and_log('G91')
    await self._send_and_log(f'G1 X{dx * PROBE_DIST:.3f} Y{dy * PROBE_DIST:.3f} F{PROBE_FEED}')

    # Poll for alarm state
    while True:
        await asyncio.sleep(0.05)
        state = self.grbl.status.get('state', '')
        if 'Alarm' in state:
            px = self.grbl.status['wpos']['x']
            py = self.grbl.status['wpos']['y']
            await self._send_and_log('$X')  # Clear alarm
            await asyncio.sleep(0.1)
            # Back off
            await self._send_and_log(f'G0 X{-dx * BACKOFF:.3f} Y{-dy * BACKOFF:.3f}')
            await self._wait_idle()
            await self._send_and_log('G90')
            return (px, py)
        if state == 'Idle':
            # No contact
            await self._send_and_log('G90')
            return None

    return None

# Probe 3 directions
for angle in angles:
    # Return to start before each probe
    await self._send_and_log(f'G0 X{start_x} Y{start_y}')
    await self._wait_idle()

    point = await probe_direction(angle)
    if point is None:
        await self._log(f'ERROR: No contact at {angle}°')
        await self._send_and_log('$21=1')
        return
    contact_points.append(point)

# Calculate circle center from 3 points (circumcenter)
(x1, y1), (x2, y2), (x3, y3) = contact_points

# Circumcenter formula
A = x1 * (y2 - y3) - y1 * (x2 - x3) + x2 * y3 - x3 * y2
if abs(A) < 0.0001:
    await self._log('ERROR: Points are collinear')
    await self._send_and_log('$21=1')
    return

B = (x1**2 + y1**2) * (y3 - y2) + (x2**2 + y2**2) * (y1 - y3) + (x3**2 + y3**2) * (y2 - y1)
C = (x1**2 + y1**2) * (x2 - x3) + (x2**2 + y2**2) * (x3 - x1) + (x3**2 + y3**2) * (x1 - x2)

x_center = -B / (2 * A)
y_center = -C / (2 * A)

# Calculate radius (average distance from center to points)
radii = [math.sqrt((x - x_center)**2 + (y - y_center)**2) for x, y in contact_points]
radius = sum(radii) / len(radii)
diameter = radius * 2

# Re-enable hard limits
await self._send_and_log('$21=1')

await self._log(f'Fixture center: X{x_center:.3f} Y{y_center:.3f}')
await self._log(f'Diameter: {diameter:.1f}mm')

# Z probe at edge (not center - bolt in the way)
# Move to edge: center + radius in direction of first contact point
(px, py) = contact_points[0]
dx = px - x_center
dy = py - y_center
dist = math.sqrt(dx**2 + dy**2)
# Normalize and scale to radius (actual edge position)
edge_x = x_center + (dx / dist) * radius
edge_y = y_center + (dy / dist) * radius

await self._send_and_log(f'G0 X{edge_x:.3f} Y{edge_y:.3f}')
await self._wait_idle()

await self._log(f'At edge: X{edge_x:.3f} Y{edge_y:.3f}')
await self._log('Probing Z (top surface at edge)...')
await self._send_and_log('G91')
await self._send_and_log(f'G38.3 Z-10 F{PROBE_FEED}')
await self._wait_idle()

if not self.grbl.last_probe['success']:
    await self._log('ERROR: No Z contact')
    await self._send_and_log('G0 Z10')
    await self._send_and_log('G90')
    return

z_top = self.grbl.status['wpos']['z']

# Move to center at fixture top, set G59 zero there
# Currently at edge, at z_top. Move to center first (stay at surface level)
move_x = -(dx / dist) * radius
move_y = -(dy / dist) * radius
await self._send_and_log(f'G0 X{move_x:.3f} Y{move_y:.3f}')
await self._wait_idle()

# Now at center, on fixture surface - set G59 to X0 Y0 Z0 here
await self._send_and_log('G10 L20 P6 X0 Y0 Z0')
await self._log('G59 zeroed at fixture center/top')

# Retract 5mm
await self._send_and_log('G0 Z5')
await self._wait_idle()

await self._log(f'Fixture top Z: {z_top:.3f}mm (in original coords)')
await self._log('Switch to G59 for fixture-relative coords')

# Store fixture in MACHINE coordinates (MPos = WPos + WCO)
# Cylinder from mz (top) down to -infinity
wco = self.grbl.status.get('wco', {'x': 0, 'y': 0, 'z': 0})
fixture = {
    'mx': round(x_center + wco['x'], 3),  # machine X
    'my': round(y_center + wco['y'], 3),  # machine Y
    'mz': round(z_top + wco['z'], 3),     # machine Z (top of fixture)
    'radius': round(radius, 3)
}
self.fixtures.append(fixture)
await self._log(f'Stored in MPos: X{fixture["mx"]:.3f} Y{fixture["my"]:.3f} Z{fixture["mz"]:.3f} R{fixture["radius"]:.1f}')

# Broadcast updated fixtures list to client
await self.broadcast_fixtures()

await self._log(f'Fixture #{len(self.fixtures)} added')
await self._log('=== PROBE FIXTURE COMPLETE ===')
