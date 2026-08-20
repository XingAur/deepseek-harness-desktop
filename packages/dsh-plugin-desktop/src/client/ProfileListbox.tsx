import { useEffect, useId, useRef, useState } from 'react'
import type { ProfileStatus, ProfileSummary } from './ProfileSelector'

export interface ProfileListboxProps {
  profiles: readonly ProfileSummary[]
  selectedId: string
  pending: boolean
  status(profile: ProfileSummary): ProfileStatus
  onSelect(profileId: string): Promise<void>
}

export function ProfileListbox({ profiles, selectedId, pending, status, onSelect }: ProfileListboxProps) {
  const id = useId()
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const listboxRef = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(0)
  const selectedIndex = Math.max(0, profiles.findIndex((profile) => profile.id === selectedId))
  const selected = profiles[selectedIndex]

  useEffect(() => {
    if (!open) return
    listboxRef.current?.focus()
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [open])

  useEffect(() => {
    if (pending) setOpen(false)
  }, [pending])

  const show = (index = selectedIndex) => {
    if (pending || profiles.length === 0) return
    setActiveIndex(index)
    setOpen(true)
  }

  const choose = async (index: number) => {
    const profile = profiles[index]
    if (profile === undefined || pending) return
    setOpen(false)
    await onSelect(profile.id)
    triggerRef.current?.focus()
  }

  const onTriggerKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp' || event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      show(event.key === 'ArrowUp' ? profiles.length - 1 : selectedIndex)
    }
  }

  const onListboxKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape' || event.key === 'Tab') {
      setOpen(false)
      if (event.key === 'Escape') {
        event.preventDefault()
        triggerRef.current?.focus()
      }
      return
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp' || event.key === 'Home' || event.key === 'End') {
      event.preventDefault()
      setActiveIndex((current) => {
        if (event.key === 'Home') return 0
        if (event.key === 'End') return profiles.length - 1
        const direction = event.key === 'ArrowDown' ? 1 : -1
        return (current + direction + profiles.length) % profiles.length
      })
      return
    }
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      void choose(activeIndex)
    }
  }

  if (profiles.length === 1 && selected !== undefined) {
    return (
      <div className="dshDesktopProfileControl">
        <div className="dshDesktopProfileTrigger dshDesktopProfileTriggerStatic" aria-label={`当前 Profile：${selected.name}`}>
          <span className="dshDesktopProfileStatusDot" data-status={status(selected)} />
          <span className="dshDesktopProfileTriggerCopy">
            <strong>{selected.name}</strong>
            <small>{selected.runtimeVersion ? `Runtime v${selected.runtimeVersion}` : '当前 Profile'}</small>
          </span>
        </div>
      </div>
    )
  }

  return (
    <div ref={rootRef} className="dshDesktopProfileControl">
      <button
        ref={triggerRef}
        type="button"
        className="dshDesktopProfileTrigger"
        aria-label={`Profile：${selected?.name ?? '正在读取'}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={`${id}-listbox`}
        disabled={pending || selected === undefined}
        onClick={() => open ? setOpen(false) : show()}
        onKeyDown={onTriggerKeyDown}
      >
        <span className="dshDesktopProfileStatusDot" data-status={selected === undefined ? 'ready' : status(selected)} />
        <span className="dshDesktopProfileTriggerCopy">
          <strong>{selected?.name ?? '正在读取…'}</strong>
          <small>{selected?.runtimeVersion ? `Runtime v${selected.runtimeVersion}` : 'Profile'}</small>
        </span>
        <span className="dshDesktopProfileChevron" aria-hidden="true">⌄</span>
      </button>
      {open && (
        <div
          ref={listboxRef}
          id={`${id}-listbox`}
          className="dshDesktopProfileListbox"
          role="listbox"
          aria-label="选择 Profile"
          aria-activedescendant={`${id}-option-${activeIndex}`}
          tabIndex={-1}
          onKeyDown={onListboxKeyDown}
        >
          {profiles.map((profile, index) => {
            const itemStatus = status(profile)
            const selectedItem = profile.id === selectedId
            return (
              <button
                key={profile.id}
                id={`${id}-option-${index}`}
                type="button"
                role="option"
                aria-selected={selectedItem}
                data-active={index === activeIndex || undefined}
                onPointerMove={() => setActiveIndex(index)}
                onClick={() => void choose(index)}
              >
                <span className="dshDesktopProfileStatusDot" data-status={itemStatus} />
                <span>
                  <strong>{profile.name}</strong>
                  <small>{profile.permissionMode === 'read-only' ? '只读' : '工作区可写'} · {statusCopy(itemStatus)}</small>
                </span>
                {selectedItem && <span aria-hidden="true">✓</span>}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

function statusCopy(status: ProfileStatus) {
  return ({ active: '已启用', switching: '切换中', recovered: '上次可用', invalid: '需要修复', ready: '可切换' })[status]
}
