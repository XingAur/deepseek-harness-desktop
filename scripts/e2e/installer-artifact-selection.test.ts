import { describe, expect, it } from 'vitest'
import { selectChangedInstaller } from './installer-artifact-selection.mjs'

describe('installer artifact selection', () => {
  it('selects a unique newly added executable', () => {
    expect(selectChangedInstaller(new Map(), new Map([['new.exe', 'a']]))).toBe('new.exe')
  })

  it('selects a unique executable whose content hash changed', () => {
    expect(selectChangedInstaller(new Map([['same.exe', 'old']]), new Map([['same.exe', 'new']]))).toBe('same.exe')
  })

  it('rejects builds with no changed executable', () => {
    expect(() => selectChangedInstaller(new Map([['old.exe', 'a']]), new Map([['old.exe', 'a']]))).toThrow('没有生成')
  })

  it('rejects ambiguous builds with multiple changed executables', () => {
    expect(() => selectChangedInstaller(new Map(), new Map([['one.exe', 'a'], ['two.exe', 'b']]))).toThrow('one.exe, two.exe')
  })
})
