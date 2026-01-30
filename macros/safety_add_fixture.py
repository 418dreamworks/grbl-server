# Add Fixture
# Records current position as a fixture (no-go zone)
# Then moves tool around fixture perimeter for visual confirmation

# Get current work position
x = self.grbl.status.wpos['x']
y = self.grbl.status.wpos['y']
z = self.grbl.status.wpos['z']

await self._log(f'=== ADD FIXTURE at X{x:.3f} Y{y:.3f} Z{z:.3f} ===')

# TODO: Store fixture in self.fixtures list
# TODO: Move tool around fixture perimeter for visual
# TODO: Broadcast fixture update to client

await self._log('Fixture added (stub - needs implementation)')
await self._log('=== ADD FIXTURE COMPLETE ===')
