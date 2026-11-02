/** Official-token stylesheet for overlay extras. Injected once; never copies official source. */

const STYLE_ID = 'dsh-overlay-custom-models'

const CSS = `
.dshOverlayProviderExtras,
.dshOverlayProxyFooter {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 8px;
  color: var(--dsw-alias-label-primary);
  font-size: 14px;
  line-height: 22px;
}
.dshOverlayProxyFooter {
  margin-top: 16px;
  padding: 12px 14px;
  border: 0.5px solid var(--dsw-alias-settings-card-stroke);
  background: var(--dsw-alias-settings-card-fill);
  border-radius: var(--dsw-radius-xl);
}
.dshOverlayProxyFooter { min-width: 0; }
.dshOverlayProxyModes {
  min-width: 0;
  margin: 0;
  padding: 0;
  border: 0;
}
.dshOverlayProxyModes legend {
  margin-bottom: 8px;
  padding: 0;
  color: var(--dsw-alias-label-secondary);
  font-size: 12px;
}
.dshOverlayProxyModes .dshOverlayRow { gap: 8px 18px; }
.dshOverlayProxyModes input { accent-color: var(--dsw-alias-button-primary-fill); }
.dshOverlayProxyField { display: flex; min-width: 0; flex-direction: column; gap: 6px; }
.dshOverlayProxyField > label { color: var(--dsw-alias-label-secondary); font-size: 12px; }
.dshOverlayProxyField .dshOverlayInput { width: 100%; min-width: 0; }
.dshOverlayProxyFooter input:disabled,
.dshOverlayProxyFooter textarea:disabled { opacity: 0.5; cursor: default; }
.dshOverlayProxyFooter :is(button, input, textarea):focus-visible {
  outline: 2px solid var(--dsw-alias-label-primary);
  outline-offset: 2px;
}
.dshOverlayProxyStatus {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 8px;
  padding-top: 12px;
  border-top: 0.5px solid var(--dsw-alias-border-l3);
}
.dshOverlayProxyStatus h4 { margin: 0; font-size: 12px; font-weight: 500; }
.dshOverlayProxyStatus dl {
  display: grid;
  grid-template-columns: minmax(100px, auto) minmax(0, 1fr);
  gap: 4px 16px;
  margin: 0;
  font-size: 12px;
  line-height: 18px;
}
.dshOverlayProxyStatus dt { color: var(--dsw-alias-label-tertiary); }
.dshOverlayProxyStatus dd { min-width: 0; margin: 0; }
.dshOverlayProxyFooter :is(p, dd, code) { overflow-wrap: anywhere; }
.dshOverlayProxyFooter code { font-size: 12px; }
@media (max-width: 480px) {
  .dshOverlayProxyStatus dl { grid-template-columns: minmax(0, 1fr); gap: 2px; }
  .dshOverlayProxyStatus dd + dt { margin-top: 6px; }
}
.dshOverlayTitle {
  margin: 0;
  font-size: 16px;
  line-height: 24px;
  font-weight: 500;
}
.dshOverlayIntro,
.dshOverlayHint {
  margin: 0;
  font-size: 12px;
  line-height: 18px;
  color: var(--dsw-alias-label-tertiary);
}
.dshOverlaySaved {
  margin: 0;
  font-size: 12px;
  line-height: 18px;
  color: var(--dsw-alias-state-success-primary);
}
.dshOverlayAlert {
  margin: 0;
  font-size: 12px;
  line-height: 18px;
  color: var(--dsw-alias-state-error-primary);
}
.dshOverlayRow {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.dshOverlayDot {
  box-sizing: border-box;
  display: inline-block;
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.dshOverlayDotOk { background: var(--dsw-alias-state-success-primary); }
.dshOverlayDotBad { background: var(--dsw-alias-state-error-primary); }
.dshOverlayDotIdle { background: var(--dsw-alias-label-tertiary); }
.dshOverlayDotBusy { background: var(--dsw-alias-state-warn-label); }
.dshOverlayBtnPrimary,
.dshOverlayBtnSecondary {
  box-sizing: border-box;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  height: 36px;
  padding: 0 14px;
  border-radius: var(--dsw-radius-md);
  font: inherit;
  font-size: 14px;
  line-height: 22px;
  cursor: pointer;
}
.dshOverlayBtnPrimary {
  border: none;
  background: var(--dsw-alias-button-primary-fill);
  color: var(--dsw-alias-label-primary-foreground);
}
.dshOverlayBtnPrimary:hover:not(:disabled) {
  background: var(--dsw-alias-button-primary-hover);
}
.dshOverlayBtnSecondary {
  border: 0.5px solid var(--dsw-alias-border-l3);
  background: transparent;
  color: var(--dsw-alias-label-primary);
}
.dshOverlayBtnSecondary:hover:not(:disabled) {
  background: var(--dsw-alias-interactive-bg-hover-solid);
}
.dshOverlayBtnPrimary:disabled,
.dshOverlayBtnSecondary:disabled {
  cursor: default;
  opacity: 0.5;
}
.dshOverlayInput {
  box-sizing: border-box;
  height: 36px;
  padding: 0 12px;
  border: 0.5px solid var(--dsw-alias-border-l3);
  border-radius: var(--dsw-radius-md);
  background: transparent;
  color: var(--dsw-alias-label-primary);
  font: inherit;
  font-size: 14px;
  min-width: 220px;
}
.dshOverlayFieldset {
  border: 0.5px solid var(--dsw-alias-border-l3);
  border-radius: var(--dsw-radius-md);
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.dshOverlayFieldset legend,
.dshOverlayFieldset summary {
  padding: 0 4px;
  font-size: 12px;
  color: var(--dsw-alias-label-secondary);
  cursor: pointer;
  list-style: none;
}
.dshOverlayFieldset summary::-webkit-details-marker { display: none; }
.dshOverlayFieldset:not([open]) { gap: 0; }
.dshOverlayCheck {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  line-height: 22px;
}
.dshOverlayLink {
  color: var(--dsw-alias-label-primary);
  font-size: 14px;
}
.dshOverlaySettings {
  display: flex;
  flex-direction: column;
  gap: 20px;
  width: min(100%, 920px);
  color: var(--dsw-alias-label-primary);
}
.dshOverlaySettingsHero {
  display: flex;
  align-items: center;
  gap: 14px;
}
.dshOverlaySettingsHero h2,
.dshOverlaySettingsCard h3 {
  margin: 0;
  font-weight: 600;
}
.dshOverlaySettingsHero h2 { font-size: 22px; line-height: 30px; }
.dshOverlaySettingsHero p {
  margin: 3px 0 0;
  color: var(--dsw-alias-label-tertiary);
  font-size: 13px;
  line-height: 20px;
}
.dshOverlaySettingsHeroIcon {
  display: grid;
  place-items: center;
  flex: none;
  width: 44px;
  height: 44px;
  border-radius: var(--dsw-radius-lg);
  background: var(--dsw-alias-interactive-bg-hover-solid);
  font-size: 20px;
}
.dshOverlaySettingsCard,
.dshOverlayMcpCard {
  border: 0.5px solid var(--dsw-alias-settings-card-stroke);
  background: var(--dsw-alias-settings-card-fill);
  border-radius: var(--dsw-radius-xl);
}
.dshOverlaySettingsCard { padding: 18px; }
.dshOverlaySettingsHead,
.dshOverlayMcpHead,
.dshOverlaySettingsFooter {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.dshOverlaySettingsHead { margin-bottom: 16px; }
.dshOverlaySettingsHead h3 { font-size: 16px; line-height: 24px; }
.dshOverlaySettingsList,
.dshOverlayMcpList { display: grid; gap: 10px; }
.dshOverlaySkillRow {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 13px;
  border: 0.5px solid var(--dsw-alias-border-l4);
  border-radius: var(--dsw-radius-lg);
  background: var(--dsw-alias-bg-layer-1);
}
.dshOverlaySkillIcon {
  display: grid;
  place-items: center;
  flex: none;
  width: 30px;
  height: 30px;
  border-radius: var(--dsw-radius-md);
  background: var(--dsw-alias-interactive-bg-hover-solid);
}
.dshOverlaySkillCopy { display: flex; flex: 1; min-width: 0; flex-direction: column; gap: 3px; }
.dshOverlaySkillCopy strong { font-size: 14px; line-height: 21px; }
.dshOverlaySkillCopy span { color: var(--dsw-alias-label-tertiary); font-size: 12px; line-height: 18px; }
.dshOverlaySkillCopy b { color: var(--dsw-alias-label-secondary); font-weight: 500; }
.dshOverlayBadges { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 5px; max-width: 260px; }
.dshOverlayBadges em {
  padding: 3px 7px;
  border-radius: 999px;
  background: var(--dsw-alias-interactive-bg-hover-solid);
  color: var(--dsw-alias-label-secondary);
  font-size: 11px;
  line-height: 16px;
  font-style: normal;
}
.dshOverlaySettingsEmpty {
  padding: 34px 18px;
  border: 0.5px dashed var(--dsw-alias-border-l3);
  border-radius: var(--dsw-radius-lg);
  color: var(--dsw-alias-label-tertiary);
  font-size: 13px;
  text-align: center;
}
.dshOverlayMcpCard { padding: 15px; }
.dshOverlayMcpHead { margin-bottom: 14px; }
.dshOverlayMcpHead > span:first-child { display: flex; min-width: 0; flex-direction: column; gap: 2px; }
.dshOverlayMcpHead strong { overflow: hidden; font-size: 14px; text-overflow: ellipsis; white-space: nowrap; }
.dshOverlayMcpHead small { color: var(--dsw-alias-label-tertiary); font-size: 11px; }
.dshOverlayToggle { display: inline-flex; align-items: center; gap: 6px; color: var(--dsw-alias-label-secondary); font-size: 12px; }
.dshOverlayFormGrid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-bottom: 13px; }
.dshOverlayFormGrid label { display: flex; min-width: 0; flex-direction: column; gap: 6px; color: var(--dsw-alias-label-secondary); font-size: 12px; }
.dshOverlayFormGrid label small { color: var(--dsw-alias-label-tertiary); line-height: 17px; }
.dshOverlayFormGrid .dshOverlayInput { width: 100%; min-width: 0; }
.dshOverlayFormWide { grid-column: 1 / -1; }
.dshOverlayTextarea {
  box-sizing: border-box;
  width: 100%;
  min-height: 76px;
  resize: vertical;
  padding: 9px 11px;
  border: 0.5px solid var(--dsw-alias-border-l3);
  border-radius: var(--dsw-radius-md);
  background: transparent;
  color: var(--dsw-alias-label-primary);
  font: 12px/19px ui-monospace, SFMono-Regular, Menlo, monospace;
}
.dshOverlayDanger { color: var(--dsw-alias-state-error-primary); }
.dshOverlayImport { margin-top: 14px; padding: 11px 12px; border: 0.5px solid var(--dsw-alias-border-l4); border-radius: var(--dsw-radius-lg); }
.dshOverlayImport summary { cursor: pointer; color: var(--dsw-alias-label-secondary); font-size: 13px; font-weight: 500; }
.dshOverlayImportText { margin: 9px 0; min-height: 110px; }
.dshOverlaySettingsFooter { justify-content: flex-start; margin-top: 16px; }
.dshOverlayTemplateGrid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
  gap: 10px;
}
.dshOverlayTemplateCard {
  display: flex;
  min-height: 150px;
  cursor: pointer;
  flex-direction: column;
  align-items: flex-start;
  gap: 7px;
  padding: 14px;
  border: 0.5px solid var(--dsw-alias-border-l4);
  border-radius: var(--dsw-radius-lg);
  background: var(--dsw-alias-bg-layer-1);
  color: var(--dsw-alias-label-primary);
  text-align: left;
  transition: border-color 120ms ease, background 120ms ease, transform 120ms ease;
}
.dshOverlayTemplateCard:hover,
.dshOverlayTemplateCard:focus-visible {
  border-color: var(--dsw-alias-border-l2);
  background: var(--dsw-alias-interactive-bg-hover-solid);
  transform: translateY(-1px);
}
.dshOverlayTemplateCard strong { font-size: 14px; }
.dshOverlayTemplateCard small { flex: 1; color: var(--dsw-alias-label-tertiary); font-size: 12px; line-height: 18px; }
.dshOverlayTemplateCard em { color: var(--dsw-alias-label-secondary); font-size: 11px; font-style: normal; font-weight: 600; }
.dshOverlayTemplateIcon {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  border-radius: var(--dsw-radius-md);
  background: var(--dsw-alias-interactive-bg-hover-solid);
  font-size: 17px;
}
.dshOverlayTemplateConfig {
  margin-top: 14px;
  padding: 15px;
  border: 0.5px solid var(--dsw-alias-border-l3);
  border-radius: var(--dsw-radius-lg);
  background: var(--dsw-alias-bg-layer-1);
}
.dshOverlayTemplateConfig .dshOverlaySettingsHead { align-items: flex-start; }
.dshOverlayTemplateConfig h3 { margin: 0; }
.dshOverlayMcpIdentity,
.dshOverlayMcpHead > span.dshOverlayMcpIdentity {
  display: flex;
  min-width: 0;
  flex-direction: row;
  align-items: center;
  gap: 9px;
}
.dshOverlayMcpIdentity > span { display: flex; min-width: 0; flex-direction: column; gap: 2px; }
.dshOverlayStatusDot { width: 9px; height: 9px; flex: none; border-radius: 50%; background: var(--dsw-alias-label-quaternary); }
.dshOverlayMcpCard[data-state='ok'] .dshOverlayStatusDot { background: #20a464; box-shadow: 0 0 0 3px rgba(32,164,100,.13); }
.dshOverlayMcpCard[data-state='failed'] .dshOverlayStatusDot { background: var(--dsw-alias-state-error-primary); }
.dshOverlayMcpCard[data-state='testing'] .dshOverlayStatusDot,
.dshOverlayMcpCard[data-state='pending'] .dshOverlayStatusDot { background: #d99b20; }
.dshOverlayMcpSummary {
  overflow: hidden;
  margin: -5px 0 9px 18px;
  color: var(--dsw-alias-label-tertiary);
  font: 11px/17px ui-monospace, SFMono-Regular, Menlo, monospace;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dshOverlayMcpChips { display: flex; flex-wrap: wrap; gap: 5px; margin: 0 0 12px 18px; }
.dshOverlayMcpChips span { padding: 2px 6px; border-radius: 999px; background: var(--dsw-alias-interactive-bg-hover-solid); color: var(--dsw-alias-label-tertiary); font-size: 10px; }
.dshOverlayMcpEditor { margin-top: 13px; padding-top: 13px; border-top: 0.5px solid var(--dsw-alias-border-l4); }
.dshOverlayMcpActions { display: flex; flex-wrap: wrap; align-items: center; gap: 7px; }
@media (max-width: 720px) {
  .dshOverlaySettingsHead { align-items: flex-start; flex-direction: column; }
  .dshOverlayFormGrid { grid-template-columns: 1fr; }
  .dshOverlayFormWide { grid-column: auto; }
  .dshOverlaySkillRow { flex-wrap: wrap; }
  .dshOverlayBadges { justify-content: flex-start; max-width: none; padding-left: 42px; }
}
`

export function injectOverlayStyles(): void {
  if (typeof document === 'undefined') return
  if (document.getElementById(STYLE_ID) !== null) return
  const style = document.createElement('style')
  style.id = STYLE_ID
  style.textContent = CSS
  document.head.appendChild(style)
}
