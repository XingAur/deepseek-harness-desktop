import { describe, expect, it } from 'vitest'
import { parseTaskEditorAction } from '../src/task-editor-window.ts'
import { parseNlScheduleAnswer } from '../src/scheduler/nl-schedule.ts'

describe('parseTaskEditorAction', () => {
  it('parses every documented action', () => {
    expect(parseTaskEditorAction('dsh-task-editor://close')).toEqual({ action: 'close' })
    expect(parseTaskEditorAction('dsh-task-editor://submit?task=%7B%22id%22%3A1%7D')).toEqual({
      action: 'submit',
      task: '{"id":1}',
    })
    expect(parseTaskEditorAction('dsh-task-editor://preview?schedule=0%209%20*%20*%20*')).toEqual({
      action: 'preview',
      schedule: '0 9 * * *',
    })
    expect(parseTaskEditorAction('dsh-task-editor://remove?id=t1')).toEqual({ action: 'remove', id: 't1' })
    expect(parseTaskEditorAction('dsh-task-editor://run?id=t1')).toEqual({ action: 'run', id: 't1' })
    expect(parseTaskEditorAction('dsh-task-editor://enable?id=t1&enabled=1')).toEqual({
      action: 'enable',
      id: 't1',
      enabled: true,
    })
    expect(parseTaskEditorAction('dsh-task-editor://enable?id=t1&enabled=0')).toEqual({
      action: 'enable',
      id: 't1',
      enabled: false,
    })
    expect(parseTaskEditorAction('dsh-task-editor://interpret?text=%E6%AF%8F%E5%A4%A9')).toEqual({
      action: 'interpret',
      text: '每天',
    })
    expect(parseTaskEditorAction('dsh-task-editor://pick-directory')).toEqual({ action: 'pick-directory' })
  })

  it('rejects foreign schemes and malformed payloads', () => {
    expect(parseTaskEditorAction('https://example.com/close')).toBeUndefined()
    expect(parseTaskEditorAction('dsh-task-editor://close?extra=1')).toBeUndefined()
    expect(parseTaskEditorAction('dsh-task-editor://remove')).toBeUndefined()
    expect(parseTaskEditorAction('dsh-task-editor://submit')).toBeUndefined()
    expect(parseTaskEditorAction('dsh-task-editor://enable?id=t1')).toBeUndefined()
    expect(parseTaskEditorAction('dsh-task-editor://unknown')).toBeUndefined()
    expect(parseTaskEditorAction('dsh-task-editor://close/path')).toBeUndefined()
    expect(parseTaskEditorAction('not a url')).toBeUndefined()
  })
})

describe('parseNlScheduleAnswer', () => {
  it('accepts a valid cron answer with explanation', () => {
    expect(parseNlScheduleAnswer('cron 0 9 * * * | 工作日早九点')).toEqual({
      expression: '0 9 * * *',
      explanation: '工作日早九点',
    })
    expect(parseNlScheduleAnswer('cron 30 8 * * 1-5')).toEqual({
      expression: '30 8 * * 1-5',
      explanation: null,
    })
  })

  it('picks the cron line from a chatty answer', () => {
    const answer = 'Sure!\ncron 0 22 * * * | nightly at ten\nHope that helps.'
    expect(parseNlScheduleAnswer(answer)).toEqual({
      expression: '0 22 * * *',
      explanation: 'nightly at ten',
    })
  })

  it('rejects non-schedules and unusable expressions', () => {
    expect(parseNlScheduleAnswer('none')).toBeNull()
    expect(parseNlScheduleAnswer('The user wants something else.')).toBeNull()
    expect(parseNlScheduleAnswer('cron @daily')).toBeNull()
    expect(parseNlScheduleAnswer('cron 0 25 * * *')).toBeNull()
    expect(parseNlScheduleAnswer('cron')).toBeNull()
    expect(parseNlScheduleAnswer('')).toBeNull()
  })
})
