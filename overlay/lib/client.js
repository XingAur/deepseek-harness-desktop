window.__ModuleLoader__.load({id:"dsh-overlay-custom",factory:(require)=>{var module={exports:{}};var exports=module.exports;
var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __export = (target, all) => {
  for (var name in all)
    __defProp(target, name, { get: all[name], enumerable: true });
};
var __copyProps = (to, from, except, desc) => {
  if (from && typeof from === "object" || typeof from === "function") {
    for (let key of __getOwnPropNames(from))
      if (!__hasOwnProp.call(to, key) && key !== except)
        __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
  }
  return to;
};
var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

// src/client/index.ts
var index_exports = {};
__export(index_exports, {
  apply: () => apply,
  inject: () => inject
});
module.exports = __toCommonJS(index_exports);

// src/client/RemoteEntry.tsx
var import_react2 = require("react");

// src/client/menu-actions.ts
var import_react = require("react");
var MENU_ACTION_EVENT = "dsh-overlay:menu-action";
var ACCOUNT_MENU_PATCH_EVENT = "dsh-overlay:account-menu-patch";
function flags() {
  return typeof window === "undefined" ? void 0 : window;
}
function accountMenuPatched() {
  return flags()?.__DSH_ACCOUNT_MENU_PATCH__ === true;
}
function useAccountMenuPatched() {
  const [patched, setPatched] = (0, import_react.useState)(accountMenuPatched);
  (0, import_react.useEffect)(() => {
    const handler = () => {
      setPatched(accountMenuPatched());
    };
    window.addEventListener(ACCOUNT_MENU_PATCH_EVENT, handler);
    return () => {
      window.removeEventListener(ACCOUNT_MENU_PATCH_EVENT, handler);
    };
  }, []);
  return patched;
}
function useMenuAction(action, open) {
  (0, import_react.useEffect)(() => {
    const handler = (event) => {
      if (event.detail === action) open();
    };
    window.addEventListener(MENU_ACTION_EVENT, handler);
    return () => {
      window.removeEventListener(MENU_ACTION_EVENT, handler);
    };
  }, [action, open]);
}
function publishUpdatePending(pending) {
  const target = flags();
  if (target === void 0) return;
  target.__DSH_OVERLAY_UPDATE_PENDING__ = pending;
  window.dispatchEvent(new CustomEvent("dsh-overlay:update-pending", { detail: pending }));
}

// src/client/origin-input.ts
function normalizeRelayOrigin(value) {
  const trimmed2 = value.trim();
  if (trimmed2 === "") return null;
  const withScheme = /^[a-z][a-z0-9+.-]*:\/\//iu.test(trimmed2) ? trimmed2 : `https://${trimmed2}`;
  try {
    const url = new URL(withScheme);
    if (url.protocol !== "https:" && url.protocol !== "http:") return null;
    if (url.pathname !== "/" || url.search !== "" || url.hash !== "") return null;
    if (url.username !== "" || url.password !== "") return null;
    if (url.protocol === "http:" && !/^(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?$/u.test(url.host)) return null;
    return url.origin;
  } catch {
    return null;
  }
}

// src/client/RemoteEntry.tsx
var import_jsx_runtime = require("react/jsx-runtime");
var NAMESPACE = "overlay.remote";
var en = {
  label: "Mobile",
  waiting: "Waiting for remote connection\u2026",
  connected: "Remote connected",
  copy: "Copy pairing link",
  copied: "Copied",
  disabled: "Enter the relay origin below. It will be remembered for future launches.",
  close: "Close",
  origin: "Relay origin (https://\u2026)",
  saveOrigin: "Save and connect",
  saving: "Saving\u2026",
  invalidOrigin: "Use an https:// relay origin. Public relays must use HTTPS; local development may use http://localhost.",
  saveFailed: "Could not save the relay origin. Check the address and its TLS certificate."
};
var zh = {
  label: "\u624B\u673A\u8FDE\u63A5",
  waiting: "\u7B49\u5F85\u8FDC\u7A0B\u8FDE\u63A5\u2026",
  connected: "\u8FDC\u7A0B\u5DF2\u8FDE\u63A5",
  copy: "\u590D\u5236\u914D\u5BF9\u94FE\u63A5",
  copied: "\u5DF2\u590D\u5236",
  disabled: "\u5728\u4E0B\u65B9\u586B\u5199\u4E2D\u7EE7\u5730\u5740\uFF1B\u4FDD\u5B58\u540E\u4EE5\u540E\u542F\u52A8\u4F1A\u81EA\u52A8\u8BB0\u4F4F\u3002",
  close: "\u5173\u95ED",
  origin: "\u4E2D\u7EE7\u5730\u5740\uFF08https://\u2026\uFF09",
  saveOrigin: "\u4FDD\u5B58\u5E76\u8FDE\u63A5",
  saving: "\u4FDD\u5B58\u4E2D\u2026",
  invalidOrigin: "\u8BF7\u4F7F\u7528 https:// \u5F00\u5934\u7684\u4E2D\u7EE7\u5730\u5740\u3002\u516C\u7F51\u4E2D\u7EE7\u5FC5\u987B\u4F7F\u7528 HTTPS\uFF1B\u672C\u673A\u5F00\u53D1\u53EF\u7528 http://localhost\u3002",
  saveFailed: "\u4FDD\u5B58\u5931\u8D25\uFF0C\u8BF7\u68C0\u67E5\u4E2D\u7EE7\u5730\u5740\u548C TLS \u8BC1\u4E66\u3002"
};
var STATE_PATH = "/api/desktop/remote/state";
var OPEN_PATH = "/api/desktop/remote/pairing/open";
var STYLE_ID = "dsh-overlay-remote-entry-styles";
var CSS = `
.dshOverlayRemoteEntry {
  box-sizing: border-box;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 32px;
  min-width: 32px;
  padding: 0;
  border: none;
  border-radius: 8px;
  background: transparent;
  cursor: pointer;
  color: var(--dsw-alias-label-secondary);
  font: inherit;
  font-size: 13px;
  line-height: 20px;
}
.dshOverlayRemoteEntryWide {
  justify-content: flex-start;
  padding: 0 10px 0 8px;
  width: calc(100% + 4px);
  margin: 0 -2px;
  height: 42px;
}
.dshOverlayRemoteEntry:hover {
  background: var(--dsw-alias-interactive-bg-hover);
  color: var(--dsw-alias-label-primary);
}
.dshOverlayRemoteEntry:focus-visible {
  outline: none;
  box-shadow: 0 0 0 2px var(--dsw-alias-border-l3);
}
.dshOverlayRemoteOnline { color: var(--dsw-alias-state-success-primary); }
.dshOverlayRemoteOnline:hover { color: var(--dsw-alias-state-success-primary); }
.dshOverlayRemoteWaiting { color: var(--dsw-alias-state-warn-primary); }
.dshOverlayRemoteWaiting:hover { color: var(--dsw-alias-state-warn-primary); }
.dshOverlayRemoteEntryLabel {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dshOverlayRemoteModal {
  position: fixed;
  z-index: 40;
  right: 24px;
  bottom: 24px;
  max-width: 360px;
  padding: 12px 14px;
  border: 0.5px solid var(--dsw-alias-settings-card-stroke);
  border-radius: var(--dsw-radius-xl);
  background: var(--dsw-alias-settings-card-fill);
  color: var(--dsw-alias-label-primary);
  font-size: 14px;
  line-height: 22px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.dshOverlayRemoteModal p { margin: 0; word-break: break-all; }
.dshOverlayRemoteError { color: var(--dsw-alias-state-error-primary, #e5484d); font-size: 13px; line-height: 20px; }
.dshOverlayRemoteQr {
  width: 232px;
  height: 232px;
  margin: 0 auto;
  color: var(--dsw-alias-label-primary);
}
.dshOverlayRemoteQr svg { width: 100%; height: 100%; display: block; }
`;
function installRemoteStyles() {
  if (typeof document === "undefined") return;
  if (document.getElementById(STYLE_ID) !== null) return;
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = CSS;
  document.head.appendChild(style);
}
function PhoneIcon() {
  return /* @__PURE__ */ (0, import_jsx_runtime.jsxs)(
    "svg",
    {
      "aria-hidden": true,
      width: "16",
      height: "16",
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: "currentColor",
      strokeWidth: "1.5",
      strokeLinecap: "round",
      strokeLinejoin: "round",
      children: [
        /* @__PURE__ */ (0, import_jsx_runtime.jsx)("rect", { x: "5", y: "2", width: "14", height: "20", rx: "2", ry: "2" }),
        /* @__PURE__ */ (0, import_jsx_runtime.jsx)("path", { d: "M12 18h.01" })
      ]
    }
  );
}
function OverlayRemoteEntry({ t, wide }) {
  const [view, setView] = (0, import_react2.useState)();
  const [open, setOpen] = (0, import_react2.useState)(false);
  const [copied, setCopied] = (0, import_react2.useState)(false);
  const [originDraft, setOriginDraft] = (0, import_react2.useState)("");
  const [saving, setSaving] = (0, import_react2.useState)(false);
  const [error, setError] = (0, import_react2.useState)();
  const menuPatched = useAccountMenuPatched();
  (0, import_react2.useEffect)(() => {
    let stopped = false;
    const read = () => {
      void fetch(STATE_PATH, { credentials: "same-origin", headers: { accept: "application/json" } }).then(async (response) => response.ok ? await response.json() : void 0).then((next) => {
        if (!stopped && next !== void 0) setView(next);
      }).catch(() => {
      });
    };
    read();
    const timer = window.setInterval(read, 4e3);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, []);
  const enable = (0, import_react2.useCallback)(() => {
    void fetch(OPEN_PATH, {
      method: "POST",
      credentials: "same-origin",
      headers: { accept: "application/json" },
      redirect: "error"
    }).then(async (response) => response.ok ? await response.json() : void 0).then((next) => {
      if (next !== void 0) setView(next);
    }).catch(() => {
    });
    setOpen(true);
  }, []);
  useMenuAction("remote", enable);
  const saveOrigin = (0, import_react2.useCallback)(() => {
    const origin = normalizeRelayOrigin(originDraft);
    if (origin === null) {
      setError(t("invalidOrigin"));
      return;
    }
    setError(void 0);
    setSaving(true);
    void fetch("/api/desktop/remote/origin", {
      method: "POST",
      credentials: "same-origin",
      headers: { accept: "application/json", "content-type": "application/json" },
      redirect: "error",
      body: JSON.stringify({ origin })
    }).then(async (response) => {
      const value = await response.json().catch(() => void 0);
      if (!response.ok || value === void 0) throw new Error("save failed");
      setView(value);
      setOriginDraft(value.relayOrigin ?? origin);
    }).catch(() => {
      setError(t("saveFailed"));
    }).finally(() => {
      setSaving(false);
    });
  }, [originDraft, t]);
  const copy = (0, import_react2.useCallback)(() => {
    if (view?.pairUrl == null) return;
    void navigator.clipboard.writeText(view.pairUrl).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    }).catch(() => {
    });
  }, [view?.pairUrl]);
  const online = view?.connected === true;
  const waiting = view?.waiting === true && !online;
  return /* @__PURE__ */ (0, import_jsx_runtime.jsxs)(import_jsx_runtime.Fragment, { children: [
    menuPatched ? null : /* @__PURE__ */ (0, import_jsx_runtime.jsxs)(
      "button",
      {
        type: "button",
        className: [
          "dshOverlayRemoteEntry",
          wide ? "dshOverlayRemoteEntryWide" : "",
          online ? "dshOverlayRemoteOnline" : waiting ? "dshOverlayRemoteWaiting" : ""
        ].filter((part) => part !== "").join(" "),
        "aria-label": t("label"),
        title: online ? t("connected") : waiting ? t("waiting") : t("label"),
        onClick: enable,
        children: [
          /* @__PURE__ */ (0, import_jsx_runtime.jsx)(PhoneIcon, {}),
          wide ? /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "dshOverlayRemoteEntryLabel", children: t("label") }) : null
        ]
      }
    ),
    open ? /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "dshOverlayRemoteModal", role: "dialog", "aria-modal": "true", "aria-label": t("label"), children: [
      view?.state === "disabled" ? /* @__PURE__ */ (0, import_jsx_runtime.jsxs)(import_jsx_runtime.Fragment, { children: [
        /* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", { children: t("disabled") }),
        /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
          "input",
          {
            className: "dshOverlayInput",
            value: originDraft,
            placeholder: t("origin"),
            onChange: (event) => {
              setOriginDraft(event.target.value);
              setError(void 0);
            }
          }
        ),
        /* @__PURE__ */ (0, import_jsx_runtime.jsx)("button", { type: "button", className: "dshOverlayBtnPrimary", onClick: saveOrigin, disabled: saving, children: saving ? t("saving") : t("saveOrigin") }),
        error !== void 0 ? /* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", { className: "dshOverlayRemoteError", role: "alert", children: error }) : null
      ] }) : /* @__PURE__ */ (0, import_jsx_runtime.jsxs)(import_jsx_runtime.Fragment, { children: [
        view?.qrSvg != null && view.qrSvg !== "" ? /* @__PURE__ */ (0, import_jsx_runtime.jsx)("div", { className: "dshOverlayRemoteQr", dangerouslySetInnerHTML: { __html: view.qrSvg } }) : null,
        /* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", { children: view?.pairUrl ?? view?.error ?? t("waiting") })
      ] }),
      view?.pairUrl != null ? /* @__PURE__ */ (0, import_jsx_runtime.jsx)("button", { type: "button", className: "dshOverlayBtnSecondary", onClick: copy, children: copied ? t("copied") : t("copy") }) : null,
      /* @__PURE__ */ (0, import_jsx_runtime.jsx)("button", { type: "button", className: "dshOverlayBtnSecondary", onClick: () => setOpen(false), children: t("close") })
    ] }) : null
  ] });
}
function applyOverlayRemoteEntry(ctx) {
  installRemoteStyles();
  ctx.effect(() => ctx.locale.register(NAMESPACE, { zh, en }), "dsh-overlay-custom: remote locale");
  ctx.effect(() => ctx.slots.inject("sidebar.footer.action", () => ctx.slots.register({
    name: "sidebar.footer.action",
    id: "overlay-remote",
    priority: -1,
    order: 26,
    label: () => ctx.locale.bind(NAMESPACE)("label"),
    inject: () => ({ t: ctx.locale.bind(NAMESPACE) })
  }, OverlayRemoteEntry)), "dsh-overlay-custom: remote sidebar entry");
}

// src/client/ProviderCardExtras.tsx
var import_react4 = require("react");

// src/oauth-providers.ts
var OAUTH_LOGIN_PROVIDERS = ["openai-codex", "xai"];
function isOauthLoginProvider(value) {
  return OAUTH_LOGIN_PROVIDERS.includes(value);
}

// src/client/overlay-styles.ts
var STYLE_ID2 = "dsh-overlay-custom-models";
var CSS2 = `
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
`;
function injectOverlayStyles() {
  if (typeof document === "undefined") return;
  if (document.getElementById(STYLE_ID2) !== null) return;
  const style = document.createElement("style");
  style.id = STYLE_ID2;
  style.textContent = CSS2;
  document.head.appendChild(style);
}

// src/client/ProxyFooter.tsx
var import_react3 = require("react");
var import_jsx_runtime2 = require("react/jsx-runtime");
var NAMESPACE2 = "overlay.proxy";
var en2 = {
  title: "Model network",
  intro: "Applies to all model providers, not MCP or other tools. No provider list is needed.",
  mode: "Connection mode",
  modeSystem: "Follow system (default)",
  modeManual: "Manual proxy",
  modeDirect: "Direct",
  systemHint: "Use the detected system HTTP/HTTPS proxies and bypass rules. Unsupported or unreadable settings do not silently fall back to direct.",
  manualHint: "Use an HTTP or HTTPS proxy for model requests. A failed proxy does not fall back to direct.",
  directHint: "Connect directly, ignoring system proxies and proxy environment variables.",
  url: "Manual proxy URL",
  urlPlaceholder: "http://127.0.0.1:7890",
  credentialsHint: "Stored credentials are hidden and preserved while this URL is unchanged.",
  urlEditedHint: "Editing replaces the entire URL. Include credentials again if the proxy requires them; credential-bearing input is masked.",
  bypass: "Extra direct destinations",
  bypassHint: "Separate domains or IP addresses with commas or new lines. Matching destinations bypass the proxy.",
  loopbackHint: "Loopback destinations always connect directly. Successfully detected system bypass rules also apply in manual proxy mode.",
  save: "Save",
  saving: "Saving\u2026",
  test: "Test connection",
  testing: "Testing\u2026",
  testHint: "Test the current inputs without saving them. Success only confirms this test connection, not every model provider.",
  refresh: "Refresh detection",
  refreshing: "Refreshing\u2026",
  loading: "Loading proxy settings\u2026",
  saved: "Saved. New model requests use these settings; in-flight requests keep their original route.",
  refreshed: "System detection and saved status refreshed. Unsaved inputs were kept.",
  unsaved: "Unsaved changes. The effective settings below have not changed.",
  loadFailed: "Could not load proxy settings.",
  refreshFailed: "Could not refresh proxy detection.",
  saveFailed: "Could not save proxy settings.",
  testFailed: "Connection test failed.",
  testOk: "Connection test succeeded. Nothing was saved.",
  testOkProxy: "The test connected through a proxy. Nothing was saved.",
  testOkDirect: "The test connected directly. Nothing was saved.",
  testOrigin: "Test connection address",
  effectiveTitle: "Effective settings for new model requests",
  effectiveMode: "Saved mode",
  effectiveProxy: "Manual proxy",
  effectiveSystem: "Use the detected routes below, subject to destination bypass rules.",
  effectiveUnavailable: "System routing is unavailable. Requests that need it will fail rather than silently connect directly.",
  appliedBypass: "Saved extra direct destinations",
  systemTitle: "Detected system settings",
  systemReady: "System proxy detected",
  systemDirect: "No system proxy enabled (direct)",
  systemUnsupported: "Unsupported system proxy configuration",
  systemError: "System proxy detection failed",
  httpProxy: "HTTP route",
  httpsProxy: "HTTPS route",
  systemBypass: "System bypass rules",
  none: "None",
  directRoute: "Direct",
  stale: "Showing the last successfully read status. Refresh to try again.",
  configWarning: "Saved configuration needs attention.",
  errorNetwork: "The request could not reach the application. Check the connection and try again.",
  errorResponse: "The application returned an invalid response. Refresh or check that the client and host versions match.",
  errorHttp: "The application rejected the request. Try again or check the host status.",
  errorUnauthorized: "The request was not authorized. Reload the application and try again.",
  errorMode: "Choose Follow system, Manual proxy, or Direct.",
  errorUrl: "Enter a valid HTTP or HTTPS proxy URL.",
  errorNoProxy: "Manual mode requires a proxy URL.",
  errorBypass: "One or more direct destinations are invalid. Check the domain or IP address entries.",
  errorProxyRefused: "The proxy refused the connection. Check that it is running and the address is correct.",
  errorProxyAuth: "Proxy authentication failed. Check the credentials in the full proxy URL.",
  errorDns: "The connection address could not be resolved. Check DNS and proxy settings.",
  errorTimeout: "The request timed out. Check the network and try again.",
  errorTls: "TLS verification failed. Check the proxy and certificate configuration.",
  errorUnknown: "The operation failed. Check the proxy settings and try again.",
  errorConfig: "The proxy configuration is invalid.",
  errorRead: "The saved proxy configuration could not be read.",
  errorWrite: "The proxy configuration could not be written. Check the application data directory permissions.",
  errorUrlLength: "The proxy URL is too long.",
  errorScheme: "Only HTTP and HTTPS proxy URLs are supported.",
  errorPlatform: "System proxy detection is not supported on this platform. Choose Manual proxy or Direct.",
  errorSystemRead: "System proxy settings could not be read. Refresh detection or choose another mode.",
  errorSystemConfig: "The system proxy settings are invalid. Check the system proxy address and port.",
  errorPac: "Automatic proxy configuration (PAC) is not supported. Choose Manual proxy or Direct.",
  errorAutoDiscovery: "Automatic proxy discovery is not supported. Choose Manual proxy or Direct.",
  errorSocks: "This system SOCKS proxy configuration is not supported. Use an HTTP/HTTPS proxy or Direct.",
  errorScoped: "Interface-scoped system proxy settings are not supported. Choose Manual proxy or Direct.",
  errorList: "This system proxy list or failover configuration is not supported. Choose Manual proxy or Direct.",
  errorBypassUnsupported: "This system bypass rule is not supported. Choose Manual proxy or Direct.",
  errorTarget: "The connection test destination is invalid. Check the application configuration.",
  errorRuntime: "Model routing is unavailable. Restart the application and retry; the proxy was not bypassed.",
  errorTransport: "This transport cannot safely use the selected route. No direct fallback was attempted."
};
var zh2 = {
  title: "\u6A21\u578B\u7F51\u7EDC",
  intro: "\u9002\u7528\u4E8E\u6240\u6709\u6A21\u578B\u5382\u5546\uFF0C\u4E0D\u5F71\u54CD MCP \u6216\u5176\u4ED6\u5DE5\u5177\uFF0C\u65E0\u9700\u7EF4\u62A4\u5382\u5546\u540D\u5355\u3002",
  mode: "\u8FDE\u63A5\u6A21\u5F0F",
  modeSystem: "\u8DDF\u968F\u7CFB\u7EDF\uFF08\u9ED8\u8BA4\uFF09",
  modeManual: "\u624B\u52A8\u4EE3\u7406",
  modeDirect: "\u76F4\u8FDE",
  systemHint: "\u4F7F\u7528\u68C0\u6D4B\u5230\u7684\u7CFB\u7EDF HTTP/HTTPS \u4EE3\u7406\u53CA\u7ED5\u8FC7\u89C4\u5219\u3002\u4E0D\u652F\u6301\u6216\u65E0\u6CD5\u8BFB\u53D6\u7684\u7CFB\u7EDF\u914D\u7F6E\u4E0D\u4F1A\u9759\u9ED8\u56DE\u9000\u4E3A\u76F4\u8FDE\u3002",
  manualHint: "\u6A21\u578B\u8BF7\u6C42\u4F7F\u7528 HTTP \u6216 HTTPS \u4EE3\u7406\uFF1B\u4EE3\u7406\u4E0D\u53EF\u7528\u65F6\u4E0D\u4F1A\u56DE\u9000\u4E3A\u76F4\u8FDE\u3002",
  directHint: "\u76F4\u63A5\u8FDE\u63A5\uFF0C\u5FFD\u7565\u7CFB\u7EDF\u4EE3\u7406\u53CA\u4EE3\u7406\u73AF\u5883\u53D8\u91CF\u3002",
  url: "\u624B\u52A8\u4EE3\u7406 URL",
  urlPlaceholder: "http://127.0.0.1:7890",
  credentialsHint: "\u5DF2\u4FDD\u5B58\u7684\u51ED\u636E\u4E0D\u4F1A\u56DE\u663E\uFF1B\u4E0D\u4FEE\u6539\u6B64\u5730\u5740\u5373\u53EF\u4FDD\u7559\u51ED\u636E\u3002",
  urlEditedHint: "\u7F16\u8F91\u5730\u5740\u4F1A\u66FF\u6362\u5B8C\u6574 URL\uFF1B\u5982\u9700\u8BA4\u8BC1\uFF0C\u8BF7\u91CD\u65B0\u586B\u5199\u51ED\u636E\u3002\u542B\u51ED\u636E\u7684\u8F93\u5165\u4F1A\u88AB\u906E\u6321\u3002",
  bypass: "\u989D\u5916\u76F4\u8FDE\u5730\u5740",
  bypassHint: "\u586B\u5199\u57DF\u540D\u6216 IP \u5730\u5740\uFF0C\u7528\u9017\u53F7\u6216\u6362\u884C\u5206\u9694\uFF1B\u5339\u914D\u7684\u76EE\u6807\u5C06\u7ED5\u8FC7\u4EE3\u7406\u3002",
  loopbackHint: "\u56DE\u73AF\u5730\u5740\u59CB\u7EC8\u76F4\u8FDE\uFF1B\u624B\u52A8\u4EE3\u7406\u6A21\u5F0F\u4E5F\u4F1A\u9075\u5FAA\u6210\u529F\u8BFB\u53D6\u7684\u7CFB\u7EDF\u7ED5\u8FC7\u89C4\u5219\u3002",
  save: "\u4FDD\u5B58",
  saving: "\u4FDD\u5B58\u4E2D\u2026",
  test: "\u6D4B\u8BD5\u8FDE\u63A5",
  testing: "\u6D4B\u8BD5\u4E2D\u2026",
  testHint: "\u6309\u5F53\u524D\u8F93\u5165\u6D4B\u8BD5\uFF0C\u4E0D\u4F1A\u4FDD\u5B58\u3002\u6210\u529F\u4EC5\u4EE3\u8868\u672C\u6B21\u8FDE\u63A5\u53EF\u8FBE\uFF0C\u4E0D\u4EE3\u8868\u6240\u6709\u6A21\u578B\u5382\u5546\u5747\u53EF\u7528\u3002",
  refresh: "\u5237\u65B0\u68C0\u6D4B",
  refreshing: "\u5237\u65B0\u4E2D\u2026",
  loading: "\u6B63\u5728\u8BFB\u53D6\u4EE3\u7406\u914D\u7F6E\u2026",
  saved: "\u5DF2\u4FDD\u5B58\uFF0C\u540E\u7EED\u6A21\u578B\u8BF7\u6C42\u4F7F\u7528\u65B0\u914D\u7F6E\uFF1B\u8FDB\u884C\u4E2D\u7684\u8BF7\u6C42\u4ECD\u4F7F\u7528\u539F\u8DEF\u7531\u3002",
  refreshed: "\u7CFB\u7EDF\u68C0\u6D4B\u4E0E\u5DF2\u4FDD\u5B58\u72B6\u6001\u5DF2\u5237\u65B0\uFF0C\u672A\u4FDD\u5B58\u7684\u8F93\u5165\u5DF2\u4FDD\u7559\u3002",
  unsaved: "\u6709\u672A\u4FDD\u5B58\u7684\u4FEE\u6539\uFF1B\u4E0B\u65B9\u751F\u6548\u914D\u7F6E\u5C1A\u672A\u6539\u53D8\u3002",
  loadFailed: "\u8BFB\u53D6\u4EE3\u7406\u914D\u7F6E\u5931\u8D25\u3002",
  refreshFailed: "\u5237\u65B0\u4EE3\u7406\u68C0\u6D4B\u5931\u8D25\u3002",
  saveFailed: "\u4FDD\u5B58\u4EE3\u7406\u914D\u7F6E\u5931\u8D25\u3002",
  testFailed: "\u8FDE\u63A5\u6D4B\u8BD5\u5931\u8D25\u3002",
  testOk: "\u8FDE\u63A5\u6D4B\u8BD5\u6210\u529F\uFF0C\u672A\u4FDD\u5B58\u4EFB\u4F55\u914D\u7F6E\u3002",
  testOkProxy: "\u672C\u6B21\u6D4B\u8BD5\u901A\u8FC7\u4EE3\u7406\u8FDE\u63A5\u6210\u529F\uFF0C\u672A\u4FDD\u5B58\u4EFB\u4F55\u914D\u7F6E\u3002",
  testOkDirect: "\u672C\u6B21\u6D4B\u8BD5\u76F4\u8FDE\u6210\u529F\uFF0C\u672A\u4FDD\u5B58\u4EFB\u4F55\u914D\u7F6E\u3002",
  testOrigin: "\u672C\u6B21\u6D4B\u8BD5\u8FDE\u63A5\u5730\u5740",
  effectiveTitle: "\u540E\u7EED\u6A21\u578B\u8BF7\u6C42\u7684\u751F\u6548\u914D\u7F6E",
  effectiveMode: "\u5DF2\u4FDD\u5B58\u6A21\u5F0F",
  effectiveProxy: "\u624B\u52A8\u4EE3\u7406",
  effectiveSystem: "\u6309\u4E0B\u65B9\u68C0\u6D4B\u7ED3\u679C\u9009\u62E9\u8DEF\u7531\uFF0C\u540C\u65F6\u9075\u5FAA\u76EE\u6807\u5730\u5740\u7684\u7ED5\u8FC7\u89C4\u5219\u3002",
  effectiveUnavailable: "\u7CFB\u7EDF\u8DEF\u7531\u4E0D\u53EF\u7528\uFF1B\u9700\u8981\u8BE5\u8DEF\u7531\u7684\u8BF7\u6C42\u5C06\u5931\u8D25\uFF0C\u4E0D\u4F1A\u9759\u9ED8\u76F4\u8FDE\u3002",
  appliedBypass: "\u5DF2\u4FDD\u5B58\u7684\u989D\u5916\u76F4\u8FDE\u5730\u5740",
  systemTitle: "\u7CFB\u7EDF\u4EE3\u7406\u68C0\u6D4B",
  systemReady: "\u5DF2\u68C0\u6D4B\u5230\u7CFB\u7EDF\u4EE3\u7406",
  systemDirect: "\u672A\u542F\u7528\u7CFB\u7EDF\u4EE3\u7406\uFF08\u76F4\u8FDE\uFF09",
  systemUnsupported: "\u7CFB\u7EDF\u4EE3\u7406\u914D\u7F6E\u6682\u4E0D\u652F\u6301",
  systemError: "\u7CFB\u7EDF\u4EE3\u7406\u68C0\u6D4B\u5931\u8D25",
  httpProxy: "HTTP \u8DEF\u7531",
  httpsProxy: "HTTPS \u8DEF\u7531",
  systemBypass: "\u7CFB\u7EDF\u7ED5\u8FC7\u89C4\u5219",
  none: "\u65E0",
  directRoute: "\u76F4\u8FDE",
  stale: "\u5F53\u524D\u663E\u793A\u4E0A\u6B21\u8BFB\u53D6\u6210\u529F\u7684\u72B6\u6001\uFF0C\u8BF7\u5237\u65B0\u91CD\u8BD5\u3002",
  configWarning: "\u5DF2\u4FDD\u5B58\u7684\u914D\u7F6E\u9700\u8981\u5904\u7406\u3002",
  errorNetwork: "\u65E0\u6CD5\u8FDE\u63A5\u5E94\u7528\u670D\u52A1\uFF0C\u8BF7\u68C0\u67E5\u8FDE\u63A5\u540E\u91CD\u8BD5\u3002",
  errorResponse: "\u5E94\u7528\u8FD4\u56DE\u7684\u6570\u636E\u65E0\u6548\uFF0C\u8BF7\u5237\u65B0\u6216\u68C0\u67E5\u524D\u540E\u7AEF\u7248\u672C\u662F\u5426\u5339\u914D\u3002",
  errorHttp: "\u5E94\u7528\u62D2\u7EDD\u4E86\u6B64\u8BF7\u6C42\uFF0C\u8BF7\u91CD\u8BD5\u6216\u68C0\u67E5\u670D\u52A1\u72B6\u6001\u3002",
  errorUnauthorized: "\u8BF7\u6C42\u672A\u83B7\u6388\u6743\uFF0C\u8BF7\u91CD\u65B0\u52A0\u8F7D\u5E94\u7528\u540E\u91CD\u8BD5\u3002",
  errorMode: "\u8BF7\u9009\u62E9\u8DDF\u968F\u7CFB\u7EDF\u3001\u624B\u52A8\u4EE3\u7406\u6216\u76F4\u8FDE\u3002",
  errorUrl: "\u8BF7\u8F93\u5165\u6709\u6548\u7684 HTTP \u6216 HTTPS \u4EE3\u7406 URL\u3002",
  errorNoProxy: "\u624B\u52A8\u6A21\u5F0F\u5FC5\u987B\u586B\u5199\u4EE3\u7406 URL\u3002",
  errorBypass: "\u90E8\u5206\u76F4\u8FDE\u5730\u5740\u65E0\u6548\uFF0C\u8BF7\u68C0\u67E5\u57DF\u540D\u6216 IP \u5730\u5740\u6761\u76EE\u3002",
  errorProxyRefused: "\u4EE3\u7406\u62D2\u7EDD\u8FDE\u63A5\uFF0C\u8BF7\u68C0\u67E5\u4EE3\u7406\u662F\u5426\u542F\u52A8\u4EE5\u53CA\u5730\u5740\u662F\u5426\u6B63\u786E\u3002",
  errorProxyAuth: "\u4EE3\u7406\u8BA4\u8BC1\u5931\u8D25\uFF0C\u8BF7\u68C0\u67E5\u5B8C\u6574\u4EE3\u7406 URL \u4E2D\u7684\u51ED\u636E\u3002",
  errorDns: "\u65E0\u6CD5\u89E3\u6790\u8FDE\u63A5\u5730\u5740\uFF0C\u8BF7\u68C0\u67E5 DNS \u548C\u4EE3\u7406\u914D\u7F6E\u3002",
  errorTimeout: "\u8BF7\u6C42\u8D85\u65F6\uFF0C\u8BF7\u68C0\u67E5\u7F51\u7EDC\u540E\u91CD\u8BD5\u3002",
  errorTls: "TLS \u6821\u9A8C\u5931\u8D25\uFF0C\u8BF7\u68C0\u67E5\u4EE3\u7406\u4E0E\u8BC1\u4E66\u914D\u7F6E\u3002",
  errorUnknown: "\u64CD\u4F5C\u5931\u8D25\uFF0C\u8BF7\u68C0\u67E5\u4EE3\u7406\u914D\u7F6E\u540E\u91CD\u8BD5\u3002",
  errorConfig: "\u4EE3\u7406\u914D\u7F6E\u65E0\u6548\u3002",
  errorRead: "\u65E0\u6CD5\u8BFB\u53D6\u5DF2\u4FDD\u5B58\u7684\u4EE3\u7406\u914D\u7F6E\u3002",
  errorWrite: "\u65E0\u6CD5\u5199\u5165\u4EE3\u7406\u914D\u7F6E\uFF0C\u8BF7\u68C0\u67E5\u5E94\u7528\u6570\u636E\u76EE\u5F55\u7684\u6743\u9650\u3002",
  errorUrlLength: "\u4EE3\u7406 URL \u8FC7\u957F\u3002",
  errorScheme: "\u4EC5\u652F\u6301 HTTP \u548C HTTPS \u4EE3\u7406 URL\u3002",
  errorPlatform: "\u5F53\u524D\u5E73\u53F0\u4E0D\u652F\u6301\u7CFB\u7EDF\u4EE3\u7406\u68C0\u6D4B\uFF0C\u8BF7\u9009\u62E9\u624B\u52A8\u4EE3\u7406\u6216\u76F4\u8FDE\u3002",
  errorSystemRead: "\u65E0\u6CD5\u8BFB\u53D6\u7CFB\u7EDF\u4EE3\u7406\u8BBE\u7F6E\uFF0C\u8BF7\u5237\u65B0\u68C0\u6D4B\u6216\u9009\u62E9\u5176\u4ED6\u6A21\u5F0F\u3002",
  errorSystemConfig: "\u7CFB\u7EDF\u4EE3\u7406\u8BBE\u7F6E\u65E0\u6548\uFF0C\u8BF7\u68C0\u67E5\u7CFB\u7EDF\u4EE3\u7406\u5730\u5740\u548C\u7AEF\u53E3\u3002",
  errorPac: "\u6682\u4E0D\u652F\u6301\u81EA\u52A8\u4EE3\u7406\u914D\u7F6E\uFF08PAC\uFF09\uFF0C\u8BF7\u9009\u62E9\u624B\u52A8\u4EE3\u7406\u6216\u76F4\u8FDE\u3002",
  errorAutoDiscovery: "\u6682\u4E0D\u652F\u6301\u81EA\u52A8\u53D1\u73B0\u4EE3\u7406\uFF0C\u8BF7\u9009\u62E9\u624B\u52A8\u4EE3\u7406\u6216\u76F4\u8FDE\u3002",
  errorSocks: "\u6682\u4E0D\u652F\u6301\u6B64\u7CFB\u7EDF SOCKS \u4EE3\u7406\u914D\u7F6E\uFF0C\u8BF7\u4F7F\u7528 HTTP/HTTPS \u4EE3\u7406\u6216\u76F4\u8FDE\u3002",
  errorList: "\u6682\u4E0D\u652F\u6301\u6B64\u7CFB\u7EDF\u4EE3\u7406\u5217\u8868\u6216\u6545\u969C\u8F6C\u79FB\u914D\u7F6E\uFF0C\u8BF7\u9009\u62E9\u624B\u52A8\u4EE3\u7406\u6216\u76F4\u8FDE\u3002",
  errorBypassUnsupported: "\u6682\u4E0D\u652F\u6301\u6B64\u7CFB\u7EDF\u7ED5\u8FC7\u89C4\u5219\uFF0C\u8BF7\u9009\u62E9\u624B\u52A8\u4EE3\u7406\u6216\u76F4\u8FDE\u3002",
  errorScoped: "\u6682\u4E0D\u652F\u6301\u6309\u7F51\u7EDC\u63A5\u53E3\u533A\u5206\u7684\u7CFB\u7EDF\u4EE3\u7406\u914D\u7F6E\uFF0C\u8BF7\u9009\u62E9\u624B\u52A8\u4EE3\u7406\u6216\u76F4\u8FDE\u3002",
  errorTarget: "\u8FDE\u63A5\u6D4B\u8BD5\u7684\u76EE\u6807\u5730\u5740\u65E0\u6548\uFF0C\u8BF7\u68C0\u67E5\u5E94\u7528\u914D\u7F6E\u3002",
  errorRuntime: "\u6A21\u578B\u7F51\u7EDC\u8DEF\u7531\u4E0D\u53EF\u7528\uFF0C\u8BF7\u91CD\u542F\u5E94\u7528\u540E\u91CD\u8BD5\uFF1B\u7A0B\u5E8F\u672A\u7ED5\u8FC7\u4EE3\u7406\u3002",
  errorTransport: "\u6B64\u8FDE\u63A5\u65B9\u5F0F\u6682\u65F6\u65E0\u6CD5\u5B89\u5168\u4F7F\u7528\u6240\u9009\u8DEF\u7531\uFF0C\u7A0B\u5E8F\u672A\u56DE\u9000\u76F4\u8FDE\u3002"
};
var MODE_LABELS = {
  system: "modeSystem",
  manual: "modeManual",
  direct: "modeDirect"
};
var MODE_HINTS = {
  system: "systemHint",
  manual: "manualHint",
  direct: "directHint"
};
var SYSTEM_LABELS = {
  ready: "systemReady",
  direct: "systemDirect",
  unsupported: "systemUnsupported",
  error: "systemError"
};
var OPERATION_ERRORS = {
  load: "loadFailed",
  refresh: "refreshFailed",
  save: "saveFailed",
  test: "testFailed"
};
var ERROR_KEYS = {
  "invalid-config": "errorConfig",
  "invalid-mode": "errorMode",
  "invalid-url": "errorUrl",
  "url-too-long": "errorUrlLength",
  "unsupported-proxy-scheme": "errorScheme",
  "invalid-bypass": "errorBypass",
  "manual-url-required": "errorNoProxy",
  "config-read-failed": "errorRead",
  "config-write-failed": "errorWrite",
  "system-proxy-unsupported-platform": "errorPlatform",
  "system-proxy-read-failed": "errorSystemRead",
  "system-proxy-invalid-config": "errorSystemConfig",
  "system-proxy-pac-unsupported": "errorPac",
  "system-proxy-autodiscovery-unsupported": "errorAutoDiscovery",
  "system-proxy-socks-unsupported": "errorSocks",
  "system-proxy-scoped-unsupported": "errorScoped",
  "system-proxy-list-unsupported": "errorList",
  "system-proxy-bypass-unsupported": "errorBypassUnsupported",
  "no-proxy": "errorNoProxy",
  "proxy-refused": "errorProxyRefused",
  "proxy-auth": "errorProxyAuth",
  dns: "errorDns",
  timeout: "errorTimeout",
  tls: "errorTls",
  unknown: "errorUnknown",
  "request-failed": "errorUnknown",
  "invalid-target-url": "errorTarget",
  "model-proxy-disposed": "errorRuntime",
  "model-proxy-runtime-unavailable": "errorRuntime",
  "model-proxy-transport-unsupported": "errorTransport"
};
function errorKey(code) {
  return typeof code === "string" && Object.hasOwn(ERROR_KEYS, code) ? ERROR_KEYS[code] : "errorUnknown";
}
var ProxyRequestError = class extends Error {
  constructor(key) {
    super(key);
    this.key = key;
  }
  key;
};
function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function isStringArray(value) {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}
function isMode(value) {
  return value === "system" || value === "manual" || value === "direct";
}
function isSystemStatus(value) {
  return value === "ready" || value === "direct" || value === "unsupported" || value === "error";
}
function displayOrigin(value) {
  if (value === "") return "";
  try {
    const parsed = new URL(value);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") return parsed.origin;
  } catch {
  }
  throw new ProxyRequestError("errorResponse");
}
function configView(payload) {
  const system = payload.system;
  if (!isMode(payload.mode) || typeof payload.url !== "string" || typeof payload.hasCredentials !== "boolean" || !isStringArray(payload.bypass) || !isRecord(system) || !isSystemStatus(system.status) || typeof system.httpProxy !== "string" || typeof system.httpsProxy !== "string" || !isStringArray(system.bypass)) throw new ProxyRequestError("errorResponse");
  return {
    mode: payload.mode,
    url: displayOrigin(payload.url),
    hasCredentials: payload.hasCredentials,
    bypass: payload.bypass,
    system: {
      status: system.status,
      httpProxy: displayOrigin(system.httpProxy),
      httpsProxy: displayOrigin(system.httpsProxy),
      bypass: system.bypass,
      ...typeof system.error === "string" ? { error: system.error } : {}
    },
    ...typeof payload.error === "string" ? { error: payload.error } : {}
  };
}
function requestBody(draft) {
  const url = draft.url.trim();
  if (draft.mode === "manual" && url === "") throw new ProxyRequestError("errorNoProxy");
  if (draft.urlEdited && url !== "") {
    let parsed;
    try {
      parsed = new URL(url);
    } catch {
      throw new ProxyRequestError("errorUrl");
    }
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") throw new ProxyRequestError("errorScheme");
  }
  return {
    mode: draft.mode,
    bypass: draft.bypassText.split(/[,\n]/u).map((entry) => entry.trim()).filter(Boolean),
    // The GET URL has no userinfo. Sending it unchanged would erase the saved credentials.
    ...draft.urlEdited ? { url } : {}
  };
}
async function requestPayload(request, draft) {
  const body = draft === void 0 ? void 0 : requestBody(draft);
  const response = await fetch(`/api/desktop/proxy/${request.kind === "test" ? "test" : "config"}`, {
    method: body === void 0 ? "GET" : "POST",
    credentials: "same-origin",
    redirect: "error",
    cache: "no-store",
    signal: request.controller.signal,
    headers: { accept: "application/json", ...body === void 0 ? {} : { "content-type": "application/json" } },
    ...body === void 0 ? {} : { body: JSON.stringify(body) }
  });
  if (response.status === 401 || response.status === 403) throw new ProxyRequestError("errorUnauthorized");
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new ProxyRequestError(response.ok ? "errorResponse" : "errorHttp");
  }
  if (!response.ok) throw new ProxyRequestError(isRecord(payload) && typeof payload.error === "string" ? errorKey(payload.error) : "errorHttp");
  if (!isRecord(payload) || typeof payload.ok !== "boolean") throw new ProxyRequestError("errorResponse");
  if (!payload.ok) throw new ProxyRequestError(errorKey(payload.error));
  return payload;
}
function OverlayProxyFooter(props) {
  const { t } = props;
  const id = (0, import_react3.useId)();
  const [view, setView] = (0, import_react3.useState)();
  const [mode, setMode] = (0, import_react3.useState)("system");
  const [url, setUrl] = (0, import_react3.useState)("");
  const [urlEdited, setUrlEdited] = (0, import_react3.useState)(false);
  const [bypassText, setBypassText] = (0, import_react3.useState)("");
  const [dirty, setDirty] = (0, import_react3.useState)(false);
  const [busy, setBusy] = (0, import_react3.useState)("load");
  const [notice, setNotice] = (0, import_react3.useState)();
  const [test, setTest] = (0, import_react3.useState)();
  const [error, setError] = (0, import_react3.useState)();
  const mounted = (0, import_react3.useRef)(false);
  const draftDirty = (0, import_react3.useRef)(false);
  const pending = (0, import_react3.useRef)(null);
  const run = (0, import_react3.useCallback)(async (kind, draft2) => {
    if (!mounted.current || pending.current !== null) return;
    const request = { kind, controller: new AbortController(), timedOut: false };
    pending.current = request;
    const isCurrent = () => mounted.current && pending.current === request;
    request.timeout = setTimeout(() => {
      request.timedOut = true;
      request.controller.abort();
    }, 2e4);
    setBusy(kind);
    setError(void 0);
    setNotice(void 0);
    setTest(void 0);
    try {
      const payload = await requestPayload(request, draft2);
      if (!isCurrent()) return;
      if (request.timedOut) throw new ProxyRequestError("errorTimeout");
      if (kind === "test") {
        if (payload.route !== void 0 && payload.route !== "proxy" && payload.route !== "direct" || payload.origin !== void 0 && typeof payload.origin !== "string") {
          throw new ProxyRequestError("errorResponse");
        }
        setTest({
          ...payload.route === "proxy" || payload.route === "direct" ? { route: payload.route } : {},
          ...typeof payload.origin === "string" ? { origin: displayOrigin(payload.origin) } : {}
        });
      } else {
        const next = configView(payload);
        setView(next);
        if (kind !== "refresh" || !draftDirty.current) {
          setMode(next.mode);
          setUrl(next.url);
          setUrlEdited(false);
          setBypassText(next.bypass.join("\n"));
          draftDirty.current = false;
          setDirty(false);
        }
        if (kind === "save") setNotice("saved");
        if (kind === "refresh") setNotice("refreshed");
      }
    } catch (cause) {
      if (isCurrent()) setError({ operation: kind, key: request.timedOut ? "errorTimeout" : cause instanceof ProxyRequestError ? cause.key : "errorNetwork" });
    } finally {
      clearTimeout(request.timeout);
      if (isCurrent()) {
        pending.current = null;
        setBusy(void 0);
      }
    }
  }, []);
  (0, import_react3.useEffect)(() => {
    mounted.current = true;
    void run("load");
    return () => {
      mounted.current = false;
      const request = pending.current;
      pending.current = null;
      if (request !== null) {
        clearTimeout(request.timeout);
        request.controller.abort();
      }
    };
  }, [run]);
  const changed = () => {
    draftDirty.current = true;
    setDirty(true);
    setNotice(void 0);
    setError(void 0);
    setTest(void 0);
    const request = pending.current;
    if (request?.kind === "test") {
      pending.current = null;
      clearTimeout(request.timeout);
      request.controller.abort();
      setBusy(void 0);
    }
  };
  const inputDisabled = view === void 0 || busy === "load" || busy === "save";
  const draft = { mode, url, urlEdited, bypassText };
  const systemUnavailable = view?.system.status === "unsupported" || view?.system.status === "error";
  const testMessage = test?.route === "proxy" ? "testOkProxy" : test?.route === "direct" ? "testOkDirect" : "testOk";
  return /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)("section", { className: "dshOverlayProxyFooter", "aria-labelledby": `${id}-title`, "aria-busy": busy !== void 0, children: [
    /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("h3", { className: "dshOverlayTitle", id: `${id}-title`, children: t("title") }),
    /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("p", { className: "dshOverlayIntro", children: t("intro") }),
    /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)("fieldset", { className: "dshOverlayProxyModes", disabled: inputDisabled, "aria-describedby": `${id}-mode-hint`, children: [
      /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("legend", { children: t("mode") }),
      /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("div", { className: "dshOverlayRow", children: ["system", "manual", "direct"].map((value) => /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)("label", { className: "dshOverlayCheck", children: [
        /* @__PURE__ */ (0, import_jsx_runtime2.jsx)(
          "input",
          {
            type: "radio",
            name: `${id}-mode`,
            value,
            checked: mode === value,
            onChange: () => {
              changed();
              setMode(value);
            }
          }
        ),
        t(MODE_LABELS[value])
      ] }, value)) })
    ] }),
    /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("p", { className: "dshOverlayHint", id: `${id}-mode-hint`, children: t(MODE_HINTS[mode]) }),
    mode === "manual" ? /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)("div", { className: "dshOverlayProxyField", children: [
      /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("label", { htmlFor: `${id}-url`, children: t("url") }),
      /* @__PURE__ */ (0, import_jsx_runtime2.jsx)(
        "input",
        {
          id: `${id}-url`,
          className: "dshOverlayInput",
          value: url,
          type: urlEdited && /\/\/[^/?#]*@/u.test(url) ? "password" : "url",
          autoComplete: "off",
          autoCapitalize: "none",
          spellCheck: false,
          placeholder: t("urlPlaceholder"),
          disabled: inputDisabled,
          "aria-describedby": `${id}-url-hint`,
          onChange: (event) => {
            changed();
            setUrlEdited(true);
            setUrl(event.target.value);
          }
        }
      ),
      /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)("p", { className: "dshOverlayHint", id: `${id}-url-hint`, children: [
        view?.hasCredentials && !urlEdited ? `${t("credentialsHint")} ` : "",
        t("urlEditedHint")
      ] })
    ] }) : null,
    /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)("div", { className: "dshOverlayProxyField", children: [
      /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("label", { htmlFor: `${id}-bypass`, children: t("bypass") }),
      /* @__PURE__ */ (0, import_jsx_runtime2.jsx)(
        "textarea",
        {
          id: `${id}-bypass`,
          className: "dshOverlayTextarea",
          rows: 3,
          value: bypassText,
          autoComplete: "off",
          autoCapitalize: "none",
          spellCheck: false,
          disabled: inputDisabled,
          "aria-describedby": `${id}-bypass-hint`,
          onChange: (event) => {
            changed();
            setBypassText(event.target.value);
          }
        }
      ),
      /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)("p", { className: "dshOverlayHint", id: `${id}-bypass-hint`, children: [
        t("bypassHint"),
        " ",
        t("loopbackHint")
      ] })
    ] }),
    dirty ? /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("p", { className: "dshOverlayHint", role: "status", children: t("unsaved") }) : null,
    /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)("div", { className: "dshOverlayRow", children: [
      /* @__PURE__ */ (0, import_jsx_runtime2.jsx)(
        "button",
        {
          type: "button",
          className: "dshOverlayBtnSecondary",
          disabled: busy !== void 0,
          onClick: () => {
            void run("refresh");
          },
          children: busy === "refresh" ? t("refreshing") : t("refresh")
        }
      ),
      /* @__PURE__ */ (0, import_jsx_runtime2.jsx)(
        "button",
        {
          type: "button",
          className: "dshOverlayBtnSecondary",
          disabled: busy !== void 0 || view === void 0,
          "aria-describedby": `${id}-test-hint`,
          onClick: () => {
            void run("test", draft);
          },
          children: busy === "test" ? t("testing") : t("test")
        }
      ),
      /* @__PURE__ */ (0, import_jsx_runtime2.jsx)(
        "button",
        {
          type: "button",
          className: "dshOverlayBtnPrimary",
          disabled: busy !== void 0 || view === void 0,
          onClick: () => {
            void run("save", draft);
          },
          children: busy === "save" ? t("saving") : t("save")
        }
      )
    ] }),
    /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("p", { className: "dshOverlayHint", id: `${id}-test-hint`, children: t("testHint") }),
    busy === "load" ? /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("p", { className: "dshOverlayHint", role: "status", children: t("loading") }) : null,
    notice !== void 0 ? /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("p", { className: "dshOverlaySaved", role: "status", children: t(notice) }) : null,
    test !== void 0 ? /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)("p", { className: "dshOverlaySaved", role: "status", children: [
      t(testMessage),
      test.origin ? /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)(import_jsx_runtime2.Fragment, { children: [
        " ",
        t("testOrigin"),
        ": ",
        /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("code", { children: test.origin })
      ] }) : null
    ] }) : null,
    error !== void 0 ? /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)("p", { className: "dshOverlayAlert", role: "alert", children: [
      t(OPERATION_ERRORS[error.operation]),
      " ",
      t(error.key)
    ] }) : null,
    view !== void 0 ? /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)("div", { className: "dshOverlayProxyStatus", children: [
      /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("h4", { children: t("effectiveTitle") }),
      error?.operation === "refresh" || error?.operation === "load" ? /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("p", { className: "dshOverlayHint", children: t("stale") }) : null,
      view.error ? /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)("p", { className: "dshOverlayAlert", role: "alert", children: [
        t("configWarning"),
        " ",
        t(errorKey(view.error))
      ] }) : null,
      /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)("dl", { children: [
        /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("dt", { children: t("effectiveMode") }),
        /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("dd", { children: t(MODE_LABELS[view.mode]) }),
        view.mode === "manual" ? /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)(import_jsx_runtime2.Fragment, { children: [
          /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("dt", { children: t("effectiveProxy") }),
          /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("dd", { children: /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("code", { children: view.url || t("none") }) })
        ] }) : null,
        /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("dt", { children: t("appliedBypass") }),
        /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("dd", { children: view.bypass.join(", ") || t("none") })
      ] }),
      view.mode === "direct" ? /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("p", { className: "dshOverlayHint", children: t("directHint") }) : null,
      view.mode === "system" ? /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("p", { className: systemUnavailable ? "dshOverlayAlert" : "dshOverlayHint", children: t(systemUnavailable ? "effectiveUnavailable" : "effectiveSystem") }) : null,
      /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("h4", { children: t("systemTitle") }),
      /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)("p", { className: systemUnavailable ? "dshOverlayAlert" : "dshOverlayHint", children: [
        t(SYSTEM_LABELS[view.system.status]),
        view.system.error ? ` \xB7 ${t(errorKey(view.system.error))}` : ""
      ] }),
      !systemUnavailable ? /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)("dl", { children: [
        /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("dt", { children: t("httpProxy") }),
        /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("dd", { children: /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("code", { children: view.system.httpProxy || t("directRoute") }) }),
        /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("dt", { children: t("httpsProxy") }),
        /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("dd", { children: /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("code", { children: view.system.httpsProxy || t("directRoute") }) }),
        /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("dt", { children: t("systemBypass") }),
        /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("dd", { children: view.system.bypass.join(", ") || t("none") })
      ] }) : null
    ] }) : null
  ] });
}
function applyOverlayProxyFooter(ctx) {
  ctx.effect(() => ctx.locale.register(NAMESPACE2, { zh: zh2, en: en2 }), "dsh-overlay-custom: proxy locale");
  ctx.effect(() => ctx.slots.inject("settings.models.footer", () => ctx.slots.register({
    name: "settings.models.footer",
    id: "overlay-model-proxy",
    inject: () => ({ t: ctx.locale.bind(NAMESPACE2) })
  }, OverlayProxyFooter)), "dsh-overlay-custom: proxy footer");
}

