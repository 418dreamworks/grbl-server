# Rotary Chuck Find
# Zero the A axis at chuck position

await self._log('=== CHUCK FIND START ===')
await self._send_and_log('G10 L20 P1 A0')
await self._log('A axis zeroed at chuck')
await self._log('=== CHUCK FIND COMPLETE ===')
