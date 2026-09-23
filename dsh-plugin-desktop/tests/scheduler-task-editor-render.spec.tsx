/**
 * Server-render smoke for the task editor React app: the module graph loads
 * in plain Node and the initial render completes without a browser, catching
 * top-level or render-path browser access and broken JSX composition.
 */

import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { TaskEditorApp } from '../src/native-ui/task-editor/App.tsx'
import { taskEditorCopy } from '../src/task-editor-copy.ts'

describe('task editor app render smoke', () => {
  it('renders the loading shell without a browser environment', () => {
    const markup = renderToStaticMarkup(<TaskEditorApp />)
    expect(markup).toContain('dshNativeContent')
    expect(markup.length).toBeGreaterThan(200)
  })

  it('ships both locale dictionaries with every schedule template labeled', () => {
    for (const locale of ['zh', 'en'] as const) {
      const copy = taskEditorCopy(locale)
      expect(copy.templates.length).toBeGreaterThan(3)
      for (const template of copy.templates) {
        expect(template.label.length).toBeGreaterThan(0)
        expect(template.rule.kind === 'cron'
          ? template.rule.expression.length
          : template.rule.seconds).toBeGreaterThan(0)
      }
    }
  })
})
