# Rotary Tailstock Find
# Zero the A axis at tailstock position

await self._log('=== TAILSTOCK FIND START ===')
await self._send_and_log('G10 L20 P1 A0')
await self._log('A axis zeroed at tailstock')
await self._log('=== TAILSTOCK FIND COMPLETE ===')
