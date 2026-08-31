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
    .dshDesktopSidebarSurface { position: relative; display: flex; flex-direction: column; background: var(--dsw-specific-sidebar-fill, var(--dsw-alias-bg-layer-2, #2c2c2f)); border-right: 1px solid var(--dsh-desktop-divider); }
    .dshDesktopUpstreamSidebar { flex: 1; min-height: 0; }
    .dshDesktopFooterAction { box-sizing: border-box; display: flex; align-items: center; width: calc(100% + 4px); height: 42px; margin: 4px -2px; padding: 0 10px 0 8px; gap: 8px; border: 0; border-radius: 12px; color: var(--dsw-alias-label-primary, #e8edf2); background: transparent; font: 400 14px/22px var(--dsw-font-family, system-ui, sans-serif); text-align: left; cursor: pointer; overflow: hidden; transition: background-color .15s ease; }
    .dshDesktopFooterAction svg { width: 18px; height: 18px; flex: 0 0 18px; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
    .dshDesktopFooterActionLabel { overflow: hidden; white-space: nowrap; }
    .dshDesktopFooterAction:hover { background: var(--dsw-alias-interactive-bg-hover, rgba(127,127,127,.1)); }
    .dshDesktopFooterAction.is-active { background: var(--dsw-specific-sidebar-nav-item-active, var(--dsw-alias-interactive-bg-hover, rgba(127,127,127,.1))); }
    .dshDesktopFooterAction.is-rail { width: 36px; height: 36px; justify-content: center; gap: 0; margin: 8px 0 10px; padding: 0; border-radius: 50%; }
    .dshDesktopFooterAction:focus-visible { outline: 2px solid var(--dsw-alias-state-business-primary, #7d9cf0); outline-offset: 2px; }
    .dshDesktopConversationSurface { background: var(--dsw-alias-bg-base, #141416); }
    .dshDesktopDetailsSurface { background: var(--dsw-alias-bg-layer-1, #1c1c1f); border-left: 1px solid var(--dsh-desktop-divider); }
    .dshDesktopOverlay { position: absolute; inset: 0; pointer-events: none; z-index: 30; }
    .dshDesktopOverlay > * { pointer-events: auto; }
    .dshDesktopResizeHandle { position: absolute; top: 0; bottom: 0; width: 7px; margin-left: -3px; z-index: 20; cursor: col-resize; }
    .dshDesktopProjectsPage { box-sizing: border-box; height: 100%; overflow: auto; padding: 30px 38px 0; color: var(--dsw-alias-label-primary, #ececf0); background: var(--dsw-alias-bg-base, #151517); }
    .dshDesktopProjectsPageInner { width: min(1180px, 100%); min-height: 100%; margin: 0 auto; }
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
    .dshModelAgentCenter { box-sizing: border-box; display: grid; gap: 18px; min-height: 100%; padding: 28px 34px 44px; color: var(--dsw-alias-label-primary, #ececf0); background: var(--dsw-alias-bg-base, #151517); }
    .dshModelAgentCenterHeader { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; }
    .dshModelAgentEyebrow { margin: 0 0 5px; color: #86a2ef; font-size: 10px; font-weight: 700; letter-spacing: .11em; }
    .dshModelAgentCenter h2 { margin: 0; font-size: 22px; }
    .dshModelAgentCenterHeader > div > p:last-child { margin: 7px 0 0; color: var(--dsw-alias-label-tertiary, #85858d); font-size: 13px; }
    .dshModelAgentCenterHeader > button, .dshModelAgentActions button, .dshModelAgentDialog footer button { min-height: 34px; padding: 0 12px; border: 1px solid var(--dsh-desktop-divider); border-radius: 8px; color: inherit; background: var(--dsw-alias-bg-layer-2, #29292e); cursor: pointer; }
    .dshModelAgentCenter button:disabled { cursor: default; opacity: .48; }
    .dshModelAgentTabs { display: flex; gap: 6px; padding-bottom: 8px; border-bottom: 1px solid var(--dsh-desktop-divider); }
    .dshModelAgentTabs button { min-height: 32px; padding: 0 12px; border: 0; border-radius: 8px; color: var(--dsw-alias-label-tertiary, #85858d); background: transparent; cursor: pointer; }
    .dshModelAgentTabs button[aria-selected="true"] { color: var(--dsw-alias-label-primary, #ececf0); background: color-mix(in srgb, #7196ff 15%, transparent); }
    .dshModelAgentGrid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; align-content: start; }
    .dshModelAgentCard { display: grid; gap: 12px; padding: 16px; border: 1px solid var(--dsh-desktop-divider); border-radius: 14px; background: var(--dsw-alias-bg-layer-1, #1d1d20); }
    .dshModelAgentCardHeader { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
    .dshModelAgentCardHeader > div:first-child { display: flex; align-items: center; gap: 9px; min-width: 0; }
    .dshModelAgentCardHeader h3 { overflow: hidden; margin: 0; font-size: 15px; text-overflow: ellipsis; white-space: nowrap; }
    .dshModelAgentCardHeader small { display: block; margin-top: 3px; color: var(--dsw-alias-label-tertiary, #85858d); font-size: 11px; }
    .dshModelAgentProviderMark, .dshModelAgentAgentMark { display: grid; width: 30px; height: 30px; flex: 0 0 30px; place-items: center; border-radius: 9px; color: white; background: linear-gradient(135deg, #5578d1, #9368b4); font-weight: 700; }
    .dshModelAgentAgentMark { background: linear-gradient(135deg, #4a927d, #5578d1); }
    .dshModelAgentStatus { flex: 0 0 auto; padding: 4px 7px; border-radius: 999px; font-size: 10px; }
    .dshModelAgentStatus-not-configured, .dshModelAgentStatus-missing-cli { color: #e7bd87; background: color-mix(in srgb, #bc7b37 15%, transparent); }
    .dshModelAgentStatus-configured-unverified { color: #aebff4; background: color-mix(in srgb, #5b79cd 15%, transparent); }
    .dshModelAgentStatus-invalid-credential, .dshModelAgentStatus-network-error, .dshModelAgentStatus-quota-exhausted, .dshModelAgentStatus-incompatible { color: #e8aaaa; background: color-mix(in srgb, #b75050 14%, transparent); }
    .dshModelAgentStatus-available { color: #8fd2ae; background: color-mix(in srgb, #3f8064 16%, transparent); }
    .dshModelAgentCardText, .dshModelAgentCardHint { margin: 0; color: var(--dsw-alias-label-secondary, #b7b7bf); font-size: 12px; line-height: 1.55; }
    .dshModelAgentCardHint { color: #e8aaaa; }
    .dshModelAgentCardMeta { display: flex; flex-wrap: wrap; gap: 7px 14px; color: var(--dsw-alias-label-tertiary, #85858d); font-size: 11px; }
    .dshModelAgentActions { display: flex; gap: 7px; }
    .dshModelAgentActions button:hover, .dshModelAgentCenterHeader > button:hover { border-color: color-mix(in srgb, #7196ff 45%, var(--dsh-desktop-divider)); }
    .dshModelAgentDetails { display: grid; gap: 7px; margin: 0; font-size: 12px; }
    .dshModelAgentDetails > div { display: grid; grid-template-columns: 68px 1fr; gap: 10px; min-width: 0; }
    .dshModelAgentDetails dt { color: var(--dsw-alias-label-tertiary, #85858d); }
    .dshModelAgentDetails dd { overflow: hidden; margin: 0; text-overflow: ellipsis; white-space: nowrap; }
    .dshModelAgentEmpty, .dshModelAgentMuted { color: var(--dsw-alias-label-tertiary, #85858d); }
    .dshModelAgentEmpty { display: grid; gap: 6px; padding: 36px; border: 1px dashed var(--dsh-desktop-divider); border-radius: 14px; text-align: center; }
    .dshModelAgentEmpty span { font-size: 12px; }
    .dshModelAgentDiagnostics { display: grid; gap: 16px; }
    .dshModelAgentSummary { display: flex; align-items: baseline; gap: 7px; padding: 14px 16px; border: 1px solid var(--dsh-desktop-divider); border-radius: 12px; color: var(--dsw-alias-label-tertiary, #85858d); font-size: 12px; }
    .dshModelAgentSummary strong { margin-left: 12px; color: var(--dsw-alias-label-primary, #ececf0); font-size: 20px; }
    .dshModelAgentSummary strong:first-child { margin-left: 0; }
    .dshModelAgentDiagnostics section { display: grid; gap: 7px; }
    .dshModelAgentDiagnostics h3 { margin: 0 0 3px; font-size: 14px; }
    .dshModelAgentDiagnosticRow { display: flex; justify-content: space-between; gap: 12px; padding: 9px 11px; border-radius: 8px; background: var(--dsw-alias-bg-layer-1, #1d1d20); font-size: 12px; }
    .dshModelAgentDiagnosticRow > span:last-child { color: var(--dsw-alias-label-tertiary, #85858d); }
    .dshModelAgentDialogBackdrop { position: fixed; inset: 0; z-index: 120; display: grid; place-items: center; padding: 24px; background: rgba(7,9,14,.52); backdrop-filter: blur(7px); }
    .dshModelAgentDialog { display: grid; gap: 14px; width: min(430px, 100%); box-sizing: border-box; padding: 20px; border: 1px solid color-mix(in srgb, var(--dsh-desktop-divider) 75%, #8c99b8 25%); border-radius: 16px; background: var(--dsw-alias-bg-layer-1, #1d1d20); box-shadow: 0 28px 80px rgba(0,0,0,.38); }
    .dshModelAgentDialog header { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; }
    .dshModelAgentDialog h3 { margin: 0; font-size: 20px; }
    .dshModelAgentDialog header button { border: 0; color: var(--dsw-alias-label-tertiary, #85858d); background: transparent; font-size: 24px; cursor: pointer; }
    .dshModelAgentDialogHint { margin: 0; color: var(--dsw-alias-label-tertiary, #85858d); font-size: 12px; line-height: 1.55; }
    .dshModelAgentField { display: grid; gap: 6px; color: var(--dsw-alias-label-secondary, #b7b7bf); font-size: 12px; }
    .dshModelAgentField input { box-sizing: border-box; width: 100%; min-height: 39px; padding: 0 10px; border: 1px solid var(--dsh-desktop-divider); border-radius: 8px; color: inherit; background: var(--dsw-alias-bg-layer-2, #29292e); }
    .dshModelAgentDialog footer { display: flex; justify-content: flex-end; gap: 8px; }
    .dshModelAgentDialog footer .dshModelAgentPrimary { border-color: transparent; color: white; background: #5877cf; }
    .dshExtensionReviewFacts { display: grid; gap: 8px; margin: 0; }
    .dshExtensionReviewFacts > div { display: grid; grid-template-columns: 88px 1fr; gap: 10px; min-width: 0; }
    .dshExtensionReviewFacts dt { color: var(--dsw-alias-label-tertiary, #85858d); }
    .dshExtensionReviewFacts dd { min-width: 0; margin: 0; overflow-wrap: anywhere; }
    .dshModelAgentWarning { padding: 10px 12px; border: 1px solid color-mix(in srgb, #d9a441 35%, var(--dsh-desktop-divider)); border-radius: 10px; color: #e5c783; background: color-mix(in srgb, #d9a441 9%, transparent); font-size: 12px; line-height: 1.5; }
    .dshModelAgentError { margin-bottom: 12px; padding: 9px 11px; border-radius: 9px; color: #f0b0b0; background: color-mix(in srgb, #b94747 14%, transparent); font-size: 12px; }
    .dshMcpServerDialog { width: min(560px, 100%); }
    .dshMcpServerSection { display: grid; gap: 8px; }
    .dshMcpServerSection > strong { font-size: 12px; }
    .dshMcpServerSection ul { display: grid; gap: 6px; max-height: 150px; margin: 0; padding: 0; overflow: auto; list-style: none; }
    .dshMcpServerSection li { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 7px 9px; border-radius: 8px; background: var(--dsw-alias-bg-layer-2, #29292e); font-size: 12px; }
    .dshMcpServerSection li span { color: var(--dsw-alias-label-tertiary, #85858d); }
    .dshMcpServerTags { display: flex; flex-wrap: wrap; gap: 6px; }
    .dshMcpServerTags span { padding: 4px 8px; border-radius: 999px; color: #9ab2f5; background: color-mix(in srgb, #5b79cd 16%, transparent); font-size: 11px; }
    .dshModelAgentError { padding: 9px 11px; border-radius: 8px; color: #e8aaaa; background: color-mix(in srgb, #b75050 12%, transparent); font-size: 12px; }
    .dshDesktopProjectGrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 14px; }
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
    .dshDesktopProjectBadge { padding: 2px 7px; border-radius: 999px; font-size: 10px; }
    .dshDesktopProjectBadge[data-kind="running"] { color: #7fd0a4; background: color-mix(in srgb, #3f8064 18%, transparent); }
    .dshDesktopProjectBadge[data-kind="launchable"] { color: #9ab2f5; background: color-mix(in srgb, #5b79cd 16%, transparent); }
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
    .dshDesktopProjectSkeletons { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
    .dshDesktopProjectSkeletons span { height: 160px; border-radius: 15px; background: var(--dsw-alias-bg-layer-1, #1d1d20); opacity: .65; }
    .dshDesktopProjectEmpty { min-height: 240px; display: grid; place-content: center; justify-items: center; text-align: center; }
    .dshDesktopProjectEmptyIcon { width: 50px; height: 50px; display: grid; place-items: center; border-radius: 15px; color: #9ab2f5; background: color-mix(in srgb, #5b79cd 16%, transparent); font-size: 25px; }
    .dshDesktopProjectEmpty h2 { margin: 17px 0 7px; font-size: 22px; }
    .dshDesktopProjectEmpty p { margin: 0; color: var(--dsw-alias-label-tertiary, #85858d); }
    .dshDesktopProjectComposerDock { position: sticky; bottom: 0; z-index: 4; margin-top: 24px; padding: 12px 0 22px; background: linear-gradient(180deg, transparent, var(--dsw-alias-bg-base, #151517) 18%); }
    .dshDesktopAdoptDialog > header p { color: #86a2ef; }
    .dshDesktopAdoptRow { display: flex; justify-content: center; margin-bottom: 8px; }
    .dshDesktopAdoptButton { min-height: 30px; padding: 0 14px; border: 1px dashed color-mix(in srgb, #7196ff 40%, var(--dsh-desktop-divider)); border-radius: 999px; color: #9ab2f5; background: transparent; font-size: 12px; cursor: pointer; }
    .dshDesktopAdoptButton:hover:not(:disabled) { background: color-mix(in srgb, #7196ff 9%, transparent); }
    .dshDesktopAdoptButton:disabled { cursor: default; opacity: .48; }
    .dshDesktopAdoptList { display: grid; gap: 8px; max-height: 320px; margin: 14px 0 0; padding: 0; list-style: none; overflow: auto; }
    .dshDesktopAdoptList button { display: grid; gap: 3px; width: 100%; padding: 10px 12px; border: 1px solid var(--dsh-desktop-divider); border-radius: 10px; color: inherit; background: transparent; cursor: pointer; text-align: left; }
    .dshDesktopAdoptList button:hover:not(:disabled) { border-color: color-mix(in srgb, #7196ff 45%, var(--dsh-desktop-divider)); background: color-mix(in srgb, #7196ff 8%, transparent); }
    .dshDesktopAdoptList small { overflow: hidden; color: var(--dsw-alias-label-tertiary, #85858d); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
    .dshDesktopProjectComposer, .dshDesktopProjectConfirm { display: grid; gap: 10px; width: min(840px, 100%); box-sizing: border-box; margin: 0 auto; padding: 12px 16px 13px; border: 1px solid var(--dsh-desktop-divider); border-radius: 16px; text-align: left; background: var(--dsw-alias-bg-layer-1, #1d1d20); box-shadow: 0 14px 36px color-mix(in srgb, #0b1020 14%, transparent); transition: border-color .15s ease, box-shadow .15s ease; }
    .dshDesktopProjectComposer:focus-within { border-color: color-mix(in srgb, #7196ff 55%, var(--dsh-desktop-divider)); box-shadow: 0 0 0 2px color-mix(in srgb, #7196ff 14%, transparent), 0 14px 36px color-mix(in srgb, #0b1020 14%, transparent); }
    .dshDesktopProjectComposerBar { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 26px; color: var(--dsw-alias-label-tertiary, #85858d); font-size: 12px; }
    .dshDesktopProjectComposerContext { display: inline-flex; align-items: center; gap: 7px; min-width: 0; }
    .dshDesktopProjectComposerContext svg { width: 14px; height: 14px; flex: 0 0 14px; stroke: currentColor; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; fill: none; }
    .dshDesktopProjectComposerContext small { color: #86a2ef; font-size: 10px; font-weight: 700; letter-spacing: .08em; }
    .dshDesktopProjectComposerContext strong { overflow: hidden; color: var(--dsw-alias-label-primary, #ececf0); font-size: 13px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
    .dshDesktopProjectComposerMode { flex: 0 0 auto; padding: 3px 9px; border-radius: 999px; color: #9ab2f5; background: color-mix(in srgb, #5b79cd 16%, transparent); }
    .dshDesktopProjectComposerClear { flex: 0 0 auto; width: 26px; height: 26px; display: grid; place-items: center; border: 0; border-radius: 50%; color: var(--dsw-alias-label-tertiary, #85858d); background: transparent; font-size: 19px; line-height: 1; cursor: pointer; }
    .dshDesktopProjectComposerClear:hover:not(:disabled) { color: var(--dsw-alias-label-primary, #ececf0); background: var(--dsw-alias-interactive-bg-hover, rgba(127,127,127,.1)); }
    .dshDesktopProjectComposerClear:disabled { cursor: default; opacity: .48; }
    .dshDesktopProjectComposer > textarea { box-sizing: border-box; width: 100%; min-height: 88px; padding: 2px 2px 4px; border: 0; resize: vertical; color: var(--dsw-alias-label-primary, #ececf0); background: transparent; font: 400 14px/22px var(--dsw-font-family, system-ui, sans-serif); outline: none; }
    .dshDesktopProjectComposer > textarea::placeholder { color: var(--dsw-alias-label-tertiary, #85858d); }
    .dshDesktopProjectComposer > textarea:disabled { opacity: .55; }
    .dshDesktopProjectComposerFooter { min-height: 34px; }
    .dshDesktopProjectComposerHint { overflow: hidden; color: var(--dsw-alias-label-tertiary, #85858d); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
    .dshDesktopProjectComposerSend { width: 34px; height: 34px; margin-left: auto; display: grid; place-items: center; border: 0; border-radius: 50%; color: white; background: #5877cf; cursor: pointer; transition: background-color .15s ease, transform .15s ease; }
    .dshDesktopProjectComposerSend svg { width: 16px; height: 16px; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; fill: none; }
    .dshDesktopProjectComposerSend:hover:not(:disabled) { background: #6482dc; }
    .dshDesktopProjectComposerSend:active:not(:disabled) { transform: scale(.94); }
    .dshDesktopProjectComposerSend:disabled { cursor: default; opacity: .42; }
    .dshDesktopProjectComposerSend:focus-visible { outline: 2px solid #7196ff; outline-offset: 2px; }
    .dshDesktopProjectComposerSend[data-busy="true"] svg { animation: dshDesktopSendPulse .9s ease-in-out infinite; }
    .dshDesktopSrOnly { position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0; overflow: hidden; clip-path: inset(50%); white-space: nowrap; border: 0; }
    @keyframes dshDesktopSendPulse { 50% { opacity: .35; } }
    .dshDesktopProjectComposer [role="alert"], .dshDesktopProjectConfirm [role="alert"] { margin: 0; color: #e8aaaa; }
    .dshDesktopProjectComposerActions { display: flex; justify-content: flex-end; gap: 8px; }
    .dshDesktopProjectComposerActions button { min-height: 36px; padding: 0 14px; border: 1px solid var(--dsh-desktop-divider); border-radius: 999px; color: var(--dsw-alias-label-primary, #ececf0); background: var(--dsw-alias-bg-layer-2, #29292e); cursor: pointer; }
    .dshDesktopProjectComposerActions button:last-child { border-color: transparent; color: white; background: #5877cf; }
    .dshDesktopProjectComposerActions button:disabled { cursor: default; opacity: .55; }
    .dshDesktopProjectConfirm h3 { margin: 0; font-size: 17px; }
    .dshDesktopProjectConfirm dl { display: grid; gap: 7px; margin: 0; }
    .dshDesktopProjectConfirm dl > div { display: grid; grid-template-columns: 88px 1fr; gap: 10px; }
    .dshDesktopProjectConfirm dt { color: var(--dsw-alias-label-tertiary, #85858d); }
    .dshDesktopProjectConfirm dd { margin: 0; overflow-wrap: anywhere; }
    @keyframes dshDesktopProjectEnter { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: translateY(0); } }
    @keyframes dshDesktopRecentPulse { 55%, 100% { box-shadow: 0 0 0 7px transparent; } }
    .dshAgentWorkbench { display: grid; gap: 16px; margin-top: 22px; padding: 18px; border: 1px solid var(--dsh-desktop-divider); border-radius: 16px; background: var(--dsw-alias-bg-layer-1, #1d1d20); }
    .dshAgentWorkbenchHeader { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; }
    .dshAgentWorkbenchHeader h3, .dshAgentWorkbenchHeader p { margin: 0; }
    .dshAgentWorkbenchHeader p:last-child { margin-top: 4px; color: var(--dsw-alias-label-tertiary, #85858d); font-size: 12px; }
    .dshAgentWorkbenchHeader button, .dshAgentWorkbenchActions button, .dshAgentWorkbenchApproval button, .dshAgentWorkbenchTimeline button { min-height: 30px; padding: 0 10px; border: 1px solid var(--dsh-desktop-divider); border-radius: 8px; color: inherit; background: var(--dsw-alias-bg-layer-2, #29292e); cursor: pointer; }
    .dshAgentWorkbenchCreate { display: grid; grid-template-columns: minmax(120px, .5fr) minmax(150px, .6fr) minmax(260px, 1.8fr) auto; align-items: end; gap: 10px; }
    .dshAgentWorkbenchCreate label { display: grid; gap: 5px; color: var(--dsw-alias-label-tertiary, #85858d); font-size: 11px; }
    .dshAgentWorkbenchCreate select, .dshAgentWorkbenchCreate textarea { box-sizing: border-box; min-height: 34px; padding: 7px 9px; border: 1px solid var(--dsh-desktop-divider); border-radius: 8px; color: inherit; background: var(--dsw-alias-bg-layer-2, #29292e); font: inherit; }
    .dshAgentWorkbenchCreate textarea { min-height: 58px; resize: vertical; }
    .dshAgentWorkbenchPrimary { min-height: 34px; padding: 0 13px; border: 0; border-radius: 8px; color: white; background: #5877cf; cursor: pointer; }
    .dshAgentWorkbenchPrimary:disabled { cursor: default; opacity: .45; }
    .dshAgentWorkbenchNotice { padding: 10px 12px; border-radius: 9px; color: #a9c6f0; background: color-mix(in srgb, #3d5a8f 16%, transparent); font-size: 12px; }
    .dshAgentWorkbenchWarning, .dshAgentWorkbenchError { padding: 10px 12px; border-radius: 9px; font-size: 12px; }
    .dshAgentWorkbenchWarning { color: #ead8a0; background: color-mix(in srgb, #826f2f 20%, transparent); }
    .dshAgentWorkbenchError { color: #e8aaaa; background: color-mix(in srgb, #6d3636 18%, transparent); }
    .dshAgentWorkbenchColumns { display: grid; grid-template-columns: minmax(180px, .65fr) minmax(320px, 1.35fr); gap: 14px; }
    .dshAgentWorkbenchTaskList, .dshAgentWorkbenchDetail { min-width: 0; }
    .dshAgentWorkbench h4, .dshAgentWorkbench h5 { margin: 0 0 9px; }
    .dshAgentWorkbenchTaskList { display: grid; align-content: start; gap: 7px; }
    .dshAgentWorkbenchTask { display: grid; gap: 3px; padding: 10px; border: 1px solid var(--dsh-desktop-divider); border-radius: 9px; color: inherit; background: transparent; cursor: pointer; text-align: left; }
    .dshAgentWorkbenchTask.selected { border-color: #7196ff; background: color-mix(in srgb, #7196ff 9%, transparent); }
    .dshAgentWorkbenchTask span, .dshAgentWorkbenchTask small, .dshAgentWorkbenchApproval span { color: var(--dsw-alias-label-tertiary, #85858d); font-size: 11px; }
    .dshAgentWorkbenchDetail { display: grid; align-content: start; gap: 14px; }
    .dshAgentWorkbenchActions, .dshAgentWorkbenchApproval > div:last-child { display: flex; flex-wrap: wrap; gap: 7px; }
    .dshAgentWorkbenchApproval { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 9px; border: 1px solid color-mix(in srgb, #d4a945 55%, var(--dsh-desktop-divider)); border-radius: 9px; }
    .dshAgentWorkbenchApproval > div:first-child { display: grid; gap: 3px; min-width: 0; }
    .dshAgentWorkbenchTimeline { display: grid; gap: 7px; max-height: 240px; margin: 0; padding: 0 0 0 19px; overflow: auto; }
    .dshAgentWorkbenchTimeline li { padding-left: 3px; color: var(--dsw-alias-label-secondary, #b7b7bf); font-size: 12px; }
    .dshAgentWorkbenchTimeline li > span { color: #9ab2f5; font-family: ui-monospace, SFMono-Regular, monospace; font-size: 11px; }
    .dshAgentWorkbenchTimeline p { margin: 3px 0 0; overflow-wrap: anywhere; }
    .dshAgentWorkbenchDiff { padding: 12px; border: 1px solid var(--dsh-desktop-divider); border-radius: 10px; background: var(--dsw-alias-bg-layer-2, #29292e); }
    .dshAgentWorkbenchDiff > div { display: flex; align-items: center; justify-content: space-between; }
    .dshAgentWorkbenchDiff pre { max-height: 260px; margin: 8px 0 0; overflow: auto; color: #cbd7ff; font: 11px/1.5 ui-monospace, SFMono-Regular, monospace; white-space: pre-wrap; }
    .dshModelAgentWorkbenchHint { margin: 4px 0 0; color: var(--dsw-alias-label-tertiary, #85858d); font-size: 12.5px; line-height: 1.7; }
    .dshModelAgentWorkbenchHint strong { color: var(--dsw-alias-label-secondary, #b7b7bf); }
    .dshExtCenter { display: grid; gap: 18px; max-width: 1080px; margin: 0 auto; padding: 4px 2px; }
    .dshExtCenterHeader { display: flex; align-items: flex-end; justify-content: space-between; gap: 18px; }
    .dshExtCenterHeader h2 { margin: 0; font-size: 22px; letter-spacing: .2px; }
    .dshExtCenterHeader p { margin: 5px 0 0; max-width: 62ch; color: var(--dsw-alias-label-secondary, #b7b7bf); font-size: 13px; line-height: 1.65; }
    .dshExtCenterTabs { display: flex; gap: 8px; border-bottom: 1px solid var(--dsw-alias-border-secondary, rgba(127,127,127,.25)); padding-bottom: 10px; }
    .dshExtCenterTabs button { min-height: 28px; padding: 4px 12px; border: 0; background: transparent; color: var(--dsw-alias-label-secondary, #b7b7bf); cursor: pointer; }
    .dshExtCenterTabs button[aria-selected='true'] { color: var(--dsw-alias-label-primary, #ececf0); border-color: var(--dsw-alias-border-strong, currentColor); }
    .dshExtCenterPlaceholder { color: var(--dsw-alias-label-secondary, #b7b7bf); padding: 32px 0; text-align: center; }
    .dshPluginMarket { display: grid; gap: 16px; margin-top: 26px; padding-top: 22px; border-top: 1px solid var(--dsh-desktop-divider); }
    .dshPluginMarketHead { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; }
    .dshPluginMarketHead h3 { margin: 0 0 4px; font-size: 16px; }
    .dshPluginMarketHead p { margin: 0; color: var(--dsw-alias-label-tertiary, #85858d); font-size: 12px; }
    .dshPluginMarketWarning { padding: 10px 12px; border-radius: 9px; color: #e3c884; background: color-mix(in srgb, #8a7434 18%, transparent); font-size: 12px; line-height: 1.6; }
    .dshPluginMarketControls { display: grid; gap: 10px; }
    .dshPluginMarketControls input { box-sizing: border-box; width: 100%; max-width: 420px; min-height: 34px; padding: 0 12px; border: 1px solid var(--dsh-desktop-divider); border-radius: 10px; color: inherit; background: var(--dsw-alias-bg-layer-2, #29292e); font: 13px inherit; }
    .dshPluginMarketControls input:focus-visible { outline: 2px solid var(--dsw-alias-state-business-primary, #7d9cf0); outline-offset: 1px; }
    .dshPluginMarketCategories { display: flex; flex-wrap: wrap; gap: 6px; }
    .dshPluginMarketCategories button { min-height: 26px; padding: 0 10px; border: 1px solid var(--dsh-desktop-divider); border-radius: 999px; color: var(--dsw-alias-label-secondary, #b7b7bf); background: transparent; font-size: 11.5px; cursor: pointer; }
    .dshPluginMarketCategories button:hover { background: var(--dsw-alias-interactive-bg-hover, rgba(127,127,127,.1)); }
    .dshPluginMarketCategories button.is-active { color: #cfd9ff; border-color: color-mix(in srgb, #9db2ff 55%, transparent); background: color-mix(in srgb, #5877cf 20%, transparent); }
    .dshPluginMarketGrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(330px, 1fr)); gap: 12px; }
    .dshPluginCard { display: grid; grid-template-columns: 1fr auto; gap: 12px; align-items: center; padding: 15px 16px; border: 1px solid var(--dsh-desktop-divider); border-radius: 14px; background: var(--dsw-alias-bg-layer-1, #1d1d20); }
    .dshPluginCard.is-featured { grid-column: 1 / -1; border-color: color-mix(in srgb, #9db2ff 45%, var(--dsh-desktop-divider)); background: linear-gradient(120deg, color-mix(in srgb, #5877cf 12%, #1d1d20), var(--dsw-alias-bg-layer-1, #1d1d20)); }
    .dshPluginCardMain { display: grid; gap: 5px; min-width: 0; }
    .dshPluginCardTitle { display: flex; align-items: baseline; flex-wrap: wrap; gap: 8px; }
    .dshPluginCardTitle strong { font-size: 13.5px; }
    .dshPluginCardTitle small { color: var(--dsw-alias-label-tertiary, #85858d); font-size: 11px; overflow-wrap: anywhere; }
    .dshPluginCardTitle a { color: #9db2ff; text-decoration: none; }
    .dshPluginCardTitle a:hover { text-decoration: underline; }
    .dshPluginCard p { margin: 0; color: var(--dsw-alias-label-secondary, #b7b7bf); font-size: 12px; line-height: 1.55; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
    .dshPluginInstalled { color: #8fd3a0; font-size: 12px; }
    .dshPluginFailed { display: grid; gap: 8px; justify-items: end; }
    .dshPluginFailed > button { min-height: 30px; }
    .dshPluginJobLog { grid-column: 1 / -1; max-height: 120px; margin: 0; padding: 9px 11px; border: 1px solid var(--dsh-desktop-divider); border-radius: 9px; overflow: auto; color: #b9c4d6; background: #101013; font: 11px/1.6 ui-monospace, SFMono-Regular, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }
    .dshPluginMarketMore { display: flex; justify-content: center; }
    .dshAgentPage { display: block; width: 100%; height: 100%; overflow: auto; padding: 36px 40px 56px; }
    .dshAgentHome { display: grid; gap: 22px; max-width: 760px; margin: 0 auto; }
    .dshAgentHomeHero { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
    .dshAgentHomeHero h2 { margin: 0; font-size: 22px; letter-spacing: .2px; }
    .dshAgentHomeLead { margin: 6px 0 0; max-width: 56ch; color: var(--dsw-alias-label-secondary, #b7b7bf); font-size: 13px; line-height: 1.65; }
    .dshAgentCard { display: grid; gap: 16px; margin-top: 18px; padding: 20px 22px; border: 1px solid var(--dsh-desktop-divider); border-radius: 16px; background: var(--dsw-alias-bg-layer-1, #1d1d20); }
    .dshAgentCard[data-ready] { border-color: color-mix(in srgb, #6faa7d 40%, var(--dsh-desktop-divider)); }
    .dshAgentCardHead { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
    .dshAgentCardHead h3 { margin: 0; font-size: 16px; }
    .dshAgentCardNote { margin: 0; padding: 9px 12px; border-radius: 9px; color: #e3c884; background: color-mix(in srgb, #8a7434 16%, transparent); font-size: 12px; line-height: 1.6; overflow-wrap: anywhere; }
    .dshAgentPill { flex: 0 0 auto; padding: 4px 11px; border-radius: 999px; font-size: 12px; font-weight: 600; letter-spacing: .3px; }
    .dshAgentPill.is-ok { color: #8fd3a0; background: color-mix(in srgb, #3f7a51 26%, transparent); box-shadow: inset 0 0 0 1px color-mix(in srgb, #8fd3a0 30%, transparent); }
    .dshAgentPill.is-warn { color: #e3c884; background: color-mix(in srgb, #8a7434 24%, transparent); box-shadow: inset 0 0 0 1px color-mix(in srgb, #e3c884 26%, transparent); }
    .dshAgentPill.is-muted { color: var(--dsw-alias-label-tertiary, #85858d); background: color-mix(in srgb, #85858d 12%, transparent); }
    .dshAgentSteps { display: grid; gap: 0; margin: 0; padding: 0; list-style: none; }
    .dshAgentStep { position: relative; display: grid; grid-template-columns: 22px 1fr; gap: 0 12px; padding: 0 0 14px; }
    .dshAgentStep:last-child { padding-bottom: 2px; }
    .dshAgentStep::before { content: ""; position: absolute; top: 24px; bottom: 0; left: 10.5px; width: 1.5px; background: var(--dsh-desktop-divider); }
    .dshAgentStep:last-child::before { display: none; }
    .dshAgentStep[data-state="done"]::before { background: color-mix(in srgb, #6faa7d 55%, var(--dsh-desktop-divider)); }
    .dshAgentStepMark { position: relative; z-index: 1; display: inline-flex; align-items: center; justify-content: center; width: 22px; height: 22px; margin-top: 1px; border-radius: 50%; border: 1.5px solid var(--dsh-desktop-divider); background: var(--dsw-alias-bg-layer-1, #1d1d20); color: var(--dsw-alias-label-tertiary, #85858d); font-size: 11.5px; font-weight: 600; }
    .dshAgentStep[data-state="done"] .dshAgentStepMark { border-color: color-mix(in srgb, #6faa7d 70%, transparent); color: #8fd3a0; background: color-mix(in srgb, #6faa7d 16%, var(--dsw-alias-bg-layer-1, #1d1d20)); }
    .dshAgentStep[data-state="active"] .dshAgentStepMark { border-color: color-mix(in srgb, #9db2ff 75%, transparent); color: #cfd9ff; background: color-mix(in srgb, #5877cf 22%, var(--dsw-alias-bg-layer-1, #1d1d20)); }
    .dshAgentStepMark svg { width: 12px; height: 12px; stroke: currentColor; stroke-width: 2.6; stroke-linecap: round; stroke-linejoin: round; }
    .dshAgentStepPulse { width: 8px; height: 8px; border-radius: 50%; background: #9db2ff; box-shadow: 0 0 0 0 color-mix(in srgb, #9db2ff 60%, transparent); animation: dshAgentPulse 1.6s ease-out infinite; }
    @keyframes dshAgentPulse { 70% { box-shadow: 0 0 0 7px transparent; } 100% { box-shadow: 0 0 0 0 transparent; } }
    .dshAgentStepCopy { min-width: 0; padding-top: 3px; }
    .dshAgentStepCopy strong { display: block; font-size: 13.5px; }
    .dshAgentStep[data-state="done"] .dshAgentStepCopy strong { color: var(--dsw-alias-label-secondary, #b7b7bf); }
    .dshAgentStepCopy p { margin: 3px 0 0; color: var(--dsw-alias-label-tertiary, #85858d); font-size: 12px; line-height: 1.6; overflow-wrap: anywhere; }
    .dshAgentReadyNote { margin: 0; color: var(--dsw-alias-label-secondary, #b7b7bf); font-size: 12.5px; line-height: 1.7; }
    .dshAgentReadyNote strong { color: #a9c6f0; }
    .dshAgentCardActions { display: flex; flex-wrap: wrap; align-items: center; gap: 9px; margin-top: 2px; }
    .dshAgentPrimaryButton { min-height: 36px; padding: 0 16px; border: 0; border-radius: 10px; color: white; background: #5877cf; font-size: 13px; font-weight: 600; cursor: pointer; transition: filter .15s ease; }
    .dshAgentPrimaryButton:hover:not(:disabled) { filter: brightness(1.1); }
    .dshAgentPrimaryButton:disabled { cursor: default; opacity: .4; }
    .dshAgentGhostButton { min-height: 34px; padding: 0 13px; border: 1px solid var(--dsh-desktop-divider); border-radius: 10px; color: var(--dsw-alias-label-primary, #e8edf2); background: transparent; font-size: 12.5px; cursor: pointer; transition: background-color .15s ease; }
    .dshAgentGhostButton:hover:not(:disabled) { background: var(--dsw-alias-interactive-bg-hover, rgba(127,127,127,.1)); }
    .dshAgentGhostButton:disabled { cursor: default; opacity: .45; }
    .dshAgentComingHint { margin: 0; color: var(--dsw-alias-label-tertiary, #85858d); font-size: 12.5px; line-height: 1.6; }
    .dshAgentLog { border: 1px solid var(--dsh-desktop-divider); border-radius: 12px; overflow: hidden; background: #101013; }
    .dshAgentLog summary { padding: 8px 12px; color: var(--dsw-alias-label-tertiary, #85858d); font-size: 11.5px; cursor: pointer; user-select: none; }
    .dshAgentLogHead { display: flex; align-items: center; justify-content: space-between; padding: 8px 12px; border-bottom: 1px solid var(--dsh-desktop-divider); color: var(--dsw-alias-label-tertiary, #85858d); font-size: 11px; letter-spacing: .4px; }
    .dshAgentLogLive { color: #9db2ff; }
    .dshAgentLog pre { max-height: 168px; margin: 0; padding: 11px 13px; overflow: auto; color: #b9c4d6; font: 11.5px/1.65 ui-monospace, SFMono-Regular, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }
    .dshAgentAdvanced { border-top: 1px dashed var(--dsh-desktop-divider); padding-top: 4px; }
    .dshAgentAdvanced summary { padding: 8px 0; color: var(--dsw-alias-label-tertiary, #85858d); font-size: 12px; cursor: pointer; user-select: none; }
    .dshAgentAdvanced summary:hover { color: var(--dsw-alias-label-secondary, #b7b7bf); }
    .dshAgentAdvancedBody { display: grid; gap: 9px; }
    .dshAgentAdvancedBody > p { margin: 0; color: var(--dsw-alias-label-tertiary, #85858d); font-size: 12px; line-height: 1.6; }
    .dshAgentAdvancedBody code { padding: 1px 5px; border-radius: 5px; background: var(--dsw-alias-bg-layer-2, #29292e); font: 11px ui-monospace, SFMono-Regular, monospace; }
    .dshAgentAdvancedRow { display: grid; grid-template-columns: 1fr auto; gap: 8px; }
    .dshAgentAdvancedRow input { box-sizing: border-box; min-height: 34px; padding: 0 11px; border: 1px solid var(--dsh-desktop-divider); border-radius: 10px; color: inherit; background: var(--dsw-alias-bg-layer-2, #29292e); font: 12px ui-monospace, SFMono-Regular, monospace; }
    .dshAgentAdvancedRow input:focus-visible { outline: 2px solid var(--dsw-alias-state-business-primary, #7d9cf0); outline-offset: 1px; }
    .dshAgentAdvancedResult { margin: 0; color: var(--dsw-alias-label-secondary, #b7b7bf); font-size: 12px; line-height: 1.6; overflow-wrap: anywhere; }
    .dshAgentWorkbenchHost { display: grid; gap: 4px; max-width: 1080px; margin: 0 auto; }
    .dshAgentWorkbenchHost .dshAgentWorkbench { margin-top: 6px; }
    .dshAgentWorkbenchHostHeader { display: flex; align-items: center; gap: 12px; }
    .dshAgentWorkbenchHostHeader h3 { margin: 0; font-size: 18px; }
    .dshAgentWorkbenchHostHeader button { min-height: 30px; padding: 0 10px; border: 1px solid var(--dsh-desktop-divider); border-radius: 8px; color: inherit; background: var(--dsw-alias-bg-layer-2, #29292e); cursor: pointer; }
    .dshPrompts { display: grid; gap: 14px; }
    .dshPromptsStatusRow { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .dshPromptsTargetChip { display: inline-flex; align-items: center; gap: 8px; border: 1px solid var(--dsw-alias-border-secondary, rgba(127,127,127,.35)); border-radius: 999px; padding: 4px 12px; background: transparent; color: inherit; }
    .dshPromptsTargetChip:disabled { opacity: .45; cursor: not-allowed; }
    .dshPromptsTargetState { color: var(--dsw-alias-label-secondary, #b7b7bf); font-size: 12px; }
    .dshPromptsDrift { color: #d97706; font-size: 12px; }
    .dshPromptsSpacer { flex: 1; }
    .dshPromptsList { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; align-content: start; }
    .dshPromptsList button { width: 100%; text-align: left; display: grid; gap: 2px; border: 1px solid transparent; border-radius: 10px; padding: 8px 10px; background: transparent; color: inherit; cursor: pointer; }
    .dshPromptsMuted { color: var(--dsw-alias-label-secondary, #b7b7bf); font-size: 12px; }
    .dshPromptsDialogBackdrop { position: fixed; inset: 0; background: rgba(0,0,0,.45); display: grid; place-items: center; z-index: 40; }
    .dshPromptsDialog { background: var(--dsw-alias-surface-primary, #1c1c1f); color: inherit; border-radius: 14px; padding: 18px 20px; width: min(480px, 90vw); display: grid; gap: 12px; }
    .dshPromptsDialogActions { display: flex; justify-content: flex-end; gap: 10px; }
    .dshPromptsImportRow { display: flex; align-items: center; gap: 10px; }
    .dshPromptsEditor { display: grid; gap: 10px; }
    .dshPromptsEditorActions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .dshPromptsEditorActions button { border: 1px solid var(--dsw-alias-border-secondary, rgba(127,127,127,.4)); border-radius: 8px; padding: 5px 12px; background: transparent; color: inherit; cursor: pointer; }
    .dshPromptsPanes { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 10px; }
    .dshPromptsPanes textarea { min-height: 220px; resize: vertical; border-radius: 10px; border: 1px solid var(--dsw-alias-border-secondary, rgba(127,127,127,.35)); background: transparent; color: inherit; padding: 10px; font: 13px/1.6 ui-monospace, monospace; }
    .dshPromptsPreview { overflow: auto; border: 1px dashed var(--dsw-alias-border-secondary, rgba(127,127,127,.35)); border-radius: 10px; padding: 10px; display: grid; gap: 8px; align-content: start; }
    .dshPromptsActivateGroup { display: flex; gap: 12px; border: 0; padding: 0; }
    .dshPromptsActivateGroup legend { color: var(--dsw-alias-label-secondary, #b7b7bf); font-size: 12px; }
    .dshPromptsListItem.is-active { border-color: var(--dsw-alias-border-secondary, rgba(127,127,127,.4)); background: var(--dsw-alias-surface-secondary, rgba(127,127,127,.08)); }
    @media (max-width: 760px) {
      .dshDesktopProjectsPage { padding-inline: 20px; }
      .dshDesktopProfileEditor, .dshDesktopProfileSettingsList article { grid-template-columns: 1fr; }
      .dshDesktopProfileSettingsActions, .dshDesktopProfileSettingsList small { grid-column: 1; grid-row: auto; }
      .dshModelAgentCenter { padding: 22px 20px 34px; }
      .dshModelAgentCenterHeader { display: grid; }
      .dshModelAgentTabs { overflow-x: auto; }
      .dshAgentWorkbenchCreate, .dshAgentWorkbenchColumns { grid-template-columns: 1fr; }
      .dshAgentWorkbenchApproval { align-items: flex-start; flex-direction: column; }
    }
    @media (prefers-reduced-motion: reduce) {
      .dshDesktopProjectCard, .dshDesktopProjectCard:hover, .dshDesktopProjectCard[data-recent="true"]::after, .dshDesktopProjectComposerSend[data-busy="true"] svg { animation: none !important; transform: none !important; transition-duration: .01ms !important; }
    }
  `
  document.head.append(style)
  return () => style.remove()
}
