# Window state machine

Implementation: `desktop/src/mode-manager.js` (pure, unit-tested in
`desktop/src/mode-manager.test.js`). Applied to real windows by
`desktop/src/main.js`'s `applyMode()`.

## States

| Mode | Overlay window | Command Center window |
|---|---|---|
| `HIDDEN` | hidden | hidden |
| `OVERLAY_COMPACT` | shown, compact card | hidden |
| `OVERLAY_EXPANDED` | shown, expanded card | hidden |
| `COMMAND_CENTER_WINDOWED` | hidden | shown, normal |
| `COMMAND_CENTER_FULLSCREEN` | hidden | shown, `setFullScreen(true)` |

`OVERLAY_COMPACT`/`OVERLAY_EXPANDED` are both "the overlay window is
visible"; which card renders is a renderer-side concern (`Overlay.tsx`'s
existing `mode` prop), not a separate Electron window. This keeps "opening
the Command Center does not leave an unwanted invisible overlay intercepting
mouse input" (brief §7) structurally true — the overlay `BrowserWindow` is
actually hidden, not just visually empty, whenever the Command Center is
active.

## Transitions

```
                    ┌─────────────────────────────┐
                    │                               │
                    ▼                               │
   ┌─────────┐  ┌──────────────────┐  ┌──────────────────────────┐
   │ HIDDEN  │◄─┤ OVERLAY_COMPACT   │◄─┤ OVERLAY_EXPANDED          │
   └────┬────┘  └─────────┬─────────┘  └───────────────────────────┘
        │                 │        ▲
        │                 │        │
        ▼                 ▼        │
   ┌──────────────────────────┐    │
   │ COMMAND_CENTER_WINDOWED  │────┘
   └───────────┬──────────────┘
               │        ▲
               ▼        │
   ┌──────────────────────────────┐
   │ COMMAND_CENTER_FULLSCREEN    │
   └───────────────────────────────┘
```

Rules encoded in `TRANSITIONS` (`mode-manager.js`):

- Every mode can reach `HIDDEN` directly — the tray "hide" / panic path must
  never be blocked by whatever mode the app happens to be in.
- Every mode except `OVERLAY_COMPACT` itself can reach `OVERLAY_COMPACT`
  directly — "a clear, consistent way to return to the compact overlay"
  (brief §6) from anywhere.
- `HIDDEN` can only start into `OVERLAY_COMPACT` or
  `COMMAND_CENTER_WINDOWED` — never straight into expanded overlay or
  full-screen Command Center, so a cold start or restore-from-tray always
  lands in the smaller, less disruptive state first.
- `OVERLAY_COMPACT ⇄ OVERLAY_EXPANDED` — the expand/collapse pair.
- `COMMAND_CENTER_WINDOWED ⇄ COMMAND_CENTER_FULLSCREEN` — the
  maximize/restore pair.

## Concurrency

`beginTransition(target)` throws if a transition is already in flight
(`transitioning` flag) — this is the mechanism behind brief §7's "avoid
simultaneous conflicting mode transitions". The mode only becomes current,
and `onChange` listeners only fire, when the caller invokes the returned
`settle()` — in `main.js` that happens synchronously after `applyMode()`
succeeds, so a mode is never reported as current while its windows are
still being created/shown/hidden. If `applyMode()` throws, `settle()` in the
`finally` block still fires (the transition is considered "handled", not
silently stuck) — window-level errors surface via Electron's own logging,
not by leaving the state machine wedged mid-transition.

## Persistence

`window-state-store.js` persists per-mode window bounds
(`overlay.{x,y,width,height}`, `commandCenter.{x,y,width,height,maximized}`),
`initialMode`, and `lastActiveWorkspace` to
`app.getPath('userData')/window-state.json`. On restore, `resolveBounds()`
checks the saved position against `screen.getAllDisplays()`
(`isBoundsVisible`, minimum 40px overlap with some display's work area); if
the saved monitor is gone or the position is otherwise off-screen, it
re-centers on the primary display instead of restoring an invisible window —
this is the direct implementation of brief §7/§8's "never restore a window
entirely outside the visible desktop."
