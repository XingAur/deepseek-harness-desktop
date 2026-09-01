import { useSyncExternalStore, type ReactNode } from 'react'
import type { ExtensionCenterState } from './extension-center-state'
import type { LocalProjectsState } from './local-projects-state'

export interface SidebarFooterActionsProps {
  wide: boolean
  localProjects: LocalProjectsState
  extensionCenter: ExtensionCenterState
}

interface FooterButtonProps {
  label: string
  wide: boolean
  active: boolean
  onClick(): void
  children: ReactNode
}

function FooterButton({ label, wide, active, onClick, children }: FooterButtonProps) {
  return (
    <button
      type="button"
      className={`dshDesktopFooterAction${wide ? '' : ' is-rail'}${active ? ' is-active' : ''}`}
      aria-label={label}
      aria-pressed={active}
      title={wide ? undefined : label}
      onClick={onClick}
    >
      {children}
      {wide && <span className="dshDesktopFooterActionLabel">{label}</span>}
    </button>
  )
}

/**
 * 侧边栏 footer 的桌面功能按钮组：单一槽位注册，
 * 内部竖排单列，与官方「设置」按钮的纵向节奏一致；窄栏（rail）模式只显图标。
 */
export function SidebarFooterActions({ wide, localProjects, extensionCenter }: SidebarFooterActionsProps) {
  const projectsOpen = useSyncExternalStore(localProjects.subscribe, localProjects.getSnapshot)
  const centerOpen = useSyncExternalStore(extensionCenter.subscribe, extensionCenter.getSnapshot)
  return (
    <div className={`dshDesktopFooterActions${wide ? '' : ' is-rail'}`} role="group" aria-label="桌面功能">
      <FooterButton label="本地项目" wide={wide} active={projectsOpen} onClick={() => localProjects.toggle()}>
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none">
          <path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z" />
          <path d="m4 7.5 8 4.5 8-4.5M12 12v9" />
        </svg>
      </FooterButton>
      <FooterButton label="扩展中心" wide={wide} active={centerOpen} onClick={() => extensionCenter.toggle()}>
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none">
          <path d="M10 4h4v4h4v4h-4v4h-4v-4H6V8h4V4Z" />
          <path d="m16.5 15.5 3 3-3 3-3-3 3-3Z" />
        </svg>
      </FooterButton>
    </div>
  )
}