// src/client/ProviderCardExtras.tsx
var import_jsx_runtime3 = require("react/jsx-runtime");
var NAMESPACE3 = "overlay.model";
var en3 = {
  test: "Test connection",
  testing: "Testing\u2026",
  testOk: "Connection OK",
  testFailed: "Connection failed",
  fetchList: "Fetch model list",
  filled: "Live models",
  models: "Models",
  login: "Sign in (OAuth)",
  loggingIn: "Waiting for browser\u2026",
  openLogin: "Open sign-in page",
  cancelLogin: "Cancel sign-in",
  loginOk: "Signed in",
  loginUnavailable: "Official login API unavailable after this upgrade",
  loginFailed: "Sign-in failed",
  submitCode: "Submit",
  statusOk: "Signed in",
  statusBad: "Not signed in",
  statusIdle: "Sign-in state unknown",
  refreshLatest: "Refresh latest models",
  refreshing: "Refreshing\u2026",
  candidates: "Models shown in picker",
  saveSelection: "Save shown models",
  savedHint: "Saved. Restart the app to apply.",
  restartHint: "Checked models appear in the model picker after a restart.",
  loginFirst: "Sign in first to fetch the newest models",
  proxyHint: "OpenAI blocked this region. Check Model network below and the proxy exit region, then sign in again.",
  refreshNeedLogin: "Sign in first, then refresh.",
  refreshEmpty: "Live list was empty. Check the model proxy and try again.",
  refreshApplied: "Wrote the live list. Restart to update the picker.",
  refreshReady: "Updated the list below. Save, then restart to update the picker."
};
var zh3 = {
  test: "\u6D4B\u8BD5\u8FDE\u63A5",
  testing: "\u6D4B\u8BD5\u4E2D\u2026",
  testOk: "\u8FDE\u63A5\u6B63\u5E38",
  testFailed: "\u8FDE\u63A5\u5931\u8D25",
  fetchList: "\u62C9\u53D6\u6A21\u578B\u5217\u8868",
  filled: "\u7EBF\u4E0A\u6A21\u578B",
  models: "\u6A21\u578B",
  login: "\u767B\u5F55\uFF08OAuth\uFF09",
  loggingIn: "\u7B49\u5F85\u6D4F\u89C8\u5668\u2026",
  openLogin: "\u6253\u5F00\u767B\u5F55\u9875",
  cancelLogin: "\u53D6\u6D88\u767B\u5F55",
  loginOk: "\u5DF2\u767B\u5F55",
  loginUnavailable: "\u672C\u6B21\u5B98\u7F51\u5347\u7EA7\u540E\u767B\u5F55\u63A5\u53E3\u4E0D\u53EF\u7528",
  loginFailed: "\u767B\u5F55\u5931\u8D25",
  submitCode: "\u63D0\u4EA4",
  statusOk: "\u5DF2\u767B\u5F55",
  statusBad: "\u672A\u767B\u5F55",
  statusIdle: "\u767B\u5F55\u72B6\u6001\u672A\u77E5",
  refreshLatest: "\u5237\u65B0\u6700\u65B0\u6A21\u578B",
  refreshing: "\u5237\u65B0\u4E2D\u2026",
  candidates: "\u663E\u793A\u5728\u6A21\u578B\u9009\u62E9\u5668\u4E2D\u7684\u6A21\u578B",
  saveSelection: "\u4FDD\u5B58\u663E\u793A\u7684\u6A21\u578B",
  savedHint: "\u5DF2\u4FDD\u5B58\uFF0C\u91CD\u542F\u5E94\u7528\u540E\u751F\u6548\u3002",
  restartHint: "\u52FE\u9009\u7684\u6A21\u578B\u5C06\u5728\u91CD\u542F\u540E\u51FA\u73B0\u5728\u6A21\u578B\u9009\u62E9\u5668\u3002",
  loginFirst: "\u8BF7\u5148\u767B\u5F55\uFF0C\u624D\u80FD\u62C9\u53D6\u6700\u65B0\u6A21\u578B",
  proxyHint: "OpenAI \u62D2\u7EDD\u4E86\u5F53\u524D\u5730\u533A\u3002\u8BF7\u68C0\u67E5\u4E0B\u65B9\u300C\u6A21\u578B\u7F51\u7EDC\u300D\u7684\u8FDE\u63A5\u6A21\u5F0F\u3001\u68C0\u6D4B\u7ED3\u679C\u548C\u4EE3\u7406\u51FA\u53E3\u5730\u533A\uFF0C\u518D\u767B\u5F55\u3002",
  refreshNeedLogin: "\u8BF7\u5148\u767B\u5F55\uFF0C\u518D\u5237\u65B0\u3002",
  refreshEmpty: "\u7EBF\u4E0A\u5217\u8868\u662F\u7A7A\u7684\u3002\u8BF7\u68C0\u67E5\u300C\u6A21\u578B\u7F51\u7EDC\u300D\u4E2D\u7684\u751F\u6548\u8DEF\u7531\u540E\u518D\u8BD5\u3002",
  refreshApplied: "\u5DF2\u5199\u5165\u6700\u65B0\u5217\u8868\u3002\u91CD\u542F\u540E\u9009\u62E9\u5668\u624D\u4F1A\u66F4\u65B0\u3002",
  refreshReady: "\u5DF2\u66F4\u65B0\u4E0B\u65B9\u5217\u8868\u3002\u52FE\u9009\u540E\u70B9\u4FDD\u5B58\uFF0C\u518D\u91CD\u542F\u9009\u62E9\u5668\u624D\u4F1A\u53D8\u3002"
};
var PROBE_PATH = "/api/desktop/models/probe";
var LIVE_PATH = "/api/desktop/models/live";
var SELECTION_PATH = "/api/desktop/models/selection";
var OAUTH_BEGIN = "/api/desktop/oauth/begin";
var OAUTH_STATUS = "/api/desktop/oauth/status";
var OAUTH_CANCEL = "/api/desktop/oauth/cancel";
var OAUTH_ANSWER = "/api/desktop/oauth/answer";
function statusDotClass(phase) {
  if (phase === "authorized") return "dshOverlayDotOk";
  if (phase === "failed" || phase === "cancelled") return "dshOverlayDotBad";
  if (phase === "running" || phase === "need-prompt") return "dshOverlayDotBusy";
  return "dshOverlayDotIdle";
}
async function postJson(path, body) {
  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    redirect: "error",
    headers: { accept: "application/json", "content-type": "application/json" },
    body: JSON.stringify(body)
  });
  return await response.json().catch(() => void 0);
}
function OverlayProviderCardExtras(props) {
  const { t } = props;
  const provider = props.provider.provider;
  const oauth = isOauthLoginProvider(provider);
  const [probe, setProbe] = (0, import_react4.useState)();
  const [busy, setBusy] = (0, import_react4.useState)();
  const [oauthView, setOauthView] = (0, import_react4.useState)();
  const [code, setCode] = (0, import_react4.useState)("");
  const [candidates, setCandidates] = (0, import_react4.useState)([]);
  const [checked, setChecked] = (0, import_react4.useState)(/* @__PURE__ */ new Set());
  const [saved, setSaved] = (0, import_react4.useState)(false);
  const [refreshNote, setRefreshNote] = (0, import_react4.useState)();
  const run = (0, import_react4.useCallback)(async (mode) => {
    if (busy !== void 0) return;
    setBusy(mode);
    setProbe(void 0);
    try {
      const response = await fetch(PROBE_PATH, {
        method: "POST",
        credentials: "same-origin",
        redirect: "error",
        headers: { accept: "application/json", "content-type": "application/json" },
        body: JSON.stringify({ settingsNs: props.provider.settingsNs, provider })
      });
      const view = await response.json().catch(() => void 0);
      if (view !== void 0 && typeof view.ok === "boolean") {
        setProbe(view);
        return;
      }
      throw new Error(`HTTP ${String(response.status)}`);
    } catch (cause) {
      setProbe({ ok: false, status: 0, models: [], error: cause instanceof Error ? cause.message : String(cause) });
    } finally {
      setBusy(void 0);
    }
  }, [busy, props.provider.settingsNs, provider]);
  const applyLive = (0, import_react4.useCallback)((view) => {
    const ids = view.applied === true && view.configured.length > 0 ? [...view.configured] : [.../* @__PURE__ */ new Set([...view.live, ...view.configured])].sort();
    setCandidates(ids);
    setChecked(new Set(ids));
  }, []);
  const refreshLatest = (0, import_react4.useCallback)(() => {
    if (busy !== void 0) return;
    setBusy("refresh");
    setSaved(false);
    setRefreshNote(void 0);
    void postJson(LIVE_PATH, { provider }).then((view) => {
      if (view === void 0) {
        setRefreshNote(t("refreshEmpty"));
        return;
      }
      if (!view.ok) {
        setRefreshNote(view.error === "no-credential" ? t("refreshNeedLogin") : view.error === "proxy-required" ? t("proxyHint") : `${t("refreshEmpty")}${view.error !== void 0 ? ` (${view.error})` : ""}`);
        if (view.configured.length > 0) applyLive(view);
        return;
      }
      applyLive(view);
      setRefreshNote(t("refreshReady"));
    }).finally(() => {
      setBusy(void 0);
    });
  }, [applyLive, busy, provider, t]);
  const toggle = (0, import_react4.useCallback)((id) => {
    setChecked((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    setSaved(false);
  }, []);
  const saveSelection = (0, import_react4.useCallback)(() => {
    if (busy !== void 0) return;
    setBusy("save");
    void postJson(SELECTION_PATH, { provider, models: [...checked] }).then((outcome) => {
      if (outcome?.ok === true) setSaved(true);
    }).finally(() => {
      setBusy(void 0);
    });
  }, [busy, checked, provider]);
  (0, import_react4.useEffect)(() => {
    if (!oauth) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const response = await fetch(`${OAUTH_STATUS}?provider=${encodeURIComponent(provider)}`, {
          credentials: "same-origin",
          redirect: "error",
          headers: { accept: "application/json" }
        });
        const payload = await response.json().catch(() => void 0);
        if (cancelled || payload === void 0) return;
        setOauthView(payload);
      } catch {
      }
    };
    void tick();
    void fetch(`${SELECTION_PATH}?provider=${encodeURIComponent(provider)}`, {
      credentials: "same-origin",
      redirect: "error",
      headers: { accept: "application/json" }
    }).then(async (response) => {
      const payload = await response.json().catch(() => void 0);
      if (cancelled || payload?.ok !== true || !Array.isArray(payload.models)) return;
      setCandidates((previous) => [.../* @__PURE__ */ new Set([...previous, ...payload.models])].sort());
      setChecked((previous) => previous.size > 0 ? previous : new Set(payload.models));
    }).catch(() => {
    });
    const id = window.setInterval(() => {
      void tick();
    }, 1500);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [oauth, provider]);
  (0, import_react4.useEffect)(() => {
    const url = oauthView?.url;
    if (typeof url !== "string" || url === "") return;
    window.open(url, "_blank", "noopener");
  }, [oauthView?.url]);
  const startLogin = (0, import_react4.useCallback)(() => {
    void postJson(OAUTH_BEGIN, { provider }).then((view) => {
      if (view !== void 0) setOauthView(view);
      else setOauthView({
        phase: "failed",
        message: null,
        url: null,
        code: null,
        prompt: null,
        error: "login-request-failed"
      });
    });
  }, [provider]);
  const cancelLogin = (0, import_react4.useCallback)(() => {
    void postJson(OAUTH_CANCEL, { provider }).then((view) => {
      if (view !== void 0) setOauthView(view);
    });
  }, [provider]);
  const submitCode = (0, import_react4.useCallback)(() => {
    void postJson(OAUTH_ANSWER, { provider, text: code }).then((view) => {
      if (view !== void 0) setOauthView(view);
      setCode("");
    });
  }, [code, provider]);
  const phase = oauthView?.phase ?? "idle";
  const statusText = phase === "authorized" ? t("statusOk") : phase === "failed" || phase === "cancelled" ? t("statusBad") : t("statusIdle");
  return /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "dshOverlayProviderExtras", children: [
    oauth ? /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { children: [
      /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "dshOverlayRow", children: [
        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
          "span",
          {
            role: "img",
            "aria-label": statusText,
            title: statusText,
            className: `dshOverlayDot ${statusDotClass(phase)}`
          }
        ),
        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
          "button",
          {
            type: "button",
            className: "dshOverlayBtnPrimary",
            onClick: startLogin,
            disabled: phase === "running" || phase === "need-prompt",
            children: phase === "running" || phase === "need-prompt" ? t("loggingIn") : phase === "authorized" ? t("loginOk") : t("login")
          }
        ),
        oauthView?.url !== null && oauthView?.url !== void 0 ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("a", { className: "dshOverlayLink", href: oauthView.url, target: "_blank", rel: "noreferrer", children: t("openLogin") }) : null,
        oauthView?.code !== null && oauthView?.code !== void 0 ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("code", { children: oauthView.code }) : null,
        phase === "running" || phase === "need-prompt" ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("button", { type: "button", className: "dshOverlayBtnSecondary", onClick: cancelLogin, children: t("cancelLogin") }) : null
      ] }),
      oauthView?.phase === "need-prompt" ? /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { children: [
        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("p", { className: "dshOverlayHint", children: oauthView.prompt?.message }),
        (oauthView.prompt?.options ?? []).length > 0 ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("div", { className: "dshOverlayRow", children: oauthView.prompt?.options?.map((option) => /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
          "button",
          {
            type: "button",
            className: "dshOverlayBtnSecondary",
            onClick: () => {
              void postJson(OAUTH_ANSWER, { provider, text: option.id }).then((view) => {
                if (view !== void 0) setOauthView(view);
              });
            },
            children: option.label
          },
          option.id
        )) }) : /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("label", { className: "dshOverlayCheck", children: [
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
            "input",
            {
              className: "dshOverlayInput",
              value: code,
              onChange: (event) => {
                setCode(event.target.value);
              },
              placeholder: oauthView.prompt?.placeholder ?? ""
            }
          ),
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("button", { type: "button", className: "dshOverlayBtnSecondary", onClick: submitCode, children: t("submitCode") })
        ] })
      ] }) : null,
      phase === "unavailable" ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("p", { className: "dshOverlayHint", role: "status", children: t("loginUnavailable") }) : null,
      phase === "failed" ? /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("p", { className: "dshOverlayAlert", role: "alert", children: [
        t("loginFailed"),
        ": ",
        oauthView?.error === "proxy-required" || (oauthView?.error ?? "").includes("unsupported_country") ? t("proxyHint") : oauthView?.error ?? ""
      ] }) : null,
      /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "dshOverlayRow", children: [
        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("button", { type: "button", className: "dshOverlayBtnSecondary", disabled: busy !== void 0, onClick: refreshLatest, children: busy === "refresh" ? t("refreshing") : t("refreshLatest") }),
        phase !== "authorized" && candidates.length === 0 ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "dshOverlayHint", children: t("loginFirst") }) : null,
        refreshNote !== void 0 ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("p", { className: saved ? "dshOverlaySaved" : "dshOverlayAlert", role: "status", children: refreshNote }) : null
      ] }),
      candidates.length > 0 ? /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("details", { className: "dshOverlayFieldset", children: [
        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("summary", { children: t("candidates") }),
        candidates.map((id) => /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("label", { className: "dshOverlayCheck", children: [
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("input", { type: "checkbox", checked: checked.has(id), onChange: () => {
            toggle(id);
          } }),
          id
        ] }, id)),
        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("button", { type: "button", className: "dshOverlayBtnPrimary", disabled: busy !== void 0, onClick: saveSelection, children: t("saveSelection") }),
        saved ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("p", { className: "dshOverlaySaved", role: "status", children: t("savedHint") }) : null,
        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("p", { className: "dshOverlayHint", children: t("restartHint") })
      ] }) : null
    ] }) : null,
    !oauth && provider === "deepseek" ? /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)(import_jsx_runtime3.Fragment, { children: [
      /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "dshOverlayRow", children: [
        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
          "button",
          {
            type: "button",
            className: "dshOverlayBtnSecondary",
            disabled: busy !== void 0,
            onClick: () => {
              void run("probe");
            },
            children: busy === "probe" ? t("testing") : probe?.ok === true ? t("testOk") : t("test")
          }
        ),
        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
          "button",
          {
            type: "button",
            className: "dshOverlayBtnSecondary",
            disabled: busy !== void 0,
            onClick: () => {
              void run("fetch");
            },
            children: t("fetchList")
          }
        )
      ] }),
      probe !== void 0 && !probe.ok ? /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("p", { className: "dshOverlayAlert", role: "alert", children: [
        t("testFailed"),
        ": ",
        probe.error ?? ""
      ] }) : null,
      probe?.ok === true && probe.models.length > 0 ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("ul", { "aria-label": t("models"), children: probe.models.map((id) => /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("li", { children: id }, id)) }) : null
    ] }) : null
  ] });
}
function applyOverlayModelExtras(ctx) {
  injectOverlayStyles();
  applyOverlayProxyFooter(ctx);
  ctx.effect(() => ctx.locale.register(NAMESPACE3, { zh: zh3, en: en3 }), "dsh-overlay-custom: model locale");
  const extras = () => ({ t: ctx.locale.bind(NAMESPACE3) });
  ctx.effect(() => ctx.slots.inject("settings.models.provider-card", () => ctx.slots.register({
    name: "settings.models.provider-card",
    key: "llm-deepseek",
    inject: extras
  }, OverlayProviderCardExtras)), "dsh-overlay-custom: deepseek extras");
  ctx.effect(() => ctx.slots.inject("settings.models.provider-card", () => ctx.slots.register({
    name: "settings.models.provider-card",
    key: "llm-pi-ai",
    inject: extras
  }, OverlayProviderCardExtras)), "dsh-overlay-custom: pi-ai extras");
}

