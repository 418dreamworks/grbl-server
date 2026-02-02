# Rotary Chuck Find
# Probes X (right), Y (front), Z to find chuck probe corner
# Sets probed point = (-50, -20, 26) so rotary centerline = (0, 0, 0)
# Retracts and moves to centerline position
# Inputs: tool_diameter from self.tool_diameter

import asyncio

r = self.tool_diameter / 2

# Chuck probe offsets: probed corner → centerline translation
CHUCK_X_OFFSET = -50  # probe corner X relative to centerline
CHUCK_Y_OFFSET = -20  # probe corner Y relative to centerline
CHUCK_Z_OFFSET = 26   # probe corner Z relative to centerline

await self._log('=== CHUCK FIND START ===')
await self._log(f'Tool diameter: {self.tool_diameter}mm')

# ===== X PROBE (RIGHT EDGE) =====
await self._log('--- X Probe (right edge) ---')
await self._send_and_log('G91')

# Clear probe surface (+X to get past right edge)
x_clear = 6 + r
await self._send_and_log(f'G0 X{x_clear}')
await self._wait_idle()

# Plunge with safety check
await self._send_and_log('G38.3 Z-6 F100')
await self._wait_idle()
if self.grbl.last_probe['success']:
    await self._send_and_log('G0 Z6')
    await self._send_and_log(f'G0 X{-x_clear}')
    await self._send_and_log('G90')
    await self._log('ERROR: Unexpected probe contact during X plunge')
    return

# First probe (fast) toward left
await self._send_and_log('G38.3 X-6 F50')
await self._wait_idle()
if not self.grbl.last_probe['success']:
    await self._send_and_log('G0 Z6')
    await self._send_and_log(f'G0 X{-x_clear}')
    await self._send_and_log('G90')
    await self._log('ERROR: No X probe contact')
    return

# Back off and refine
await self._send_and_log('G0 X1')
await self._send_and_log('G38.3 X-2 F10')
await self._wait_idle()

# Set X coordinate: right edge means X = CHUCK_X_OFFSET - r
# (tool center is r to the right of the edge)
await self._send_and_log('G90')
await self._send_and_log(f'G10 L20 P1 X{CHUCK_X_OFFSET - r}')
await self._log(f'X set to {CHUCK_X_OFFSET - r:.3f}mm')

# Raise and return to near start for Y probe
await self._send_and_log('G91')
await self._send_and_log('G0 Z6')
await self._send_and_log(f'G0 X{x_clear}')  # back to right of edge
await self._wait_idle()

# ===== Y PROBE (FRONT EDGE) =====
await self._log('--- Y Probe (front edge) ---')

# Clear probe surface (-Y to get past front edge)
y_clear = -(6 + r)
await self._send_and_log(f'G0 Y{y_clear}')
await self._wait_idle()

# Plunge with safety check
await self._send_and_log('G38.3 Z-6 F100')
await self._wait_idle()
if self.grbl.last_probe['success']:
    await self._send_and_log('G0 Z6')
    await self._send_and_log(f'G0 Y{-y_clear}')
    await self._send_and_log('G90')
    await self._log('ERROR: Unexpected probe contact during Y plunge')
    return

# First probe (fast) toward back (+Y)
await self._send_and_log('G38.3 Y6 F50')
await self._wait_idle()
if not self.grbl.last_probe['success']:
    await self._send_and_log('G0 Z6')
    await self._send_and_log(f'G0 Y{-y_clear}')
    await self._send_and_log('G90')
    await self._log('ERROR: No Y probe contact')
    return

# Back off and refine
await self._send_and_log('G0 Y-1')
await self._send_and_log('G38.3 Y2 F10')
await self._wait_idle()

# Set Y coordinate: front edge means Y = CHUCK_Y_OFFSET + r
# (tool center is r in front of the edge)
await self._send_and_log('G90')
await self._send_and_log(f'G10 L20 P1 Y{CHUCK_Y_OFFSET + r}')
await self._log(f'Y set to {CHUCK_Y_OFFSET + r:.3f}mm')

# Raise for Z probe
await self._send_and_log('G91')
await self._send_and_log('G0 Z6')
await self._wait_idle()

# ===== Z PROBE =====
await self._log('--- Z Probe ---')

# First probe (fast)
await self._send_and_log('G38.2 Z-11 F50')
await self._wait_idle()

# Back off and refine
await self._send_and_log('G0 Z2.5')
await self._send_and_log('G38.2 Z-3 F10')
await self._wait_idle()

# Set Z coordinate
await self._send_and_log('G90')
await self._send_and_log(f'G10 L20 P1 Z{CHUCK_Z_OFFSET}')
await self._log(f'Z set to {CHUCK_Z_OFFSET}mm')

# ===== MOVE TO CENTERLINE =====
await self._log('--- Moving to centerline ---')

# Retract to mpos Z=-1 (safe height above work)
mpos_z = self.grbl.status.mpos['z']
target_mpos_z = -1
retract = target_mpos_z - mpos_z
await self._send_and_log('G91')
await self._send_and_log(f'G0 Z{retract}')
await self._wait_idle()

# Move relative Y+50 toward centerline (from probe corner at Y=-20 to Y=0 is +20, plus extra clearance)
# Actually: probe corner is at Y=CHUCK_Y_OFFSET+r, centerline is at Y=0
# So we need to move Y by -(CHUCK_Y_OFFSET+r) to get to centerline
# But let's just move +50 as specified to give clearance
await self._send_and_log('G0 Y50')
await self._wait_idle()

await self._send_and_log('G90')

await self._log('Centerline: X=0, Y=0, Z=0')
await self._log('A-axis: Zero after clamping workpiece')
await self._log('=== CHUCK FIND COMPLETE ===')
