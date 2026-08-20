const STYLE_ID = 'dsh-desktop-advanced-styles'

export function installAdvancedStyles(): () => void {
  document.getElementById(STYLE_ID)?.remove()
  const style = document.createElement('style')
  style.id = STYLE_ID
  style.textContent = `
    html, body, #root { width: 100%; height: 100%; margin: 0; overflow: hidden; background: transparent !important; }
    body[data-dsh-desktop-mode="advanced"] { color: var(--dsw-alias-label-primary, #f4f4f5); --dsh-desktop-divider: var(--dsw-alias-border-l2, rgba(255,255,255,.08)); }
    body[data-dsh-desktop-mode="advanced"][data-dsh-desktop-theme="light"] { --dsh-desktop-divider: rgba(29,38,58,.035); }
    .dshDesktopFrame { position: relative; display: grid; width: 100%; height: 100%; min-width: 0; background: var(--dsw-alias-bg-base, #151517); }
    .dshDesktopSidebarSurface, .dshDesktopConversationSurface, .dshDesktopDetailsSurface { min-width: 0; min-height: 0; overflow: hidden; border-color: var(--dsh-desktop-divider); }
    .dshDesktopSidebarSurface { position: relative; display: flex; flex-direction: column; background: var(--dsw-alias-bg-layer-2, #2c2c2f); border-right: 1px solid var(--dsh-desktop-divider); }
    [data-desktop-platform="darwin"] .dshDesktopSidebarSurface { background: color-mix(in srgb, var(--dsw-alias-bg-layer-2, #262629) 88%, transparent); }
    .dshDesktopUpstreamSidebar { flex: 1; min-height: 0; }
    .dshDesktopProjectsEntry { display: flex; align-items: center; gap: 10px; min-height: 44px; margin: 10px 12px 14px; padding: 0 15px; border: 1px solid transparent; border-radius: 10px; color: var(--dsw-alias-label-primary, #e8edf2); background: color-mix(in srgb, currentColor 3.5%, transparent); font: 500 15px/1 system-ui, sans-serif; text-align: left; cursor: pointer; transition: color .15s ease, background .15s ease, border-color .15s ease; }
    .dshDesktopProjectsEntry svg { width: 18px; height: 18px; flex: 0 0 18px; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
    .dshDesktopProjectsEntry:hover { background: color-mix(in srgb, currentColor 7.5%, transparent); }
    .dshDesktopProjectsEntry[data-active="true"] { border-color: color-mix(in srgb, #6f8fe9 40%, transparent); background: color-mix(in srgb, #6482dc 16%, transparent); }
    .dshDesktopProjectsEntry[data-collapsed="true"] { width: 44px; min-height: 44px; justify-content: center; margin: 8px auto 14px; padding: 0; border-radius: 11px; }
    .dshDesktopProjectsEntry:focus-visible { outline: 2px solid #7d9cf0; outline-offset: 2px; }
    .dshDesktopConversationSurface { background: var(--dsw-alias-bg-base, #141416); }
    .dshDesktopDetailsSurface { background: var(--dsw-alias-bg-layer-1, #1c1c1f); border-left: 1px solid var(--dsh-desktop-divider); }
    .dshDesktopOverlay { position: absolute; inset: 0; pointer-events: none; z-index: 30; }
    .dshDesktopOverlay > * { pointer-events: auto; }
    .dshDesktopResizeHandle { position: absolute; top: 0; bottom: 0; width: 7px; margin-left: -3px; z-index: 20; cursor: col-resize; }
    .dshDesktopProjectsPage { box-sizing: border-box; height: 100%; overflow: auto; padding: 30px 38px 0; color: var(--dsw-alias-label-primary, #ececf0); background: var(--dsw-alias-bg-base, #151517); }
    .dshDesktopProjectsPageInner { width: min(1180px, 100%); min-height: 100%; margin: 0 auto; }
    .dshDesktopProjectsHeader { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; padding-bottom: 20px; border-bottom: 1px solid var(--dsh-desktop-divider); }
    .dshDesktopProjectsHeader p { margin: 0 0 8px; color: #7196ff; font-size: 11px; font-weight: 700; letter-spacing: .14em; }
    .dshDesktopProjectsHeader h1 { margin: 0; font-size: 30px; font-weight: 600; }
    .dshDesktopProjectsHeader span { display: block; margin-top: 8px; color: var(--dsw-alias-label-tertiary, #85858d); font-size: 13px; }
    .dshDesktopProjectsHeader button { border: 0; color: var(--dsw-alias-label-tertiary, #8d8d95); background: transparent; font-size: 28px; cursor: pointer; }
    .dshDesktopProfileSelector { position: relative; z-index: 8; display: flex; align-items: center; gap: 12px; margin-top: 14px; }
    .dshDesktopProfileSelector[aria-busy="true"]::after { position: absolute; inset: 0; content: ""; pointer-events: none; background: linear-gradient(100deg, transparent 20%, color-mix(in srgb, #7196ff 9%, transparent) 45%, transparent 70%); animation: dshDesktopProfileSweep 1.25s ease-in-out infinite; }
    .dshDesktopProfileControl { position: relative; }
    .dshDesktopProfileTrigger { display: flex; align-items: center; gap: 9px; min-width: 205px; min-height: 40px; padding: 5px 9px; border: 1px solid var(--dsh-desktop-divider); border-radius: 999px; color: var(--dsw-alias-label-primary, #ececf0); background: color-mix(in srgb, var(--dsw-alias-bg-layer-1, #1d1d20) 88%, transparent); cursor: pointer; }
    .dshDesktopProfileTriggerStatic { cursor: default; }
    .dshDesktopProfileTrigger:focus-visible { outline: 2px solid #7196ff; outline-offset: 2px; }
    .dshDesktopProfileTrigger:disabled { cursor: default; opacity: .62; }
    .dshDesktopProfileTriggerCopy { display: grid; min-width: 0; flex: 1; text-align: left; }
    .dshDesktopProfileTriggerCopy strong { overflow: hidden; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
    .dshDesktopProfileTriggerCopy small { color: var(--dsw-alias-label-tertiary, #85858d); font-size: 10px; }
    .dshDesktopProfileChevron { color: var(--dsw-alias-label-tertiary, #85858d); font-size: 15px; }
    .dshDesktopProfileStatusDot { width: 7px; height: 7px; flex: 0 0 7px; border-radius: 99px; background: #6d7482; }
    .dshDesktopProfileStatusDot[data-status="active"] { background: #70c891; box-shadow: 0 0 0 3px color-mix(in srgb, #70c891 16%, transparent); }
    .dshDesktopProfileStatusDot[data-status="switching"] { background: #7196ff; }
    .dshDesktopProfileStatusDot[data-status="recovered"] { background: #d6ae67; }
    .dshDesktopProfileStatusDot[data-status="invalid"] { background: #d77b7b; }
    .dshDesktopProfileListbox { position: absolute; top: calc(100% + 7px); left: 0; z-index: 40; display: grid; width: 286px; max-height: 280px; overflow: auto; padding: 6px; border: 1px solid color-mix(in srgb, var(--dsh-desktop-divider) 80%, #7196ff 20%); border-radius: 12px; background: var(--dsw-alias-bg-layer-1, #1d1d20); box-shadow: 0 18px 46px rgba(0,0,0,.28); }
    .dshDesktopProfileListbox:focus { outline: none; }
    .dshDesktopProfileListbox [role="option"] { display: grid; grid-template-columns: 9px minmax(0, 1fr) 18px; align-items: center; gap: 9px; min-height: 48px; padding: 6px 9px; border: 0; border-radius: 8px; color: inherit; background: transparent; cursor: pointer; text-align: left; }
    .dshDesktopProfileListbox [role="option"][data-active="true"], .dshDesktopProfileListbox [role="option"][aria-selected="true"] { background: color-mix(in srgb, #6482dc 15%, transparent); }
    .dshDesktopProfileListbox [role="option"] > span:nth-child(2) { display: grid; }
    .dshDesktopProfileListbox [role="option"] strong { font-size: 12px; }
    .dshDesktopProfileListbox [role="option"] small { margin-top: 2px; color: var(--dsw-alias-label-tertiary, #85858d); font-size: 10px; }
    .dshDesktopProfileMeta { display: flex; align-items: center; gap: 8px; min-height: 34px; color: var(--dsw-alias-label-tertiary, #85858d); font-size: 12px; }
    .dshDesktopProfileMeta small { padding: 3px 7px; border-radius: 6px; color: #9ab2f5; background: color-mix(in srgb, #5b79cd 16%, transparent); }
    .dshDesktopProfileError { margin: 0 0 0 auto; color: #e8aaaa; font-size: 12px; }
    .dshDesktopProfileSettings { display: grid; gap: 18px; color: var(--dsw-alias-label-primary, #ececf0); }
    .dshDesktopProfileSettings > header { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; }
    .dshDesktopProfileSettings h2 { margin: 0; font-size: 20px; }
    .dshDesktopProfileSettings header p { margin: 6px 0 0; color: var(--dsw-alias-label-tertiary, #85858d); font-size: 13px; }
    .dshDesktopProfileSettings button, .dshDesktopProfileEditor input, .dshDesktopProfileEditor select { min-height: 34px; border: 1px solid var(--dsh-desktop-divider); border-radius: 8px; color: inherit; background: var(--dsw-alias-bg-layer-2, #29292e); }
    .dshDesktopProfileSettings button { padding: 0 12px; cursor: pointer; }
    .dshDesktopProfileSettings button:disabled { cursor: default; opacity: .48; }
    .dshDesktopProfileSettingsError { padding: 10px 12px; border-radius: 8px; color: #e8aaaa; background: color-mix(in srgb, #b75050 12%, transparent); }
    .dshDesktopProfileEditor { display: grid; grid-template-columns: 1fr 1.5fr 150px auto; align-items: end; gap: 10px; padding: 14px; border: 1px solid var(--dsh-desktop-divider); border-radius: 12px; background: var(--dsw-alias-bg-layer-1, #1d1d20); }
    .dshDesktopProfileEditor label { display: grid; gap: 5px; color: var(--dsw-alias-label-tertiary, #85858d); font-size: 12px; }
    .dshDesktopProfileEditor input, .dshDesktopProfileEditor select { box-sizing: border-box; width: 100%; padding: 0 9px; }
    .dshDesktopProfileEditor > div { display: flex; gap: 6px; }
    .dshDesktopProfileSettingsList { display: grid; gap: 10px; }
    .dshDesktopProfileSettingsList article { display: grid; grid-template-columns: minmax(140px, .8fr) minmax(180px, 1.4fr) auto; align-items: center; gap: 10px 18px; padding: 14px; border: 1px solid var(--dsh-desktop-divider); border-radius: 12px; }
    .dshDesktopProfileSettingsTitle { display: flex; align-items: center; gap: 8px; }
    .dshDesktopProfileSettingsTitle span { padding: 3px 6px; border-radius: 5px; color: #9ab2f5; background: color-mix(in srgb, #5b79cd 15%, transparent); font-size: 10px; }
    .dshDesktopProfileSettingsList p { overflow: hidden; margin: 0; color: var(--dsw-alias-label-secondary, #b7b7bf); text-overflow: ellipsis; white-space: nowrap; }
    .dshDesktopProfileSettingsList small { grid-column: 1 / 3; color: var(--dsw-alias-label-tertiary, #85858d); }
    .dshDesktopProfileSettingsActions { grid-column: 3; grid-row: 1 / 3; display: flex; gap: 6px; }
    .dshDesktopProjectGrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 14px; margin-top: 24px; }
    .dshDesktopProjectCard { position: relative; border: 1px solid var(--dsh-desktop-divider); border-radius: 15px; background: var(--dsw-alias-bg-layer-1, #1d1d20); animation: dshDesktopProjectEnter 180ms ease-out both; transition: transform 160ms ease, border-color 160ms ease, box-shadow 160ms ease; }
    .dshDesktopProjectCard:hover { transform: translateY(-2px); border-color: color-mix(in srgb, #7795e8 32%, var(--dsh-desktop-divider)); box-shadow: 0 10px 28px color-mix(in srgb, #23386f 14%, transparent); }
    .dshDesktopProjectCard[data-selected="true"] { border-color: color-mix(in srgb, #7196ff 68%, var(--dsh-desktop-divider)); box-shadow: 0 0 0 2px color-mix(in srgb, #7196ff 18%, transparent), 0 12px 30px color-mix(in srgb, #23386f 12%, transparent); }
    .dshDesktopProjectCard[data-recent="true"]::after { position: absolute; top: 14px; right: 14px; width: 7px; height: 7px; border-radius: 999px; content: ""; background: #7795e8; box-shadow: 0 0 0 0 color-mix(in srgb, #7795e8 34%, transparent); animation: dshDesktopRecentPulse 2.4s ease-out infinite; }
    .dshDesktopProjectCard[data-unavailable="true"] { border-color: color-mix(in srgb, #d27676 50%, var(--dsh-desktop-divider)); }
    .dshDesktopProjectCardSurface { overflow: hidden; border-radius: inherit; cursor: default; }
    .dshDesktopProjectCardSurface:focus-visible { outline: 2px solid #7196ff; outline-offset: 3px; }
    .dshDesktopProjectCover { height: 68px; display: flex; align-items: flex-end; padding: 12px 14px; box-sizing: border-box; background: var(--dsh-project-cover); }
    .dshDesktopProjectCover span { width: 30px; height: 30px; display: grid; place-items: center; border: 1px solid rgba(255,255,255,.24); border-radius: 9px; color: white; background: rgba(13,18,31,.28); backdrop-filter: blur(7px); }
    .dshDesktopProjectCard[data-cover="aurora-blue"], [data-cover="aurora-blue"] { --dsh-project-cover: linear-gradient(125deg, #294f96, #6c8ee3 58%, #8db8d8); }
    .dshDesktopProjectCard[data-cover="sunset"], [data-cover="sunset"] { --dsh-project-cover: linear-gradient(125deg, #8a3f5e, #d87461 56%, #e9b56e); }
    .dshDesktopProjectCard[data-cover="forest"], [data-cover="forest"] { --dsh-project-cover: linear-gradient(125deg, #245345, #3f8064 55%, #87a968); }
    .dshDesktopProjectCard[data-cover="graphite"], [data-cover="graphite"] { --dsh-project-cover: linear-gradient(125deg, #30343d, #5b616e 55%, #858c99); }
    .dshDesktopProjectCard[data-cover="violet"], [data-cover="violet"] { --dsh-project-cover: linear-gradient(125deg, #513674, #835ca6 55%, #b181bf); }
    .dshDesktopProjectCardBody { min-width: 0; padding: 14px 16px 15px; }
    .dshDesktopProjectCard h2 { overflow: hidden; margin: 0 0 6px; font-size: 16px; text-overflow: ellipsis; white-space: nowrap; }
    .dshDesktopProjectCardBody > p { overflow: hidden; margin: 0; color: var(--dsw-alias-label-tertiary, #85858d); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
    .dshDesktopProjectCardBody > input { box-sizing: border-box; width: 100%; min-height: 29px; margin: -5px 0 3px; padding: 0 7px; border: 1px solid #7196ff; border-radius: 6px; color: inherit; background: var(--dsw-alias-bg-layer-2, #29292e); }
    .dshDesktopProjectMeta { display: flex; justify-content: space-between; gap: 10px; margin: 15px 0 0; color: var(--dsw-alias-label-tertiary, #85858d); font-size: 11px; }
    .dshDesktopProjectMeta strong { color: #e49a9a; }
    .dshDesktopProjectRenameError { display: block; margin-top: 7px; color: #e8aaaa; }
    .dshDesktopProjectContextMenu { position: fixed; z-index: 80; display: grid; width: 188px; padding: 6px; border: 1px solid color-mix(in srgb, var(--dsh-desktop-divider) 72%, #7196ff 28%); border-radius: 11px; color: var(--dsw-alias-label-primary, #ececf0); background: color-mix(in srgb, var(--dsw-alias-bg-layer-1, #1d1d20) 96%, transparent); box-shadow: 0 18px 45px rgba(0,0,0,.32); backdrop-filter: blur(18px); }
    .dshDesktopProjectContextMenu > button, .dshDesktopProjectMenuBack { display: flex; width: 100%; min-height: 34px; align-items: center; justify-content: space-between; padding: 0 9px; border: 0; border-radius: 7px; color: inherit; background: transparent; cursor: pointer; text-align: left; }
    .dshDesktopProjectContextMenu > button:hover, .dshDesktopProjectContextMenu > button:focus-visible, .dshDesktopProjectMenuBack:hover { outline: none; background: color-mix(in srgb, #7196ff 14%, transparent); }
    .dshDesktopProjectContextMenu > button:disabled { opacity: .48; cursor: default; }
    .dshDesktopProjectMenuDivider { height: 1px; margin: 5px 4px; background: var(--dsh-desktop-divider); }
    .dshDesktopProjectContextMenu > .dshDesktopProjectMenuDanger { color: #ec9a9a; }
    .dshDesktopProjectCoverPicker { display: grid; gap: 5px; }
    .dshDesktopProjectCoverGrid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 5px; }
    .dshDesktopProjectCoverGrid button { display: grid; gap: 4px; padding: 5px; border: 1px solid transparent; border-radius: 7px; color: inherit; background: transparent; cursor: pointer; }
    .dshDesktopProjectCoverGrid button:focus-visible, .dshDesktopProjectCoverGrid button[aria-checked="true"] { outline: none; border-color: #7196ff; background: color-mix(in srgb, #7196ff 10%, transparent); }
    .dshDesktopProjectCoverGrid button > span { height: 31px; border-radius: 5px; background: var(--dsh-project-cover); }
    .dshDesktopProjectCoverGrid small { font-size: 10px; }
    .dshDesktopProjectDialogBackdrop { position: fixed; inset: 0; z-index: 120; display: grid; place-items: center; padding: 24px; background: rgba(7,9,14,.52); backdrop-filter: blur(7px); }
    .dshDesktopProjectDeleteDialog { box-sizing: border-box; width: min(470px, 100%); padding: 20px; border: 1px solid color-mix(in srgb, var(--dsh-desktop-divider) 75%, #8c99b8 25%); border-radius: 16px; color: var(--dsw-alias-label-primary, #ececf0); background: var(--dsw-alias-bg-layer-1, #1d1d20); box-shadow: 0 28px 80px rgba(0,0,0,.38); }
    .dshDesktopProjectDeleteDialog > header { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; }
    .dshDesktopProjectDeleteDialog > header p { margin: 0 0 4px; color: #e08f8f; font-size: 11px; font-weight: 700; letter-spacing: .08em; }
    .dshDesktopProjectDeleteDialog h2 { margin: 0; font-size: 21px; }
    .dshDesktopProjectDeleteDialog > header button { border: 0; color: var(--dsw-alias-label-tertiary, #85858d); background: transparent; font-size: 24px; cursor: pointer; }
    .dshDesktopProjectDeletePath { overflow: hidden; margin: 12px 0 16px; color: var(--dsw-alias-label-tertiary, #85858d); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
    .dshDesktopProjectDeleteDialog fieldset { display: grid; gap: 8px; margin: 0; padding: 0; border: 0; }
    .dshDesktopProjectDeleteDialog fieldset > label { display: grid; grid-template-columns: 18px 1fr; gap: 10px; padding: 11px; border: 1px solid var(--dsh-desktop-divider); border-radius: 10px; cursor: pointer; }
    .dshDesktopProjectDeleteDialog fieldset > label:has(input:checked) { border-color: color-mix(in srgb, #7196ff 60%, var(--dsh-desktop-divider)); background: color-mix(in srgb, #7196ff 9%, transparent); }
    .dshDesktopProjectDeleteDialog fieldset > label[data-disabled="true"] { cursor: default; opacity: .52; }
    .dshDesktopProjectDeleteDialog fieldset input { margin-top: 3px; }
    .dshDesktopProjectDeleteDialog fieldset span { display: grid; gap: 3px; }
    .dshDesktopProjectDeleteDialog fieldset strong { font-size: 13px; }
    .dshDesktopProjectDeleteDialog fieldset small { color: var(--dsw-alias-label-tertiary, #85858d); font-size: 11px; }
    .dshDesktopProjectDeleteNameCheck { display: grid; gap: 6px; margin-top: 14px; color: var(--dsw-alias-label-secondary, #b7b7bf); font-size: 12px; }
    .dshDesktopProjectDeleteNameCheck input { box-sizing: border-box; width: 100%; min-height: 38px; padding: 0 10px; border: 1px solid var(--dsh-desktop-divider); border-radius: 8px; color: inherit; background: var(--dsw-alias-bg-layer-2, #29292e); }
    .dshDesktopProjectDeleteError { margin: 12px 0 0; color: #e8aaaa; font-size: 12px; }
    .dshDesktopProjectDeleteDialog > footer { display: flex; justify-content: flex-end; gap: 8px; margin-top: 18px; }
    .dshDesktopProjectDeleteDialog > footer button { min-height: 37px; padding: 0 14px; border: 1px solid var(--dsh-desktop-divider); border-radius: 8px; color: inherit; background: var(--dsw-alias-bg-layer-2, #29292e); cursor: pointer; }
    .dshDesktopProjectDeleteDialog > footer .dshDesktopProjectDeleteDanger { border-color: transparent; color: white; background: #b64f54; }
    .dshDesktopProjectDeleteDialog button:disabled, .dshDesktopProjectDeleteDialog input:disabled { cursor: default; opacity: .5; }
    .dshDesktopProjectError { margin-top: 22px; padding: 16px; border: 1px solid color-mix(in srgb, #d27676 45%, transparent); border-radius: 12px; color: #e8aaaa; background: color-mix(in srgb, #6d3636 18%, transparent); }
    .dshDesktopProjectSkeletons { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-top: 24px; }
    .dshDesktopProjectSkeletons span { height: 160px; border-radius: 15px; background: var(--dsw-alias-bg-layer-1, #1d1d20); opacity: .65; }
    .dshDesktopProjectEmpty { min-height: 240px; display: grid; place-content: center; justify-items: center; text-align: center; }
    .dshDesktopProjectEmptyIcon { width: 50px; height: 50px; display: grid; place-items: center; border-radius: 15px; color: #9ab2f5; background: color-mix(in srgb, #5b79cd 16%, transparent); font-size: 25px; }
    .dshDesktopProjectEmpty h2 { margin: 17px 0 7px; font-size: 22px; }
    .dshDesktopProjectEmpty p { margin: 0; color: var(--dsw-alias-label-tertiary, #85858d); }
    .dshDesktopProjectComposerDock { position: sticky; bottom: 0; z-index: 4; margin-top: 24px; padding: 12px 0 22px; background: linear-gradient(180deg, transparent, var(--dsw-alias-bg-base, #151517) 18%); }
    .dshDesktopProjectComposer, .dshDesktopProjectConfirm { display: grid; gap: 12px; width: min(840px, 100%); box-sizing: border-box; margin: 0 auto; padding: 16px; border: 1px solid var(--dsh-desktop-divider); border-radius: 13px; text-align: left; background: var(--dsw-alias-bg-layer-1, #1d1d20); box-shadow: 0 14px 36px color-mix(in srgb, #0b1020 14%, transparent); }
    .dshDesktopProjectComposer label { display: grid; gap: 5px; color: var(--dsw-alias-label-tertiary, #85858d); font-size: 12px; }
    .dshDesktopProjectComposer textarea, .dshDesktopProjectComposer input, .dshDesktopProjectComposer select { box-sizing: border-box; width: 100%; border: 1px solid var(--dsh-desktop-divider); border-radius: 8px; color: var(--dsw-alias-label-primary, #ececf0); background: var(--dsw-alias-bg-layer-2, #29292e); }
    .dshDesktopProjectComposer textarea { min-height: 82px; padding: 9px; resize: vertical; }
    .dshDesktopProjectComposer input, .dshDesktopProjectComposer select { min-height: 36px; padding: 0 9px; }
    .dshDesktopProjectComposerRow { display: grid; grid-template-columns: minmax(220px, 1.5fr) minmax(130px, .7fr) minmax(130px, .7fr); gap: 10px; }
    .dshDesktopProjectComposer .dshDesktopProjectCreateDirectory { display: flex; align-items: center; gap: 7px; }
    .dshDesktopProjectCreateDirectory input { width: auto; min-height: 0; }
    .dshDesktopProjectComposer > small { color: var(--dsw-alias-label-tertiary, #85858d); }
    .dshDesktopProjectModifyComposer > header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
    .dshDesktopProjectModifyComposer > header div { display: grid; gap: 3px; }
    .dshDesktopProjectModifyComposer > header small { color: #86a2ef; font-size: 10px; font-weight: 700; letter-spacing: .08em; }
    .dshDesktopProjectModifyComposer > header strong { font-size: 15px; }
    .dshDesktopProjectModifyComposer > header button { border: 0; color: var(--dsw-alias-label-tertiary, #85858d); background: transparent; font-size: 21px; cursor: pointer; }
    .dshDesktopProjectModifyComposer .dshDesktopProjectComposerActions { align-items: center; }
    .dshDesktopProjectModifyComposer .dshDesktopProjectComposerActions span { overflow: hidden; margin-right: auto; color: var(--dsw-alias-label-tertiary, #85858d); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
    .dshDesktopProjectComposer [role="alert"], .dshDesktopProjectConfirm [role="alert"] { margin: 0; color: #e8aaaa; }
    .dshDesktopProjectComposerActions { display: flex; justify-content: flex-end; gap: 8px; }
    .dshDesktopProjectComposerActions button { min-height: 36px; padding: 0 13px; border: 1px solid var(--dsh-desktop-divider); border-radius: 8px; color: var(--dsw-alias-label-primary, #ececf0); background: var(--dsw-alias-bg-layer-2, #29292e); cursor: pointer; }
    .dshDesktopProjectComposerActions button:last-child { border-color: transparent; color: white; background: #5877cf; }
    .dshDesktopProjectComposerActions button:disabled { cursor: default; opacity: .55; }
    .dshDesktopProjectConfirm h3 { margin: 0; font-size: 17px; }
    .dshDesktopProjectConfirm dl { display: grid; gap: 7px; margin: 0; }
    .dshDesktopProjectConfirm dl > div { display: grid; grid-template-columns: 88px 1fr; gap: 10px; }
    .dshDesktopProjectConfirm dt { color: var(--dsw-alias-label-tertiary, #85858d); }
    .dshDesktopProjectConfirm dd { margin: 0; overflow-wrap: anywhere; }
    @keyframes dshDesktopProjectEnter { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: translateY(0); } }
    @keyframes dshDesktopRecentPulse { 55%, 100% { box-shadow: 0 0 0 7px transparent; } }
    @keyframes dshDesktopProfileSweep { from { transform: translateX(-100%); } to { transform: translateX(100%); } }
    @media (max-width: 760px) {
      .dshDesktopProjectsPage { padding-inline: 20px; }
      .dshDesktopProjectComposerRow, .dshDesktopProfileEditor, .dshDesktopProfileSettingsList article { grid-template-columns: 1fr; }
      .dshDesktopProfileSettingsActions, .dshDesktopProfileSettingsList small { grid-column: 1; grid-row: auto; }
    }
    @media (prefers-reduced-motion: reduce) {
      .dshDesktopProjectCard, .dshDesktopProjectCard:hover, .dshDesktopProjectCard[data-recent="true"]::after, .dshDesktopProfileSelector[aria-busy="true"]::after { animation: none !important; transform: none !important; transition-duration: .01ms !important; }
    }
  `
  document.head.append(style)
  return () => style.remove()
}
