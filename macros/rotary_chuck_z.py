# Chuck Z - Set Z=0 at rotary center
# User positions tool on jaw surface, clicks ChuckZ
# Jaw is 25.525mm above center, so current position = Z 25.525

await self._log('=== CHUCK Z ===')
await self._wait_idle()

JAW_TO_CENTER = 25.525
await self._send_and_log(f'G10 L20 P1 Z{JAW_TO_CENTER:.3f}')
await self._log(f'Z set to {JAW_TO_CENTER:.3f} (jaw surface = {JAW_TO_CENTER}mm above center)')

await self._log('=== CHUCK Z COMPLETE ===')
