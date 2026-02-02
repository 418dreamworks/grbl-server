# Clear All Fixtures
# Removes all stored fixtures

await self._log('=== CLEAR ALL FIXTURES ===')

count = len(self.fixtures)
self.fixtures = []

await self.broadcast_fixtures()

await self._log(f'Cleared {count} fixtures')
await self._log('=== CLEAR COMPLETE ===')
