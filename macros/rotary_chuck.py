# Rotary Chuck Find
# User sets Z=0 on jaw surface, Y=0 with tool edge against reference surface
# Applies calibrated offsets to set Y=0 at rotary center, Z=0 at rotary center

r = self.tool_diameter / 2

await self._log('=== CHUCK FIND ===')
await self._wait_idle()

# Y offset: tool center is at r from reference edge
REF_EDGE_TO_CENTER = 20.050
CHUCK_Y_OFFSET = r + REF_EDGE_TO_CENTER
y = self.grbl.status.wpos['y']
await self._send_and_log(f'G10 L20 P1 Y{y - CHUCK_Y_OFFSET:.3f}')
await self._log(f'Y offset: {y:.3f} -> {y - CHUCK_Y_OFFSET:.3f} (r={r:.3f} + ref-to-center={REF_EDGE_TO_CENTER})')

# Z offset: center is in -Z direction from jaw surface
CHUCK_Z_OFFSET = -25.525
z = self.grbl.status.wpos['z']
await self._send_and_log(f'G10 L20 P1 Z{z - CHUCK_Z_OFFSET:.3f}')
await self._log(f'Z offset: {z:.3f} -> {z - CHUCK_Z_OFFSET:.3f} (jaw-to-center={CHUCK_Z_OFFSET})')

await self._log('=== CHUCK FIND COMPLETE ===')
