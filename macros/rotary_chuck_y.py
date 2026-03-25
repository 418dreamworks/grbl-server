# Chuck Y - Set Y=0 at rotary center
# User positions tool edge against reference surface, clicks ChuckY
# Tool center is at r from edge, center is (r + REF_EDGE_TO_CENTER) south of tool

r = self.tool_diameter / 2

await self._log('=== CHUCK Y ===')
await self._wait_idle()

REF_EDGE_TO_CENTER = 20.050
CHUCK_Y_OFFSET = r + REF_EDGE_TO_CENTER
await self._send_and_log(f'G10 L20 P1 Y{-CHUCK_Y_OFFSET:.3f}')
await self._log(f'Y set to {-CHUCK_Y_OFFSET:.3f} (r={r:.3f} + ref-to-center={REF_EDGE_TO_CENTER})')

await self._log('=== CHUCK Y COMPLETE ===')
