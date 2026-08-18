# Titlebar Dragging and Workbench Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the permanent 30px Tauri titlebar reliably draggable and remove the duplicated Electron caption space above the embedded DeepSeek Harness workbench.

**Architecture:** The trusted outer React shell handles native drag/maximize gestures through the existing narrow Tauri command boundary. The iframe desktop plugin becomes a pure three-column host for the unchanged upstream sidebar, conversation and details slots, with no native-caption rows or top padding of its own.

**Tech Stack:** Tauri 2.11, React 18, TypeScript 6, Vitest 4, DeepSeek Harness client slots.

---

## File structure

- `src/TitleBar.tsx`: translates primary-button press counts into native drag or maximize operations.
- `src/TitleBar.test.tsx`: specifies single press, double press and traffic-button event routing.
- `packages/dsh-plugin-desktop/src/client/AdvancedFrame.tsx`: hosts product slots without Electron caption elements.
- `packages/dsh-plugin-desktop/src/client/styles.ts`: sizes the three product surfaces from iframe `y = 0` while preserving market and resize styling.
- `packages/dsh-plugin-desktop/tests/advanced-frame.spec.tsx`: renders both platform variants and prevents caption elements from returning.
- `packages/dsh-plugin-desktop/tests/styles.spec.ts`: prevents caption rows and top padding from returning.
- `packages/dsh-plugin-desktop/package.json`, `package-lock.json`: bump the local plugin version so the managed profile installs the corrected client bundle.

### Task 1: Reliable trusted-titlebar gestures

**Files:**
- Modify: `src/TitleBar.test.tsx`
- Modify: `src/TitleBar.tsx`

- [ ] **Step 1: Replace the declarative-only drag test with failing gesture-routing tests**

```tsx
it('starts native dragging from blank primary-button title space', () => {
  const controls = fakeControls()
  render(<TitleBar controls={controls} />)
  fireEvent.mouseDown(screen.getByRole('banner'), { buttons: 1, detail: 1 })
  expect(controls.startDragging).toHaveBeenCalledOnce()
  expect(controls.toggleMaximize).not.toHaveBeenCalled()
})

it('toggles maximize instead of dragging on a primary-button double press', () => {
  const controls = fakeControls()
  render(<TitleBar controls={controls} />)
  fireEvent.mouseDown(screen.getByRole('banner'), { buttons: 1, detail: 2 })
  expect(controls.toggleMaximize).toHaveBeenCalledOnce()
  expect(controls.startDragging).not.toHaveBeenCalled()
})
```

Keep the traffic-light mapping test. In the button-routing test, send `fireEvent.mouseDown(screen.getByRole('button', { name: '关闭窗口' }), { buttons: 1, detail: 1 })` and assert that both `startDragging` and the header's `toggleMaximize` path remain uncalled.

- [ ] **Step 2: Run the focused test and confirm the red state**

Run: `npm test -- --run src/TitleBar.test.tsx`

Expected: FAIL because `startDragging` is never called and double-press behavior is still attached to `doubleClick`.

- [ ] **Step 3: Implement the manual Tauri titlebar event pattern**

```tsx
const handleMouseDown = (event: MouseEvent<HTMLElement>) => {
  if (event.buttons !== 1 || (event.target as HTMLElement).closest('button')) return
  if (event.detail === 2) void controls.toggleMaximize()
  else void controls.startDragging()
}

```

Replace the header's `onDoubleClick={toggleMaximize}` prop with `onMouseDown={handleMouseDown}` and delete the old `toggleMaximize` handler.

- [ ] **Step 4: Run the focused test and confirm the green state**

Run: `npm test -- --run src/TitleBar.test.tsx`

Expected: 4 titlebar tests pass; blank-area single press drags, double press maximizes, and traffic buttons do neither through the header.

### Task 2: Remove the duplicated iframe caption row

**Files:**
- Create: `packages/dsh-plugin-desktop/tests/advanced-frame.spec.tsx`
- Create: `packages/dsh-plugin-desktop/tests/styles.spec.ts`
- Modify: `packages/dsh-plugin-desktop/src/client/AdvancedFrame.tsx`
- Modify: `packages/dsh-plugin-desktop/src/client/styles.ts`

- [ ] **Step 1: Add failing platform frame tests**

```tsx
import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AdvancedFrame } from '../src/client/AdvancedFrame'
import { DesktopLayoutState } from '../src/client/layout-state'

class ResizeObserverStub {
  observe() {}
  disconnect() {}
}
vi.stubGlobal('ResizeObserver', ResizeObserverStub)

for (const platform of ['win32', 'darwin'] as const) {
  it(`does not reserve an inner ${platform} caption row`, () => {
    const { container } = render(
      <AdvancedFrame
        layout={new DesktopLayoutState()}
        platform={platform}
        renderSlot={(name) => <div data-slot={name} />}
        useSessions={(selector) => selector({ byId: {} })}
      />,
    )
    expect(container.querySelector('.dshDesktopWindowsCaptionRow')).toBeNull()
    expect(container.querySelector('.dshDesktopMacCaptionRow')).toBeNull()
    expect(container.querySelector('[data-slot="sidebar"]')).toBeInTheDocument()
    expect(container.querySelector('[data-slot="conversation"]')).toBeInTheDocument()
  })
}
```