// src/client/SettingsSections.tsx
var import_react6 = require("react");

// src/client/capability-locales.ts
var CAPABILITY_NS = "overlay.capabilities";
var capabilityZh = {
  skillsNav: "\u6280\u80FD",
  skillsTitle: "\u6280\u80FD",
  skillsIntro: "\u5F53\u524D\u5B98\u7F51\u684C\u9762\u7EC4\u5408\u5DF2\u7ECF\u53D1\u73B0\u5E76\u53EF\u4F9B\u4F1A\u8BDD\u8C03\u7528\u7684\u6280\u80FD\u3002\u8F93\u5165\u201C/\u201D\u4E5F\u53EF\u4EE5\u76F4\u63A5\u9009\u62E9\u6280\u80FD\u3002",
  skillsLoading: "\u6B63\u5728\u8BFB\u53D6\u6280\u80FD\u76EE\u5F55\u2026",
  skillsEmpty: "\u5C1A\u672A\u53D1\u73B0\u6280\u80FD\u3002\u53EF\u4EE5\u5728\u7528\u6237\u6216\u9879\u76EE Skills \u76EE\u5F55\u4E2D\u6DFB\u52A0\u6280\u80FD\u3002",
  skillsUnavailable: "\u5F53\u524D\u7EC4\u5408\u672A\u6302\u8F7D\u6280\u80FD\u76EE\u5F55\u3002",
  refresh: "\u5237\u65B0",
  userInvocable: "\u7528\u6237\u53EF\u8C03\u7528",
  modelInvocable: "\u6A21\u578B\u53EF\u8C03\u7528",
  whenToUse: "\u9002\u7528\u573A\u666F",
  privateSource: "\u672C\u673A\u79C1\u6709",
  projectSource: "\u9879\u76EE\u516C\u7528",
  bundledSource: "\u5185\u7F6E",
  mcpNav: "MCP",
  mcpTitle: "MCP \u670D\u52A1\u5668",
  mcpIntro: "\u6BCF\u4E2A MCP \u90FD\u662F\u4E00\u5F20\u72EC\u7ACB\u5361\u7247\uFF0C\u53EF\u4EE5\u6D4B\u8BD5\u8FDE\u63A5\u3001\u7F16\u8F91\u3001\u542F\u505C\u3001\u5220\u9664\u548C\u5BFC\u51FA\u914D\u7F6E\u3002",
  mcpLoading: "\u6B63\u5728\u8BFB\u53D6 MCP \u914D\u7F6E\u2026",
  mcpEmpty: "\u5C1A\u672A\u914D\u7F6E MCP\u3002\u53EF\u4EE5\u4ECE\u5E38\u7528\u6A21\u677F\u5F00\u59CB\uFF0C\u6216\u8005\u7C98\u8D34\u73B0\u6210 JSON\u3002",
  addServer: "\u81EA\u5B9A\u4E49 MCP",
  templates: "\u5E38\u7528 MCP",
  importJson: "\u5BFC\u5165 JSON",
  exportAll: "\u5BFC\u51FA\u5168\u90E8",
  importHint: '\u652F\u6301 Claude Desktop \u7684 {"mcpServers": {...}}\u3001\u5355\u4E2A\u670D\u52A1\u5668\u6216\u670D\u52A1\u5668\u6570\u7EC4\u3002',
  save: "\u4FDD\u5B58\u5168\u90E8",
  saving: "\u4FDD\u5B58\u4E2D\u2026",
  savedRestart: "\u5DF2\u4FDD\u5B58\uFF0C\u91CD\u542F\u5B98\u7F51\u7248 DSH \u540E\u751F\u6548\u3002",
  saveFailed: "\u4FDD\u5B58\u5931\u8D25\uFF0C\u8BF7\u68C0\u67E5\u914D\u7F6E\u3002",
  test: "\u6D4B\u8BD5\u8FDE\u63A5",
  testing: "\u6D4B\u8BD5\u4E2D\u2026",
  testOk: "\u8FDE\u63A5\u6210\u529F",
  testFailed: "\u8FDE\u63A5\u5931\u8D25",
  enabled: "\u5DF2\u542F\u7528",
  disabled: "\u5DF2\u505C\u7528",
  delete: "\u5220\u9664",
  edit: "\u7F16\u8F91",
  done: "\u5B8C\u6210",
  exportJson: "\u5BFC\u51FA JSON",
  serverName: "\u540D\u79F0",
  transport: "\u8FDE\u63A5\u65B9\u5F0F",
  stdio: "\u672C\u5730\u547D\u4EE4",
  http: "\u8FDC\u7A0B MCP",
  command: "\u542F\u52A8\u547D\u4EE4",
  arguments: "\u53C2\u6570\uFF08\u6BCF\u884C\u4E00\u4E2A\uFF09",
  environment: "\u73AF\u5883\u53D8\u91CF\uFF08KEY=value\uFF0C\u6BCF\u884C\u4E00\u4E2A\uFF09",
  workingDirectory: "\u5DE5\u4F5C\u76EE\u5F55\uFF08\u53EF\u9009\uFF09",
  url: "MCP \u5730\u5740",
  headers: "\u8BF7\u6C42\u5934\uFF08KEY=value\uFF0C\u6BCF\u884C\u4E00\u4E2A\uFF09",
  secretHint: "\u5DF2\u4FDD\u5B58\u7684\u654F\u611F\u503C\u4E0D\u4F1A\u56DE\u663E\uFF1B\u7559\u7A7A\u4F1A\u4FDD\u7559\u539F\u503C\u3002",
  invalidJson: "\u65E0\u6CD5\u8BC6\u522B\u8FD9\u6BB5 MCP JSON\u3002",
  duplicateName: "MCP \u540D\u79F0\u4E0D\u80FD\u91CD\u590D\u3002",
  customServer: "\u81EA\u5B9A\u4E49 MCP",
  toolCount: "\u4E2A\u5DE5\u5177",
  connectionUnknown: "\u5C1A\u672A\u6D4B\u8BD5",
  pendingRestart: "\u7B49\u5F85\u91CD\u542F\u751F\u6548",
  redactedExport: "\u4E3A\u4FDD\u62A4\u8D26\u53F7\uFF0C\u5BFC\u51FA\u7684 JSON \u4E0D\u5305\u542B\u5DF2\u4FDD\u5B58\u7684 Token\u3001\u5BC6\u7801\u548C\u8BF7\u6C42\u5934\u503C\u3002",
  configure: "\u914D\u7F6E",
  addNow: "\u6DFB\u52A0",
  cancel: "\u53D6\u6D88",
  requiredFields: "\u8BF7\u586B\u5199\u6240\u6709\u5FC5\u586B\u9879\u3002",
  databaseType: "\u6570\u636E\u5E93\u7C7B\u578B",
  status: "\u72B6\u6001",
  advancedSettings: "\u9AD8\u7EA7\u8BBE\u7F6E"
};
var capabilityEn = {
  skillsNav: "Skills",
  skillsTitle: "Skills",
  skillsIntro: "Skills discovered by the official Desktop composition and available to sessions. Type \u201C/\u201D to invoke one directly.",
  skillsLoading: "Loading skills\u2026",
  skillsEmpty: "No skills discovered. Add one to a user or project Skills directory.",
  skillsUnavailable: "The active composition has no skill registry.",
  refresh: "Refresh",
  userInvocable: "User invocable",
  modelInvocable: "Model invocable",
  whenToUse: "When to use",
  privateSource: "Private",
  projectSource: "Project",
  bundledSource: "Bundled",
  mcpNav: "MCP",
  mcpTitle: "MCP servers",
  mcpIntro: "Every MCP is an independent card with test, edit, enable, delete, and JSON export actions.",
  mcpLoading: "Loading MCP configuration\u2026",
  mcpEmpty: "No MCP configured. Start from a common template or paste existing JSON.",
  addServer: "Custom MCP",
  templates: "Common MCPs",
  importJson: "Import JSON",
  exportAll: "Export all",
  importHint: 'Accepts Claude Desktop {"mcpServers": {...}}, one server, or a server array.',
  save: "Save all",
  saving: "Saving\u2026",
  savedRestart: "Saved. Restart official DSH to apply.",
  saveFailed: "Save failed. Check the configuration.",
  test: "Test connection",
  testing: "Testing\u2026",
  testOk: "Connected",
  testFailed: "Connection failed",
  enabled: "Enabled",
  disabled: "Disabled",
  delete: "Delete",
  edit: "Edit",
  done: "Done",
  exportJson: "Export JSON",
  serverName: "Name",
  transport: "Transport",
  stdio: "Local command",
  http: "Remote MCP",
  command: "Command",
  arguments: "Arguments (one per line)",
  environment: "Environment (KEY=value, one per line)",
  workingDirectory: "Working directory (optional)",
  url: "MCP URL",
  headers: "Headers (KEY=value, one per line)",
  secretHint: "Stored secrets are not returned. Leave blank to keep them.",
  invalidJson: "This MCP JSON could not be imported.",
  duplicateName: "MCP names must be unique.",
  customServer: "Custom MCP",
  toolCount: "tools",
  connectionUnknown: "Not tested",
  pendingRestart: "Restart required",
  redactedExport: "For account safety, exported JSON omits stored tokens, passwords, and header values.",
  configure: "Configure",
  addNow: "Add",
  cancel: "Cancel",
  requiredFields: "Fill every required field.",
  databaseType: "Database type",
  status: "Status",
  advancedSettings: "Advanced settings"
};

