# Chuck Z - Set Z=0 at rotary center
# User sets Z=0 on jaw surface

await self._log('=== CHUCK Z ===')
await self._wait_idle()

CHUCK_Z_OFFSET = -25.525
z = self.grbl.status.wpos['z']
await self._send_and_log(f'G10 L20 P1 Z{z - CHUCK_Z_OFFSET:.3f}')
await self._log(f'Z offset: {z:.3f} -> {z - CHUCK_Z_OFFSET:.3f} (jaw-to-center={CHUCK_Z_OFFSET})')

await self._log('=== CHUCK Z COMPLETE ===')