- [ ] **Step 2: Add a failing injected-style regression test**

```ts
import { describe, expect, it } from 'vitest'
import { installAdvancedStyles } from '../src/client/styles'

it('starts product surfaces at the top of the iframe', () => {
  const dispose = installAdvancedStyles()
  const css = document.getElementById('dsh-desktop-advanced-styles')?.textContent ?? ''
  expect(css).not.toContain('CaptionRow')
  expect(css).not.toMatch(/padding-top:\s*(48|58)px/)
  expect(css).toContain('.dshDesktopResizeHandle { position: absolute; top: 0;')
  dispose()
})
```

- [ ] **Step 3: Run both focused tests and confirm the red state**

Run: `npm run plugin:test -- --run tests/advanced-frame.spec.tsx tests/styles.spec.ts`

Expected: FAIL because `AdvancedFrame` renders caption elements and the stylesheet reserves 48/58px.

- [ ] **Step 4: Remove caption elements and top offsets**

Delete both conditional caption elements from `AdvancedFrame.tsx`. In `styles.ts`, delete `.dshDesktopMacCaptionRow` / `.dshDesktopWindowsCaptionRow`, remove all three surface `padding-top` declarations, and change the resize handle start from `top: 48px` to `top: 0`. Preserve platform sidebar backgrounds, three-column surfaces, market styles and overlay behavior.

- [ ] **Step 5: Run focused plugin tests and confirm the green state**

Run: `npm run plugin:test -- --run tests/advanced-frame.spec.tsx tests/styles.spec.ts`

Expected: both new test files pass.

### Task 3: Deliver and verify the corrected managed plugin

**Files:**
- Modify: `packages/dsh-plugin-desktop/package.json`
- Modify: `package-lock.json`
- Generated local artifact only: `runtime-build/windows-x86_64-layout/dsh-runtime-windows-x86_64.zip`
- Local development manifest only: `runtime/manifests/runtime-windows-x86_64.json`

- [ ] **Step 1: Bump the desktop plugin from 0.1.2 to 0.1.3**

Set the workspace package version and its `package-lock.json` workspace entry to `0.1.3`. This forces the Runtime launcher to replace the profile's cached `0.1.2` plugin.

- [ ] **Step 2: Run the complete repository gate**

Run: `npm run check`

Expected: root tests, plugin tests, TypeScript/Vite build and plugin build all pass without warnings.

Run: `cargo fmt -- --check && cargo test` from `src-tauri`.

Expected: formatting passes and all Rust tests pass, including Runtime cleanup, redaction, diagnostics and health deadline tests.

- [ ] **Step 3: Build a local Windows Runtime archive**

Run:

```powershell
npm run runtime:build -- --target=windows-x86_64 --version=0.1.5-local --url=file:///D:/TraeCode/deepseek-harness-desktop/runtime-build/windows-x86_64-layout/dsh-runtime-windows-x86_64.zip --output=runtime-build/windows-x86_64-layout
```

Expected: archive and unsigned manifest are produced, and the build's isolated profile reports `@dsh/desktop-plugin` version `0.1.3`.

- [ ] **Step 4: Sign the local manifest without making it a release artifact**

Run:

```powershell
$env:DSH_DESKTOP_SIGNING_PRIVATE_KEY = 'wbAbExHsjryIT22fTuRA3W61tJdaXFC7YxoAeN9uKnQ'
$env:DSH_DESKTOP_SIGNING_PUBLIC_KEY = 'cmFlmJvjXIrMN8AbIXxF2c6Gnpt9rDFd_Zhbl0U7AlI'
node scripts/sign-manifest.mjs runtime-build/windows-x86_64-layout/manifest-windows-x86_64.unsigned.json runtime/manifests/runtime-windows-x86_64.json
```

Expected: the ignored local manifest has a non-empty signature and its URL, size and SHA-256 point to the new layout archive. Do not stage or commit this absolute `D:` manifest.

- [ ] **Step 5: Run the corrected Tauri application and verify the Windows interaction**

Start the development app with the local manifest. In a restored window, drag the blank 30px titlebar area across the centered title and confirm the native window position changes. Double-click the same area and confirm maximize/restore. Confirm the sidebar and conversation surface begin immediately below the outer titlebar, the no-workspace state remains official, and an actual session uses the official active-conversation surface.

- [ ] **Step 6: Check the final diff and artifact boundaries**

Run: `git diff --check` and `git status --short`.

Expected: no whitespace errors; the ignored/local absolute manifest and Runtime ZIP are not included in the intended merge scope.