// src/client/McpSettingsSection.tsx
var import_react5 = require("react");

// src/client/mcp-templates.ts
var YUNXIAO_MCP_URL = "https://openapi-rdc.aliyuncs.com/ai/mcp?toolsets=organization-management,project-management";
var DB_TYPES = [
  { value: "postgresql", label: { en: "PostgreSQL", zh: "PostgreSQL" } },
  { value: "mysql", label: { en: "MySQL", zh: "MySQL" } },
  { value: "oracle", label: { en: "Oracle", zh: "Oracle" } },
  { value: "sqlserver", label: { en: "SQL Server", zh: "SQL Server" } },
  { value: "sqlite", label: { en: "SQLite", zh: "SQLite" } }
];
var DB_PORTS = { postgresql: "5432", mysql: "3306", oracle: "1521", sqlserver: "1433" };
var trimmed = (values, key) => (values[key] ?? "").trim();
var isNetworkDatabase = (values) => trimmed(values, "dbtype") !== "sqlite";
var databasePort = (values) => trimmed(values, "port") || DB_PORTS[trimmed(values, "dbtype")] || "";
function databaseBuild(values) {
  const type = trimmed(values, "dbtype");
  if (type === "sqlite") {
    return { transport: "stdio", command: "npx", args: ["-y", "@executeautomation/database-server", trimmed(values, "file")], env: {} };
  }
  const host = trimmed(values, "host");
  const port = databasePort(values);
  const database = trimmed(values, "database");
  const env = { DSH_DB_USER: trimmed(values, "user"), DSH_DB_PASSWORD: trimmed(values, "password") };
  if (type === "oracle") {
    return {
      transport: "stdio",
      command: "uvx",
      env,
      args: ["mcp-server-oracle", "--user", "${DSH_DB_USER}", "--password", "${DSH_DB_PASSWORD}", "--dsn", `${host}:${port}/${database}`]
    };
  }
  const mode = type === "sqlserver" ? "--sqlserver" : type === "mysql" ? "--mysql" : "--postgresql";
  const hostFlag = type === "sqlserver" ? "--server" : "--host";
  return {
    transport: "stdio",
    command: "npx",
    env,
    args: ["-y", "@executeautomation/database-server", mode, hostFlag, host, "--port", port, "--database", database, "--user", "${DSH_DB_USER}", "--password", "${DSH_DB_PASSWORD}"]
  };
}
var MCP_TEMPLATES = [
  {
    id: "database",
    name: { en: "Database", zh: "\u6570\u636E\u5E93" },
    serverName: "database",
    icon: "database",
    description: { en: "Choose a database type, then fill only its connection information.", zh: "\u53EA\u9700\u9009\u62E9\u6570\u636E\u5E93\u7C7B\u578B\uFF0C\u518D\u586B\u5199\u8FDE\u63A5\u4FE1\u606F\u3002" },
    fields: [
      { key: "dbtype", label: { en: "Database type", zh: "\u6570\u636E\u5E93\u7C7B\u578B" }, options: DB_TYPES, initial: "postgresql", required: true },
      { key: "host", label: { en: "Host", zh: "\u5730\u5740" }, placeholder: { en: "192.168.1.10", zh: "192.168.1.10" }, required: true, visibleWhen: isNetworkDatabase },
      { key: "port", label: { en: "Port (optional)", zh: "\u7AEF\u53E3\uFF08\u53EF\u4E0D\u586B\uFF09" }, visibleWhen: isNetworkDatabase },
      { key: "user", label: { en: "Username", zh: "\u7528\u6237\u540D" }, required: true, visibleWhen: isNetworkDatabase },
      { key: "password", label: { en: "Password", zh: "\u5BC6\u7801" }, secret: true, required: true, visibleWhen: isNetworkDatabase },
      { key: "database", label: { en: "Database / service name", zh: "\u6570\u636E\u5E93\u540D / \u670D\u52A1\u540D" }, required: true, visibleWhen: isNetworkDatabase },
      { key: "file", label: { en: "SQLite file", zh: "SQLite \u6587\u4EF6\u8DEF\u5F84" }, placeholder: { en: "/Users/you/data.db", zh: "/Users/\u4F60/data.db" }, required: true, visibleWhen: (values) => !isNetworkDatabase(values) }
    ],
    build: databaseBuild
  },
  {
    id: "yunxiao",
    name: { en: "Yunxiao", zh: "\u4E91\u6548" },
    serverName: "yunxiao",
    icon: "cloud",
    description: { en: "Query, comment, and update Aliyun Yunxiao work items.", zh: "\u67E5\u8BE2\u3001\u8BC4\u8BBA\u548C\u66F4\u65B0\u963F\u91CC\u4E91\u4E91\u6548\u5DE5\u4F5C\u9879\u3002" },
    fields: [{ key: "token", label: { en: "Yunxiao access token", zh: "\u4E91\u6548\u4E2A\u4EBA\u8BBF\u95EE\u4EE4\u724C" }, secret: true, required: true }],
    build: (values) => ({ transport: "streamable-http", url: YUNXIAO_MCP_URL, headers: { Authorization: `Bearer ${trimmed(values, "token")}` } })
  },
  {
    id: "gitlab",
    name: { en: "GitLab", zh: "GitLab" },
    serverName: "gitlab",
    icon: "git",
    description: { en: "Browse projects, issues, merge requests, and code.", zh: "\u8BBF\u95EE\u9879\u76EE\u3001Issue\u3001\u5408\u5E76\u8BF7\u6C42\u548C\u4EE3\u7801\u3002" },
    fields: [
      { key: "host", label: { en: "GitLab URL", zh: "GitLab \u5730\u5740" }, initial: "https://gitlab.com", required: true },
      { key: "token", label: { en: "Personal access token", zh: "\u4E2A\u4EBA\u8BBF\u95EE\u4EE4\u724C" }, secret: true, required: true }
    ],
    build: (values) => ({ transport: "stdio", command: "npx", args: ["-y", "@zereight/mcp-gitlab"], env: { GITLAB_PERSONAL_ACCESS_TOKEN: trimmed(values, "token"), GITLAB_API_URL: `${trimmed(values, "host").replace(/\/+$/u, "")}/api/v4` } })
  },
  {
    id: "playwright",
    name: { en: "Browser", zh: "\u6D4F\u89C8\u5668" },
    serverName: "browser",
    icon: "globe",
    description: { en: "Open pages, click, capture screenshots, and inspect web apps.", zh: "\u6253\u5F00\u7F51\u9875\u3001\u70B9\u51FB\u3001\u622A\u56FE\u548C\u68C0\u67E5 Web \u5E94\u7528\u3002" },
    fields: [],
    build: () => ({ transport: "stdio", command: "npx", args: ["-y", "@playwright/mcp@latest"], env: {} })
  },
  {
    id: "filesystem",
    name: { en: "Files", zh: "\u6587\u4EF6" },
    serverName: "files",
    icon: "folder",
    description: { en: "Allow access to one selected folder.", zh: "\u5141\u8BB8\u6A21\u578B\u8BBF\u95EE\u4E00\u4E2A\u6307\u5B9A\u76EE\u5F55\u3002" },
    fields: [{ key: "dir", label: { en: "Allowed folder", zh: "\u5141\u8BB8\u8BBF\u95EE\u7684\u76EE\u5F55" }, placeholder: { en: "/Users/you/Documents", zh: "/Users/\u4F60/Documents" }, required: true }],
    build: (values) => ({ transport: "stdio", command: "npx", args: ["-y", "@modelcontextprotocol/server-filesystem", trimmed(values, "dir")], env: {} })
  },
  {
    id: "memory",
    name: { en: "Memory", zh: "\u8BB0\u5FC6" },
    serverName: "memory",
    icon: "brain",
    description: { en: "Local knowledge graph shared across sessions.", zh: "\u53EF\u8DE8\u4F1A\u8BDD\u4F7F\u7528\u7684\u672C\u5730\u77E5\u8BC6\u56FE\u8C31\u3002" },
    fields: [],
    build: () => ({ transport: "stdio", command: "npx", args: ["-y", "@modelcontextprotocol/server-memory"], env: {} })
  },
  {
    id: "http-generic",
    name: { en: "Remote MCP", zh: "\u8FDC\u7A0B MCP" },
    serverName: "remote-mcp",
    icon: "globe",
    description: { en: "Connect any Streamable HTTP MCP endpoint.", zh: "\u8FDE\u63A5\u4EFB\u610F Streamable HTTP MCP \u5730\u5740\u3002" },
    fields: [
      { key: "url", label: { en: "MCP URL", zh: "MCP \u5730\u5740" }, placeholder: { en: "https://host/mcp", zh: "https://host/mcp" }, required: true },
      { key: "headerName", label: { en: "Header name", zh: "\u8BF7\u6C42\u5934\u540D\u79F0" }, initial: "Authorization" },
      { key: "headerValue", label: { en: "Header value", zh: "\u8BF7\u6C42\u5934\u503C" }, secret: true }
    ],
    build: (values) => {
      const name = trimmed(values, "headerName");
      const value = trimmed(values, "headerValue");
      return { transport: "streamable-http", url: trimmed(values, "url"), headers: name && value ? { [name]: value } : {} };
    }
  }
];
function templateInitialValues(template) {
  return Object.fromEntries(template.fields.map((field) => [field.key, field.initial ?? ""]));
}
function templateRequirementsMet(template, values) {
  return template.fields.every((field) => field.visibleWhen?.(values) === false || field.required !== true || trimmed(values, field.key).length > 0);
}
function templateText(text) {
  return typeof navigator !== "undefined" && !navigator.language.toLowerCase().startsWith("zh") ? text.en : text.zh;
}

