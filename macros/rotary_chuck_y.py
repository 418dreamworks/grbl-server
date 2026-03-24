# Chuck Y - Set Y=0 at rotary center
# User sets Y=0 with tool edge against reference surface

r = self.tool_diameter / 2

await self._log('=== CHUCK Y ===')
await self._wait_idle()

REF_EDGE_TO_CENTER = 20.050
CHUCK_Y_OFFSET = r + REF_EDGE_TO_CENTER
y = self.grbl.status.wpos['y']
await self._send_and_log(f'G10 L20 P1 Y{y - CHUCK_Y_OFFSET:.3f}')
await self._log(f'Y offset: {y:.3f} -> {y - CHUCK_Y_OFFSET:.3f} (r={r:.3f} + ref-to-center={REF_EDGE_TO_CENTER})')

await self._log('=== CHUCK Y COMPLETE ===')
