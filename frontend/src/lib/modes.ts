// Which modes each page offers. The server decides what it will accept
// (app/modes.py); these lists decide what is worth showing, and they are kept
// apart because the two answers differ: a mode can be retired from the composer
// while old clips still carry it.
import type { Mode } from '@/api/types'

/** Offered in the composer. Grows as each mode gets a workflow that can render it. */
export const COMPOSE_MODES: Mode[] = ['i2v', 't2v']

/** Anything a stored clip might carry, so older work stays filterable. */
export const FILTER_MODES: Mode[] = ['i2v', 't2v', 'r2v']