// src/client/McpSettingsSection.tsx
var import_jsx_runtime4 = require("react/jsx-runtime");
async function jsonRequest(path, init) {
  const response = await fetch(path, {
    credentials: "same-origin",
    redirect: "error",
    ...init,
    headers: { accept: "application/json", ...init?.body === void 0 ? {} : { "content-type": "application/json" }, ...init?.headers }
  });
  const value = await response.json().catch(() => void 0);
  if (!response.ok || value === void 0) throw new Error(`HTTP ${String(response.status)}`);
  return value;
}
function idFrom(name) {
  const normalized = name.trim().replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48);
  return `${normalized || "mcp"}-${Date.now().toString(36)}`;
}
function draftFromView(row) {
  return { id: row.id, serverName: row.serverName, transport: row.transport, command: row.command ?? "", argsText: row.args.join("\n"), envText: "", envKeys: row.envKeys, cwd: row.cwd ?? "", url: row.url ?? "", headersText: "", headerKeys: row.headerKeys, disabled: row.disabled };
}
function emptyDraft() {
  const name = `mcp-${Date.now().toString(36).slice(-4)}`;
  return { id: idFrom(name), serverName: name, transport: "stdio", command: "", argsText: "", envText: "", envKeys: [], cwd: "", url: "", headersText: "", headerKeys: [], disabled: false, dirty: true };
}
function parsePairs(text) {
  const entries = text.split("\n").map((line) => line.trim()).filter(Boolean).map((line) => {
    const split = line.indexOf("=");
    if (split <= 0) throw new Error("invalid pair");
    return [line.slice(0, split).trim(), line.slice(split + 1)];
  });
  return entries.length === 0 ? void 0 : Object.fromEntries(entries);
}
function rowPayload(row) {
  return {
    id: row.id,
    serverName: row.serverName.trim(),
    transport: row.transport,
    disabled: row.disabled,
    ...row.transport === "stdio" ? {
      command: row.command.trim(),
      args: row.argsText.split("\n").map((value) => value.trim()).filter(Boolean),
      ...row.cwd.trim() ? { cwd: row.cwd.trim() } : {},
      ...row.envText.trim() ? { env: parsePairs(row.envText) } : {}
    } : { url: row.url.trim(), ...row.headersText.trim() ? { headers: parsePairs(row.headersText) } : {} }
  };
}
function importedDrafts(value) {
  const source = value && typeof value === "object" && !Array.isArray(value) && "mcpServers" in value ? value.mcpServers : value;
  const entries = Array.isArray(source) ? source.map((row, index) => [typeof row?.serverName === "string" ? row.serverName : typeof row?.name === "string" ? row.name : `mcp-${String(index + 1)}`, row]).filter((entry) => entry[1] !== null && typeof entry[1] === "object") : source !== null && typeof source === "object" ? Object.entries(source).filter((entry) => entry[1] !== null && typeof entry[1] === "object") : [];
  return entries.map(([name, raw]) => {
    const url = typeof raw.url === "string" ? raw.url : "";
    const env = raw.env && typeof raw.env === "object" ? raw.env : {};
    const headers = raw.headers && typeof raw.headers === "object" ? raw.headers : {};
    const pairs = (record) => Object.entries(record).filter((entry) => typeof entry[1] === "string" && entry[1] !== "__REDACTED__").map(([key, item]) => `${key}=${item}`).join("\n");
    const redactedKeys = (record) => Object.entries(record).filter(([, item]) => item === "__REDACTED__").map(([key]) => key);
    return {
      id: idFrom(name),
      serverName: name.replace(/[^A-Za-z0-9_-]+/g, "-").slice(0, 32),
      transport: url ? "streamable-http" : "stdio",
      command: typeof raw.command === "string" ? raw.command : "",
      argsText: Array.isArray(raw.args) ? raw.args.filter((item) => typeof item === "string").join("\n") : "",
      envText: pairs(env),
      envKeys: redactedKeys(env),
      cwd: typeof raw.cwd === "string" ? raw.cwd : "",
      url,
      headersText: pairs(headers),
      headerKeys: redactedKeys(headers),
      disabled: raw.disabled === true,
      dirty: true
    };
  });
}
function uniqueName(base, rows) {
  const names = new Set(rows.map((row) => row.serverName));
  if (!names.has(base)) return base;
  for (let index = 2; index < 100; index += 1) if (!names.has(`${base}-${String(index)}`)) return `${base}-${String(index)}`;
  return `${base}-${Date.now().toString(36).slice(-4)}`;
}
function fromTemplate(template, values, rows) {
  const name = uniqueName(template.serverName, rows);
  const built = template.build(values);
  return built.transport === "stdio" ? { id: idFrom(name), serverName: name, transport: "stdio", command: built.command, argsText: built.args.join("\n"), envText: Object.entries(built.env).map(([key, value]) => `${key}=${value}`).join("\n"), envKeys: [], cwd: "", url: "", headersText: "", headerKeys: [], disabled: false, dirty: true } : { id: idFrom(name), serverName: name, transport: "streamable-http", command: "", argsText: "", envText: "", envKeys: [], cwd: "", url: built.url, headersText: Object.entries(built.headers).map(([key, value]) => `${key}=${value}`).join("\n"), headerKeys: [], disabled: false, dirty: true };
}
function safeMapKeys(text, stored) {
  try {
    return [.../* @__PURE__ */ new Set([...stored, ...Object.keys(parsePairs(text) ?? {})])].sort();
  } catch {
    return [...stored];
  }
}
function exportConfig(row) {
  return row.transport === "stdio" ? { command: row.command, args: row.argsText.split("\n").filter(Boolean), ...row.cwd ? { cwd: row.cwd } : {}, env: Object.fromEntries(safeMapKeys(row.envText, row.envKeys).map((key) => [key, "__REDACTED__"])), ...row.disabled ? { disabled: true } : {} } : { url: row.url, headers: Object.fromEntries(safeMapKeys(row.headersText, row.headerKeys).map((key) => [key, "__REDACTED__"])), ...row.disabled ? { disabled: true } : {} };
}
function downloadJson(name, rows) {
  const body = JSON.stringify({ mcpServers: Object.fromEntries(rows.map((row) => [row.serverName, exportConfig(row)])) }, null, 2);
  const url = URL.createObjectURL(new Blob([`${body}
`], { type: "application/json" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  URL.revokeObjectURL(url);
}
function cardSummary(row) {
  if (row.transport === "streamable-http") return row.url || "\u2014";
  const first = row.argsText.split("\n").filter(Boolean).slice(0, 2).join(" ");
  return `${row.command}${first ? ` ${first}` : ""}` || "\u2014";
}
function DraftEditor({ row, t, update }) {
  return /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("div", { className: "dshOverlayFormGrid dshOverlayMcpEditor", children: [
    /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("label", { children: [
      /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("span", { children: t("serverName") }),
      /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("input", { className: "dshOverlayInput", value: row.serverName, onChange: (event) => {
        update({ serverName: event.target.value, dirty: true });
      } })
    ] }),
    /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("label", { children: [
      /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("span", { children: t("transport") }),
      /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("select", { className: "dshOverlayInput", value: row.transport, onChange: (event) => {
        update({ transport: event.target.value, dirty: true });
      }, children: [
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("option", { value: "stdio", children: t("stdio") }),
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("option", { value: "streamable-http", children: t("http") })
      ] })
    ] }),
    row.transport === "stdio" ? /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)(import_jsx_runtime4.Fragment, { children: [
      /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("label", { children: [
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("span", { children: t("command") }),
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("input", { className: "dshOverlayInput", value: row.command, onChange: (event) => {
          update({ command: event.target.value, dirty: true });
        }, placeholder: "npx" })
      ] }),
      /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("label", { children: [
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("span", { children: t("workingDirectory") }),
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("input", { className: "dshOverlayInput", value: row.cwd, onChange: (event) => {
          update({ cwd: event.target.value, dirty: true });
        } })
      ] }),
      /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("label", { className: "dshOverlayFormWide", children: [
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("span", { children: t("arguments") }),
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("textarea", { className: "dshOverlayTextarea", value: row.argsText, onChange: (event) => {
          update({ argsText: event.target.value, dirty: true });
        } })
      ] }),
      /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("label", { className: "dshOverlayFormWide", children: [
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("span", { children: t("environment") }),
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("textarea", { className: "dshOverlayTextarea", value: row.envText, onChange: (event) => {
          update({ envText: event.target.value, dirty: true });
        }, placeholder: row.envKeys.join("\n") }),
        row.envKeys.length ? /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("small", { children: [
          t("secretHint"),
          " ",
          row.envKeys.join(", ")
        ] }) : null
      ] })
    ] }) : /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)(import_jsx_runtime4.Fragment, { children: [
      /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("label", { className: "dshOverlayFormWide", children: [
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("span", { children: t("url") }),
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("input", { className: "dshOverlayInput", value: row.url, onChange: (event) => {
          update({ url: event.target.value, dirty: true });
        }, placeholder: "https://host.example/mcp" })
      ] }),
      /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("label", { className: "dshOverlayFormWide", children: [
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("span", { children: t("headers") }),
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("textarea", { className: "dshOverlayTextarea", value: row.headersText, onChange: (event) => {
          update({ headersText: event.target.value, dirty: true });
        }, placeholder: row.headerKeys.join("\n") }),
        row.headerKeys.length ? /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("small", { children: [
          t("secretHint"),
          " ",
          row.headerKeys.join(", ")
        ] }) : null
      ] })
    ] })
  ] });
}
function McpCard({ row, t, probe, expanded, update, remove, test, toggleExpanded }) {
  const stateLabel = probe?.kind === "testing" ? t("testing") : probe?.kind === "ok" ? t("testOk") : probe?.kind === "failed" ? t("testFailed") : row.dirty ? t("pendingRestart") : t("connectionUnknown");
  return /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("article", { className: "dshOverlayMcpCard", "data-state": probe?.kind ?? (row.dirty ? "pending" : "unknown"), children: [
    /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("div", { className: "dshOverlayMcpHead", children: [
      /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("span", { className: "dshOverlayMcpIdentity", children: [
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("i", { className: "dshOverlayStatusDot" }),
        /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("span", { children: [
          /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("strong", { children: row.serverName || t("customServer") }),
          /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("small", { children: [
            row.transport === "stdio" ? t("stdio") : t("http"),
            " \xB7 ",
            stateLabel
          ] })
        ] })
      ] }),
      /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("label", { className: "dshOverlayToggle", children: [
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("input", { type: "checkbox", checked: !row.disabled, onChange: (event) => {
          update({ disabled: !event.target.checked, dirty: true });
        } }),
        row.disabled ? t("disabled") : t("enabled")
      ] })
    ] }),
    /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("p", { className: "dshOverlayMcpSummary", children: cardSummary(row) }),
    /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("div", { className: "dshOverlayMcpChips", children: [
      safeMapKeys(row.envText, row.envKeys).map((key) => /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("span", { children: [
        "ENV \xB7 ",
        key
      ] }, `e-${key}`)),
      safeMapKeys(row.headersText, row.headerKeys).map((key) => /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("span", { children: [
        "HEADER \xB7 ",
        key
      ] }, `h-${key}`))
    ] }),
    expanded ? /* @__PURE__ */ (0, import_jsx_runtime4.jsx)(DraftEditor, { row, t, update }) : null,
    /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("div", { className: "dshOverlayMcpActions", children: [
      /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("button", { className: "dshOverlayBtnSecondary", type: "button", disabled: probe?.kind === "testing", onClick: test, children: probe?.kind === "testing" ? t("testing") : t("test") }),
      /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("button", { className: "dshOverlayBtnSecondary", type: "button", onClick: toggleExpanded, children: expanded ? t("done") : t("edit") }),
      /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("button", { className: "dshOverlayBtnSecondary", type: "button", onClick: () => {
        downloadJson(`${row.serverName || "mcp"}.json`, [row]);
      }, children: t("exportJson") }),
      /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("button", { className: "dshOverlayBtnSecondary dshOverlayDanger", type: "button", onClick: remove, children: t("delete") }),
      probe?.result ? /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("span", { className: probe.kind === "ok" ? "dshOverlaySaved" : "dshOverlayAlert", children: probe.kind === "ok" && probe.result.toolCount !== void 0 ? `${String(probe.result.toolCount)} ${t("toolCount")}` : probe.result.detail ?? "" }) : null
    ] })
  ] });
}
function TemplateConfigurator({ template, rows, t, close, add }) {
  const [values, setValues] = (0, import_react5.useState)(() => templateInitialValues(template));
  const valid = templateRequirementsMet(template, values);
  return /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("div", { className: "dshOverlayTemplateConfig", children: [
    /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("div", { className: "dshOverlaySettingsHead", children: [
      /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("span", { children: [
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("h3", { children: templateText(template.name) }),
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("p", { className: "dshOverlayHint", children: templateText(template.description) })
      ] }),
      /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("button", { className: "dshOverlayBtnSecondary", type: "button", onClick: close, children: t("cancel") })
    ] }),
    /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("div", { className: "dshOverlayFormGrid", children: template.fields.filter((field) => field.visibleWhen?.(values) !== false).map((field) => /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("label", { children: [
      /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("span", { children: [
        templateText(field.label),
        field.required ? " *" : ""
      ] }),
      field.options ? /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("select", { className: "dshOverlayInput", value: values[field.key] ?? "", onChange: (event) => {
        setValues((current) => ({ ...current, [field.key]: event.target.value }));
      }, children: field.options.map((option) => /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("option", { value: option.value, children: templateText(option.label) }, option.value)) }) : /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("input", { className: "dshOverlayInput", type: field.secret ? "password" : "text", value: values[field.key] ?? "", placeholder: field.placeholder ? templateText(field.placeholder) : "", onChange: (event) => {
        setValues((current) => ({ ...current, [field.key]: event.target.value }));
      } })
    ] }, field.key)) }),
    !valid ? /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("p", { className: "dshOverlayAlert", children: t("requiredFields") }) : null,
    /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("button", { className: "dshOverlayBtnPrimary", type: "button", disabled: !valid, onClick: () => {
      add(fromTemplate(template, values, rows));
      close();
    }, children: t("addNow") })
  ] });
}
function OverlayMcpSection({ t }) {
  const [rows, setRows] = (0, import_react5.useState)();
  const [expanded, setExpanded] = (0, import_react5.useState)(/* @__PURE__ */ new Set());
  const [probes, setProbes] = (0, import_react5.useState)({});
  const [busy, setBusy] = (0, import_react5.useState)(false);
  const [note, setNote] = (0, import_react5.useState)();
  const [importText, setImportText] = (0, import_react5.useState)("");
  const [template, setTemplate] = (0, import_react5.useState)();
  const load = (0, import_react5.useCallback)(() => {
    void jsonRequest("/api/desktop/mcp").then((view) => {
      setRows(view.servers.map(draftFromView));
    }).catch(() => {
      setRows([]);
      setNote("failed");
    });
  }, []);
  (0, import_react5.useEffect)(load, [load]);
  const duplicate = (0, import_react5.useMemo)(() => rows !== void 0 && new Set(rows.map((row) => row.serverName.trim())).size !== rows.length, [rows]);
  const update = (index, patch) => {
    setRows((current) => current?.map((row, rowIndex) => rowIndex === index ? { ...row, ...patch } : row));
    setNote(void 0);
  };
  const save = () => {
    if (rows === void 0 || duplicate) {
      setNote("duplicate");
      return;
    }
    setBusy(true);
    setNote(void 0);
    try {
      void jsonRequest("/api/desktop/mcp", { method: "POST", body: JSON.stringify({ servers: rows.map(rowPayload) }) }).then(() => {
        setNote("saved");
        load();
      }).catch(() => {
        setNote("failed");
      }).finally(() => {
        setBusy(false);
      });
    } catch {
      setNote("failed");
      setBusy(false);
    }
  };
  const test = (row) => {
    setProbes((current) => ({ ...current, [row.id]: { kind: "testing" } }));
    let payload;
    try {
      payload = rowPayload(row);
    } catch {
      setProbes((current) => ({ ...current, [row.id]: { kind: "failed", result: { ok: false, detail: t("saveFailed") } } }));
      return;
    }
    void jsonRequest("/api/desktop/mcp/test", { method: "POST", body: JSON.stringify({ id: row.id, row: payload }) }).then((result) => {
      setProbes((current) => ({ ...current, [row.id]: { kind: result.ok ? "ok" : "failed", result } }));
    }).catch(() => {
      setProbes((current) => ({ ...current, [row.id]: { kind: "failed", result: { ok: false } } }));
    });
  };
  const importRows = () => {
    try {
      const parsed = importedDrafts(JSON.parse(importText));
      if (!parsed.length) throw new Error("empty");
      setRows((current) => [...current ?? [], ...parsed]);
      setImportText("");
      setNote(void 0);
      setExpanded((current) => /* @__PURE__ */ new Set([...current, ...parsed.map((row) => row.id)]));
    } catch {
      setNote("invalid");
    }
  };
  return /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("div", { className: "dshOverlaySettings", children: [
    /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("header", { className: "dshOverlaySettingsHero", children: [
      /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("span", { className: "dshOverlaySettingsHeroIcon", children: "\u2318" }),
      /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("span", { children: [
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("h2", { children: t("mcpTitle") }),
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("p", { children: t("mcpIntro") })
      ] })
    ] }),
    /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("section", { className: "dshOverlaySettingsCard", children: [
      /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("div", { className: "dshOverlaySettingsHead", children: [
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("h3", { children: t("templates") }),
        /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("div", { className: "dshOverlayRow", children: [
          /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("button", { className: "dshOverlayBtnSecondary", type: "button", disabled: !rows?.length, onClick: () => {
            if (rows?.length) downloadJson("dsh-mcp-servers.json", rows);
          }, children: t("exportAll") }),
          /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("button", { className: "dshOverlayBtnPrimary", type: "button", onClick: () => {
            const row = emptyDraft();
            setRows((current) => [...current ?? [], row]);
            setExpanded((current) => new Set(current).add(row.id));
          }, children: [
            "\uFF0B ",
            t("addServer")
          ] })
        ] })
      ] }),
      /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("div", { className: "dshOverlayTemplateGrid", children: MCP_TEMPLATES.map((item) => /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("button", { className: "dshOverlayTemplateCard", type: "button", onClick: () => {
        if (item.fields.length === 0) setRows((current) => [...current ?? [], fromTemplate(item, {}, current ?? [])]);
        else setTemplate(item);
      }, children: [
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("span", { className: "dshOverlayTemplateIcon", children: { database: "\u25A6", git: "\u2442", cloud: "\u2601", globe: "\u25CE", folder: "\u25A1", brain: "\u2726" }[item.icon] }),
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("strong", { children: templateText(item.name) }),
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("small", { children: templateText(item.description) }),
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("em", { children: item.fields.length ? t("configure") : t("addNow") })
      ] }, item.id)) }),
      template && rows ? /* @__PURE__ */ (0, import_jsx_runtime4.jsx)(TemplateConfigurator, { template, rows, t, close: () => {
        setTemplate(void 0);
      }, add: (row) => {
        setRows((current) => [...current ?? [], row]);
        setExpanded((current) => new Set(current).add(row.id));
      } }, template.id) : null
    ] }),
    /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("section", { className: "dshOverlaySettingsCard", children: [
      /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("div", { className: "dshOverlaySettingsHead", children: /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("h3", { children: [
        t("mcpTitle"),
        rows ? ` \xB7 ${String(rows.length)}` : ""
      ] }) }),
      rows === void 0 ? /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("p", { className: "dshOverlayHint", children: t("mcpLoading") }) : null,
      rows?.length === 0 ? /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("div", { className: "dshOverlaySettingsEmpty", children: t("mcpEmpty") }) : null,
      /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("div", { className: "dshOverlayMcpList", children: rows?.map((row, index) => /* @__PURE__ */ (0, import_jsx_runtime4.jsx)(McpCard, { row, t, probe: probes[row.id], expanded: expanded.has(row.id), update: (patch) => {
        update(index, patch);
      }, test: () => {
        test(row);
      }, remove: () => {
        setRows((current) => current?.filter((_, rowIndex) => rowIndex !== index));
        setNote(void 0);
      }, toggleExpanded: () => {
        setExpanded((current) => {
          const next = new Set(current);
          if (next.has(row.id)) next.delete(row.id);
          else next.add(row.id);
          return next;
        });
      } }, row.id)) }),
      /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("details", { className: "dshOverlayImport", children: [
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("summary", { children: t("importJson") }),
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("p", { className: "dshOverlayHint", children: t("importHint") }),
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("textarea", { className: "dshOverlayTextarea dshOverlayImportText", value: importText, onChange: (event) => {
          setImportText(event.target.value);
        }, placeholder: '{"mcpServers":{"example":{"command":"npx","args":["-y","server-package"]}}}' }),
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("button", { className: "dshOverlayBtnSecondary", type: "button", onClick: importRows, children: t("importJson") })
      ] }),
      /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("p", { className: "dshOverlayHint", children: t("redactedExport") }),
      /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("div", { className: "dshOverlaySettingsFooter", children: [
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("button", { className: "dshOverlayBtnPrimary", type: "button", disabled: busy || rows === void 0, onClick: save, children: busy ? t("saving") : t("save") }),
        note === "saved" ? /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("span", { className: "dshOverlaySaved", children: t("savedRestart") }) : null,
        note === "failed" ? /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("span", { className: "dshOverlayAlert", children: t("saveFailed") }) : null,
        note === "invalid" ? /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("span", { className: "dshOverlayAlert", children: t("invalidJson") }) : null,
        note === "duplicate" || duplicate ? /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("span", { className: "dshOverlayAlert", children: t("duplicateName") }) : null
      ] })
    ] })
  ] });
}

// src/client/SettingsSections.tsx
var import_jsx_runtime5 = require("react/jsx-runtime");
async function jsonRequest2(path) {
  const response = await fetch(path, { credentials: "same-origin", redirect: "error", headers: { accept: "application/json" } });
  const value = await response.json().catch(() => void 0);
  if (!response.ok || value === void 0) throw new Error(`HTTP ${String(response.status)}`);
  return value;
}
function sourceLabel(source, t) {
  if (source === "bundled" || source === "runtime") return t("bundledSource");
  if (source.startsWith("project-")) return t("projectSource");
  return t("privateSource");
}
function OverlaySkillsSection({ t }) {
  const [view, setView] = (0, import_react6.useState)();
  const [failed, setFailed] = (0, import_react6.useState)(false);
  const load = (0, import_react6.useCallback)(() => {
    setFailed(false);
    void jsonRequest2("/api/desktop/skills").then(setView).catch(() => {
      setFailed(true);
    });
  }, []);
  (0, import_react6.useEffect)(load, [load]);
  return /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)("div", { className: "dshOverlaySettings", children: [
    /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)("header", { className: "dshOverlaySettingsHero", children: [
      /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("span", { className: "dshOverlaySettingsHeroIcon", children: "\u2726" }),
      /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)("span", { children: [
        /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("h2", { children: t("skillsTitle") }),
        /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("p", { children: t("skillsIntro") })
      ] })
    ] }),
    /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)("section", { className: "dshOverlaySettingsCard", children: [
      /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)("div", { className: "dshOverlaySettingsHead", children: [
        /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)("h3", { children: [
          t("skillsTitle"),
          view?.available ? ` \xB7 ${String(view.skills.length)}` : ""
        ] }),
        /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("button", { className: "dshOverlayBtnSecondary", type: "button", onClick: load, children: t("refresh") })
      ] }),
      view === void 0 && !failed ? /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("p", { className: "dshOverlayHint", children: t("skillsLoading") }) : null,
      failed || view?.available === false ? /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("p", { className: "dshOverlayAlert", children: t("skillsUnavailable") }) : null,
      view?.available === true && view.skills.length === 0 ? /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("div", { className: "dshOverlaySettingsEmpty", children: t("skillsEmpty") }) : null,
      /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("div", { className: "dshOverlaySettingsList", children: view?.skills.map((skill) => /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)("article", { className: "dshOverlaySkillRow", children: [
        /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("span", { className: "dshOverlaySkillIcon", children: "\u2726" }),
        /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)("span", { className: "dshOverlaySkillCopy", children: [
          /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("strong", { children: skill.name }),
          /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("span", { children: skill.description }),
          skill.whenToUse ? /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)("span", { children: [
            /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)("b", { children: [
              t("whenToUse"),
              "\uFF1A"
            ] }),
            skill.whenToUse
          ] }) : null
        ] }),
        /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)("span", { className: "dshOverlayBadges", children: [
          skill.userInvocable ? /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("em", { children: t("userInvocable") }) : null,
          skill.modelInvocable ? /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("em", { children: t("modelInvocable") }) : null,
          /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("em", { children: sourceLabel(skill.source, t) })
        ] })
      ] }, skill.name)) })
    ] })
  ] });
}
function applyOverlayCapabilitySettings(ctx) {
  injectOverlayStyles();
  const t = ctx.locale.bind(CAPABILITY_NS);
  ctx.effect(() => ctx.locale.register(CAPABILITY_NS, { zh: capabilityZh, en: capabilityEn }), "dsh-overlay-custom: capability settings locale");
  ctx.effect(() => ctx.slots.inject("settings.section", () => ctx.slots.register({ name: "settings.section", id: "overlay-skills", order: 90, label: () => t("skillsNav"), locale: CAPABILITY_NS, inject: () => ({}) }, OverlaySkillsSection)), "dsh-overlay-custom: skills settings");
  ctx.effect(() => ctx.slots.inject("settings.section", () => ctx.slots.register({ name: "settings.section", id: "overlay-mcp", order: 95, label: () => t("mcpNav"), locale: CAPABILITY_NS, inject: () => ({}) }, OverlayMcpSection)), "dsh-overlay-custom: mcp settings");
}

