import { expect, test } from 'vitest'

test('native dialogs throw in tests, so none can slip back in', () => {
  // eslint-disable-next-line no-alert -- the tripwire must call it to prove it throws
  expect(() => window.alert('hi')).toThrow(/banned/)
  // eslint-disable-next-line no-alert -- the tripwire must call it to prove it throws
  expect(() => window.confirm('sure?')).toThrow(/banned/)
  // eslint-disable-next-line no-alert -- the tripwire must call it to prove it throws
  expect(() => window.prompt('name?')).toThrow(/banned/)
})