// src/client/UpdateEntry.tsx
var import_react7 = require("react");
var import_jsx_runtime6 = require("react/jsx-runtime");
var NAMESPACE4 = "overlay.updates";
var en4 = {
  label: "Updates",
  title: "Version and updates",
  current: "Current",
  officialLatest: "Official latest",
  buildLatest: "This build latest",
  upToDate: "Everything is up to date.",
  officialUpdate: "The official app has a newer release. Rebuild or download it from the official releases page.",
  buildUpdate: "A newer portable build is available for this repository.",
  notes: "What changed",
  openOfficial: "Open official releases",
  openBuild: "Open this repository releases",
  refresh: "Check now",
  refreshing: "Checking\u2026",
  updateNow: "Update now",
  recheck: "Check again",
  manualUpdate: "Portable builds update manually. This opens the matching release download page; it does not overwrite the installed app.",
  close: "Close",
  unavailable: "Update check is unavailable right now.",
  dev: "development build"
};
var zh4 = {
  label: "\u66F4\u65B0",
  title: "\u7248\u672C\u4E0E\u66F4\u65B0",
  current: "\u5F53\u524D\u7248\u672C",
  officialLatest: "\u5B98\u65B9\u6700\u65B0",
  buildLatest: "\u672C\u4ED3\u5E93\u6700\u65B0",
  upToDate: "\u5DF2\u662F\u6700\u65B0\u3002",
  officialUpdate: "\u5B98\u65B9\u5DF2\u53D1\u5E03\u65B0\u7248\u672C\uFF0C\u9700\u8981\u91CD\u65B0\u6784\u5EFA\u6216\u5230\u5B98\u65B9 Releases \u4E0B\u8F7D\u5B98\u65B9\u5305\u3002",
  buildUpdate: "\u672C\u4ED3\u5E93\u6709\u65B0\u7684\u4FBF\u643A\u5305\u6784\u5EFA\u3002",
  notes: "\u66F4\u65B0\u5185\u5BB9",
  openOfficial: "\u6253\u5F00\u5B98\u65B9 Releases",
  openBuild: "\u6253\u5F00\u672C\u4ED3\u5E93 Releases",
  refresh: "\u7ACB\u5373\u68C0\u67E5",
  refreshing: "\u68C0\u67E5\u4E2D\u2026",
  updateNow: "\u7ACB\u5373\u66F4\u65B0",
  recheck: "\u91CD\u65B0\u68C0\u67E5",
  manualUpdate: "\u4FBF\u643A\u5305\u4E3A\u624B\u52A8\u66F4\u65B0\uFF1A\u8FD9\u91CC\u4F1A\u6253\u5F00\u5BF9\u5E94 Release \u7684\u4E0B\u8F7D\u9875\uFF0C\u4E0D\u4F1A\u81EA\u52A8\u8986\u76D6\u5B89\u88C5\u3002",
  close: "\u5173\u95ED",
  unavailable: "\u6682\u65F6\u65E0\u6CD5\u68C0\u67E5\u66F4\u65B0\u3002",
  dev: "\u5F00\u53D1\u6784\u5EFA"
};
var STATE_PATH2 = "/api/desktop/updates/state";
var STYLE_ID3 = "dsh-overlay-updates-entry-styles";
var CSS3 = `
.dshOverlayUpdateEntry {
  position: relative;
  box-sizing: border-box;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 32px;
  min-width: 32px;
  padding: 0;
  border: none;
  border-radius: 8px;
  background: transparent;
  cursor: pointer;
  color: var(--dsw-alias-label-secondary);
  font: inherit;
  font-size: 13px;
  line-height: 20px;
}
.dshOverlayUpdateEntry:hover { background: var(--dsw-alias-bg-hover); }
.dshOverlayUpdateEntryWide { justify-content: flex-start; padding: 0 10px 0 8px; width: calc(100% + 4px); margin: 0 -2px; height: 42px; }
.dshOverlayUpdateDot {
  position: absolute; top: 6px; right: 6px; width: 8px; height: 8px; border-radius: 50%;
  background: var(--dsw-alias-state-error-primary, #e5484d); box-shadow: 0 0 0 2px var(--dsw-alias-bg-base, #fff);
}
.dshOverlayUpdateModal {
  position: fixed; z-index: 80; top: 96px; left: 50%; transform: translateX(-50%);
  width: min(560px, calc(100vw - 32px)); max-height: calc(100vh - 160px); overflow: auto;
  box-sizing: border-box; padding: 16px; border-radius: 14px;
  background: var(--dsw-alias-bg-elevated, #fff); color: var(--dsw-alias-label-primary, #111);
  box-shadow: 0 18px 48px rgba(0, 0, 0, .24); font-size: 13px; line-height: 20px;
}
.dshOverlayUpdateModal h3 { margin: 0 0 10px; font-size: 15px; line-height: 22px; }
.dshOverlayUpdateRow { display: flex; gap: 8px; align-items: baseline; margin: 0 0 6px; }
.dshOverlayUpdateRow b { flex: 0 0 84px; font-weight: 600; opacity: .72; }
.dshOverlayUpdateNote { margin: 4px 0 10px; }
.dshOverlayUpdateAlert { margin: 0 0 10px; padding: 8px 10px; border-radius: 8px; background: var(--dsw-alias-bg-warning, #fff6e5); color: var(--dsw-alias-label-primary, #111); }
.dshOverlayUpdateNotes { margin: 0 0 12px; padding: 10px; max-height: 220px; overflow: auto; white-space: pre-wrap; border-radius: 8px; background: var(--dsw-alias-bg-base, #f7f7f8); }
.dshOverlayUpdateActions { display: flex; flex-wrap: wrap; gap: 8px; }
.dshOverlayUpdateActions a, .dshOverlayUpdateActions button {
  box-sizing: border-box; padding: 4px 10px; border-radius: 8px; border: 1px solid var(--dsw-alias-border-default, rgba(0,0,0,.12));
  background: transparent; color: inherit; font: inherit; text-decoration: none; cursor: pointer;
}
.dshOverlayUpdateActions a:hover, .dshOverlayUpdateActions button:hover { background: var(--dsw-alias-bg-hover, rgba(0,0,0,.05)); }
.dshOverlayUpdateActions .dshOverlayUpdatePrimary {
  border-color: transparent; background: var(--dsw-alias-button-primary-fill, #4d6bfe); color: var(--dsw-alias-label-primary-foreground, #fff);
}
.dshOverlayUpdateActions .dshOverlayUpdatePrimary:hover { background: var(--dsw-alias-button-primary-hover, #3f5ae0); }
.dshOverlayUpdateHint { margin: 8px 0 0; color: var(--dsw-alias-label-tertiary, rgba(0,0,0,.55)); font-size: 12px; line-height: 18px; }
`;
function BellIcon() {
  return /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("svg", { width: "16", height: "16", viewBox: "0 0 16 16", "aria-hidden": "true", focusable: "false", children: /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("path", { fill: "currentColor", d: "M8 1.6a3.4 3.4 0 0 0-3.4 3.4v2.1l-1 1.8a.8.8 0 0 0 .7 1.2h7.4a.8.8 0 0 0 .7-1.2l-1-1.8V5A3.4 3.4 0 0 0 8 1.6Zm0 12.1a1.8 1.8 0 0 0 1.7-1.2H6.3A1.8 1.8 0 0 0 8 13.7Z" }) });
}
function installStyles() {
  if (typeof document === "undefined" || document.getElementById(STYLE_ID3) !== null) return;
  const style = document.createElement("style");
  style.id = STYLE_ID3;
  style.textContent = CSS3;
  document.head.append(style);
}
function OverlayUpdateEntry({ t, wide }) {
  const [view, setView] = (0, import_react7.useState)();
  const [open, setOpen] = (0, import_react7.useState)(false);
  const [checking, setChecking] = (0, import_react7.useState)(false);
  const [failed, setFailed] = (0, import_react7.useState)(false);
  const menuPatched = useAccountMenuPatched();
  const load = (0, import_react7.useCallback)((force) => {
    setFailed(false);
    if (force) setChecking(true);
    void fetch(STATE_PATH2, {
      method: force ? "POST" : "GET",
      credentials: "same-origin",
      redirect: "error",
      headers: { accept: "application/json" }
    }).then(async (response) => {
      const value = await response.json().catch(() => void 0);
      if (!response.ok || value === void 0) throw new Error(`HTTP ${String(response.status)}`);
      setView(value);
    }).catch(() => {
      setFailed(true);
    }).finally(() => {
      setChecking(false);
    });
  }, []);
  (0, import_react7.useEffect)(() => {
    load(false);
  }, [load]);
  (0, import_react7.useEffect)(() => {
    const timer = window.setInterval(() => {
      load(false);
    }, 10 * 60 * 1e3);
    return () => {
      window.clearInterval(timer);
    };
  }, [load]);
  (0, import_react7.useEffect)(() => {
    if (open) load(true);
  }, [open, load]);
  const pending = view !== void 0 && (view.officialUpdate || view.buildUpdate);
  const updateUrl = view?.buildUpdate === true ? view.build?.url : view?.officialUpdate === true ? view.official?.url : void 0;
  const openFromMenu = (0, import_react7.useCallback)(() => {
    setOpen(true);
    load(true);
  }, [load]);
  useMenuAction("updates", openFromMenu);
  (0, import_react7.useEffect)(() => {
    publishUpdatePending(pending);
  }, [pending]);
  return /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)(import_jsx_runtime6.Fragment, { children: [
    menuPatched ? null : /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)(
      "button",
      {
        type: "button",
        className: ["dshOverlayUpdateEntry", wide ? "dshOverlayUpdateEntryWide" : ""].filter((part) => part !== "").join(" "),
        "aria-label": t("label"),
        title: pending ? view?.buildUpdate === true ? t("buildUpdate") : t("officialUpdate") : t("label"),
        onClick: () => {
          setOpen(true);
        },
        children: [
          /* @__PURE__ */ (0, import_jsx_runtime6.jsx)(BellIcon, {}),
          wide ? /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { children: t("label") }) : null,
          pending ? /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "dshOverlayUpdateDot", "aria-hidden": "true" }) : null
        ]
      }
    ),
    open ? /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)("div", { className: "dshOverlayUpdateModal", role: "dialog", "aria-modal": "true", "aria-label": t("title"), children: [
      /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("h3", { children: t("title") }),
      /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)("p", { className: "dshOverlayUpdateRow", children: [
        /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("b", { children: t("current") }),
        /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)("span", { children: [
          view?.current.buildVersion ?? "\u2026",
          view?.current.buildVersion.endsWith("_dev") === true ? ` \xB7 ${t("dev")}` : ""
        ] })
      ] }),
      /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)("p", { className: "dshOverlayUpdateRow", children: [
        /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("b", { children: t("officialLatest") }),
        /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { children: view?.official?.version ?? "\u2014" })
      ] }),
      /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)("p", { className: "dshOverlayUpdateRow", children: [
        /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("b", { children: t("buildLatest") }),
        /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { children: view?.build?.version ?? "\u2014" })
      ] }),
      failed || view?.error !== null && view?.error !== void 0 ? /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("p", { className: "dshOverlayUpdateAlert", children: t("unavailable") }) : null,
      pending ? /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("p", { className: "dshOverlayUpdateAlert", children: view?.buildUpdate === true ? t("buildUpdate") : t("officialUpdate") }) : null,
      !pending && view !== void 0 && view.error === null ? /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("p", { className: "dshOverlayUpdateRow", children: t("upToDate") }) : null,
      view?.build?.notes !== void 0 && view.build.notes !== "" ? /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)(import_jsx_runtime6.Fragment, { children: [
        /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("p", { className: "dshOverlayUpdateNote", children: /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("b", { children: t("notes") }) }),
        /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("pre", { className: "dshOverlayUpdateNotes", children: view.build.notes })
      ] }) : null,
      view?.official?.notes !== void 0 && view.official.notes !== "" && view.build?.notes === void 0 ? /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)(import_jsx_runtime6.Fragment, { children: [
        /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("p", { className: "dshOverlayUpdateNote", children: /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("b", { children: t("notes") }) }),
        /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("pre", { className: "dshOverlayUpdateNotes", children: view.official.notes })
      ] }) : null,
      /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)("div", { className: "dshOverlayUpdateActions", children: [
        updateUrl !== void 0 ? /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("a", { className: "dshOverlayUpdatePrimary", href: updateUrl, target: "_blank", rel: "noreferrer noopener", children: t("updateNow") }) : null,
        /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("button", { type: "button", onClick: () => {
          load(true);
        }, disabled: checking, children: checking ? t("refreshing") : t("recheck") }),
        /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("button", { type: "button", onClick: () => {
          setOpen(false);
        }, children: t("close") })
      ] }),
      pending ? /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("p", { className: "dshOverlayUpdateHint", children: t("manualUpdate") }) : null
    ] }) : null
  ] });
}
function applyOverlayUpdateEntry(ctx) {
  installStyles();
  ctx.effect(() => ctx.locale.register(NAMESPACE4, { zh: zh4, en: en4 }), "dsh-overlay-custom: updates locale");
  ctx.effect(() => ctx.slots.inject("sidebar.footer.action", () => ctx.slots.register({
    name: "sidebar.footer.action",
    id: "overlay-updates",
    priority: -1,
    order: 24,
    label: () => ctx.locale.bind(NAMESPACE4)("label"),
    inject: () => ({ t: ctx.locale.bind(NAMESPACE4) })
  }, OverlayUpdateEntry)), "dsh-overlay-custom: update sidebar entry");
}

// src/client/index.ts
var inject = ["slots", "locale"];
function apply(ctx) {
  applyOverlayRemoteEntry(ctx);
  applyOverlayModelExtras(ctx);
  applyOverlayCapabilitySettings(ctx);
  applyOverlayUpdateEntry(ctx);
}
return module.exports;}});
