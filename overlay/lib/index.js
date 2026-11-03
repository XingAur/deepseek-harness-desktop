var __defProp = Object.defineProperty;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __esm = (fn, res, err) => function __init() {
  if (err) throw err[0];
  try {
    return fn && (res = (0, fn[__getOwnPropNames(fn)[0]])(fn = 0)), res;
  } catch (e) {
    throw err = [e], e;
  }
};
var __export = (target, all) => {
  for (var name2 in all)
    __defProp(target, name2, { get: all[name2], enumerable: true });
};

// <define:__DSH_OVERLAY_META__>
var define_DSH_OVERLAY_META_default;
var init_define_DSH_OVERLAY_META = __esm({
  "<define:__DSH_OVERLAY_META__>"() {
    define_DSH_OVERLAY_META_default = { officialVersion: "0.2.1-rc.5", buildVersion: "0.2.1-rc.5_dev" };
  }
});

// src/overlay-config.ts
var overlay_config_exports = {};
__export(overlay_config_exports, {
  readOverlayConfig: () => readOverlayConfig,
  readOverlayConfigSync: () => readOverlayConfigSync,
  writeOverlayConfig: () => writeOverlayConfig
});
import { readFileSync } from "node:fs";
import { chmod, mkdir, readFile, writeFile } from "node:fs/promises";
import { join as join2 } from "node:path";
import { parse, stringify } from "yaml";
function asRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value : {};
}
function readOverlayConfigSync(home) {
  try {
    return asRecord(parse(readFileSync(join2(home, FILE), "utf8")));
  } catch {
    return {};
  }
}
async function readOverlayConfig(home) {
  try {
    return asRecord(parse(await readFile(join2(home, FILE), "utf8")));
  } catch {
    return {};
  }
}
async function writeOverlayConfig(home, patch) {
  const current = await readOverlayConfig(home);
  const next = { ...current, ...patch };
  await mkdir(home, { recursive: true, mode: 448 });
  const path = join2(home, FILE);
  await writeFile(path, `${stringify(next)}
`, { encoding: "utf8", mode: 384 });
  await chmod(path, 384);
  return next;
}
var FILE;
var init_overlay_config = __esm({
  "src/overlay-config.ts"() {
    init_define_DSH_OVERLAY_META();
    FILE = "overlay-custom.yaml";
  }
});

// src/index.ts
init_define_DSH_OVERLAY_META();

// src/dsh-home.ts
init_define_DSH_OVERLAY_META();
import { homedir } from "node:os";
import { join } from "node:path";
function resolveDshHome() {
  const env = process.env.DSH_HOME;
  if (typeof env === "string" && env.trim() !== "") return env.trim();
  return join(homedir(), ".dsh");
}

// src/index.ts
init_overlay_config();

// src/models.ts
init_define_DSH_OVERLAY_META();

// src/model-core.ts
init_define_DSH_OVERLAY_META();
import { chmod as chmod2, mkdir as mkdir2, readFile as readFile2, writeFile as writeFile2 } from "node:fs/promises";
import { join as join3 } from "node:path";
import { parse as parse2, stringify as stringify2 } from "yaml";

// src/models-endpoint.ts
init_define_DSH_OVERLAY_META();
function modelsEndpoint(baseURL) {
  const trimmed = baseURL.replace(/\/+$/u, "");
  const root = trimmed.endsWith("/anthropic") ? trimmed.slice(0, -"/anthropic".length) : trimmed;
  return `${root}/models`;
}

// src/model-core.ts
var REQUEST_TIMEOUT_MS = 2e4;
function section(document, name2) {
  const value = document[name2];
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value : {};
}
async function readYaml(path) {
  try {
    const parsed = parse2(await readFile2(path, "utf8"));
    return parsed !== null && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}
function apiKeyOf(home) {
  const value = section(home, "refs")["DEEPSEEK_API_KEY"];
  return typeof value === "string" && value !== "" ? value : null;
}
async function readModelDiagnosticsState(home) {
  const document = await readYaml(join3(home, "settings.yaml"));
  const credentials = await readYaml(join3(home, ".credentials.yaml"));
  const key = apiKeyOf(credentials);
  const defaultModel = section(document, "agent-default-model");
  const llm = section(document, "llm-deepseek");
  return {
    provider: typeof defaultModel.provider === "string" ? defaultModel.provider : null,
    model: typeof defaultModel.model === "string" ? defaultModel.model : null,
    baseURL: typeof llm.baseURL === "string" ? llm.baseURL : null,
    keyPresent: key !== null,
    keyPreview: key === null ? null : `${key.slice(0, 6)}\u2026${key.slice(-4)}`
  };
}
async function probeOnce(endpoint, key) {
  try {
    const response = await fetch(endpoint, {
      headers: { authorization: `Bearer ${key}`, accept: "application/json" },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS)
    });
    if (!response.ok) {
      const detail = (await response.text().catch(() => "")).replace(/\s+/gu, " ").trim().slice(0, 200);
      return {
        ok: false,
        status: response.status,
        models: [],
        error: detail === "" ? `HTTP ${String(response.status)}` : `HTTP ${String(response.status)}: ${detail}`
      };
    }
    const payload = await response.json();
    const models = (Array.isArray(payload.data) ? payload.data : []).map((item) => typeof item?.id === "string" ? item.id : null).filter((id) => id !== null).sort();
    return { ok: true, status: response.status, models, error: null };
  } catch (cause) {
    return { ok: false, status: 0, models: [], error: cause instanceof Error ? cause.message : String(cause) };
  }
}
async function probeModels(home) {
  const state = await readModelDiagnosticsState(home);
  if (state.baseURL === null) return { ok: false, status: 0, models: [], error: "no baseURL configured" };
  const key = apiKeyOf(await readYaml(join3(home, ".credentials.yaml")));
  if (key === null) return { ok: false, status: 0, models: [], error: "no API key stored" };
  return await probeWithRetry(modelsEndpoint(state.baseURL), key);
}
function deriveKeyRef(provider) {
  return `${provider.toUpperCase().replace(/[^a-z0-9]+/giu, "_")}_API_KEY`;
}
async function probeProvider(home, provider) {
  if (provider === "" || provider.length > 100 || !/^[a-z0-9][a-z0-9-]*$/u.test(provider)) {
    return { ok: false, status: 0, models: [], error: "invalid provider id" };
  }
  const document = await readYaml(join3(home, "settings.yaml"));
  const profiles = section(section(document, "llm-pi-ai"), "providers");
  const profile = profiles[provider];
  const baseURL = profile !== null && typeof profile === "object" && !Array.isArray(profile) && typeof profile.baseURL === "string" ? profile.baseURL : null;
  if (baseURL === null || baseURL === "") {
    return { ok: false, status: 0, models: [], error: `no baseURL configured for ${provider}` };
  }
  const profileRecord = profile;
  const ref = typeof profileRecord.apiKeyEnv === "string" && profileRecord.apiKeyEnv !== "" ? profileRecord.apiKeyEnv : deriveKeyRef(provider);
  const key = section(await readYaml(join3(home, ".credentials.yaml")), "refs")[ref];
  if (typeof key !== "string" || key === "") {
    return { ok: false, status: 0, models: [], error: `no API key stored for ${provider}` };
  }
  return await probeWithRetry(modelsEndpoint(baseURL), key);
}
async function probeWithRetry(endpoint, key) {
  const first = await probeOnce(endpoint, key);
  if (first?.ok === true) return first;
  if (first !== null && first.status > 0 && first.status < 500) return first;
  const second = await probeOnce(endpoint, key);
  return second ?? first ?? { ok: false, status: 0, models: [], error: "probe failed" };
}

// src/model-selection.ts
init_define_DSH_OVERLAY_META();
import { chmod as chmod3, copyFile, mkdir as mkdir3, readFile as readFile3, rename, writeFile as writeFile3 } from "node:fs/promises";
import { dirname, join as join4 } from "node:path";
import { parse as parse3, stringify as stringify3 } from "yaml";
var PATCH_RELATIVE = join4("profiles", "desktop", "cordis.patch.yml");
var MAX_MODELS = 200;
var MODEL_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/u;
var PROVIDER_ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,99}$/u;
function isValidProviderId(value) {
  return PROVIDER_ID_PATTERN.test(value);
}
var XAI_DROP_RE = /(?:build|multi-agent|-[0-9]{4}(?:-|$))/iu;
var EFFORTS_56 = { minimal: "low", xhigh: "xhigh", max: "max" };
var EFFORTS_6 = {
  off: null,
  minimal: "low",
  low: "low",
  medium: "medium",
  high: "high",
  xhigh: "xhigh",
  max: "max"
};
var OFFICIAL_CODEX = [
  { id: "gpt-5.3-codex-spark", name: "GPT-5.3 Codex Spark", reasoningEfforts: EFFORTS_56 },
  { id: "gpt-5.4", name: "GPT-5.4", reasoningEfforts: EFFORTS_56 },
  { id: "gpt-5.4-mini", name: "GPT-5.4 mini", reasoningEfforts: EFFORTS_56 },
  { id: "gpt-5.5", name: "GPT-5.5", reasoningEfforts: EFFORTS_56 },
  { id: "gpt-5.6-luna", name: "GPT-5.6 Luna", reasoningEfforts: EFFORTS_56 },
  { id: "gpt-5.6-sol", name: "GPT-5.6 Sol", reasoningEfforts: EFFORTS_56 },
  { id: "gpt-5.6-terra", name: "GPT-5.6 Terra", reasoningEfforts: EFFORTS_56 },
  { id: "gpt-6-astra", name: "GPT-6 Astra", reasoningEfforts: EFFORTS_6 }
];
function canonicalizeCodexId(id) {
  return id.replace(/^(gpt)-(\d+)-(\d+)\b/iu, "$1-$2.$3");
}
function displayNameFor(id, provider) {
  if (provider === "xai" && id.startsWith("grok-")) {
    return `Grok ${id.slice(5).replace(/-/gu, " ")}`;
  }
  const planets = id.match(/-(luna|sol|terra|astra)$/iu);
  const base = id.replace(/^gpt-/iu, "GPT-");
  if (planets === null) return base;
  const planet = planets[1];
  return `${base.slice(0, -planet.length - 1)} ${planet.slice(0, 1).toUpperCase()}${planet.slice(1)}`;
}
function codexSku(raw) {
  const id = canonicalizeCodexId(raw).toLowerCase().replace(/-wm$/u, "").replace(/-t-mini$/u, "-mini");
  if (/-(?:thinking|instant|pro|reserve)$/u.test(id) || id === "gpt-reserve" || id === "research" || id === "o3-pro") {
    return null;
  }
  if (/codex-spark|^gpt-5\.3$/u.test(id) || id === "gpt-5.3-mini") {
    return { id: "gpt-5.3-codex-spark", name: "GPT-5.3 Codex Spark", reasoningEfforts: EFFORTS_56 };
  }
  const official = OFFICIAL_CODEX.find((entry) => entry.id === id);
  if (official !== void 0) return official;
  const planet = /^(gpt-\d+(?:\.\d+)?)-(luna|sol|terra|astra)$/u.exec(id);
  if (planet !== null) {
    const efforts = planet[1].startsWith("gpt-6") ? EFFORTS_6 : EFFORTS_56;
    return { id, name: displayNameFor(id, "openai-codex"), reasoningEfforts: efforts };
  }
  if (/^gpt-5\.4-mini$/u.test(id)) return { id: "gpt-5.4-mini", name: "GPT-5.4 mini", reasoningEfforts: EFFORTS_56 };
  if (/^gpt-5\.4$/u.test(id)) return { id: "gpt-5.4", name: "GPT-5.4", reasoningEfforts: EFFORTS_56 };
  if (/^gpt-5\.5-mini$/u.test(id)) return { id: "gpt-5.5-mini", name: "GPT-5.5 mini", reasoningEfforts: EFFORTS_56 };
  if (/^gpt-5\.5$/u.test(id)) return { id: "gpt-5.5", name: "GPT-5.5", reasoningEfforts: EFFORTS_56 };
  if (/^gpt-5\.6-mini$/u.test(id)) return { id: "gpt-5.6-mini", name: "GPT-5.6 mini", reasoningEfforts: EFFORTS_56 };
  if (/^gpt-5\.6$/u.test(id)) return null;
  if (/^gpt-6$/u.test(id)) return null;
  return null;
}
function foldPickerModels(provider, models) {
  if (provider === "xai") {
    return [...new Set(models)].filter((id) => id.startsWith("grok-") && !XAI_DROP_RE.test(id)).sort().map((id) => ({ id, name: displayNameFor(id, provider), input: ["text", "image"] }));
  }
  if (provider !== "openai-codex") {
    return [...new Set(models)].sort().map((id) => ({ id, name: displayNameFor(id, provider) }));
  }
  const byId = /* @__PURE__ */ new Map();
  for (const entry of OFFICIAL_CODEX) byId.set(entry.id, entry);
  for (const raw of models) {
    const sku = codexSku(raw);
    if (sku === null) continue;
    byId.set(sku.id, sku);
  }
  return [...byId.values()].sort((left, right) => left.id.localeCompare(right.id)).map((entry) => ({ ...entry, input: ["text", "image"] }));
}
function profilesForIds(provider, models) {
  if (provider === "xai") {
    return [...new Set(models)].filter((id) => id.startsWith("grok-") && !XAI_DROP_RE.test(id)).sort().map((id) => ({ id, name: displayNameFor(id, provider), input: ["text", "image"] }));
  }
  if (provider !== "openai-codex") {
    return [...new Set(models)].sort().map((id) => ({ id, name: displayNameFor(id, provider) }));
  }
  const byId = /* @__PURE__ */ new Map();
  for (const raw of models) {
    const sku = codexSku(raw);
    if (sku === null) continue;
    byId.set(sku.id, sku);
  }
  return [...byId.values()].sort((left, right) => left.id.localeCompare(right.id)).map((entry) => ({ ...entry, input: ["text", "image"] }));
}
function normalizeModelIds(models) {
  const seen = /* @__PURE__ */ new Set();
  for (const value of models) {
    if (typeof value !== "string" || !MODEL_ID_PATTERN.test(value)) {
      throw new Error(`invalid model id: ${String(value)}`);
    }
    seen.add(value);
    if (seen.size > MAX_MODELS) throw new Error(`too many models (max ${String(MAX_MODELS)})`);
  }
  return [...seen].sort();
}
function asRecord2(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value : {};
}
async function readYaml2(path) {
  try {
    return parse3(await readFile3(path, "utf8"));
  } catch {
    return null;
  }
}
function modelIdsOf(profile) {
  const raw = profile.models;
  if (!Array.isArray(raw)) return [];
  const ids = [];
  for (const entry of raw) {
    if (typeof entry === "string") ids.push(entry);
    else {
      const record3 = asRecord2(entry);
      if (typeof record3.id === "string") ids.push(record3.id);
    }
  }
  return ids;
}
async function readConfiguredModels(home, provider) {
  if (!isValidProviderId(provider)) return [];
  const settings = asRecord2(await readYaml2(join4(home, "settings.yaml")));
  const settingsProviders = asRecord2(asRecord2(settings["llm-pi-ai"])["providers"]);
  const merged = new Set(modelIdsOf(asRecord2(settingsProviders[provider])));
  const patch = await readYaml2(join4(home, PATCH_RELATIVE));
  if (Array.isArray(patch)) {
    for (const entry of patch) {
      const record3 = asRecord2(entry);
      if (record3.id !== "llm-pi-ai") continue;
      const providers = asRecord2(asRecord2(record3.config)["providers"]);
      for (const id of modelIdsOf(asRecord2(providers[provider]))) merged.add(id);
    }
  }
  return [...merged].sort();
}
async function writeModelSelection(home, provider, models) {
  if (!isValidProviderId(provider)) throw new Error("invalid provider id");
  const normalized = normalizeModelIds(models);
  const folded = profilesForIds(provider, normalized);
  const path = join4(home, PATCH_RELATIVE);
  const parsed = await readYaml2(path);
  const entries = Array.isArray(parsed) ? parsed : [];
  let entry = entries.find((candidate) => asRecord2(candidate).id === "llm-pi-ai");
  if (entry === void 0) {
    entry = { id: "llm-pi-ai", name: "@deepseek-ai/dsh-llm-pi-ai", config: { providers: {} } };
    entries.push(entry);
  }
  const record3 = asRecord2(entry);
  const config = asRecord2(record3.config);
  const providers = asRecord2(config.providers);
  const profile = asRecord2(providers[provider]);
  providers[provider] = {
    ...profile,
    models: folded.map((entry2) => ({
      id: entry2.id,
      name: entry2.name,
      ...entry2.input === void 0 ? {} : { input: entry2.input },
      ...entry2.reasoningEfforts === void 0 ? {} : { reasoningEfforts: entry2.reasoningEfforts }
    }))
  };
  record3.config = { ...config, providers };
  await mkdir3(dirname(path), { recursive: true, mode: 448 });
  const temporary = `${path}.overlay-tmp`;
  await writeFile3(temporary, `${stringify3(entries)}
`, { encoding: "utf8", mode: 384 });
  try {
    await rename(temporary, path);
  } catch {
    await copyFile(temporary, path);
    await chmod3(path, 384);
  }
  return folded.map((entry2) => entry2.id);
}

// src/live-models.ts
init_define_DSH_OVERLAY_META();
import { readFile as readFile4 } from "node:fs/promises";
import { join as join5 } from "node:path";
import { parse as parse4 } from "yaml";
var REQUEST_TIMEOUT_MS2 = 15e3;
function asRecord3(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value : {};
}
async function readOAuthCredential(home, provider) {
  let document;
  try {
    document = parse4(await readFile4(join5(home, ".credentials.yaml"), "utf8"));
  } catch {
    return null;
  }
  const record3 = asRecord3(asRecord3(asRecord3(document)["records"])[`llm-pi-ai/${provider}`]);
  const payload = asRecord3(record3.payload);
  const credential = asRecord3(payload.credential);
  const access = typeof payload.access === "string" ? payload.access : typeof credential.access === "string" ? credential.access : null;
  if (access === null || access === "") return null;
  const accountId = typeof payload.accountId === "string" ? payload.accountId : null;
  return { access, accountId };
}
function extractModelIds(payload) {
  const record3 = asRecord3(payload);
  const ids = /* @__PURE__ */ new Set();
  const rows = Array.isArray(payload) ? payload : Array.isArray(record3.data) ? record3.data : Array.isArray(record3.models) ? record3.models : [];
  for (const item of rows) {
    if (typeof item === "string" && item !== "") {
      ids.add(item);
      continue;
    }
    const row = asRecord3(item);
    const id = [row.id, row.slug, row.model].find((value) => typeof value === "string" && value !== "");
    if (typeof id === "string") ids.add(id);
  }
  const models = asRecord3(record3.models);
  for (const key of Object.keys(models)) ids.add(key);
  return [...ids].sort();
}
function endpointsFor(provider) {
  if (provider === "xai") {
    return [{ url: "https://api.x.ai/v1/models", accountHeader: false }];
  }
  return [
    { url: "https://chatgpt.com/backend-api/codex/models?client_version=1.0.0", accountHeader: true },
    { url: "https://chatgpt.com/backend-api/models?client_version=1.0.0", accountHeader: true },
    { url: "https://api.openai.com/v1/models", accountHeader: false }
  ];
}
async function loadJson(url, headers) {
  const response = await fetch(url, { headers, signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS2) });
  return { status: response.status, body: await response.json().catch(() => null) };
}
async function fetchLiveModelIds(provider, credential) {
  let lastError = null;
  for (const endpoint of endpointsFor(provider)) {
    try {
      const headers = {
        authorization: `Bearer ${credential.access}`,
        accept: "application/json",
        originator: "pi",
        "user-agent": "pi"
      };
      if (endpoint.accountHeader && credential.accountId !== null) {
        headers["chatgpt-account-id"] = credential.accountId;
      }
      const response = await loadJson(endpoint.url, headers);
      if (response.status < 200 || response.status >= 300) {
        lastError = `HTTP ${String(response.status)}`;
        continue;
      }
      const models = extractModelIds(response.body);
      if (models.length > 0) return { ids: models, error: null };
      lastError = "empty-list";
    } catch {
      lastError = "network";
    }
  }
  return { ids: [], error: lastError ?? "live-empty" };
}

// src/oauth-providers.ts
init_define_DSH_OVERLAY_META();
var OAUTH_LOGIN_PROVIDERS = ["openai-codex", "xai"];
function isOauthLoginProvider(value) {
  return OAUTH_LOGIN_PROVIDERS.includes(value);
}
function oauthCredentialKey(provider) {
  return `llm-pi-ai/${provider}`;
}

// src/http.ts
init_define_DSH_OVERLAY_META();
function writeJson(res, status, value) {
  const body = `${JSON.stringify(value)}
`;
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "content-length": String(Buffer.byteLength(body))
  });
  res.end(body);
}
function sameOriginMutatingRequest(req) {
  const origin = req.headers.origin;
  const referer = req.headers.referer;
  const host = req.headers.host ?? "";
  if (origin === void 0 || origin === "") {
    if (typeof referer === "string" && referer.startsWith("dsh-app:")) return true;
    return true;
  }
  try {
    const parsed = new URL(origin);
    if (parsed.protocol === "dsh-app:") return true;
    return parsed.host !== "" && parsed.host === host;
  } catch {
    return false;
  }
}
var MAX_BODY_BYTES = 64 * 1024;
async function readJsonBody(req) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > MAX_BODY_BYTES) throw new Error("request body too large");
    chunks.push(chunk);
  }
  if (chunks.length === 0) return {};
  const parsed = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("request body must be a JSON object");
  }
  return parsed;
}
function rejectUnauthorized(req, res, rejection) {
  if (rejection === void 0) return false;
  res.writeHead(rejection);
  res.end(rejection === 401 ? "unauthorized" : "forbidden");
  return true;
}

// src/models.ts
function applyOverlayModels(ctx, modelNetwork) {
  const home = resolveDshHome();
  const reject = (req, res) => rejectUnauthorized(req, res, ctx.connection.requestRejection(req));
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: "/api/desktop/models/probe",
    handler: (req, res) => {
      if (reject(req, res)) return;
      if (req.method !== "POST") {
        res.writeHead(405, { allow: "POST", "cache-control": "no-store" });
        res.end("method not allowed");
        return;
      }
      if (!sameOriginMutatingRequest(req)) {
        res.writeHead(403, { "cache-control": "no-store" });
        res.end("forbidden");
        return;
      }
      void (async () => {
        try {
          const body = await readJsonBody(req).catch(() => ({}));
          const provider = typeof body.settingsNs === "string" && body.settingsNs === "llm-pi-ai" && typeof body.provider === "string" ? body.provider : null;
          const outcome = await modelNetwork.run(() => provider === null ? probeModels(home) : probeProvider(home, provider));
          writeJson(res, 200, outcome);
        } catch (cause) {
          ctx.logger.error(`dsh-overlay-custom: model probe failed: ${cause instanceof Error ? cause.message : String(cause)}`);
          writeJson(res, 502, { ok: false, status: 0, models: [], error: "probe-failed" });
        }
      })();
    }
  }), "dsh-overlay-custom: model probe");
  const providerOf = (body, query) => {
    const raw = typeof body.provider === "string" ? body.provider : query;
    return typeof raw === "string" && isValidProviderId(raw) ? raw : null;
  };
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: "/api/desktop/models/live",
    handler: (req, res) => {
      if (reject(req, res)) return;
      if (req.method !== "POST") {
        res.writeHead(405, { allow: "POST", "cache-control": "no-store" });
        res.end("method not allowed");
        return;
      }
      if (!sameOriginMutatingRequest(req)) {
        res.writeHead(403, { "cache-control": "no-store" });
        res.end("forbidden");
        return;
      }
      void (async () => {
        const body = await readJsonBody(req).catch(() => ({}));
        const provider = providerOf(body, null);
        if (provider === null) {
          writeJson(res, 400, { ok: false, live: [], configured: [], error: "invalid-provider" });
          return;
        }
        const configured = await readConfiguredModels(home, provider);
        if (!isOauthLoginProvider(provider)) {
          writeJson(res, 200, { ok: true, live: [], configured });
          return;
        }
        const credential = await readOAuthCredential(home, provider);
        if (credential === null) {
          writeJson(res, 200, { ok: false, live: [], configured, error: "no-credential" });
          return;
        }
        const fetched = await modelNetwork.run(() => fetchLiveModelIds(provider, credential));
        if (fetched.ids.length === 0) {
          writeJson(res, 200, { ok: false, live: [], configured, error: fetched.error ?? "live-empty" });
          return;
        }
        const folded = foldPickerModels(provider, fetched.ids).map((entry) => entry.id);
        writeJson(res, 200, { ok: true, live: folded, configured: folded, applied: false });
      })().catch(() => {
        writeJson(res, 502, { ok: false, live: [], configured: [], error: "network" });
      });
    }
  }), "dsh-overlay-custom: live models");
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: "/api/desktop/models/selection",
    handler: (req, res) => {
      if (reject(req, res)) return;
      if (req.method !== "GET" && req.method !== "POST") {
        res.writeHead(405, { allow: "GET, POST", "cache-control": "no-store" });
        res.end("method not allowed");
        return;
      }
      if (req.method === "GET") {
        const url = new URL(req.url ?? "", "http://127.0.0.1");
        const provider = providerOf({}, url.searchParams.get("provider"));
        if (provider === null) {
          writeJson(res, 400, { ok: false, models: [], error: "invalid-provider" });
          return;
        }
        void readConfiguredModels(home, provider).then((models) => {
          writeJson(res, 200, { ok: true, models });
        }).catch(() => {
          writeJson(res, 500, { ok: false, models: [], error: "read-failed" });
        });
        return;
      }
      if (!sameOriginMutatingRequest(req)) {
        res.writeHead(403, { "cache-control": "no-store" });
        res.end("forbidden");
        return;
      }
      void (async () => {
        const body = await readJsonBody(req).catch(() => ({}));
        const provider = providerOf(body, null);
        if (provider === null) {
          writeJson(res, 400, { ok: false, models: [], error: "invalid-provider" });
          return;
        }
        if (!Array.isArray(body.models)) {
          writeJson(res, 400, { ok: false, models: [], error: "models-must-be-array" });
          return;
        }
        try {
          const models = await writeModelSelection(home, provider, body.models);
          writeJson(res, 200, { ok: true, models });
        } catch (cause) {
          ctx.logger.warn(`dsh-overlay-custom: selection write failed: ${cause instanceof Error ? cause.message : String(cause)}`);
          writeJson(res, 400, { ok: false, models: [], error: "write-failed" });
        }
      })();
    }
  }), "dsh-overlay-custom: model selection");
}

// src/oauth.ts
init_define_DSH_OVERLAY_META();

// src/model-proxy-manager.ts
init_define_DSH_OVERLAY_META();

// src/model-proxy.ts
init_define_DSH_OVERLAY_META();
import { randomUUID } from "node:crypto";
import { mkdir as mkdir4, readFile as readFile5, rename as rename2, rm, writeFile as writeFile4 } from "node:fs/promises";
import { dirname as dirname2, join as join6, resolve } from "node:path";
import { parse as parse5, stringify as stringify4 } from "yaml";

// src/model-proxy-routing.ts
init_define_DSH_OVERLAY_META();
import { isIP } from "node:net";
import { domainToASCII } from "node:url";
var SystemProxyError = class extends Error {
  code;
  constructor(code) {
    super(code);
    this.name = "SystemProxyError";
    this.code = code;
  }
};
var MAX_BYPASS_RULES = 256;
var MAX_BYPASS_LENGTH = 256;
var TRANSPORT_PROTOCOLS = /* @__PURE__ */ new Set(["http:", "https:", "ws:", "wss:"]);
function invalidBypass() {
  throw new Error("invalid-bypass");
}
function ipv4Number(host) {
  if (isIP(host) !== 4) return void 0;
  return host.split(".").reduce((value, octet) => value << 8n | BigInt(octet), 0n);
}
function ipv6Number(host) {
  let text = host.toLowerCase().replace(/^\[|\]$/g, "");
  if (isIP(text) !== 6 || text.includes("%")) return void 0;
  if (text.includes(".")) {
    const index = text.lastIndexOf(":");
    const ipv4 = ipv4Number(text.slice(index + 1));
    if (ipv4 === void 0) return void 0;
    text = `${text.slice(0, index)}:${(ipv4 >> 16n).toString(16)}:${(ipv4 & 0xffffn).toString(16)}`;
  }
  const [left, right] = text.split("::");
  const start = left ? left.split(":") : [];
  const end = right ? right.split(":") : [];
  const parts = text.includes("::") ? [...start, ...Array(8 - start.length - end.length).fill("0"), ...end] : start;
  if (parts.length !== 8) return void 0;
  return parts.reduce((value, part) => value << 16n | BigInt(`0x${part}`), 0n);
}
function canonicalHost(host) {
  const unwrapped = host.replace(/^\[|\]$/g, "").replace(/\.$/, "").toLowerCase();
  if (isIP(unwrapped) === 6) return new URL(`http://[${unwrapped}]`).hostname.slice(1, -1);
  if (isIP(unwrapped) === 4) return unwrapped;
  return domainToASCII(unwrapped);
}
function parseRule(rule) {
  if (rule.startsWith("[")) {
    const match = /^\[([^\]]+)\](?::([0-9]+))?$/.exec(rule);
    if (!match || isIP(match[1]) !== 6) return invalidBypass();
    return { host: match[1], port: match[2] };
  }
  if (isIP(rule) === 6) return { host: rule };
  const pieces = rule.split(":");
  if (pieces.length > 2 || pieces.length === 2 && !/^[0-9]+$/.test(pieces[1])) return invalidBypass();
  return { host: pieces[0], port: pieces[1] };
}
function normalizeCidr(rule) {
  const parts = rule.split("/");
  if (parts.length !== 2 || !/^\d+$/.test(parts[1])) return invalidBypass();
  let host = parts[0].replace(/^\[|\]$/g, "");
  if (/^\d+(?:\.\d+){0,3}$/.test(host)) {
    const octets = host.split(".").map(Number);
    if (octets.some((value) => value > 255)) return invalidBypass();
    host = [...octets, ...Array(4 - octets.length).fill(0)].join(".");
  }
  const family = isIP(host);
  const prefix = Number(parts[1]);
  if (!family || host.includes("%") || prefix > (family === 4 ? 32 : 128)) return invalidBypass();
  return `${canonicalHost(host)}/${prefix}`;
}
function parseProxyBypass(value, source = "user") {
  if (!Array.isArray(value) || value.length > MAX_BYPASS_RULES) return invalidBypass();
  const result2 = [];
  for (const item of value) {
    if (typeof item !== "string") return invalidBypass();
    const rule = item.trim().toLowerCase();
    if (!rule || rule.length > MAX_BYPASS_LENGTH || /[\s@\\?#]/.test(rule)) return invalidBypass();
    if (rule === "<local>" && source === "system") {
      result2.push(rule);
      continue;
    }
    if (rule.includes("/")) {
      result2.push(normalizeCidr(rule));
      continue;
    }
    const { host, port } = parseRule(rule);
    if (port !== void 0 && (Number(port) < 1 || Number(port) > 65535)) return invalidBypass();
    let normalized = canonicalHost(host);
    if (!normalized) return invalidBypass();
    if (!isIP(normalized)) {
      const domain = (host.startsWith(".") ? host.slice(1) : host).replace(/\.$/, "");
      if (!domain || source === "user" && domain !== "*" && domain.replace(/^\*\./, "").includes("*")) return invalidBypass();
      const labels = domain.split(".");
      if (labels.some((label) => !label || !/^[\p{L}\p{N}_*-]+$/u.test(label))) return invalidBypass();
      normalized = labels.map((label) => label.includes("*") ? label : domainToASCII(`${label}.invalid`).slice(0, -8)).join(".");
      if (!normalized || normalized.length > 253 || normalized.split(".").some((label) => !label || label.length > 63 || label.startsWith("-") || label.endsWith("-"))) return invalidBypass();
      if (host.startsWith(".")) normalized = `.${normalized}`;
    } else if (isIP(normalized) === 6 && port !== void 0) {
      normalized = `[${normalized}]`;
    }
    result2.push(`${normalized}${port === void 0 ? "" : `:${Number(port)}`}`);
  }
  return Object.freeze([...new Set(result2)]);
}
function targetUrl(target) {
  try {
    const url = new URL(String(target));
    if (!TRANSPORT_PROTOCOLS.has(url.protocol)) throw new Error();
    return url;
  } catch {
    throw new SystemProxyError("invalid-target-url");
  }
}
function isLoopbackHost(hostname) {
  const host = canonicalHost(hostname);
  if (host === "localhost" || host.endsWith(".localhost")) return true;
  const ipv4 = ipv4Number(host);
  if (ipv4 !== void 0) return ipv4 >> 24n === 127n;
  const ipv6 = ipv6Number(host);
  return ipv6 === 1n || ipv6 !== void 0 && ipv6 >> 32n === 0xffffn && (ipv6 & 0xffffffffn) >> 24n === 127n;
}
function cidrMatches(host, rule) {
  const [network, size] = rule.split("/");
  const family = isIP(network);
  if (!family || isIP(host) !== family) return false;
  const address = family === 4 ? ipv4Number(host) : ipv6Number(host);
  const base = family === 4 ? ipv4Number(network) : ipv6Number(network);
  const shift = BigInt((family === 4 ? 32 : 128) - Number(size));
  return address !== void 0 && base !== void 0 && address >> shift === base >> shift;
}
function matchesRules(url, rules, source) {
  const host = canonicalHost(url.hostname);
  const port = url.port || (url.protocol === "https:" || url.protocol === "wss:" ? "443" : "80");
  for (const rule of rules) {
    if (rule === "<local>") {
      if (!isIP(host) && !host.includes(".")) return true;
      continue;
    }
    if (rule.includes("/")) {
      if (cidrMatches(host, rule)) return true;
      continue;
    }
    const entry = parseRule(rule);
    if (entry.port !== void 0 && entry.port !== port) continue;
    const pattern = canonicalHost(entry.host);
    if (isIP(pattern)) {
      if (host === pattern) return true;
    } else if (pattern.includes("*")) {
      const escaped = pattern.split("*").map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join(".*");
      if (new RegExp(`^${escaped}$`, "i").test(host)) return true;
    } else {
      const suffix = pattern.startsWith(".") ? pattern.slice(1) : pattern;
      if (host === suffix || (source === "user" || pattern.startsWith(".")) && host.endsWith(`.${suffix}`)) return true;
    }
  }
  return false;
}
function createModelProxySnapshot(config, system) {
  return Object.freeze({
    config: Object.freeze({ mode: config.mode, url: config.url, bypass: parseProxyBypass(config.bypass) }),
    system: Object.freeze({
      status: system.status,
      httpProxy: system.httpProxy,
      httpsProxy: system.httpsProxy,
      bypass: parseProxyBypass(system.bypass, "system"),
      ...system.error === void 0 ? {} : { error: system.error }
    })
  });
}
function resolveModelProxyRoute(snapshot, target) {
  const url = targetUrl(target);
  const { config, system } = snapshot;
  if (isLoopbackHost(url.hostname)) return { kind: "direct", reason: "loopback" };
  if (config.mode === "direct") return { kind: "direct", reason: "explicit-direct" };
  if (matchesRules(url, config.bypass, "user")) return { kind: "direct", reason: "user-bypass" };
  if ((system.status === "ready" || system.status === "direct") && matchesRules(url, system.bypass, "system")) {
    return { kind: "direct", reason: "system-bypass" };
  }
  if (config.mode === "manual") {
    if (!config.url) throw new SystemProxyError("manual-url-required");
    return { kind: "proxy", proxyUrl: config.url, reason: "manual" };
  }
  if (config.mode !== "system") throw new SystemProxyError("invalid-mode");
  if (system.status === "error" || system.status === "unsupported") {
    throw new SystemProxyError(system.error || "system-proxy-read-failed");
  }
  if (system.status !== "ready" && system.status !== "direct") throw new SystemProxyError("system-proxy-invalid-config");
  const proxyUrl = url.protocol === "https:" || url.protocol === "wss:" ? system.httpsProxy : system.httpProxy;
  return proxyUrl ? { kind: "proxy", proxyUrl, reason: "system" } : { kind: "direct", reason: "system-no-proxy" };
}

// src/model-proxy.ts
var MAX_PROXY_URL_BYTES = 2048;
var HTTP_PROTOCOLS = /* @__PURE__ */ new Set(["http:", "https:"]);
var MODES = /* @__PURE__ */ new Set(["system", "manual", "direct"]);
var OVERLAY_FILE = "overlay-custom.yaml";
var DEFAULT_CONFIG = Object.freeze({ mode: "system", url: "", bypass: Object.freeze([]) });
function parseModelProxyUrl(value) {
  if (value === void 0 || value === "") return "";
  if (typeof value !== "string") throw new Error("invalid-url");
  const trimmed = value.trim();
  if (!trimmed) return "";
  if (Buffer.byteLength(trimmed) > MAX_PROXY_URL_BYTES) throw new Error("url-too-long");
  if (/[\s\\\u0000-\u001f\u007f]/.test(trimmed)) throw new Error("invalid-url");
  let parsed;
  try {
    parsed = new URL(trimmed);
  } catch {
    throw new Error("invalid-url");
  }
  if (!HTTP_PROTOCOLS.has(parsed.protocol)) throw new Error("unsupported-proxy-scheme");
  if (!/^https?:\/\//i.test(trimmed) || !parsed.hostname || parsed.pathname !== "/" || parsed.search || parsed.hash) throw new Error("invalid-url");
  const authority = trimmed.slice(trimmed.indexOf("://") + 3).split("/")[0];
  if (!authority || /:$/.test(authority) || parsed.port === "0" || /[?#]/.test(trimmed)) throw new Error("invalid-url");
  return trimmed;
}
function proxyDiagnosticOrigin(value) {
  if (!value) return "";
  try {
    const parsed = new URL(parseModelProxyUrl(value));
    return parsed.origin;
  } catch {
    return "";
  }
}
function asRecord4(value, error = "invalid-config") {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new Error(error);
  return value;
}
function parseModelProxyConfig(input, previous = DEFAULT_CONFIG) {
  const source = asRecord4(input);
  const mode = Object.hasOwn(source, "mode") ? source.mode : previous.mode;
  if (typeof mode !== "string" || !MODES.has(mode)) throw new Error("invalid-mode");
  const url = Object.hasOwn(source, "url") ? parseModelProxyUrl(source.url) : parseModelProxyUrl(previous.url);
  const bypass = parseProxyBypass(Object.hasOwn(source, "bypass") ? source.bypass : previous.bypass);
  if (mode === "manual" && !url) throw new Error("manual-url-required");
  return Object.freeze({ mode, url, bypass });
}
async function readOptionalYaml(home, file) {
  let text;
  try {
    text = await readFile5(join6(home, file), "utf8");
  } catch (error) {
    if (error.code === "ENOENT") return {};
    throw new Error("config-read-failed");
  }
  try {
    const document = parse5(text);
    return document === null || document === void 0 ? {} : asRecord4(document, "config-read-failed");
  } catch {
    throw new Error("config-read-failed");
  }
}
async function configFromOverlay(home, overlay) {
  const input = {};
  if (Object.hasOwn(overlay, "modelProxyMode")) input.mode = overlay.modelProxyMode;
  if (Object.hasOwn(overlay, "modelProxyBypass")) input.bypass = overlay.modelProxyBypass;
  if (Object.hasOwn(overlay, "modelProxyUrl")) {
    input.url = overlay.modelProxyUrl;
  } else {
    const settings = await readOptionalYaml(home, "settings.yaml");
    const legacy = settings["dsh-desktop"];
    if (legacy !== void 0 && legacy !== null) {
      const source = asRecord4(legacy, "config-read-failed");
      if (Object.hasOwn(source, "modelProxyUrl")) input.url = source.modelProxyUrl;
    }
  }
  return parseModelProxyConfig(input);
}
async function readModelProxyConfig(home) {
  return configFromOverlay(home, await readOptionalYaml(home, OVERLAY_FILE));
}
var writes = /* @__PURE__ */ new Map();
async function writeModelProxyConfig(home, input) {
  const directory = resolve(home);
  const preceding = writes.get(directory) ?? Promise.resolve();
  const operation = preceding.catch(() => {
  }).then(async () => {
    const overlay = await readOptionalYaml(directory, OVERLAY_FILE);
    const source = asRecord4(input);
    const complete = ["mode", "url", "bypass"].every((key) => Object.hasOwn(source, key));
    const config = parseModelProxyConfig(input, complete ? DEFAULT_CONFIG : await configFromOverlay(directory, overlay));
    const next = {
      ...overlay,
      modelProxyMode: config.mode,
      modelProxyUrl: config.url,
      modelProxyBypass: [...config.bypass]
    };
    const path = join6(directory, OVERLAY_FILE);
    const temporary = join6(directory, `.${OVERLAY_FILE}.${randomUUID()}.tmp`);
    if (dirname2(path) !== directory || dirname2(temporary) !== directory) throw new Error("config-write-failed");
    try {
      await mkdir4(directory, { recursive: true, mode: 448 });
      await writeFile4(temporary, `${stringify4(next)}
`, { encoding: "utf8", mode: 384, flag: "wx" });
      await rename2(temporary, path);
    } catch {
      await rm(temporary, { force: true }).catch(() => {
      });
      throw new Error("config-write-failed");
    }
    return config;
  });
  writes.set(directory, operation);
  try {
    return await operation;
  } finally {
    if (writes.get(directory) === operation) writes.delete(directory);
  }
}
function modelProxyConfigView(config, system) {
  let hasCredentials = false;
  if (config.url) {
    try {
      const url = new URL(config.url);
      hasCredentials = Boolean(url.username || url.password);
    } catch {
    }
  }
  return {
    mode: config.mode,
    url: proxyDiagnosticOrigin(config.url),
    hasCredentials,
    bypass: [...config.bypass],
    system: {
      status: system.status,
      httpProxy: proxyDiagnosticOrigin(system.httpProxy),
      httpsProxy: proxyDiagnosticOrigin(system.httpsProxy),
      bypass: [...system.bypass],
      ...system.error === void 0 ? {} : { error: system.error }
    }
  };
}

// src/system-proxy.ts
init_define_DSH_OVERLAY_META();
import { execFile } from "node:child_process";
import { isIP as isIP2 } from "node:net";
import { join as join7 } from "node:path";
var MAX_SCUTIL_BYTES = 256 * 1024;
function result(status, fields = {}) {
  return Object.freeze({
    status,
    httpProxy: "",
    httpsProxy: "",
    ...fields,
    bypass: Object.freeze([...fields.bypass ?? []])
  });
}
function fail(code = "system-proxy-invalid-config") {
  throw new Error(code);
}
function invalid() {
  return fail();
}
function record(value) {
  if (value === void 0) return {};
  if (typeof value === "string") return invalid();
  return value;
}
function scutilDictionary(output) {
  if (!output || Buffer.byteLength(output) > MAX_SCUTIL_BYTES) return invalid();
  const lines = output.trim().split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (lines.length === 1 && /^<dictionary>\s*\{\s*\}$/.test(lines[0])) return {};
  if (lines[0] !== "<dictionary> {") return invalid();
  const root = /* @__PURE__ */ Object.create(null);
  const stack = [root];
  let closed = false;
  for (const line of lines.slice(1)) {
    if (closed) return invalid();
    if (line === "}") {
      stack.pop();
      if (stack.length === 0) closed = true;
      continue;
    }
    const match = /^([^:]+?)\s*:\s*(.*)$/.exec(line);
    const parent = stack.at(-1);
    if (!match || !parent) return invalid();
    const key = match[1].trim();
    const value = match[2].trim();
    if (!key || Object.hasOwn(parent, key)) return invalid();
    if (/^<(?:dictionary|array)>\s*\{$/.test(value)) {
      const child = /* @__PURE__ */ Object.create(null);
      parent[key] = child;
      stack.push(child);
    } else if (/^<(?:dictionary|array)>\s*\{\s*\}$/.test(value)) {
      parent[key] = /* @__PURE__ */ Object.create(null);
    } else if (value.startsWith("<") || value === "{") {
      return invalid();
    } else {
      parent[key] = value;
    }
  }
  if (!closed || stack.length !== 0) return invalid();
  return root;
}
function enabled(source, key) {
  const value = source[key];
  if (value === void 0 || value === "0") return false;
  if (value === "1") return true;
  return invalid();
}
function proxyAddress(source, prefix) {
  if (!enabled(source, `${prefix}Enable`)) return "";
  const host = source[`${prefix}Proxy`];
  const port = source[`${prefix}Port`];
  if (typeof host !== "string" || typeof port !== "string" || !/^\d+$/.test(port) || Number(port) < 1 || Number(port) > 65535) return invalid();
  if (!host || /[\s/@\\?#]/.test(host)) return invalid();
  if ((host.startsWith("[") || host.endsWith("]")) && !/^\[[^\]]+\]$/.test(host)) return invalid();
  const bareHost = host.replace(/^\[|\]$/g, "");
  if (bareHost.includes(":") && isIP2(bareHost) !== 6) return invalid();
  const address = `http://${isIP2(bareHost) === 6 ? `[${bareHost}]` : bareHost}:${Number(port)}`;
  let url;
  try {
    url = new URL(address);
  } catch {
    return invalid();
  }
  if (!url.hostname || url.username || url.password || url.search || url.hash || url.pathname !== "/") return invalid();
  return url.origin;
}
function exceptionList(source) {
  const exceptions = record(source.ExceptionsList);
  if (Object.keys(exceptions).some((key) => !/^\d+$/.test(key))) return invalid();
  const values = Object.keys(exceptions).sort((a, b) => Number(a) - Number(b)).map((key) => exceptions[key]);
  if (values.some((value) => typeof value !== "string")) return invalid();
  if (enabled(source, "ExcludeSimpleHostnames")) values.push("<local>");
  try {
    return parseProxyBypass(values, "system");
  } catch {
    return invalid();
  }
}
function parseScutilProxyOutput(output) {
  try {
    const source = scutilDictionary(output);
    if (enabled(source, "ProxyAutoConfigEnable")) return result("unsupported", { error: "system-proxy-pac-unsupported" });
    if (enabled(source, "ProxyAutoDiscoveryEnable")) return result("unsupported", { error: "system-proxy-autodiscovery-unsupported" });
    if (Object.keys(record(source.__SCOPED__)).length || Object.keys(record(source.__SUPPLEMENTAL__)).length) {
      return result("unsupported", { error: "system-proxy-scoped-unsupported" });
    }
    const httpProxy = proxyAddress(source, "HTTP");
    const httpsProxy = proxyAddress(source, "HTTPS");
    if (enabled(source, "SOCKSEnable") && (!httpProxy || !httpsProxy)) {
      return result("unsupported", { error: "system-proxy-socks-unsupported" });
    }
    const bypass = exceptionList(source);
    return result(httpProxy || httpsProxy ? "ready" : "direct", { httpProxy, httpsProxy, bypass });
  } catch {
    return result("error", { error: "system-proxy-invalid-config" });
  }
}
var WINDOWS_KNOWN = /* @__PURE__ */ new Set([
  "system-proxy-invalid-config",
  "system-proxy-pac-unsupported",
  "system-proxy-autodiscovery-unsupported",
  "system-proxy-socks-unsupported",
  "system-proxy-list-unsupported",
  "system-proxy-bypass-unsupported"
]);
function windowsEndpoint(value) {
  let input = value.trim();
  if (!input) return invalid();
  if (/^https:\/\//i.test(input)) return fail("system-proxy-list-unsupported");
  if (/^http:\/\//i.test(input)) input = input.slice("http://".length);
  if (/[\s/@\\?#]/.test(input)) return invalid();
  let host;
  let portText;
  if (input.startsWith("[")) {
    const match = /^\[([^\]]+)\](?::(\d+))?$/.exec(input);
    if (!match) return invalid();
    host = match[1];
    portText = match[2] || "80";
  } else {
    const index = input.lastIndexOf(":");
    if (index === -1) {
      host = input;
      portText = "80";
    } else {
      host = input.slice(0, index);
      portText = input.slice(index + 1);
    }
  }
  if (!/^\d+$/.test(portText) || Number(portText) < 1 || Number(portText) > 65535) return invalid();
  if (!host || host.includes(":") && isIP2(host) !== 6) return invalid();
  const address = `http://${isIP2(host) === 6 ? `[${host}]` : host}:${Number(portText)}`;
  let url;
  try {
    url = new URL(address);
  } catch {
    return invalid();
  }
  if (!url.hostname || url.username || url.password || url.search || url.hash || url.pathname !== "/") return invalid();
  return url.origin;
}
function parseWindowsProxyServer(raw) {
  const trimmed = raw.trim();
  if (!trimmed) return { httpProxy: "", httpsProxy: "" };
  const tokens = trimmed.split(/[;\s]+/u).map((token) => token.trim()).filter(Boolean);
  if (tokens.length === 0) return { httpProxy: "", httpsProxy: "" };
  let fallback = "";
  const mapped = {};
  for (const token of tokens) {
    const separator = token.indexOf("=");
    if (separator === -1) {
      if (fallback) return fail("system-proxy-list-unsupported");
      fallback = windowsEndpoint(token);
      continue;
    }
    const key = token.slice(0, separator).toLowerCase();
    const value = token.slice(separator + 1);
    if (key === "ftp") continue;
    if (key !== "http" && key !== "https" && key !== "socks") return fail("system-proxy-list-unsupported");
    if (Object.hasOwn(mapped, key)) return fail("system-proxy-list-unsupported");
    if (key === "socks") {
      mapped.socks = windowsEndpoint(value);
      continue;
    }
    mapped[key] = windowsEndpoint(value);
  }
  const httpProxy = mapped.http ?? fallback;
  const httpsProxy = mapped.https ?? fallback;
  if (mapped.socks && (!httpProxy || !httpsProxy)) return fail("system-proxy-socks-unsupported");
  return { httpProxy, httpsProxy };
}
function parseWindowsProxyBypass(raw) {
  const tokens = raw.split(";").map((token) => token.trim()).filter(Boolean);
  if (tokens.some((token) => token.toLowerCase() === "<-loopback>" || /^<.+>$/u.test(token) && token.toLowerCase() !== "<local>")) {
    return fail("system-proxy-bypass-unsupported");
  }
  try {
    return parseProxyBypass(tokens, "system");
  } catch {
    return invalid();
  }
}
function parseWindowsIeProxyOutput(output) {
  try {
    if (!output || Buffer.byteLength(output) > MAX_SCUTIL_BYTES) return invalid();
    const parsed = JSON.parse(output);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return invalid();
    const source = parsed;
    if (source.schemaVersion !== 1 || source.ok !== true) return result("error", { error: "system-proxy-read-failed" });
    if (typeof source.autoDetect !== "boolean" || typeof source.autoConfigUrl !== "string" || typeof source.proxy !== "string" || typeof source.proxyBypass !== "string") return invalid();
    if (source.autoConfigUrl.trim()) return result("unsupported", { error: "system-proxy-pac-unsupported" });
    if (source.autoDetect) {
      if (source.autoDetectError === "error") return result("error", { error: "system-proxy-read-failed" });
      if (source.autoDetectFound === true) return result("unsupported", { error: "system-proxy-autodiscovery-unsupported" });
      if (source.autoDetectFound !== false || source.autoDetectError !== "not-found") return invalid();
    } else if (source.autoDetectFound === true || source.autoDetectError && source.autoDetectError !== "") {
      return invalid();
    }
    const { httpProxy, httpsProxy } = parseWindowsProxyServer(source.proxy);
    const bypass = parseWindowsProxyBypass(source.proxyBypass);
    return result(httpProxy || httpsProxy ? "ready" : "direct", { httpProxy, httpsProxy, bypass });
  } catch (cause) {
    const code = cause instanceof Error ? cause.message : "";
    if (WINDOWS_KNOWN.has(code)) return result(code.endsWith("unsupported") ? "unsupported" : "error", { error: code });
    return result("error", { error: "system-proxy-invalid-config" });
  }
}
var WINDOWS_IE_PROXY_SCRIPT = `
$ErrorActionPreference = 'Stop'
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class DshWinHttpProxy {
  [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
  public struct Config {
    public bool AutoDetect;
    public IntPtr AutoConfigUrl;
    public IntPtr Proxy;
    public IntPtr ProxyBypass;
  }
  [DllImport("winhttp.dll", SetLastError = true)]
  public static extern bool WinHttpGetIEProxyConfigForCurrentUser(out Config config);
  [DllImport("winhttp.dll", SetLastError = true, CharSet = CharSet.Unicode)]
  public static extern bool WinHttpDetectAutoProxyConfigUrl(int flags, out IntPtr url);
  [DllImport("kernel32.dll")]
  public static extern IntPtr GlobalFree(IntPtr hMem);
  public static string TakeString(IntPtr pointer) {
    if (pointer == IntPtr.Zero) return "";
    try { return Marshal.PtrToStringUni(pointer) ?? ""; }
    finally { GlobalFree(pointer); }
  }
}
'@
function Emit($payload) { [Console]::Out.Write(($payload | ConvertTo-Json -Compress)) }
$config = New-Object DshWinHttpProxy+Config
if (-not [DshWinHttpProxy]::WinHttpGetIEProxyConfigForCurrentUser([ref]$config)) {
  Emit @{ schemaVersion = 1; ok = $false }
  exit 1
}
$autoConfigUrl = [DshWinHttpProxy]::TakeString($config.AutoConfigUrl)
$payload = @{
  schemaVersion = 1
  ok = $true
  autoDetect = [bool]$config.AutoDetect
  autoConfigUrl = $autoConfigUrl
  proxy = [DshWinHttpProxy]::TakeString($config.Proxy)
  proxyBypass = [DshWinHttpProxy]::TakeString($config.ProxyBypass)
  autoDetectFound = $false
  autoDetectError = ''
}
if ($payload.autoDetect -and -not $autoConfigUrl) {
  $discovered = [IntPtr]::Zero
  if ([DshWinHttpProxy]::WinHttpDetectAutoProxyConfigUrl(3, [ref]$discovered)) {
    [void][DshWinHttpProxy]::TakeString($discovered)
    $payload.autoDetectFound = $true
  } else {
    $code = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
    if ($code -eq 12180) { $payload.autoDetectError = 'not-found' }
    else { $payload.autoDetectError = 'error' }
  }
}
Emit $payload
`.trim();
function windowsPowershellPath() {
  const root = process.env.SystemRoot;
  if (root && /^[A-Za-z]:[\\/][^.]/u.test(root) && !root.includes("..")) {
    return join7(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
  }
  return join7("C:\\Windows", "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
}
function windowsEncodedCommand() {
  return Buffer.from(WINDOWS_IE_PROXY_SCRIPT, "utf16le").toString("base64");
}
var runSystemCommand = (command, args) => new Promise((resolve2, reject) => {
  const timeout = command.endsWith("powershell.exe") ? 8e3 : 3e3;
  execFile(command, [...args], {
    encoding: "utf8",
    timeout,
    maxBuffer: MAX_SCUTIL_BYTES,
    windowsHide: true
  }, (error, stdout) => {
    if (error) reject(new Error("system-proxy-read-failed"));
    else resolve2(stdout);
  });
});
async function readSystemProxy(options = {}) {
  const platform = options.platform ?? process.platform;
  const runner = options.runner ?? runSystemCommand;
  try {
    if (platform === "darwin") {
      return parseScutilProxyOutput(await runner("/usr/sbin/scutil", ["--proxy"]));
    }
    if (platform === "win32") {
      return parseWindowsIeProxyOutput(await runner(windowsPowershellPath(), [
        "-NoProfile",
        "-NonInteractive",
        "-WindowStyle",
        "Hidden",
        "-EncodedCommand",
        windowsEncodedCommand()
      ]));
    }
    return result("unsupported", { error: "system-proxy-unsupported-platform" });
  } catch {
    return result("error", { error: "system-proxy-read-failed" });
  }
}

// src/model-proxy-runtime.ts
init_define_DSH_OVERLAY_META();
import { AsyncLocalStorage } from "node:async_hooks";
import { createRequire } from "node:module";

// src/model-proxy-node.ts
init_define_DSH_OVERLAY_META();
import http from "node:http";
import https from "node:https";
import http2 from "node:http2";
import { syncBuiltinESMExports } from "node:module";
import { urlToHttpOptions } from "node:url";
import { HttpProxyAgent } from "http-proxy-agent";
import { HttpsProxyAgent } from "https-proxy-agent";
var ModelProxyTransportError = class extends Error {
  code;
  constructor(code) {
    super(code);
    this.name = "ModelProxyTransportError";
    this.code = code;
  }
};
var HttpAgent = http.Agent;
var HttpsAgent = https.Agent;
var plainHttpConnection = HttpAgent.prototype.createConnection;
var plainHttpsConnection = HttpsAgent.prototype.createConnection;
var ModelProxyNodeAgents = class {
  #resolve;
  #agents = /* @__PURE__ */ new Set();
  #cache = /* @__PURE__ */ new Map();
  #noAgent = Object.freeze({});
  constructor(resolve2) {
    this.#resolve = resolve2;
  }
  agent(target, options) {
    if (options.createConnection || options.socketPath || options.socket) {
      throw new ModelProxyTransportError("model-proxy-custom-connection-unsupported");
    }
    const secure = target.protocol === "https:";
    const source = options.agent === false ? void 0 : options.agent ?? (secure ? https.globalAgent : http.globalAgent);
    const prototype = secure ? HttpsAgent.prototype : HttpAgent.prototype;
    if (source && (source.constructor !== (secure ? HttpsAgent : HttpAgent) || source.createConnection !== (secure ? plainHttpsConnection : plainHttpConnection) || ["addRequest", "getName", "createSocket", "removeSocket", "reuseSocket", "keepSocketAlive"].some((method2) => source[method2] !== prototype[method2]))) {
      throw new ModelProxyTransportError("model-proxy-custom-agent-unsupported");
    }
    const sourceOptions = source?.options ?? {};
    const route = this.#resolve(target);
    const headers = Array.isArray(options.headers) ? options.headers.reduce((entries, value, index, values) => {
      if (index % 2 === 0) entries.push([String(value), values[index + 1]]);
      return entries;
    }, []) : Object.entries(options.headers ?? {});
    if (String(options.method).toUpperCase() === "CONNECT" || headers.some(([name2]) => name2.toLowerCase() === "upgrade")) {
      throw new ModelProxyTransportError("model-proxy-upgrade-unsupported");
    }
    if (route.kind === "proxy" && !secure) {
      const host = headers.find(([name2]) => name2.toLowerCase() === "host")?.[1];
      if (host !== void 0) {
        let sameOrigin = false;
        try {
          sameOrigin = new URL(`${target.protocol}//${String(host)}`).origin === target.origin;
        } catch {
        }
        if (!sameOrigin) throw new ModelProxyTransportError("model-proxy-native-host-unsupported");
      }
    }
    const cacheKey = source ?? this.#noAgent;
    let agents = this.#cache.get(cacheKey);
    if (!agents) {
      agents = /* @__PURE__ */ new Map();
      this.#cache.set(cacheKey, agents);
    }
    const key = `${target.protocol}|${route.kind === "proxy" ? route.proxyUrl : "direct"}`;
    let agent = agents.get(key);
    if (!agent) {
      const base = { ...sourceOptions, proxyEnv: {}, ...options.agent === false ? { keepAlive: false } : {} };
      if (base.createConnection || base.socketPath || base.socket) {
        throw new ModelProxyTransportError("model-proxy-custom-connection-unsupported");
      }
      const proxyOptions = Object.fromEntries(["keepAlive", "keepAliveMsecs", "maxSockets", "maxTotalSockets", "maxFreeSockets", "scheduling", "timeout", "localAddress", "family", "lookup"].filter((name2) => name2 in base).map((name2) => [name2, base[name2]]));
      agent = route.kind === "direct" ? secure ? new HttpsAgent(base) : new HttpAgent(base) : secure ? new HttpsProxyAgent(route.proxyUrl, proxyOptions) : new HttpProxyAgent(route.proxyUrl, proxyOptions);
      agents.set(key, agent);
      this.#agents.add(agent);
    }
    for (const [name2, value] of Object.entries(sourceOptions)) {
      if (name2 !== "proxyEnv" && !(name2 in options)) options[name2] = value;
    }
    return agent;
  }
  dispose() {
    for (const agent of this.#agents) agent.destroy();
    this.#agents.clear();
    this.#cache.clear();
  }
};
function normalize(args, protocol) {
  const first = args[0];
  let options;
  let callback;
  if (typeof first === "string" || first instanceof URL) {
    const url = first instanceof URL ? first : new URL(first);
    options = { ...urlToHttpOptions(url) };
    if (typeof args[1] === "object" && args[1] !== null) {
      Object.assign(options, args[1]);
      callback = args[2];
    } else callback = args[1];
  } else if (first && typeof first === "object") {
    options = { ...first };
    callback = args[1];
  } else throw new ModelProxyTransportError("model-proxy-native-request-invalid");
  const scheme = options.protocol ?? protocol;
  if (scheme !== protocol) throw new ModelProxyTransportError("model-proxy-native-protocol-invalid");
  const hostname = String(options.hostname ?? options.host ?? "localhost");
  const host = hostname.includes(":") && !hostname.startsWith("[") ? `[${hostname}]` : hostname;
  let target;
  try {
    target = new URL(`${scheme}//${host}${options.port ? `:${options.port}` : ""}`);
  } catch {
    throw new ModelProxyTransportError("model-proxy-native-request-invalid");
  }
  if (options.path && !options.path.startsWith("/")) throw new ModelProxyTransportError("model-proxy-native-path-unsupported");
  return { options, callback, target };
}
function installModelProxyNodeGuards(current) {
  const original = { httpRequest: http.request, httpGet: http.get, httpsRequest: https.request, httpsGet: https.get, http2Connect: http2.connect };
  function request2(protocol, previous, receiver, args) {
    const scope = current();
    if (!scope) return Reflect.apply(previous, receiver, args);
    scope.assertActive();
    const { options, callback, target } = normalize(args, protocol);
    options.agent = scope.native.agent(target, options);
    delete options.proxyEnv;
    const release = scope.retain();
    try {
      const result2 = Reflect.apply(previous, receiver, callback === void 0 ? [options] : [options, callback]);
      result2.once("close", release);
      return result2;
    } catch (error) {
      release();
      throw error;
    }
  }
  const httpRequest = function(...args) {
    return request2("http:", original.httpRequest, this, args);
  };
  const httpsRequest = function(...args) {
    return request2("https:", original.httpsRequest, this, args);
  };
  const httpGet = function(...args) {
    if (!current()) return Reflect.apply(original.httpGet, this, args);
    const result2 = request2("http:", original.httpRequest, this, args);
    result2.end();
    return result2;
  };
  const httpsGet = function(...args) {
    if (!current()) return Reflect.apply(original.httpsGet, this, args);
    const result2 = request2("https:", original.httpsRequest, this, args);
    result2.end();
    return result2;
  };
  const http2Connect = function(...args) {
    if (current()) throw new ModelProxyTransportError("model-proxy-http2-unsupported");
    const session = Reflect.apply(original.http2Connect, this, args);
    const previous = session.request;
    session.request = function(...requestArgs) {
      if (current()) throw new ModelProxyTransportError("model-proxy-http2-unsupported");
      return Reflect.apply(previous, this, requestArgs);
    };
    return session;
  };
  try {
    http.request = httpRequest;
    http.get = httpGet;
    https.request = httpsRequest;
    https.get = httpsGet;
    http2.connect = http2Connect;
    syncBuiltinESMExports();
  } catch {
    http.request = original.httpRequest;
    http.get = original.httpGet;
    https.request = original.httpsRequest;
    https.get = original.httpsGet;
    http2.connect = original.http2Connect;
    syncBuiltinESMExports();
    throw new ModelProxyTransportError("model-proxy-native-install-failed");
  }
  return () => {
    if (http.request === httpRequest) http.request = original.httpRequest;
    if (http.get === httpGet) http.get = original.httpGet;
    if (https.request === httpsRequest) https.request = original.httpsRequest;
    if (https.get === httpsGet) https.get = original.httpsGet;
    if (http2.connect === http2Connect) http2.connect = original.http2Connect;
    syncBuiltinESMExports();
  };
}

// src/model-proxy-runtime.ts
var HUB = /* @__PURE__ */ Symbol.for("dsh.overlay.model-proxy-runtime.v1");
var MODERN = /* @__PURE__ */ Symbol.for("undici.globalDispatcher.2");
var LEGACY = /* @__PURE__ */ Symbol.for("undici.globalDispatcher.1");
var require2 = createRequire(import.meta.url);
var globals = globalThis;
function transparentDispatcher(previous, dispatch) {
  return new Proxy(previous, {
    get(target, property) {
      if (property === "dispatch") return dispatch;
      const value = Reflect.get(target, property, target);
      return typeof value === "function" ? value.bind(target) : value;
    }
  });
}
function ensureHub() {
  const existing = globals[HUB];
  if (existing) {
    if (globals[MODERN] !== existing.modern || globals[LEGACY] !== existing.legacy) {
      throw new ModelProxyTransportError("model-proxy-dispatcher-replaced");
    }
    return existing;
  }
  const savedLegacy = globals[LEGACY];
  const savedModern = globals[MODERN];
  const undici = require2("undici");
  const previousModern = savedModern ?? undici.getGlobalDispatcher();
  const previousLegacy = savedLegacy ?? globals[LEGACY];
  const instances = /* @__PURE__ */ new Set();
  const current = () => {
    let selected;
    for (const reference of instances) {
      const runtime = reference.deref();
      if (!runtime) {
        instances.delete(reference);
        continue;
      }
      const scope = runtime.currentScope();
      if (scope && (!selected || scope.order > selected.order)) selected = scope;
    }
    return selected;
  };
  const modern = transparentDispatcher(previousModern, (options, handler) => {
    const scope = current();
    return scope ? scope.dispatch(options, handler) : previousModern.dispatch(options, handler);
  });
  let undoNative;
  try {
    undici.setGlobalDispatcher(modern);
    const bridge = globals[LEGACY];
    const legacy = transparentDispatcher(previousLegacy, (options, handler) => {
      const scope = current();
      return scope ? bridge.dispatch(options, handler) : previousLegacy.dispatch(options, handler);
    });
    globals[LEGACY] = legacy;
    if (globals[MODERN] !== modern || globals[LEGACY] !== legacy) throw new Error();
    undoNative = installModelProxyNodeGuards(current);
    const hub = { undici, instances, modern, legacy, order: 0 };
    Object.defineProperty(globals, HUB, { value: hub, configurable: false });
    return hub;
  } catch {
    undoNative?.();
    globals[MODERN] = previousModern;
    globals[LEGACY] = previousLegacy;
    throw new ModelProxyTransportError("model-proxy-runtime-install-failed");
  }
}
function completeHandler(handler, release) {
  return new Proxy(handler, {
    get(target, property) {
      const value = Reflect.get(target, property, target);
      if (property === "onResponseEnd" || property === "onResponseError") {
        return (...args) => {
          try {
            return typeof value === "function" ? Reflect.apply(value, target, args) : void 0;
          } finally {
            release();
          }
        };
      }
      return typeof value === "function" ? value.bind(target) : value;
    }
  });
}
var ModelProxyRuntime = class {
  #hub;
  #options;
  #storage = new AsyncLocalStorage();
  #snapshots = /* @__PURE__ */ new WeakMap();
  #generations = /* @__PURE__ */ new WeakMap();
  #open = /* @__PURE__ */ new Set();
  #latest;
  #disposed = false;
  #disposePromise;
  #resolveDispose;
  #rejectDispose;
  #closeFailed = false;
  constructor(options = {}) {
    this.#options = options;
    this.#hub = ensureHub();
    this.#hub.instances.add(new WeakRef(this));
  }
  /** A stable owned copy: even accidentally mutable caller input is isolated. */
  snapshot(scope) {
    let copy = this.#snapshots.get(scope);
    if (!copy) {
      copy = createModelProxySnapshot(scope.config, scope.system);
      this.#snapshots.set(scope, copy);
      this.#snapshots.set(copy, copy);
    }
    return copy;
  }
  /** Internal hub interface; storage is instance-owned, never a shared ALS. */
  currentScope() {
    return this.#storage.getStore();
  }
  #assertInstalled() {
    if (globals[MODERN] !== this.#hub.modern || globals[LEGACY] !== this.#hub.legacy) {
      throw new ModelProxyTransportError("model-proxy-dispatcher-replaced");
    }
  }
  #generation(input) {
    if (this.#disposed) throw new ModelProxyTransportError("model-proxy-runtime-disposed");
    this.#assertInstalled();
    const snapshot = this.snapshot(input);
    let generation = this.#generations.get(snapshot);
    if (!generation || generation.closing) {
      generation = {
        snapshot,
        direct: new this.#hub.undici.Agent(this.#options.agentOptions),
        proxies: /* @__PURE__ */ new Map(),
        native: new ModelProxyNodeAgents((target) => resolveModelProxyRoute(snapshot, target)),
        references: 0,
        retired: false,
        closing: false
      };
      this.#generations.set(snapshot, generation);
      this.#open.add(generation);
    }
    if (this.#latest !== generation) {
      if (this.#latest) {
        this.#latest.retired = true;
        this.#tryClose(this.#latest);
      }
      this.#latest = generation;
    }
    generation.retired = false;
    return generation;
  }
  #retain(generation) {
    if (generation.closing) throw new ModelProxyTransportError("model-proxy-scope-closed");
    generation.references++;
    let released = false;
    return () => {
      if (released) return;
      released = true;
      generation.references--;
      this.#tryClose(generation);
    };
  }
  #acquire(input) {
    const generation = this.#generation(input);
    const finish = this.#retain(generation);
    const scope = {
      owner: this,
      generation,
      native: generation.native,
      order: ++this.#hub.order,
      released: false,
      assertActive: () => {
        if (scope.released || generation.closing) throw new ModelProxyTransportError("model-proxy-scope-closed");
        this.#assertInstalled();
      },
      retain: () => {
        scope.assertActive();
        return this.#retain(generation);
      },
      dispatch: (options, handler) => {
        scope.assertActive();
        if (options.upgrade || String(options.method).toUpperCase() === "CONNECT") {
          throw new ModelProxyTransportError("model-proxy-upgrade-unsupported");
        }
        const origin = String(options.origin);
        if (!/^https?:\/\//.test(origin)) throw new ModelProxyTransportError("model-proxy-target-unsupported");
        const route = resolveModelProxyRoute(generation.snapshot, origin);
        let agent = generation.direct;
        if (route.kind === "proxy") {
          let proxy = generation.proxies.get(route.proxyUrl);
          if (!proxy) {
            proxy = new this.#hub.undici.ProxyAgent({ ...this.#options.proxyAgentOptions, uri: route.proxyUrl });
            generation.proxies.set(route.proxyUrl, proxy);
          }
          agent = proxy;
        }
        const release = scope.retain();
        try {
          return agent.dispatch(options, completeHandler(handler, release));
        } catch (error) {
          release();
          throw error;
        }
      }
    };
    return { scope, release: () => {
      scope.released = true;
      finish();
    } };
  }
  #tryClose(generation) {
    if (!generation.retired || generation.references !== 0 || generation.closing) return;
    generation.closing = true;
    generation.native.dispose();
    void Promise.allSettled([generation.direct, ...generation.proxies.values()].map((agent) => agent.close())).then((results) => {
      if (results.some((result2) => result2.status === "rejected")) this.#closeFailed = true;
      generation.proxies.clear();
      this.#open.delete(generation);
      this.#finishDispose();
    });
  }
  #finishDispose() {
    if (!this.#disposed || this.#open.size) return;
    if (this.#closeFailed) this.#rejectDispose?.(new ModelProxyTransportError("model-proxy-dispose-failed"));
    else this.#resolveDispose?.();
  }
  #enter(scope, fn) {
    const entered = Object.create(scope);
    Object.defineProperty(entered, "order", { value: ++this.#hub.order });
    return this.#storage.run(entered, fn);
  }
  /** Only fn's execution/async descendants are scoped, never its awaiter. */
  async run(snapshot, fn) {
    const lease = this.#acquire(snapshot);
    try {
      return await this.#enter(lease.scope, fn);
    } finally {
      lease.release();
    }
  }
  /**
   * Factory and iterator operations execute in the scope. The consumer's
   * continuation (including tools invoked after yield) executes outside it.
   * Serializing operations matches async-generator next/return/throw ordering.
   */
  wrapStream(snapshot, factory) {
    const frozen = this.snapshot(snapshot);
    let lease;
    let iterator;
    let done = false;
    let queue = Promise.resolve();
    const finish = () => {
      if (!done) {
        done = true;
        lease?.release();
      }
    };
    const operation = (method2, value) => {
      const result2 = queue.then(async () => {
        if (done) {
          if (method2 === "throw") throw value;
          return { done: true, value: method2 === "return" ? value : void 0 };
        }
        if (!lease && method2 !== "next") {
          finish();
          if (method2 === "throw") throw value;
          return { done: true, value };
        }
        try {
          lease ??= this.#acquire(frozen);
          const result3 = await this.#enter(lease.scope, async () => {
            iterator ??= factory()[Symbol.asyncIterator]();
            if (method2 === "throw" && !iterator.throw) {
              await iterator.return?.();
              throw value;
            }
            if (method2 === "return" && !iterator.return) return { done: true, value };
            return await iterator[method2](value);
          });
          if (result3.done) finish();
          return result3;
        } catch (error) {
          finish();
          throw error;
        }
      });
      queue = result2.catch(() => {
      });
      return result2;
    };
    return {
      next: (value) => operation("next", value),
      return: (value) => operation("return", value),
      throw: (error) => operation("throw", error),
      [Symbol.asyncIterator]() {
        return this;
      }
    };
  }
  /** Stop new scopes; wait for active streams and response bodies, then close. */
  dispose() {
    if (this.#disposePromise) return this.#disposePromise;
    this.#disposed = true;
    this.#disposePromise = new Promise((resolve2, reject) => {
      this.#resolveDispose = resolve2;
      this.#rejectDispose = reject;
    });
    for (const generation of this.#open) {
      generation.retired = true;
      this.#tryClose(generation);
    }
    this.#finishDispose();
    return this.#disposePromise;
  }
};

// src/model-proxy-manager.ts
var PROBE_URL = "https://opencode.ai/";
var PROBE_TIMEOUT_MS = 8e3;
var SAFE_CODES = /* @__PURE__ */ new Set([
  "invalid-config",
  "invalid-mode",
  "invalid-url",
  "url-too-long",
  "unsupported-proxy-scheme",
  "invalid-bypass",
  "manual-url-required",
  "config-read-failed",
  "config-write-failed",
  "system-proxy-unsupported-platform",
  "system-proxy-read-failed",
  "system-proxy-invalid-config",
  "system-proxy-pac-unsupported",
  "system-proxy-autodiscovery-unsupported",
  "system-proxy-socks-unsupported",
  "system-proxy-scoped-unsupported",
  "system-proxy-list-unsupported",
  "system-proxy-bypass-unsupported",
  "model-proxy-disposed",
  "model-proxy-transport-unsupported",
  "model-proxy-runtime-unavailable",
  "invalid-target-url"
]);
var CODE_ALIASES = {
  "model-proxy-custom-agent-unsupported": "model-proxy-transport-unsupported",
  "model-proxy-custom-connection-unsupported": "model-proxy-transport-unsupported",
  "model-proxy-http2-unsupported": "model-proxy-transport-unsupported",
  "model-proxy-target-unsupported": "model-proxy-transport-unsupported",
  "model-proxy-runtime-disposed": "model-proxy-runtime-unavailable",
  "model-proxy-dispatcher-replaced": "model-proxy-runtime-unavailable",
  "model-proxy-runtime-install-failed": "model-proxy-runtime-unavailable",
  "model-proxy-native-install-failed": "model-proxy-runtime-unavailable",
  "model-proxy-scope-closed": "model-proxy-runtime-unavailable"
};
function modelProxyErrorCode(cause) {
  let error = cause;
  const seen = /* @__PURE__ */ new Set();
  while (error !== null && typeof error === "object" && !seen.has(error)) {
    seen.add(error);
    const record3 = error;
    for (const value of [record3.code, record3.message]) {
      if (typeof value === "string" && SAFE_CODES.has(value)) return value;
      if (typeof value === "string" && Object.hasOwn(CODE_ALIASES, value)) return CODE_ALIASES[value];
    }
    const code = typeof record3.code === "string" ? record3.code : "";
    const message = typeof record3.message === "string" ? record3.message : "";
    if (record3.statusCode === 407 || /\b407\b|proxy[^\n]*auth/i.test(message)) return "proxy-auth";
    if (code === "ECONNREFUSED") return "proxy-refused";
    if (code === "ENOTFOUND" || code === "EAI_AGAIN") return "dns";
    if (record3.name === "TimeoutError" || /TIMEOUT/.test(code) || /timed?\s*out|timeout/i.test(message)) return "timeout";
    if (/CERT|TLS|SSL/.test(code) || /certificate|\bTLS\b|\bSSL\b/i.test(message)) return "tls";
    error = record3.cause;
  }
  return "unknown";
}
var ModelProxyManager = class {
  #home;
  #runtime;
  #system;
  #probeUrl;
  #probeTimeoutMs;
  #pending = Promise.resolve();
  #cached;
  #disposed = false;
  constructor(home, options = {}) {
    this.#home = home;
    this.#runtime = options.runtime ?? new ModelProxyRuntime();
    this.#system = options.system ?? (() => readSystemProxy());
    this.#probeUrl = options.probeUrl ?? PROBE_URL;
    this.#probeTimeoutMs = options.probeTimeoutMs ?? PROBE_TIMEOUT_MS;
  }
  #assertOpen() {
    if (this.#disposed) throw new Error("model-proxy-disposed");
  }
  async #read() {
    this.#assertOpen();
    await this.#pending;
    const config = await readModelProxyConfig(this.#home);
    const system = await this.#system();
    this.#assertOpen();
    return { config, system };
  }
  async view() {
    const { config, system } = await this.#read();
    return modelProxyConfigView(config, system);
  }
  /** Refresh OS facts at a new call boundary, reusing the same immutable snapshot when unchanged. */
  async capture() {
    const { config, system } = await this.#read();
    const identity = JSON.stringify({ config, system });
    if (this.#cached?.identity === identity) return this.#cached.snapshot;
    const snapshot = createModelProxySnapshot(config, system);
    this.#cached = { identity, snapshot };
    return snapshot;
  }
  /** Serialize persistence. A failed write leaves the previous published configuration intact. */
  save(input) {
    this.#assertOpen();
    const operation = this.#pending.then(async () => {
      this.#assertOpen();
      const previous = await readModelProxyConfig(this.#home);
      const next = parseModelProxyConfig(input, previous);
      const system = await this.#system();
      createModelProxySnapshot(next, system);
      this.#assertOpen();
      const saved = await writeModelProxyConfig(this.#home, next);
      this.#cached = void 0;
      return modelProxyConfigView(saved, system);
    });
    this.#pending = operation.then(() => {
    }, () => {
    });
    return operation;
  }
  async run(operation) {
    const snapshot = await this.capture();
    return await this.#runtime.run(snapshot, operation);
  }
  /** The consumer receives chunks outside the model network scope. */
  async *stream(factory) {
    const snapshot = await this.capture();
    yield* this.#runtime.wrapStream(snapshot, factory);
  }
  /** An explicit network-only probe; it never writes settings or submits a generation request. */
  async test(input) {
    let route;
    const origin = new URL(this.#probeUrl).origin;
    try {
      const { config, system } = await this.#read();
      const proposed = parseModelProxyConfig(input, config);
      const snapshot = createModelProxySnapshot(proposed, system);
      route = resolveModelProxyRoute(snapshot, this.#probeUrl).kind;
      return await this.#runtime.run(snapshot, async () => {
        const response = await fetch(this.#probeUrl, {
          method: "HEAD",
          redirect: "manual",
          signal: AbortSignal.timeout(this.#probeTimeoutMs),
          headers: { accept: "text/html, application/json" }
        });
        await response.body?.cancel();
        if (response.status === 407) return { ok: false, error: "proxy-auth", route, origin };
        if (response.status >= 500) return { ok: false, error: "request-failed", route, origin };
        return { ok: true, error: null, route, origin };
      });
    } catch (cause) {
      return { ok: false, error: modelProxyErrorCode(cause), ...route ? { route } : {}, origin };
    }
  }
  async dispose() {
    if (this.#disposed) return;
    this.#disposed = true;
    await this.#pending;
    await this.#runtime.dispose();
    this.#cached = void 0;
  }
};

// src/oauth.ts
function emptyView(provider, phase = "idle") {
  return { phase, provider, message: null, url: null, code: null, prompt: null, error: null };
}
function authorizationOf(ctx) {
  const bag = ctx;
  const value = typeof bag.get === "function" ? bag.get("authorization") : void 0;
  if (value === null || typeof value !== "object") return void 0;
  const auth = value;
  if (typeof auth.begin !== "function" || typeof auth.cancel !== "function") return void 0;
  return auth;
}
function parseProvider(body, query) {
  const raw = typeof body.provider === "string" ? body.provider : query;
  if (typeof raw !== "string" || !isOauthLoginProvider(raw)) return null;
  return raw;
}
function applyOverlayOauth(ctx, modelNetwork) {
  const sessions = /* @__PURE__ */ new Map();
  const reject = (req, res) => rejectUnauthorized(req, res, ctx.connection.requestRejection(req));
  const sessionOf = (provider) => {
    const existing = sessions.get(provider);
    if (existing !== void 0) return existing;
    const created = {
      view: emptyView(provider),
      controller: null,
      answer: null,
      decline: null
    };
    sessions.set(provider, created);
    return created;
  };
  const guard = (req, res, mutating) => {
    if (reject(req, res)) return true;
    if (mutating && !sameOriginMutatingRequest(req)) {
      res.writeHead(403, { "cache-control": "no-store" });
      res.end("forbidden");
      return true;
    }
    return false;
  };
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: "/api/desktop/oauth/status",
    handler: (req, res) => {
      if (guard(req, res, false)) return;
      if (req.method !== "GET") {
        res.writeHead(405, { allow: "GET", "cache-control": "no-store" });
        res.end("method not allowed");
        return;
      }
      const url = new URL(req.url ?? "", "http://127.0.0.1");
      const provider = parseProvider({}, url.searchParams.get("provider"));
      if (provider === null) {
        writeJson(res, 400, emptyView("", "failed"));
        return;
      }
      if (authorizationOf(ctx) === void 0) {
        writeJson(res, 200, emptyView(provider, "unavailable"));
        return;
      }
      const session = sessionOf(provider);
      const phase = session.view.phase;
      if (phase === "idle" || phase === "cancelled" || phase === "failed") {
        void readOAuthCredential(resolveDshHome(), provider).then((credential) => {
          if (credential !== null && session.view.phase === phase) {
            session.view = emptyView(provider, "authorized");
          }
          writeJson(res, 200, session.view);
        });
        return;
      }
      writeJson(res, 200, session.view);
    }
  }), "dsh-overlay-custom: oauth status");
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: "/api/desktop/oauth/begin",
    handler: (req, res) => {
      if (guard(req, res, true)) return;
      if (req.method !== "POST") {
        res.writeHead(405, { allow: "POST", "cache-control": "no-store" });
        res.end("method not allowed");
        return;
      }
      void (async () => {
        const body = await readJsonBody(req).catch(() => ({}));
        const provider = parseProvider(body, null);
        if (provider === null) {
          writeJson(res, 400, { ok: false, error: "unknown-provider" });
          return;
        }
        const auth = authorizationOf(ctx);
        if (auth === void 0) {
          writeJson(res, 200, emptyView(provider, "unavailable"));
          return;
        }
        const session = sessionOf(provider);
        if (session.view.phase === "running" || session.view.phase === "need-prompt") {
          writeJson(res, 200, session.view);
          return;
        }
        session.controller?.abort();
        const controller = new AbortController();
        session.controller = controller;
        session.answer = null;
        session.decline = null;
        session.view = {
          ...emptyView(provider, "running"),
          message: "starting"
        };
        writeJson(res, 202, session.view);
        void modelNetwork.run(() => auth.begin({
          key: oauthCredentialKey(provider),
          method: "oauth",
          signal: controller.signal,
          interaction: {
            notify: (notice) => {
              session.view = {
                ...session.view,
                phase: "running",
                message: typeof notice.message === "string" ? notice.message : session.view.message,
                url: typeof notice.url === "string" ? notice.url : session.view.url,
                code: typeof notice.code === "string" ? notice.code : session.view.code,
                error: null
              };
            },
            prompt: (prompt) => {
              const options = Array.isArray(prompt.options) ? prompt.options : [];
              const oauthOption = options.find((option) => option.id === "oauth" || option.id === "chatgpt");
              if (oauthOption !== void 0) return Promise.resolve(oauthOption.id);
              return new Promise((resolve2, reject2) => {
                session.answer = resolve2;
                session.decline = () => {
                  reject2(new Error("declined"));
                };
                session.view = {
                  ...session.view,
                  phase: "need-prompt",
                  message: typeof prompt.message === "string" ? prompt.message : session.view.message,
                  prompt: {
                    message: typeof prompt.message === "string" ? prompt.message : "",
                    placeholder: typeof prompt.placeholder === "string" ? prompt.placeholder : null,
                    options: options.map((option) => ({ id: option.id, label: option.label }))
                  }
                };
              });
            }
          }
        })).then((outcome) => {
          const status = outcome.status === "authorized" || outcome.status === "cancelled" ? outcome.status : "failed";
          session.view = {
            ...session.view,
            phase: status,
            prompt: null,
            error: status === "failed" ? outcome.status ?? "failed" : null
          };
          session.controller = null;
          session.answer = null;
          session.decline = null;
        }, (cause) => {
          const message = cause instanceof Error ? cause.message : String(cause);
          session.view = {
            ...emptyView(provider, message.includes("no authorization flow") ? "unavailable" : "failed"),
            error: message.includes("no authorization flow") ? "unavailable" : modelProxyErrorCode(cause)
          };
          session.controller = null;
          session.answer = null;
          session.decline = null;
        });
      })();
    }
  }), "dsh-overlay-custom: oauth begin");
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: "/api/desktop/oauth/answer",
    handler: (req, res) => {
      if (guard(req, res, true)) return;
      if (req.method !== "POST") {
        res.writeHead(405, { allow: "POST", "cache-control": "no-store" });
        res.end("method not allowed");
        return;
      }
      void (async () => {
        const body = await readJsonBody(req).catch(() => ({}));
        const provider = parseProvider(body, null);
        if (provider === null) {
          writeJson(res, 400, { ok: false });
          return;
        }
        const session = sessionOf(provider);
        if (body.decline === true) {
          session.decline?.();
          session.controller?.abort();
          session.view = emptyView(provider, "cancelled");
          writeJson(res, 200, session.view);
          return;
        }
        const text = typeof body.text === "string" ? body.text : "";
        if (session.answer === null) {
          writeJson(res, 409, session.view);
          return;
        }
        session.answer(text);
        session.answer = null;
        session.decline = null;
        session.view = { ...session.view, phase: "running", prompt: null };
        writeJson(res, 200, session.view);
      })();
    }
  }), "dsh-overlay-custom: oauth answer");
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: "/api/desktop/oauth/cancel",
    handler: (req, res) => {
      if (guard(req, res, true)) return;
      if (req.method !== "POST") {
        res.writeHead(405, { allow: "POST", "cache-control": "no-store" });
        res.end("method not allowed");
        return;
      }
      void (async () => {
        const body = await readJsonBody(req).catch(() => ({}));
        const provider = parseProvider(body, null);
        if (provider === null) {
          writeJson(res, 400, { ok: false });
          return;
        }
        const auth = authorizationOf(ctx);
        const key = oauthCredentialKey(provider);
        try {
          auth?.cancel(key);
        } catch {
        }
        sessionOf(provider).controller?.abort();
        sessionOf(provider).view = emptyView(provider, "cancelled");
        writeJson(res, 200, sessionOf(provider).view);
      })();
    }
  }), "dsh-overlay-custom: oauth cancel");
}

// src/proxy-api.ts
init_define_DSH_OVERLAY_META();
function applyOverlayProxyApi(ctx) {
  const manager = new ModelProxyManager(resolveDshHome());
  const reject = (req, res) => rejectUnauthorized(req, res, ctx.connection.requestRejection(req));
  ctx.effect(() => () => {
    void manager.dispose().catch(() => {
      ctx.logger.warn("dsh-overlay-custom: model proxy cleanup failed");
    });
  }, "dsh-overlay-custom: model network lifecycle");
  ctx.on("llm/stream", (_options, next) => manager.stream(next));
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: "/api/desktop/proxy/config",
    handler: (req, res) => {
      if (reject(req, res)) return;
      if (req.method === "GET") {
        void manager.view().then((view) => {
          writeJson(res, 200, { ok: true, ...view });
        }).catch((cause) => {
          writeJson(res, 500, { ok: false, error: modelProxyErrorCode(cause) });
        });
        return;
      }
      if (req.method !== "POST") {
        res.writeHead(405, { allow: "GET, POST", "cache-control": "no-store" });
        res.end("method not allowed");
        return;
      }
      if (!sameOriginMutatingRequest(req)) {
        res.writeHead(403, { "cache-control": "no-store" });
        res.end("forbidden");
        return;
      }
      void (async () => {
        let body;
        try {
          body = await readJsonBody(req);
        } catch {
          writeJson(res, 400, { ok: false, error: "invalid-config" });
          return;
        }
        try {
          const view = await manager.save(body);
          writeJson(res, 200, { ok: true, ...view });
        } catch (cause) {
          writeJson(res, 400, { ok: false, error: modelProxyErrorCode(cause) });
        }
      })();
    }
  }), "dsh-overlay-custom: proxy config");
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: "/api/desktop/proxy/test",
    handler: (req, res) => {
      if (reject(req, res)) return;
      if (req.method !== "POST") {
        res.writeHead(405, { allow: "POST", "cache-control": "no-store" });
        res.end("method not allowed");
        return;
      }
      if (!sameOriginMutatingRequest(req)) {
        res.writeHead(403, { "cache-control": "no-store" });
        res.end("forbidden");
        return;
      }
      void (async () => {
        let body;
        try {
          body = await readJsonBody(req);
        } catch {
          writeJson(res, 400, { ok: false, error: "invalid-config" });
          return;
        }
        try {
          writeJson(res, 200, await manager.test(body));
        } catch (cause) {
          writeJson(res, 500, { ok: false, error: modelProxyErrorCode(cause) });
        }
      })();
    }
  }), "dsh-overlay-custom: proxy test");
  return manager;
}

// src/relay.ts
init_define_DSH_OVERLAY_META();
import { randomBytes } from "node:crypto";
import { createRequire as createRequire2 } from "node:module";
import { readFile as readFile6 } from "node:fs/promises";
import { dirname as dirname3, join as join8, normalize as normalize2, sep } from "node:path";
import { fileURLToPath } from "node:url";

// src/mobile-api.ts
init_define_DSH_OVERLAY_META();
import { randomUUID as randomUUID2 } from "node:crypto";

// src/relay-tunnel.ts
init_define_DSH_OVERLAY_META();
import { request as requestHttp } from "node:http";
import { connect as connectTcp } from "node:net";
import WebSocket from "ws";
var DESKTOP_RENDERER_ACCESS_HEADER = "x-dsh-desktop-renderer";
var RELAY_REQUEST_HEADER = "x-dsh-overlay-relay";
var RELAY_REQUEST_VALUE = "1";
var CONNECT_TIMEOUT_MS = 15e3;
var REQUEST_TIMEOUT_MS3 = 3e4;
var RECONNECT_BASE_MS = 1e3;
var RECONNECT_MAX_MS = 3e4;
var MAX_FRAME_BYTES = 16 * 1024 * 1024;
var HOP_BY_HOP_HEADERS = /* @__PURE__ */ new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "proxy-connection",
  "te",
  "trailers",
  "transfer-encoding"
]);
function isForwardingIdentityHeader(name2) {
  const lower = name2.toLowerCase();
  return lower === DESKTOP_RENDERER_ACCESS_HEADER.toLowerCase() || lower === RELAY_REQUEST_HEADER || lower === "forwarded" || lower.startsWith("x-forwarded-");
}
function headerValue(value) {
  if (typeof value === "string") return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (Array.isArray(value)) {
    const parts = value.filter((item) => typeof item === "string");
    return parts.length === 0 ? null : parts.join(", ");
  }
  return null;
}
function sanitizedTunnelHeaders(headers, options) {
  const stripHopByHop = options?.hopByHop === true;
  const result2 = [];
  for (const [name2, raw] of headers) {
    const lower = name2.toLowerCase();
    if (isForwardingIdentityHeader(name2)) continue;
    if (stripHopByHop && HOP_BY_HOP_HEADERS.has(lower)) continue;
    const value = headerValue(raw);
    if (value === null) continue;
    result2.push([name2, value]);
  }
  return result2;
}
function rawTunnelRequest(method2, path, headers) {
  const lines = [`${method2} ${path} HTTP/1.1`];
  for (const [name2, value] of headers) lines.push(`${name2}: ${value}`);
  lines.push("", "");
  return lines.join("\r\n");
}
var RemoteRelayTunnel = class {
  options;
  requests = /* @__PURE__ */ new Map();
  upgrades = /* @__PURE__ */ new Map();
  socket = null;
  state = "connecting";
  stateError = null;
  disposed = false;
  reconnectTimer;
  reconnectAttempts = 0;
  constructor(options) {
    if (!/^wss?:\/\//u.test(options.relayUrl) || options.relayUrl.includes("\0")) {
      throw new Error(`dsh-overlay-custom: remote relay URL must be a ws:// or wss:// endpoint: ${JSON.stringify(options.relayUrl)}`);
    }
    if (options.pairId.length === 0 || options.secret.length === 0 || options.targetPort <= 0) {
      throw new Error("dsh-overlay-custom: remote relay pairing requires an id, a secret, and a target port");
    }
    this.options = options;
  }
  snapshot() {
    return { state: this.state, error: this.stateError };
  }
  start() {
    if (this.disposed) throw new Error("dsh-overlay-custom: remote relay tunnel already disposed");
    this.connect();
  }
  dispose() {
    this.disposed = true;
    if (this.reconnectTimer !== void 0) clearTimeout(this.reconnectTimer);
    for (const [, entry] of this.requests) entry.outgoing.destroy();
    this.requests.clear();
    for (const [, entry] of this.upgrades) entry.upstream.destroy();
    this.upgrades.clear();
    this.socket?.close(1e3, "desktop stopped the tunnel");
    this.socket = null;
  }
  connect() {
    this.state = "connecting";
    this.stateError = null;
    let socket;
    try {
      socket = new WebSocket(this.options.relayUrl, {
        handshakeTimeout: CONNECT_TIMEOUT_MS,
        perMessageDeflate: false,
        maxPayload: MAX_FRAME_BYTES
      });
    } catch (cause) {
      this.scheduleReconnect(cause instanceof Error ? cause.message : String(cause));
      return;
    }
    this.socket = socket;
    socket.on("open", () => {
      this.reconnectAttempts = 0;
      socket.send(JSON.stringify({ t: "hello", v: 1, pair: this.options.pairId, secret: this.options.secret }));
    });
    socket.on("message", (data) => {
      let frame;
      try {
        frame = JSON.parse(String(data));
      } catch {
        return;
      }
      this.handleFrame(frame).catch((cause) => {
        this.options.log?.(`relay frame failed: ${cause instanceof Error ? cause.message : String(cause)}`);
      });
    });
    socket.on("close", () => {
      if (this.socket === socket) this.socket = null;
      this.failPending("desktop tunnel disconnected");
      if (!this.disposed) this.scheduleReconnect("relay connection closed");
    });
    socket.on("error", () => {
    });
  }
  scheduleReconnect(reason) {
    if (this.disposed || this.reconnectTimer !== void 0) return;
    this.state = "failed";
    this.stateError = reason;
    const delay = Math.min(RECONNECT_BASE_MS * 2 ** this.reconnectAttempts, RECONNECT_MAX_MS);
    this.reconnectAttempts += 1;
    this.options.log?.(`relay disconnected (${reason}); reconnecting in ${String(delay)}ms`);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = void 0;
      if (!this.disposed) this.connect();
    }, delay);
  }
  send(frame) {
    if (this.socket?.readyState !== WebSocket.OPEN) return;
    try {
      this.socket.send(JSON.stringify(frame));
    } catch (cause) {
      this.options.log?.(`relay send failed: ${cause instanceof Error ? cause.message : String(cause)}`);
    }
  }
  failPending(reason) {
    for (const [id, entry] of this.requests) {
      entry.outgoing.destroy();
      this.requests.delete(id);
      this.send({ t: "fail", id, status: 502, reason });
    }
    for (const [id, entry] of this.upgrades) {
      entry.upstream.destroy();
      this.upgrades.delete(id);
      this.send({ t: "upfail", id, status: 502 });
    }
  }
  async handleFrame(frame) {
    if (frame.t === "ready") {
      this.state = "ready";
      this.stateError = null;
      return;
    }
    const id = typeof frame.id === "string" ? frame.id : "";
    if (id === "") return;
    if (frame.t === "req") {
      this.forwardRequest(id, frame);
      return;
    }
    if (frame.t === "body") {
      this.requests.get(id)?.outgoing.write(Buffer.from(String(frame.d ?? ""), "base64"));
      return;
    }
    if (frame.t === "end") {
      this.requests.get(id)?.outgoing.end();
      return;
    }
    if (frame.t === "abort") {
      const entry = this.requests.get(id);
      if (entry !== void 0) {
        this.requests.delete(id);
        entry.outgoing.destroy();
      }
      return;
    }
    if (frame.t === "upg") {
      this.forwardUpgrade(id, frame);
      return;
    }
    if (frame.t === "ud") {
      this.upgrades.get(id)?.upstream.write(Buffer.from(String(frame.d ?? ""), "base64"));
      return;
    }
    if (frame.t === "uc") {
      const entry = this.upgrades.get(id);
      if (entry !== void 0) {
        this.upgrades.delete(id);
        entry.upstream.destroy();
      }
    }
  }
  frameHeaders(frame, hopByHop = false) {
    const raw = Array.isArray(frame.headers) ? frame.headers : [];
    const headers = [];
    for (const entry of raw) {
      if (!Array.isArray(entry) || typeof entry[0] !== "string") continue;
      headers.push([entry[0], entry[1]]);
    }
    return sanitizedTunnelHeaders(headers, { hopByHop });
  }
  forwardRequest(id, frame) {
    const method2 = typeof frame.method === "string" && frame.method !== "" ? frame.method : "GET";
    const path = typeof frame.path === "string" && frame.path !== "" ? frame.path : "/";
    let outgoing;
    try {
      outgoing = requestHttp({
        host: "127.0.0.1",
        port: this.options.targetPort,
        method: method2,
        path,
        headers: {
          ...Object.fromEntries(this.frameHeaders(frame, true)),
          [RELAY_REQUEST_HEADER]: RELAY_REQUEST_VALUE
        },
        timeout: REQUEST_TIMEOUT_MS3
      }, (response) => {
        const headers = [];
        for (const [name2, value] of Object.entries(response.headers)) {
          if (value === void 0) continue;
          for (const item of Array.isArray(value) ? value : [value]) {
            headers.push([name2, String(item)]);
          }
        }
        this.send({ t: "res", id, status: response.statusCode ?? 502, headers });
        response.on("data", (chunk) => this.send({ t: "body", id, d: chunk.toString("base64") }));
        response.on("end", () => {
          this.requests.delete(id);
          this.send({ t: "fin", id });
        });
        response.on("error", () => {
          this.requests.delete(id);
          this.send({ t: "fail", id, status: 502 });
        });
      });
    } catch (cause) {
      this.options.log?.(`relay forward failed: ${cause instanceof Error ? cause.message : String(cause)}`);
      this.send({ t: "fail", id, status: 502 });
      return;
    }
    outgoing.on("timeout", () => outgoing.destroy());
    outgoing.on("error", () => {
      if (this.requests.delete(id)) this.send({ t: "fail", id, status: 502 });
    });
    this.requests.set(id, { outgoing });
  }
  forwardUpgrade(id, frame) {
    const method2 = typeof frame.method === "string" && frame.method !== "" ? frame.method : "GET";
    const path = typeof frame.path === "string" && frame.path !== "" ? frame.path : "/";
    const headers = this.frameHeaders(frame);
    headers.push([RELAY_REQUEST_HEADER, RELAY_REQUEST_VALUE]);
    const upstream = connectTcp(this.options.targetPort, "127.0.0.1");
    upstream.setTimeout(CONNECT_TIMEOUT_MS, () => upstream.destroy());
    let head = Buffer.alloc(0);
    let answered = false;
    const onUpstreamData = (chunk) => {
      if (!answered) {
        head = Buffer.concat([head, chunk]);
        const marker = head.indexOf("\r\n\r\n");
        if (marker === -1) return;
        answered = true;
        const headText = head.subarray(0, marker).toString("latin1");
        const [statusLine = "", ...headerLines] = headText.split("\r\n");
        const status = Number.parseInt(statusLine.split(" ")[1] ?? "", 10);
        const responseHeaders = [];
        for (const line of headerLines) {
          const colon = line.indexOf(":");
          if (colon === -1) continue;
          responseHeaders.push([line.slice(0, colon).trim(), line.slice(colon + 1).trim()]);
        }
        upstream.off("data", onUpstreamData);
        if (status === 101) {
          this.upgrades.set(id, { upstream });
          upstream.setTimeout(0);
          upstream.on("data", (tail) => this.send({ t: "ud", id, d: tail.toString("base64") }));
          this.send({ t: "upok", id, headers: responseHeaders });
          const rest = head.subarray(marker + 4);
          if (rest.length > 0) this.send({ t: "ud", id, d: rest.toString("base64") });
        } else {
          this.send({ t: "upfail", id, status: Number.isNaN(status) ? 502 : status });
          upstream.destroy();
        }
        return;
      }
      this.send({ t: "ud", id, d: chunk.toString("base64") });
    };
    upstream.on("data", onUpstreamData);
    upstream.on("error", () => {
      if (!answered) {
        answered = true;
        this.send({ t: "upfail", id, status: 502 });
      }
      this.upgrades.delete(id);
    });
    upstream.on("connect", () => {
      upstream.write(rawTunnelRequest(method2, path, headers));
    });
  }
};

// src/mobile-api.ts
var MAX_BODY_BYTES2 = 64 * 1024;
var MAX_PROMPT_BODY_BYTES = 12 * 1024 * 1024;
var MAX_TRANSCRIPT_ITEMS = 120;
var MAX_TEXT_CHARS = 2e3;
var MAX_TOOL_TITLE_CHARS = 200;
var MAX_PATH_CHARS = 160;
var MAX_DIFFS_PER_CALL = 8;
var MAX_DIFF_LINES_PER_SIDE = 200;
var IMAGE_MEDIA_TYPES = /* @__PURE__ */ new Set(["image/png", "image/jpeg", "image/webp", "image/gif"]);
var MAX_IMAGES_PER_PROMPT = 4;
var CLIENT_TAKEOVER_MS = 15e3;
function freshSignal(ms) {
  return AbortSignal.timeout(ms);
}
function mobilePromptParts(text) {
  return [{ type: "text", text }];
}
function mobileImageParts(value) {
  if (value === void 0 || value === null) return [];
  if (!Array.isArray(value) || value.length > MAX_IMAGES_PER_PROMPT) return null;
  const parts = [];
  for (const row of value) {
    const item = asRecord5(row);
    if (item === null) return null;
    if (typeof item.mediaType !== "string" || !IMAGE_MEDIA_TYPES.has(item.mediaType)) return null;
    if (typeof item.data !== "string" || item.data === "") return null;
    const name2 = typeof item.name === "string" && item.name !== "" ? item.name.slice(0, 200) : void 0;
    parts.push(name2 === void 0 ? { type: "image", mediaType: item.mediaType, data: item.data } : { type: "image", mediaType: item.mediaType, data: item.data, name: name2 });
  }
  return parts;
}
function mobilePromptContent(text, images) {
  return [...mobilePromptParts(text), ...images];
}
function textFromPromptContent(content) {
  if (typeof content === "string") return content.trim();
  if (!Array.isArray(content)) return "";
  const parts = [];
  for (const part of content) {
    if (typeof part === "string") {
      if (part !== "") parts.push(part);
      continue;
    }
    if (part === null || typeof part !== "object" || Array.isArray(part)) continue;
    const row = part;
    if (row.type === "text" && typeof row.text === "string" && row.text !== "") parts.push(row.text);
  }
  return parts.join("").trim();
}
function asRecord5(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  return value;
}
function asWireEvent(record3) {
  const row = asRecord5(record3);
  if (row === null) return null;
  if (row.type === "event") {
    const event = asRecord5(row.event);
    if (event === null || typeof event.type !== "string") return null;
    return {
      type: event.type,
      time: typeof event.time === "number" ? event.time : 0,
      data: event.data
    };
  }
  if (typeof row.type === "string" && row.type !== "chunks") {
    return {
      type: row.type,
      time: typeof row.time === "number" ? row.time : 0,
      data: row.data
    };
  }
  return null;
}
function countImageParts(content) {
  if (!Array.isArray(content)) return 0;
  let count = 0;
  for (const part of content) {
    const block = asRecord5(part);
    if (block?.type === "image") count += 1;
  }
  return count;
}
function clipText(value, max) {
  return value.length <= max ? value : `${value.slice(0, max)}\u2026`;
}
function diffLineCounts(oldText, newText) {
  const oldLines = oldText === null ? [] : oldText.split("\n");
  const newLines = newText.split("\n");
  if (oldLines.length > MAX_DIFF_LINES_PER_SIDE || newLines.length > MAX_DIFF_LINES_PER_SIDE) {
    return { added: null, removed: null };
  }
  let start = 0;
  while (start < oldLines.length && start < newLines.length && oldLines[start] === newLines[start]) start += 1;
  let oldEnd = oldLines.length;
  let newEnd = newLines.length;
  while (oldEnd > start && newEnd > start && oldLines[oldEnd - 1] === newLines[newEnd - 1]) {
    oldEnd -= 1;
    newEnd -= 1;
  }
  const a = oldLines.slice(start, oldEnd);
  const b = newLines.slice(start, newEnd);
  const rows = a.length + 1;
  const cols = b.length + 1;
  if (rows * cols > 16e4) return { added: null, removed: null };
  const table = new Uint32Array(rows * cols);
  const at = (i, j) => table[i * cols + j] ?? 0;
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i * cols + j] = a[i] === b[j] ? at(i + 1, j + 1) + 1 : Math.max(at(i + 1, j), at(i, j + 1));
    }
  }
  const common = at(0, 0);
  return { added: b.length - common, removed: a.length - common };
}
function mobileDiffSummaries(meta) {
  const diffs = asRecord5(meta)?.diffs;
  if (!Array.isArray(diffs) || diffs.length === 0) return null;
  const summaries = [];
  for (const row of diffs) {
    if (summaries.length >= MAX_DIFFS_PER_CALL) break;
    const diff = asRecord5(row);
    if (diff === null || typeof diff.path !== "string" || typeof diff.newText !== "string") continue;
    const counts = diffLineCounts(typeof diff.oldText === "string" ? diff.oldText : null, diff.newText);
    summaries.push({
      path: clipText(diff.path, MAX_PATH_CHARS),
      added: counts.added,
      removed: counts.removed
    });
  }
  return summaries.length > 0 ? summaries : null;
}
function mobileToolTitle(name2, argsRaw) {
  if (typeof argsRaw === "string" && argsRaw !== "") {
    try {
      const args = asRecord5(JSON.parse(argsRaw));
      if (args !== null) {
        for (const key of ["command", "cmd", "file_path", "filePath", "path", "pattern", "url", "query", "name"]) {
          const value = args[key];
          if (typeof value === "string" && value !== "") {
            const tail = key === "path" || key === "file_path" || key === "filePath" ? value.split(/[\\/]/u).filter((part) => part !== "").slice(-2).join("/") : value;
            return clipText(tail.replace(/\s+/gu, " ").trim(), MAX_TOOL_TITLE_CHARS);
          }
        }
      }
    } catch {
    }
  }
  return clipText(name2, MAX_TOOL_TITLE_CHARS);
}
function mobileTodoItems(value) {
  if (!Array.isArray(value)) return null;
  const todos = [];
  for (const row of value) {
    const item = asRecord5(row);
    if (item === null || typeof item.content !== "string" || item.content === "") continue;
    const status = item.status === "completed" || item.status === "in_progress" ? item.status : "pending";
    todos.push({ content: clipText(item.content, 400), status });
    if (todos.length >= 32) break;
  }
  return todos.length > 0 ? todos : null;
}
function mobileTranscriptFromEvents(events) {
  const items = [];
  let lastTodoJson = "";
  for (const record3 of events) {
    const event = asWireEvent(record3);
    if (event === null) continue;
    const data = asRecord5(event.data);
    if (event.type === "user/message") {
      const source = asRecord5(data?.source);
      const kind = source?.kind;
      if (kind !== void 0 && kind !== "user") continue;
      const text = textFromPromptContent(data?.content);
      const imageCount = countImageParts(data?.content);
      if (text !== "" || imageCount > 0) {
        items.push({ kind: "user", text: clipText(text, MAX_TEXT_CHARS), time: event.time, imageCount });
      }
      continue;
    }
    if (event.type === "assistant/message") {
      const message = asRecord5(data?.message);
      const content = message?.content ?? data?.content;
      if (Array.isArray(content)) {
        const textParts = [];
        for (const part of content) {
          const block = asRecord5(part);
          if (block === null) continue;
          if (block.type === "reasoning" && typeof block.text === "string" && block.text.trim() !== "") {
            items.push({ kind: "reasoning", text: clipText(block.text.trim(), MAX_TEXT_CHARS), time: event.time });
          } else if (block.type === "text" && typeof block.text === "string") {
            textParts.push(block.text);
          }
        }
        const text = textParts.join("").trim();
        if (text !== "") items.push({ kind: "assistant", text: clipText(text, MAX_TEXT_CHARS), time: event.time });
      } else {
        const text = textFromPromptContent(content);
        if (text !== "") items.push({ kind: "assistant", text: clipText(text, MAX_TEXT_CHARS), time: event.time });
      }
      continue;
    }
    if (event.type === "tool/call") {
      const callId = typeof data?.callId === "string" ? data.callId : "";
      const name2 = typeof data?.name === "string" ? data.name : "tool";
      if (callId === "") continue;
      items.push({
        kind: "tool",
        callId,
        name: name2,
        title: mobileToolTitle(name2, data?.arguments),
        time: event.time,
        end: null,
        status: "running",
        diffs: null
      });
      continue;
    }
    if (event.type === "tool/result") {
      const message = asRecord5(data?.message);
      const source = asRecord5(message?.source);
      let callId = typeof source?.callId === "string" ? source.callId : "";
      if (callId === "" && Array.isArray(message?.content)) {
        for (const part of message.content) {
          const block = asRecord5(part);
          if (typeof block?.toolCallId === "string") {
            callId = block.toolCallId;
            break;
          }
        }
      }
      if (callId === "" && typeof data?.callId === "string") callId = data.callId;
      if (callId === "") continue;
      for (let index = items.length - 1; index >= 0; index -= 1) {
        const item = items[index];
        if (item === void 0 || item.kind !== "tool" || item.callId !== callId) continue;
        items[index] = {
          ...item,
          end: event.time,
          status: data?.error !== void 0 && data?.error !== null ? "error" : "ok",
          diffs: mobileDiffSummaries(data?.meta)
        };
        break;
      }
      continue;
    }
    if (event.type === "todo/write") {
      const todos = mobileTodoItems(data?.todos);
      if (todos === null) continue;
      const json2 = JSON.stringify(todos);
      if (json2 === lastTodoJson) continue;
      lastTodoJson = json2;
      items.push({ kind: "todo", todos, time: event.time });
    }
  }
  return items.length > MAX_TRANSCRIPT_ITEMS ? items.slice(-MAX_TRANSCRIPT_ITEMS) : items;
}
function mobileFactsFromEvents(events) {
  let model = null;
  let permissionPreset = null;
  for (const record3 of events) {
    const event = asWireEvent(record3);
    if (event === null) continue;
    const data = asRecord5(event.data);
    if (event.type === "model/selection" || event.type === "request/context") {
      const provider = data !== null && typeof data.provider === "string" ? data.provider : "";
      const name2 = data !== null && typeof data.model === "string" ? data.model : "";
      if (provider !== "" && name2 !== "") {
        model = data !== null && typeof data.reasoningEffort === "string" && data.reasoningEffort !== "" ? { provider, model: name2, reasoningEffort: data.reasoningEffort } : { provider, model: name2 };
      }
      continue;
    }
    if (event.type === "permission/preset" && data !== null && typeof data.preset === "string" && data.preset !== "") {
      permissionPreset = data.preset;
    }
  }
  return { model, permissionPreset };
}
function sessionController(ctx) {
  const value = ctx.get("sessionController");
  if (value === void 0 || value === null || typeof value !== "object") return void 0;
  return value;
}
function workspaceFeed(ctx) {
  const controller = ctx.get("workspaceController");
  if (controller === void 0 || controller === null || typeof controller !== "object") return void 0;
  return controller.feed;
}
function workspaceName(path, title) {
  if (title !== "") return title;
  const parts = path.split(/[\\/]/u).filter((part) => part !== "");
  return parts.at(-1) ?? path;
}
function titleFromProjections(projections) {
  const root = asRecord5(projections);
  if (root === null) return void 0;
  const values = asRecord5(root.values) ?? root;
  if (typeof values.title === "string") return values.title;
  const nested = asRecord5(values.title);
  if (nested !== null && typeof nested.title === "string") return nested.title;
  if (nested !== null && typeof nested.value === "string") return nested.value;
  if (typeof values.displayTitle === "string") return values.displayTitle;
  return void 0;
}
function displayTitleOf(title, cwd, id) {
  if (typeof title === "string" && title !== "") return title;
  if (cwd !== null && cwd !== "") {
    const parts = cwd.split(/[\\/]/u).filter((part) => part !== "");
    const base = parts.at(-1);
    if (base !== void 0 && base !== "") return base;
  }
  return id === "" ? null : id.slice(0, 8);
}
function mobileSessionRow(summary) {
  const id = typeof summary.sessionId === "string" ? summary.sessionId : "";
  const cwd = typeof summary.cwd === "string" ? summary.cwd : null;
  return {
    id,
    title: displayTitleOf(titleFromProjections(summary.projections), cwd, id),
    running: summary.running === true,
    updatedAt: typeof summary.updatedAt === "number" ? summary.updatedAt : 0,
    cwd
  };
}
function mobilePermissionsOf(value) {
  const root = asRecord5(value);
  if (root === null || typeof root.currentValue !== "string") return null;
  if (!Array.isArray(root.options)) return null;
  const options = [];
  for (const row of root.options) {
    const option = asRecord5(row);
    if (option === null || typeof option.value !== "string" || typeof option.name !== "string") continue;
    const normalized = { value: option.value, name: option.name };
    if (typeof option.description === "string") normalized.description = clipText(option.description, 200);
    options.push(normalized);
  }
  if (options.length === 0) return null;
  return { options, currentValue: root.currentValue };
}
function mobileModelOf(value) {
  const root = asRecord5(value);
  if (root === null) return null;
  for (const key of ["next", "lastUsed"]) {
    const selection = asRecord5(root[key]);
    if (selection === null || typeof selection.provider !== "string" || typeof selection.model !== "string") continue;
    const normalized = {
      provider: selection.provider,
      model: selection.model
    };
    if (typeof selection.reasoningEffort === "string") normalized.reasoningEffort = selection.reasoningEffort;
    return normalized;
  }
  return null;
}
function mobileContextOf(value) {
  const root = asRecord5(value);
  if (root === null) return null;
  const usedTokens = typeof root.projectedTokens === "number" ? root.projectedTokens : typeof root.pressureTokens === "number" ? root.pressureTokens : null;
  const contextWindow = typeof root.contextWindow === "number" ? root.contextWindow : null;
  if (usedTokens === null && contextWindow === null) return null;
  const percent = usedTokens !== null && contextWindow !== null && contextWindow > 0 ? Math.min(100, Math.round(usedTokens / contextWindow * 100)) : null;
  return { percent, usedTokens, contextWindow };
}
function mobileTokensOf(value) {
  const root = asRecord5(value);
  if (root === null) return null;
  const read = (key) => typeof root[key] === "number" ? root[key] : 0;
  if (root.uncachedInputTokens === void 0 && root.outputTokens === void 0) return null;
  return {
    inputTokens: read("uncachedInputTokens"),
    outputTokens: read("outputTokens"),
    cacheReadTokens: read("cacheReadTokens"),
    cacheWriteTokens: read("cacheWriteTokens")
  };
}
function json(res, status, value) {
  if (res.headersSent || res.writableEnded) return;
  const body = `${JSON.stringify(value)}
`;
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "content-length": String(Buffer.byteLength(body))
  });
  res.end(body);
}
function authorityOf(url) {
  try {
    return new URL(url).host;
  } catch {
    return null;
  }
}
function sameOriginMutatingRequest2(req) {
  const origin = req.headers.origin;
  if (typeof origin !== "string" || origin === "" || origin === "null") return true;
  const host = req.headers.host;
  const originAuthority = authorityOf(origin);
  return originAuthority !== null && originAuthority === (host ?? "");
}
async function readJsonBody2(req, limitBytes = MAX_BODY_BYTES2) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > limitBytes) throw new Error("request body too large");
    chunks.push(chunk);
  }
  if (chunks.length === 0) return {};
  const parsed = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("request body must be a JSON object");
  }
  return parsed;
}
function requireController(ctx, res) {
  const controller = sessionController(ctx);
  if (controller === void 0) {
    json(res, 503, { error: "session-controller-unavailable" });
    return void 0;
  }
  return controller;
}
async function readMutatingBody(req, res, limitBytes = MAX_BODY_BYTES2, log) {
  if (req.method !== "POST") {
    res.writeHead(405, { allow: "POST", "cache-control": "no-store" });
    res.end("method not allowed");
    return null;
  }
  if (!sameOriginMutatingRequest2(req)) {
    log?.(`dsh-overlay-custom: mobile POST rejected as cross-origin: origin=${String(req.headers.origin)} host=${String(req.headers.host)} url=${req.url ?? ""}`);
    res.writeHead(403, { "cache-control": "no-store" });
    res.end("forbidden");
    return null;
  }
  try {
    return await readJsonBody2(req, limitBytes);
  } catch (cause) {
    json(res, 400, { error: "invalid-request", message: cause instanceof Error ? cause.message : String(cause) });
    return null;
  }
}
async function loadTranscript(controller, sessionId) {
  let events = [];
  if (typeof controller.inspect === "function") {
    const inspected = await controller.inspect(sessionId);
    events = inspected.events ?? [];
  } else if (typeof controller.page === "function") {
    const page = await controller.page({
      address: { kind: "session", sessionId },
      throughSeq: -1,
      maxMessages: MAX_TRANSCRIPT_ITEMS
    });
    events = page.records ?? [];
  }
  return { messages: mobileTranscriptFromEvents(events), facts: mobileFactsFromEvents(events) };
}
function authenticatedRelayRequest(ctx, req) {
  if (req.headers[RELAY_REQUEST_HEADER] !== RELAY_REQUEST_VALUE) return false;
  return ctx.connection.authorizeIndex({
    method: "GET",
    url: "/",
    headers: req.headers
  }, {
    writeHead: () => void 0,
    end: () => void 0
  });
}
function registerMobileApi(options) {
  const { ctx } = options;
  const reject = (req, res) => {
    const rejection = ctx.connection.requestRejection(req);
    if (rejection !== void 0 && !(rejection === 403 && authenticatedRelayRequest(ctx, req))) {
      res.writeHead(rejection);
      res.end(rejection === 401 ? "unauthorized" : "forbidden");
      return true;
    }
    const header = req.headers["x-dsh-mobile-client"];
    const client = typeof header === "string" && header !== "" ? header : "";
    if (client !== "") {
      const binding = options.clientBinding;
      const idle = Date.now() - binding.lastSeen > CLIENT_TAKEOVER_MS;
      if (binding.clientId === null || binding.clientId === client || idle) {
        if (binding.clientId !== null && binding.clientId !== client) {
          ctx.logger.info("dsh-overlay-custom: mobile link taken over by a new device after idle");
        }
        binding.clientId = client;
        binding.lastSeen = Date.now();
      } else {
        ctx.logger.warn(`dsh-overlay-custom: mobile link held by another online device; rejecting url=${req.url ?? ""}`);
        res.writeHead(403, { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" });
        res.end('{"error":"link-in-use"}\n');
        return true;
      }
    }
    return false;
  };
  const fail2 = (cause, res, label, code) => {
    const message = cause instanceof Error ? cause.message : String(cause);
    ctx.logger.error(`dsh-overlay-custom: ${label}: ${message}`);
    json(res, 503, { error: code, message });
  };
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: "/api/desktop/mobile/state",
    handler: (req, res) => {
      if (reject(req, res)) return;
      if (req.method !== "GET" && req.method !== "HEAD") {
        res.writeHead(405, { allow: "GET, HEAD", "cache-control": "no-store" });
        res.end("method not allowed");
        return;
      }
      const controller = requireController(ctx, res);
      if (controller === void 0) return;
      options.presence.lastSeen = Date.now();
      void controller.list({}, freshSignal(15e3)).then(
        (result2) => {
          try {
            const feed = workspaceFeed(ctx);
            const baseline = feed?.baseline();
            const archived = new Set(baseline?.archivedSessionIds ?? []);
            const rows = result2.items.filter((item) => item.origin !== "subagent" && item.blank !== true && (typeof item.sessionId === "string" && !archived.has(item.sessionId))).map(mobileSessionRow);
            const byId = new Map(rows.map((row) => [row.id, row]));
            const groups = [];
            const accounted = /* @__PURE__ */ new Set();
            for (const workspace of baseline?.items ?? []) {
              const ids = workspace.sessionIds.filter((id) => byId.has(id));
              ids.forEach((id) => accounted.add(id));
              if (ids.length > 0) {
                groups.push({
                  name: workspaceName(workspace.path, workspace.title),
                  path: workspace.path,
                  sessionIds: ids
                });
              }
            }
            const stray = rows.filter((row) => !accounted.has(row.id));
            if (stray.length > 0) {
              groups.push({ name: "", path: "", sessionIds: stray.map((row) => row.id) });
            }
            json(res, 200, {
              sessions: rows,
              groups,
              interruptions: options.interruptions.snapshot(),
              relay: { active: options.relayActive() }
            });
          } catch (cause) {
            fail2(cause, res, "mobile state assemble failed", "state-unavailable");
          }
        },
        (cause) => {
          fail2(cause, res, "mobile state read failed", "state-unavailable");
        }
      );
    }
  }), "dsh-overlay-custom: mobile state route");
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: "/api/desktop/mobile/transcript",
    handler: (req, res) => {
      if (reject(req, res)) return;
      if (req.method !== "GET" && req.method !== "HEAD") {
        res.writeHead(405, { allow: "GET, HEAD", "cache-control": "no-store" });
        res.end("method not allowed");
        return;
      }
      const controller = requireController(ctx, res);
      if (controller === void 0) return;
      const url = new URL(req.url ?? "/", "http://localhost");
      const sessionId = url.searchParams.get("sessionId") ?? "";
      if (sessionId === "") {
        json(res, 400, { error: "invalid-request" });
        return;
      }
      options.presence.lastSeen = Date.now();
      void loadTranscript(controller, sessionId).then(
        (loaded) => {
          json(res, 200, { sessionId, messages: loaded.messages, facts: loaded.facts });
        },
        (cause) => {
          fail2(cause, res, "mobile transcript read failed", "transcript-unavailable");
        }
      );
    }
  }), "dsh-overlay-custom: mobile transcript route");
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: "/api/desktop/mobile/session-info",
    handler: (req, res) => {
      if (reject(req, res)) return;
      if (req.method !== "GET" && req.method !== "HEAD") {
        res.writeHead(405, { allow: "GET, HEAD", "cache-control": "no-store" });
        res.end("method not allowed");
        return;
      }
      const controller = requireController(ctx, res);
      if (controller === void 0) return;
      const url = new URL(req.url ?? "/", "http://localhost");
      const sessionId = url.searchParams.get("sessionId") ?? "";
      if (sessionId === "") {
        json(res, 400, { error: "invalid-request" });
        return;
      }
      options.presence.lastSeen = Date.now();
      options.controlCache.ensure(controller);
      const info2 = options.controlCache.sessionInfo(sessionId);
      json(res, 200, {
        sessionId,
        permissions: info2 === null ? null : mobilePermissionsOf(info2.permissions),
        model: info2 === null ? null : mobileModelOf(info2.modelSelection),
        context: info2 === null ? null : mobileContextOf(info2.contextPressure),
        tokens: info2 === null ? null : mobileTokensOf(info2.tokenUsage),
        queue: info2 === null ? [] : info2.queue
      });
    }
  }), "dsh-overlay-custom: mobile session-info route");
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: "/api/desktop/mobile/model-catalog",
    handler: (req, res) => {
      if (reject(req, res)) return;
      if (req.method !== "GET" && req.method !== "HEAD") {
        res.writeHead(405, { allow: "GET, HEAD", "cache-control": "no-store" });
        res.end("method not allowed");
        return;
      }
      const controller = requireController(ctx, res);
      if (controller === void 0 || typeof controller.modelCatalog !== "function") {
        json(res, 503, { error: "model-catalog-unavailable" });
        return;
      }
      options.presence.lastSeen = Date.now();
      void controller.modelCatalog().then(
        (catalog) => {
          json(res, 200, catalog);
        },
        (cause) => {
          fail2(cause, res, "mobile model catalog read failed", "model-catalog-unavailable");
        }
      );
    }
  }), "dsh-overlay-custom: mobile model-catalog route");
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: "/api/desktop/mobile/create",
    handler: (req, res) => {
      if (reject(req, res)) return;
      void readMutatingBody(req, res, MAX_PROMPT_BODY_BYTES, ctx.logger.warn.bind(ctx.logger)).then(async (body) => {
        if (body === null) return;
        const controller = requireController(ctx, res);
        if (controller === void 0) return;
        const content = typeof body.content === "string" ? body.content.trim() : "";
        const images = mobileImageParts(body.images);
        const cwd = typeof body.cwd === "string" && body.cwd !== "" ? body.cwd : void 0;
        if (content === "" && (images === null || images.length === 0)) {
          json(res, 400, { error: "invalid-request" });
          return;
        }
        if (images === null) {
          json(res, 400, { error: "invalid-request" });
          return;
        }
        const created = await controller.create(cwd === void 0 ? {} : { cwd });
        if (content !== "" || images.length > 0) {
          await controller.prompt({
            sessionId: created.sessionId,
            requestId: randomUUID2(),
            mode: "queue",
            content: mobilePromptContent(content, images)
          }, freshSignal(12e4));
        }
        json(res, 200, { sessionId: created.sessionId });
      }).catch((cause) => {
        fail2(cause, res, "mobile session create failed", "create-failed");
      });
    }
  }), "dsh-overlay-custom: mobile create route");
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: "/api/desktop/mobile/prompt",
    handler: (req, res) => {
      if (reject(req, res)) return;
      void readMutatingBody(req, res, MAX_PROMPT_BODY_BYTES, ctx.logger.warn.bind(ctx.logger)).then(async (body) => {
        if (body === null) return;
        const controller = requireController(ctx, res);
        if (controller === void 0) return;
        const sessionId = body.sessionId;
        const content = typeof body.content === "string" ? body.content.trim() : "";
        const images = mobileImageParts(body.images);
        if (typeof sessionId !== "string" || sessionId === "" || images === null || content === "" && images.length === 0) {
          json(res, 400, { error: "invalid-request" });
          return;
        }
        await controller.prompt({
          sessionId,
          requestId: randomUUID2(),
          mode: "queue",
          content: mobilePromptContent(content, images)
        }, freshSignal(12e4));
        json(res, 200, { accepted: true });
      }).catch((cause) => {
        fail2(cause, res, "mobile prompt failed", "prompt-failed");
      });
    }
  }), "dsh-overlay-custom: mobile prompt route");
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: "/api/desktop/mobile/cancel",
    handler: (req, res) => {
      if (reject(req, res)) return;
      void readMutatingBody(req, res, MAX_BODY_BYTES2, ctx.logger.warn.bind(ctx.logger)).then(async (body) => {
        if (body === null) return;
        const controller = requireController(ctx, res);
        if (controller === void 0) return;
        const sessionId = body.sessionId;
        if (typeof sessionId !== "string" || sessionId === "") {
          json(res, 400, { error: "invalid-request" });
          return;
        }
        const result2 = await controller.cancel({ sessionId });
        json(res, 200, { accepted: result2.accepted === true });
      }).catch((cause) => {
        fail2(cause, res, "mobile cancel failed", "cancel-failed");
      });
    }
  }), "dsh-overlay-custom: mobile cancel route");
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: "/api/desktop/mobile/select-model",
    handler: (req, res) => {
      if (reject(req, res)) return;
      void readMutatingBody(req, res, MAX_BODY_BYTES2, ctx.logger.warn.bind(ctx.logger)).then(async (body) => {
        if (body === null) return;
        const controller = requireController(ctx, res);
        if (controller === void 0 || typeof controller.selectModel !== "function") {
          json(res, 503, { error: "select-model-unavailable" });
          return;
        }
        const sessionId = body.sessionId;
        const provider = body.provider;
        const model = body.model;
        if (typeof sessionId !== "string" || sessionId === "" || typeof provider !== "string" || provider === "" || typeof model !== "string" || model === "") {
          json(res, 400, { error: "invalid-request" });
          return;
        }
        const request2 = { sessionId, provider, model };
        if (typeof body.reasoningEffort === "string" && body.reasoningEffort !== "") request2.reasoningEffort = body.reasoningEffort;
        const selected = await controller.selectModel(request2);
        json(res, 200, { selected: selected.selected ?? null });
      }).catch((cause) => {
        fail2(cause, res, "mobile model select failed", "select-model-failed");
      });
    }
  }), "dsh-overlay-custom: mobile select-model route");
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: "/api/desktop/mobile/permission",
    handler: (req, res) => {
      if (reject(req, res)) return;
      void readMutatingBody(req, res, MAX_BODY_BYTES2, ctx.logger.warn.bind(ctx.logger)).then(async (body) => {
        if (body === null) return;
        const controller = requireController(ctx, res);
        if (controller === void 0 || typeof controller.resolveAgent !== "function") {
          json(res, 503, { error: "permission-unavailable" });
          return;
        }
        const sessionId = body.sessionId;
        const preset = body.preset;
        if (typeof sessionId !== "string" || sessionId === "" || typeof preset !== "string" || preset === "") {
          json(res, 400, { error: "invalid-request" });
          return;
        }
        const presets = ctx.get("permissionPresets");
        if (presets === void 0 || typeof presets.set !== "function") {
          json(res, 503, { error: "permission-unavailable" });
          return;
        }
        const resolved = await controller.resolveAgent(sessionId);
        const agent = resolved.agent;
        if (agent === void 0 || agent.session === void 0 || agent.session === null) {
          json(res, 503, { error: "session-unavailable" });
          return;
        }
        presets.set(agent.session, preset);
        json(res, 200, { accepted: true });
      }).catch((cause) => {
        fail2(cause, res, "mobile permission switch failed", "permission-failed");
      });
    }
  }), "dsh-overlay-custom: mobile permission route");
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: "/api/desktop/mobile/queue-remove",
    handler: (req, res) => {
      if (reject(req, res)) return;
      void readMutatingBody(req, res, MAX_BODY_BYTES2, ctx.logger.warn.bind(ctx.logger)).then(async (body) => {
        if (body === null) return;
        const controller = requireController(ctx, res);
        if (controller === void 0 || typeof controller.updateQueue !== "function") {
          json(res, 503, { error: "queue-unavailable" });
          return;
        }
        const sessionId = body.sessionId;
        const itemId = body.itemId;
        if (typeof sessionId !== "string" || sessionId === "" || typeof itemId !== "string" || itemId === "") {
          json(res, 400, { error: "invalid-request" });
          return;
        }
        await controller.updateQueue({ sessionId, itemId, action: { kind: "remove" } });
        json(res, 200, { accepted: true });
      }).catch((cause) => {
        fail2(cause, res, "mobile queue remove failed", "queue-remove-failed");
      });
    }
  }), "dsh-overlay-custom: mobile queue-remove route");
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: "/api/desktop/mobile/rename",
    handler: (req, res) => {
      if (reject(req, res)) return;
      void readMutatingBody(req, res, MAX_BODY_BYTES2, ctx.logger.warn.bind(ctx.logger)).then(async (body) => {
        if (body === null) return;
        const controller = requireController(ctx, res);
        if (controller === void 0 || typeof controller.rename !== "function") {
          json(res, 503, { error: "rename-unavailable" });
          return;
        }
        const sessionId = body.sessionId;
        const title = typeof body.title === "string" ? body.title.trim().slice(0, 200) : "";
        if (typeof sessionId !== "string" || sessionId === "" || title === "") {
          json(res, 400, { error: "invalid-request" });
          return;
        }
        await controller.rename({ sessionId, title });
        json(res, 200, { accepted: true });
      }).catch((cause) => {
        fail2(cause, res, "mobile rename failed", "rename-failed");
      });
    }
  }), "dsh-overlay-custom: mobile rename route");
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: "/api/desktop/mobile/compact",
    handler: (req, res) => {
      if (reject(req, res)) return;
      void readMutatingBody(req, res, MAX_BODY_BYTES2, ctx.logger.warn.bind(ctx.logger)).then(async (body) => {
        if (body === null) return;
        const controller = requireController(ctx, res);
        if (controller === void 0 || typeof controller.resolveAgent !== "function") {
          json(res, 503, { error: "compact-unavailable" });
          return;
        }
        const sessionId = body.sessionId;
        if (typeof sessionId !== "string" || sessionId === "") {
          json(res, 400, { error: "invalid-request" });
          return;
        }
        const commands = ctx.get("commands");
        if (commands === void 0 || typeof commands.execute !== "function") {
          json(res, 503, { error: "compact-unavailable" });
          return;
        }
        const resolved = await controller.resolveAgent(sessionId);
        const agent = resolved.agent;
        if (agent === void 0) {
          json(res, 503, { error: "session-unavailable" });
          return;
        }
        await commands.execute(agent, "/compact", [], freshSignal(12e4));
        json(res, 200, { accepted: true });
      }).catch((cause) => {
        fail2(cause, res, "mobile compact failed", "compact-failed");
      });
    }
  }), "dsh-overlay-custom: mobile compact route");
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: "/api/desktop/mobile/permission-presets",
    handler: (req, res) => {
      if (reject(req, res)) return;
      if (req.method !== "GET" && req.method !== "HEAD") {
        res.writeHead(405, { allow: "GET, HEAD", "cache-control": "no-store" });
        res.end("method not allowed");
        return;
      }
      options.presence.lastSeen = Date.now();
      const presets = ctx.get("permissionPresets");
      if (presets === void 0 || typeof presets.optionOf !== "function" || typeof presets.names !== "object") {
        json(res, 200, { options: [], defaultPreset: null });
        return;
      }
      const optionsList = [];
      try {
        for (const name2 of presets.names) {
          const option = presets.optionOf(name2);
          if (option === void 0 || typeof option.value !== "string" || typeof option.name !== "string") continue;
          const normalized = { value: option.value, name: option.name };
          if (typeof option.description === "string") normalized.description = clipText(option.description, 200);
          optionsList.push(normalized);
        }
      } catch {
      }
      const defaultPreset = typeof presets.defaultPreset === "string" ? presets.defaultPreset : null;
      json(res, 200, { options: optionsList, defaultPreset });
    }
  }), "dsh-overlay-custom: mobile permission-presets route");
  const decideRoute = (path, label, code) => {
    ctx.effect(() => ctx.webServer.register({
      kind: "exact",
      path,
      handler: (req, res) => {
        if (reject(req, res)) return;
        void readMutatingBody(req, res, MAX_BODY_BYTES2, ctx.logger.warn.bind(ctx.logger)).then((body) => {
          if (body === null) return;
          const key = body.key;
          if (typeof key !== "string" || key === "") {
            json(res, 400, { error: "invalid-request" });
            return;
          }
          const outcome = options.interruptions.decide(key, body);
          if (outcome === null) {
            json(res, 400, { error: "invalid-request" });
            return;
          }
          if (outcome === "unknown") {
            json(res, 409, { error: "already-settled" });
            return;
          }
          options.presence.lastSeen = Date.now();
          json(res, 200, { accepted: true });
        }).catch((cause) => {
          fail2(cause, res, label, code);
        });
      }
    }), `dsh-overlay-custom: ${label} route`);
  };
  decideRoute("/api/desktop/mobile/approve", "mobile approval decide", "approve-failed");
  decideRoute("/api/desktop/mobile/answer", "mobile question answer", "answer-failed");
}

// src/mobile-control.ts
init_define_DSH_OVERLAY_META();
var QUEUE_PREVIEW_CHARS = 160;
var MAX_QUEUE_ITEMS = 20;
function previewOf(content) {
  if (typeof content === "string") return content.slice(0, QUEUE_PREVIEW_CHARS);
  if (!Array.isArray(content)) return "";
  const parts = [];
  for (const part of content) {
    if (typeof part === "string") parts.push(part);
    else if (part !== null && typeof part === "object" && !Array.isArray(part)) {
      const row = part;
      if (typeof row.text === "string") parts.push(row.text);
    }
    if (parts.join("").length >= QUEUE_PREVIEW_CHARS) break;
  }
  return parts.join("").slice(0, QUEUE_PREVIEW_CHARS);
}
function queueItemsOf(value) {
  if (!Array.isArray(value)) return [];
  const items = [];
  for (const row of value) {
    if (items.length >= MAX_QUEUE_ITEMS) break;
    if (row === null || typeof row !== "object") continue;
    const item = row;
    if (typeof item.id !== "string") continue;
    const message = item.message ?? null;
    items.push({
      id: item.id,
      placement: typeof item.placement === "string" ? item.placement : "queued",
      preview: previewOf(message?.content)
    });
  }
  return items;
}
function baselineOf(frame) {
  const row = frame;
  const value = row?.value ?? null;
  if (row === null || value === null || row.type !== "baseline") return null;
  const queues = {};
  const sessions = /* @__PURE__ */ new Map();
  const queueRows = value.queues;
  if (queueRows !== null && typeof queueRows === "object") {
    for (const [sessionId, items] of Object.entries(queueRows)) {
      queues[sessionId] = queueItemsOf(items);
    }
  }
  const projectionRows = value.projections;
  if (projectionRows !== null && typeof projectionRows === "object") {
    for (const [sessionId, baseline] of Object.entries(projectionRows)) {
      const values = baseline?.values;
      const cached = {
        queue: queues[sessionId] ?? [],
        values: values !== null && typeof values === "object" ? { ...values } : {}
      };
      sessions.set(sessionId, cached);
      queues[sessionId] = cached.queue;
    }
  }
  for (const [sessionId, queue] of Object.entries(queues)) {
    if (!sessions.has(sessionId)) sessions.set(sessionId, { queue, values: {} });
  }
  return { queues, sessions };
}
var MobileControlCache = class {
  constructor(log) {
    this.log = log;
  }
  log;
  sessions = /* @__PURE__ */ new Map();
  abort = null;
  failures = 0;
  /** Ensure the subscription is running; a crashed stream retries lazily. */
  ensure(controller) {
    if (this.abort !== null || controller === void 0) return;
    const abort = new AbortController();
    this.abort = abort;
    void (async () => {
      try {
        for await (const frame of controller.control(abort.signal)) {
          const baseline = baselineOf(frame);
          if (baseline !== null) {
            this.sessions = baseline.sessions;
            continue;
          }
          const row = frame;
          if (row.type === "queue" && typeof row.sessionId === "string") {
            const cached = this.sessions.get(row.sessionId);
            const queue = queueItemsOf(row.items);
            this.sessions.set(row.sessionId, { queue, values: cached?.values ?? {} });
          } else if (row.type === "projection" && typeof row.sessionId === "string" && typeof row.key === "string") {
            const cached = this.sessions.get(row.sessionId) ?? { queue: [], values: {} };
            cached.values[row.key] = row.value;
          }
        }
      } catch (cause) {
        this.failures += 1;
        this.log(`dsh-overlay-custom: mobile control stream ended (${cause instanceof Error ? cause.message : String(cause)})`);
      } finally {
        this.abort = null;
        const delay = Math.min(3e4, 1e3 * 2 ** Math.min(this.failures, 5));
        setTimeout(() => {
          if (this.abort === null) this.retry(controller);
        }, delay);
      }
    })();
  }
  retry(controller) {
    this.ensure(controller);
  }
  /** Stop the subscription (plugin teardown). */
  dispose() {
    this.abort?.abort();
    this.abort = null;
    this.sessions = /* @__PURE__ */ new Map();
  }
  /** Phone-facing projection slice for one session, when cached. */
  sessionInfo(sessionId) {
    const cached = this.sessions.get(sessionId);
    if (cached === void 0) return null;
    return {
      queue: cached.queue,
      permissions: cached.values.permissions ?? null,
      modelSelection: cached.values.modelSelection ?? null,
      contextPressure: cached.values.contextPressure ?? null,
      tokenUsage: cached.values.tokenUsage ?? null
    };
  }
};

// src/mobile-interruptions.ts
init_define_DSH_OVERLAY_META();
import { randomUUID as randomUUID3 } from "node:crypto";
var DEFAULT_HOLD_MS = 6e4;
var DEFAULT_TICK_MS = 1e3;
var MAX_QUESTIONS = 8;
var MAX_ANSWERS = 8;
var MAX_SELECTED = 8;
var MAX_LABEL_CHARS = 200;
var MAX_CUSTOM_CHARS = 2e3;
var MobileInterruptions = class {
  entries = /* @__PURE__ */ new Map();
  phoneActive;
  now;
  log;
  holdMs;
  tickMs;
  parallel;
  constructor(options) {
    this.phoneActive = options.phoneActive;
    this.now = options.now;
    this.log = options.log;
    this.holdMs = options.holdMs ?? DEFAULT_HOLD_MS;
    this.tickMs = options.tickMs ?? DEFAULT_TICK_MS;
    this.parallel = options.parallel === true;
  }
  /** Records visible to the phone page, oldest first. */
  snapshot() {
    return [...this.entries.values()].map((entry) => entry.record);
  }
  /**
   * Register one waterfall ask. The hold resolves with a phone claim, a
   * delegate (phone gone / expired / chose desktop), or an abort; the caller
   * maps that onto the waterfall's own return contract.
   */
  hold(request2, signal) {
    const record3 = {
      kind: request2.questions === null ? "approval" : "question",
      key: randomUUID3(),
      sessionId: request2.sessionId,
      toolName: request2.toolName,
      callId: request2.callId,
      reason: request2.reason,
      questions: request2.questions,
      at: this.now(),
      delegated: false
    };
    let settleFn;
    const settled = new Promise((resolve2) => {
      settleFn = resolve2;
    });
    const entry = {
      record: record3,
      settle: (value) => {
        if (entry.settled !== null) return;
        entry.settled = value;
        if (value.kind === "delegate") entry.record = { ...entry.record, delegated: true };
        if (entry.watcher !== null) clearInterval(entry.watcher);
        settleFn(value);
      },
      settled: null,
      watcher: null
    };
    this.entries.set(record3.key, entry);
    const retire = () => {
      if (entry.watcher !== null) clearInterval(entry.watcher);
      this.entries.delete(record3.key);
    };
    if (this.parallel) {
      signal?.addEventListener("abort", () => {
        entry.settle({ kind: "abort" });
      }, { once: true });
    } else if (this.phoneActive()) {
      entry.watcher = setInterval(() => {
        if (signal?.aborted === true) {
          entry.settle({ kind: "abort" });
          return;
        }
        if (!this.phoneActive() || this.now() - record3.at > this.holdMs) {
          this.log(`dsh-overlay-custom: mobile interruption hold fell through to desktop (${record3.toolName ?? record3.kind})`);
          entry.settle({ kind: "delegate" });
        }
      }, this.tickMs);
      signal?.addEventListener("abort", () => {
        entry.settle({ kind: "abort" });
      }, { once: true });
    } else {
      entry.settle({ kind: "delegate" });
    }
    return { record: record3, settled, retire };
  }
  /**
   * Apply one phone decision. Returns `null` when the payload is malformed;
   * `'unknown'` when nothing is pending under that key (already answered,
   * delegated, or retired); `'ok'` when the settlement was recorded.
   */
  decide(key, payload) {
    const entry = this.entries.get(key);
    if (entry === void 0) return "unknown";
    if (entry.settled !== null) return "unknown";
    const body = payload;
    if (body === null || typeof body !== "object") return null;
    if (entry.record.kind === "approval") {
      const action = body.action;
      if (action === "delegate") {
        entry.settle({ kind: "delegate" });
        return "ok";
      }
      if (action !== "allow" && action !== "reject") return null;
      entry.settle({ kind: "phone-decision", decision: action });
      return "ok";
    }
    const answers = normalizeAnswers(body.answers, entry.record.questions ?? []);
    if (answers === null) return null;
    entry.settle({ kind: "phone-answer", answers });
    return "ok";
  }
};
function normalizeAnswers(value, questions) {
  if (!Array.isArray(value) || value.length === 0 || value.length > MAX_ANSWERS) return null;
  const byId = new Map(questions.map((question) => [question.id, question]));
  const answers = [];
  for (const row of value) {
    if (row === null || typeof row !== "object" || Array.isArray(row)) return null;
    const item = row;
    if (typeof item.id !== "string" || item.id === "") return null;
    const question = byId.get(item.id);
    if (question === void 0) return null;
    const labels = new Set((question.options ?? []).map((option) => option.label));
    if (!Array.isArray(item.selected)) return null;
    const selected = [];
    for (const label of item.selected) {
      if (typeof label !== "string" || label === "" || label.length > MAX_LABEL_CHARS) return null;
      if (labels.size > 0 && !labels.has(label)) return null;
      selected.push(label);
      if (selected.length > MAX_SELECTED) return null;
    }
    if (!question.multiSelect && selected.length > 1) return null;
    let custom;
    if (item.custom !== void 0) {
      if (typeof item.custom !== "string" || item.custom.length > MAX_CUSTOM_CHARS) return null;
      custom = item.custom;
    }
    if (selected.length === 0 && (custom === void 0 || custom === "")) return null;
    answers.push(custom === void 0 ? { id: item.id, selected } : { id: item.id, selected, custom });
  }
  return answers;
}
function normalizeQuestions(value) {
  if (!Array.isArray(value) || value.length === 0 || value.length > MAX_QUESTIONS) return null;
  const questions = [];
  for (const row of value) {
    if (row === null || typeof row !== "object" || Array.isArray(row)) return null;
    const item = row;
    if (typeof item.id !== "string" || item.id === "") return null;
    if (typeof item.question !== "string" || item.question === "") return null;
    const question = {
      id: item.id,
      question: clip(item.question, 2e3)
    };
    if (typeof item.detail === "string") question.detail = clip(item.detail, 4e3);
    if (typeof item.header === "string" && item.header !== "") question.header = clip(item.header, 64);
    if (item.multiSelect === true) question.multiSelect = true;
    const intent = item.intent;
    if (intent !== null && intent !== void 0 && typeof intent === "object" && intent.kind === "plan-review" && typeof intent.approve === "string") {
      question.intent = { kind: "plan-review", approve: intent.approve };
    }
    if (Array.isArray(item.options)) {
      const options = [];
      for (const optionRow of item.options) {
        if (options.length >= 16) break;
        const option = optionRow;
        if (option === null || typeof option !== "object" || typeof option.label !== "string" || option.label === "") continue;
        const normalized = { label: clip(option.label, MAX_LABEL_CHARS) };
        if (typeof option.description === "string") normalized.description = clip(option.description, 400);
        options.push(normalized);
      }
      question.options = options;
    }
    questions.push(question);
  }
  return questions;
}
function clip(value, max) {
  return value.length <= max ? value : `${value.slice(0, max)}\u2026`;
}

// src/relay-origin.ts
init_define_DSH_OVERLAY_META();
function canonicalRelayOrigin(value) {
  if (value === "") return null;
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    return null;
  }
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return null;
  if (parsed.pathname !== "/" && parsed.pathname !== "") return null;
  if (parsed.search !== "" || parsed.hash !== "") return null;
  if (parsed.username !== "" || parsed.password !== "") return null;
  if (parsed.protocol === "http:" && !/^(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?$/u.test(parsed.host)) return null;
  return parsed;
}
async function persistRelayOrigin(home, value) {
  const origin = canonicalRelayOrigin(value.trim());
  const { writeOverlayConfig: writeOverlayConfig2 } = await Promise.resolve().then(() => (init_overlay_config(), overlay_config_exports));
  await writeOverlayConfig2(home, { remoteRelayOrigin: origin === null ? "" : origin.origin });
  return origin;
}

// src/relay.ts
var QRCode = createRequire2(import.meta.url)("qrcode");
var PUBLIC_ROOT = join8(dirname3(fileURLToPath(import.meta.url)), "..", "public");
var MOBILE_CSP = [
  "default-src 'none'",
  "script-src 'self'",
  "style-src 'self'",
  "connect-src 'self'",
  "img-src 'self' data:",
  "frame-ancestors 'none'"
].join("; ");
function mintPairing(ctx, origin) {
  let token = null;
  try {
    const authenticated = ctx.connection.authenticatedUrl(origin.href);
    token = new URL(authenticated).searchParams.get("token");
  } catch {
    token = null;
  }
  const alphabet = "abcdefghijklmnopqrstuvwxyz234567";
  const pairId = Array.from(randomBytes(10), (byte) => alphabet[byte % 32]).join("");
  return { pairId, secret: randomBytes(32).toString("base64url"), token };
}
function applyOverlayRelay(ctx, originValue) {
  let origin = canonicalRelayOrigin(originValue);
  let pairing = null;
  let tunnel = null;
  const presence = { lastSeen: 0 };
  const phoneActive = () => Date.now() - presence.lastSeen < 15e3;
  const interruptions = new MobileInterruptions({
    phoneActive,
    now: () => Date.now(),
    log: (message) => ctx.logger.info(message),
    parallel: true
  });
  const controlCache = new MobileControlCache((message) => ctx.logger.warn(message));
  ctx.effect(() => () => {
    controlCache.dispose();
  }, "dsh-overlay-custom: mobile control cache");
  const clientBinding = { clientId: null, lastSeen: 0 };
  function sessionKeyOf(request2) {
    return typeof request2.agent?.session?.id === "string" ? request2.agent.session.id : null;
  }
  function gateDesktopAsk(request2) {
    const turnSignal = request2.signal;
    const gate = new AbortController();
    if (turnSignal !== void 0) {
      if (turnSignal.aborted) gate.abort(turnSignal.reason);
      else turnSignal.addEventListener("abort", () => {
        gate.abort(turnSignal.reason);
      }, { once: true });
    }
    request2.signal = gate.signal;
    return {
      abortPhoneWin() {
        if (!gate.signal.aborted) gate.abort(new Error("answered on the phone"));
      }
    };
  }
  const snapshot = () => {
    if (origin === null) {
      return { state: "disabled", waiting: false, connected: false, relayOrigin: null, pairUrl: null, qrSvg: null, error: null };
    }
    if (pairing === null) {
      return { state: "off", waiting: false, connected: false, relayOrigin: origin.origin, pairUrl: null, qrSvg: null, error: null };
    }
    const tunnelState = tunnel?.snapshot().state ?? "connecting";
    const pairUrl = pairing.token === null ? null : `${origin.href}r/${pairing.pairId}/mobile/login?token=${pairing.token}`;
    const connected = Date.now() - presence.lastSeen < 15e3;
    return {
      state: pairing.token === null ? "failed" : tunnelState,
      waiting: tunnelState === "connecting" || tunnelState === "ready",
      connected: tunnelState === "ready" && connected,
      relayOrigin: origin.origin,
      pairUrl,
      qrSvg: null,
      error: pairing.token === null ? "browser session token unavailable" : tunnel?.snapshot().error ?? null
    };
  };
  const startTunnel = () => {
    if (origin === null || pairing === null || pairing.token === null) return;
    const targetPort = ctx.webServer.port;
    if (targetPort === void 0) return;
    tunnel = new RemoteRelayTunnel({
      relayUrl: `${origin.protocol === "https:" ? "wss" : "ws"}://${origin.host}/host`,
      pairId: pairing.pairId,
      secret: pairing.secret,
      targetPort,
      log: (message) => ctx.logger.info(`dsh-overlay-custom: ${message}`)
    });
    tunnel.start();
  };
  const enable = () => {
    if (origin === null) return snapshot();
    if (pairing === null) pairing = mintPairing(ctx, origin);
    if (tunnel === null) startTunnel();
    return snapshot();
  };
  async function withQr(view) {
    if (view.pairUrl === null) return view;
    try {
      const qrSvg = await QRCode.toString(view.pairUrl, {
        type: "svg",
        margin: 1,
        width: 232,
        errorCorrectionLevel: "M"
      });
      return { ...view, qrSvg };
    } catch {
      return view;
    }
  }
  ctx.effect(() => () => {
    tunnel?.dispose();
    tunnel = null;
  }, "dsh-overlay-custom: relay tunnel lifetime");
  ctx.effect(() => ctx.on(
    "approval/request",
    (request2, next) => {
      const gate = gateDesktopAsk(request2);
      const desktop = typeof next === "function" ? Promise.resolve(next()) : Promise.resolve("unavailable");
      try {
        const handle = interruptions.hold({
          sessionId: sessionKeyOf(request2),
          toolName: typeof request2.toolName === "string" ? request2.toolName : "unknown-tool",
          callId: typeof request2.callId === "string" ? request2.callId : null,
          reason: typeof request2.reason === "string" ? request2.reason : null,
          questions: null
        }, request2.signal);
        return new Promise((resolve2, rejectHold) => {
          let done = false;
          const finish = (settle) => {
            if (done) return;
            done = true;
            handle.retire();
            settle();
          };
          void handle.settled.then((settlement) => {
            if (settlement.kind === "phone-decision") {
              gate.abortPhoneWin();
              finish(() => {
                resolve2(settlement.decision === "allow" ? "allowed-once" : "rejected");
              });
            } else if (settlement.kind === "abort") {
              finish(() => {
                resolve2("cancelled");
              });
            } else {
              handle.retire();
            }
          });
          void desktop.then((outcome) => {
            finish(() => {
              resolve2(outcome);
            });
          }, (cause) => {
            finish(() => {
              rejectHold(cause);
            });
          });
        });
      } catch (cause) {
        ctx.logger.error(`dsh-overlay-custom: approval phone hold failed: ${cause instanceof Error ? cause.message : String(cause)}`);
        return desktop;
      }
    },
    { prepend: true }
  ), "dsh-overlay-custom: remote approval hold");
  ctx.effect(() => ctx.on(
    "user-questions/request",
    (request2, next) => {
      const questions = normalizeQuestions(request2.questions);
      if (questions === null) {
        return typeof next === "function" ? Promise.resolve(next()) : Promise.reject(new Error("no user-questions answerer accepted the request"));
      }
      const gate = gateDesktopAsk(request2);
      const desktop = typeof next === "function" ? Promise.resolve(next()) : void 0;
      const handle = interruptions.hold({
        sessionId: sessionKeyOf(request2),
        toolName: "ask-user",
        callId: null,
        reason: null,
        questions
      }, request2.signal);
      return new Promise((resolve2, rejectHold) => {
        let done = false;
        const finish = (settle) => {
          if (done) return;
          done = true;
          handle.retire();
          settle();
        };
        void handle.settled.then((settlement) => {
          if (settlement.kind === "phone-answer") {
            gate.abortPhoneWin();
            finish(() => {
              resolve2({ answers: settlement.answers });
            });
          } else if (settlement.kind === "abort") {
            finish(() => {
              rejectHold(new Error("question aborted before either side answered"));
            });
          } else {
            handle.retire();
          }
        });
        if (desktop !== void 0) {
          void desktop.then((answer) => {
            finish(() => {
              resolve2(answer);
            });
          }, (cause) => {
            finish(() => {
              rejectHold(cause);
            });
          });
        }
      });
    },
    { prepend: true }
  ), "dsh-overlay-custom: remote question hold");
  registerMobileApi({
    ctx,
    interruptions,
    controlCache,
    clientBinding,
    relayActive: () => tunnel?.snapshot().state === "ready",
    presence
  });
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: "/mobile/login",
    handler: (req, res) => {
      if (req.method !== "GET") {
        res.writeHead(405, { allow: "GET", "cache-control": "no-store" });
        res.end("method not allowed");
        return;
      }
      const incoming = new URL(req.url ?? "/mobile/login", "http://localhost");
      const authenticated = ctx.connection.authorizeIndex({
        method: "GET",
        url: `/${incoming.search}`,
        headers: req.headers
      }, {
        writeHead: (status, headers = {}) => res.writeHead(status, {
          ...headers,
          ...status === 303 ? { location: "/mobile/" } : {}
        }),
        end: (body) => res.end(body)
      });
      if (authenticated) {
        res.writeHead(303, { location: "/mobile/", "cache-control": "no-store" });
        res.end();
      }
    }
  }), "dsh-overlay-custom: mobile login");
  ctx.effect(() => ctx.webServer.register({
    kind: "prefix",
    path: "/mobile",
    handler: (req, res) => {
      void serveMobilePage(req, res);
    }
  }), "dsh-overlay-custom: mobile page");
  async function serveMobilePage(req, res) {
    if (req.method !== "GET" && req.method !== "HEAD") {
      res.writeHead(405, { allow: "GET, HEAD", "cache-control": "no-store" });
      res.end("method not allowed");
      return;
    }
    const url = new URL(req.url ?? "/mobile/", "http://localhost");
    let relative = url.pathname.replace(/^\/mobile\/?/u, "");
    if (relative === "") relative = "mobile.html";
    const normalized = normalize2(relative).split(sep).join("/");
    if (normalized.startsWith("..") || normalized.includes("../") || normalized.startsWith("/") || normalized !== "mobile.html" && normalized !== "mobile.js" && normalized !== "mobile.css") {
      res.writeHead(404, { "cache-control": "no-store" });
      res.end("not found");
      return;
    }
    try {
      const body = await readFile6(join8(PUBLIC_ROOT, normalized));
      const type = normalized.endsWith(".js") ? "text/javascript; charset=utf-8" : normalized.endsWith(".css") ? "text/css; charset=utf-8" : "text/html; charset=utf-8";
      res.writeHead(200, {
        "content-type": type,
        "cache-control": "no-store",
        "x-content-type-options": "nosniff",
        ...normalized.endsWith(".html") ? { "content-security-policy": MOBILE_CSP } : {},
        "content-length": String(body.byteLength)
      });
      res.end(req.method === "HEAD" ? void 0 : body);
    } catch {
      res.writeHead(404, { "cache-control": "no-store" });
      res.end("not found");
    }
  }
  const reject = (req, res) => rejectUnauthorized(req, res, ctx.connection.requestRejection(req));
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: "/api/desktop/remote/state",
    handler: (req, res) => {
      if (reject(req, res)) return;
      if (req.method !== "GET" && req.method !== "HEAD") {
        res.writeHead(405, { allow: "GET, HEAD", "cache-control": "no-store" });
        res.end("method not allowed");
        return;
      }
      void withQr(snapshot()).then((view) => {
        writeJson(res, 200, view);
      });
    }
  }), "dsh-overlay-custom: remote state");
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: "/api/desktop/remote/origin",
    handler: (req, res) => {
      if (reject(req, res)) return;
      if (req.method !== "POST") {
        res.writeHead(405, { allow: "POST", "cache-control": "no-store" });
        res.end("method not allowed");
        return;
      }
      if (!sameOriginMutatingRequest(req)) {
        res.writeHead(403, { "cache-control": "no-store" });
        res.end("forbidden");
        return;
      }
      void (async () => {
        const body = await readJsonBody(req).catch(() => ({}));
        const raw = typeof body.origin === "string" ? body.origin : "";
        origin = await persistRelayOrigin(resolveDshHome(), raw);
        pairing = null;
        tunnel?.dispose();
        tunnel = null;
        writeJson(res, 200, await withQr(enable()));
      })();
    }
  }), "dsh-overlay-custom: remote origin");
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: "/api/desktop/remote/pairing/open",
    handler: (req, res) => {
      if (reject(req, res)) return;
      if (req.method !== "POST") {
        res.writeHead(405, { allow: "POST", "cache-control": "no-store" });
        res.end("method not allowed");
        return;
      }
      if (!sameOriginMutatingRequest(req)) {
        res.writeHead(403, { "cache-control": "no-store" });
        res.end("forbidden");
        return;
      }
      void withQr(enable()).then((view) => {
        writeJson(res, 200, view);
      });
    }
  }), "dsh-overlay-custom: remote pairing");
}

// src/skills-mcp.ts
init_define_DSH_OVERLAY_META();
import { createRequire as createRequire3 } from "node:module";
import { join as join9 } from "node:path";
import { pathToFileURL } from "node:url";

// src/mcp-probe.ts
init_define_DSH_OVERLAY_META();
import { spawn } from "node:child_process";

// src/mcp-state.ts
init_define_DSH_OVERLAY_META();
init_overlay_config();
var NAME = /^[A-Za-z0-9_-]{1,32}$/u;
var ID = /^[A-Za-z0-9_-]{1,64}$/u;
var MAX_SERVERS = 64;
var MAX_TEXT = 2048;
function record2(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value : void 0;
}
function optionalText(value, field) {
  if (value === void 0) return void 0;
  if (typeof value !== "string" || value.length === 0 || value.length > MAX_TEXT) {
    throw new Error(`${field} must be a non-empty bounded string`);
  }
  return value;
}
function stringArray(value, field) {
  if (value === void 0) return void 0;
  if (!Array.isArray(value) || value.length > 256 || value.some((entry) => typeof entry !== "string" || entry.length > MAX_TEXT)) {
    throw new Error(`${field} must be a bounded string array`);
  }
  return [...value];
}
function stringMap(value, field) {
  if (value === void 0) return void 0;
  const map = record2(value);
  if (map === void 0 || Object.keys(map).length > 256) throw new Error(`${field} must be an object`);
  const result2 = {};
  for (const [key, entry] of Object.entries(map)) {
    if (key.length === 0 || key.length > 256 || typeof entry !== "string" || entry.length > MAX_TEXT) {
      throw new Error(`${field} contains an invalid entry`);
    }
    result2[key] = entry;
  }
  return result2;
}
function parseOverlayMcpServer(value) {
  const row = record2(value);
  if (row === void 0) throw new Error("MCP server must be an object");
  if (typeof row.id !== "string" || !ID.test(row.id)) throw new Error("invalid MCP server id");
  if (typeof row.serverName !== "string" || !NAME.test(row.serverName)) throw new Error("invalid MCP server name");
  if (row.transport !== "stdio" && row.transport !== "streamable-http") throw new Error("invalid MCP transport");
  if (row.disabled !== void 0 && typeof row.disabled !== "boolean") throw new Error("invalid MCP disabled flag");
  const parsed = {
    id: row.id,
    serverName: row.serverName,
    transport: row.transport,
    ...optionalText(row.command, "command") === void 0 ? {} : { command: optionalText(row.command, "command") },
    ...stringArray(row.args, "args") === void 0 ? {} : { args: stringArray(row.args, "args") },
    ...stringMap(row.env, "env") === void 0 ? {} : { env: stringMap(row.env, "env") },
    ...optionalText(row.cwd, "cwd") === void 0 ? {} : { cwd: optionalText(row.cwd, "cwd") },
    ...optionalText(row.url, "url") === void 0 ? {} : { url: optionalText(row.url, "url") },
    ...stringMap(row.headers, "headers") === void 0 ? {} : { headers: stringMap(row.headers, "headers") },
    ...row.disabled === void 0 ? {} : { disabled: row.disabled }
  };
  if (parsed.transport === "stdio" && parsed.command === void 0) throw new Error("stdio MCP server requires command");
  if (parsed.transport === "streamable-http" && parsed.url === void 0) throw new Error("HTTP MCP server requires url");
  return parsed;
}
function parseOverlayMcpServers(value) {
  if (value === void 0) return [];
  if (!Array.isArray(value) || value.length > MAX_SERVERS) throw new Error("mcpServers must be a bounded array");
  const servers = value.map(parseOverlayMcpServer);
  const ids = /* @__PURE__ */ new Set();
  const names = /* @__PURE__ */ new Set();
  for (const server of servers) {
    if (ids.has(server.id)) throw new Error(`duplicate MCP server id: ${server.id}`);
    if (names.has(server.serverName)) throw new Error(`duplicate MCP server name: ${server.serverName}`);
    ids.add(server.id);
    names.add(server.serverName);
  }
  return servers;
}
async function readOverlayMcpServers(home) {
  return parseOverlayMcpServers((await readOverlayConfig(home)).mcpServers);
}
function mergeSecretMap(current, patch) {
  if (patch === void 0) return current === void 0 ? void 0 : { ...current };
  const next = { ...current ?? {} };
  for (const [key, value] of Object.entries(patch)) {
    if (value === null) delete next[key];
    else next[key] = value;
  }
  return Object.keys(next).length === 0 ? void 0 : next;
}
function mergeOverlayMcpServer(current, value) {
  return parseOverlayMcpServer({
    id: value.id,
    serverName: value.serverName,
    transport: value.transport,
    ...value.command === void 0 ? {} : { command: value.command },
    ...value.args === void 0 ? {} : { args: value.args },
    ...mergeSecretMap(current?.env, value.env) === void 0 ? {} : { env: mergeSecretMap(current?.env, value.env) },
    ...value.cwd === void 0 ? {} : { cwd: value.cwd },
    ...value.url === void 0 ? {} : { url: value.url },
    ...mergeSecretMap(current?.headers, value.headers) === void 0 ? {} : { headers: mergeSecretMap(current?.headers, value.headers) },
    ...value.disabled === void 0 ? {} : { disabled: value.disabled }
  });
}
async function writeOverlayMcpServers(home, writes2) {
  const current = await readOverlayMcpServers(home);
  const servers = parseOverlayMcpServers(writes2.map((write) => mergeOverlayMcpServer(current.find((server) => server.id === write.id), write)));
  await writeOverlayConfig(home, { mcpServers: servers });
  return servers;
}
function projectOverlayMcpServer(server) {
  return {
    id: server.id,
    serverName: server.serverName,
    transport: server.transport,
    command: server.command ?? null,
    args: [...server.args ?? []],
    envKeys: Object.keys(server.env ?? {}).sort(),
    cwd: server.cwd ?? null,
    url: server.url ?? null,
    headerKeys: Object.keys(server.headers ?? {}).sort(),
    disabled: server.disabled === true
  };
}
function resolvedMcpArgs(server) {
  const env = server.env ?? {};
  return (server.args ?? []).map((argument) => argument.replace(/\$\{([A-Za-z_][A-Za-z0-9_]*)\}/gu, (match, key) => env[key] ?? match));
}
function mcpClientConfig(server) {
  return server.transport === "stdio" ? {
    transport: "stdio",
    serverName: server.serverName,
    command: server.command,
    args: [...resolvedMcpArgs(server)],
    env: { ...server.env ?? {} },
    cwd: server.cwd ?? ""
  } : {
    transport: "streamable-http",
    serverName: server.serverName,
    url: server.url,
    headers: { ...server.headers ?? {} }
  };
}

// src/mcp-probe.ts
var PROTOCOL = "2024-11-05";
var CLIENT = { name: "dsh-overlay-probe", version: "0.1.0" };
function parsePayload(raw) {
  const text = raw.trim();
  if (!text.startsWith("{")) return void 0;
  try {
    const value = JSON.parse(text);
    return value !== null && typeof value === "object" ? value : void 0;
  } catch {
    return void 0;
  }
}
function parseLines(raw) {
  return raw.split("\n").map(parsePayload).filter((value) => value !== void 0);
}
function sseData(raw) {
  return raw.split("\n").filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trim()).join("\n");
}
function request(id, method2, params) {
  return `${JSON.stringify({ jsonrpc: "2.0", id, method: method2, ...params === void 0 ? {} : { params } })}
`;
}
function failure(value) {
  if (value === void 0) return { ok: false, error: "protocol", detail: "server closed before answering" };
  if (value.error !== void 0) return { ok: false, error: "protocol", detail: "server rejected the MCP handshake" };
  return { ok: false, error: "protocol", detail: "unrecognized MCP response" };
}
function info(result2) {
  if (result2 === null || typeof result2 !== "object") return {};
  const serverInfo = result2.serverInfo;
  if (serverInfo === null || typeof serverInfo !== "object") return {};
  const name2 = serverInfo.name;
  const version = serverInfo.version;
  return {
    ...typeof name2 === "string" && name2.length > 0 ? { serverInfoName: name2.slice(0, 120) } : {},
    ...typeof version === "string" && version.length > 0 ? { serverInfoVersion: version.slice(0, 60) } : {}
  };
}
function toolCount(result2) {
  if (result2 === null || typeof result2 !== "object") return void 0;
  const tools = result2.tools;
  return Array.isArray(tools) ? tools.length : void 0;
}
async function stdio(server, timeoutMs) {
  return await new Promise((resolve2) => {
    let child;
    try {
      child = spawn(server.command, resolvedMcpArgs(server), {
        ...server.cwd === void 0 ? {} : { cwd: server.cwd },
        env: { ...process.env, ...server.env },
        stdio: ["pipe", "pipe", "pipe"]
      });
    } catch {
      resolve2({ ok: false, error: "spawn", detail: "command could not be started" });
      return;
    }
    let output = "";
    let initialized = false;
    let settled = false;
    let exitGrace;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (exitGrace !== void 0) clearTimeout(exitGrace);
      child.stdout?.removeAllListeners();
      child.removeAllListeners();
      try {
        child.kill("SIGKILL");
      } catch {
      }
      resolve2(value);
    };
    const evaluate = () => {
      const messages = parseLines(output);
      const hello = messages.find((message) => message.id === 1);
      if (hello === void 0) return;
      if (hello.result === void 0) return finish(failure(hello));
      if (!initialized) {
        initialized = true;
        try {
          child.stdin?.write(`${JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" })}
`);
          child.stdin?.write(request(2, "tools/list"));
        } catch {
        }
        return;
      }
      const listed = messages.find((message) => message.id === 2);
      if (listed === void 0) return;
      const count = toolCount(listed.result);
      finish({ ok: true, ...info(hello.result), ...count === void 0 ? {} : { toolCount: count } });
    };
    const timer = setTimeout(() => finish({ ok: false, error: "timeout", detail: "no MCP answer within the timeout" }), timeoutMs);
    child.on("error", () => finish({ ok: false, error: "spawn", detail: "command could not be started" }));
    child.on("exit", () => {
      exitGrace = setTimeout(() => {
        evaluate();
        finish({ ok: false, error: "protocol", detail: "server exited before completing the handshake" });
      }, 300);
    });
    child.stdout?.on("data", (chunk) => {
      output += chunk.toString("utf8");
      evaluate();
    });
    try {
      child.stdin?.write(request(1, "initialize", { protocolVersion: PROTOCOL, capabilities: {}, clientInfo: CLIENT }));
    } catch {
      finish({ ok: false, error: "spawn", detail: "command stdin closed immediately" });
    }
  });
}
async function http3(server, timeoutMs) {
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), timeoutMs);
  const headers = { "content-type": "application/json", accept: "application/json, text/event-stream", ...server.headers };
  try {
    const helloResponse = await fetch(server.url, {
      method: "POST",
      headers,
      body: request(1, "initialize", { protocolVersion: PROTOCOL, capabilities: {}, clientInfo: CLIENT }).trim(),
      signal: abort.signal
    });
    if (!helloResponse.ok) return { ok: false, error: "http-status", detail: `server answered HTTP ${helloResponse.status}` };
    const helloRaw = await helloResponse.text();
    const hello = parsePayload(helloResponse.headers.get("content-type")?.includes("event-stream") === true ? sseData(helloRaw) : helloRaw);
    if (hello?.result === void 0) return failure(hello);
    const session = helloResponse.headers.get("mcp-session-id");
    const listedResponse = await fetch(server.url, {
      method: "POST",
      headers: { ...headers, ...session ? { "mcp-session-id": session } : {} },
      body: request(2, "tools/list").trim(),
      signal: abort.signal
    });
    if (!listedResponse.ok) return { ok: true, ...info(hello.result) };
    const listedRaw = await listedResponse.text();
    const listed = parsePayload(listedResponse.headers.get("content-type")?.includes("event-stream") === true ? sseData(listedRaw) : listedRaw);
    const count = toolCount(listed?.result);
    return { ok: true, ...info(hello.result), ...count === void 0 ? {} : { toolCount: count } };
  } catch {
    return abort.signal.aborted ? { ok: false, error: "timeout", detail: "no MCP answer within the timeout" } : { ok: false, error: "connect", detail: "endpoint could not be reached" };
  } finally {
    clearTimeout(timer);
  }
}
async function probeOverlayMcpServer(server, timeoutMs = 3e4) {
  return server.transport === "stdio" ? await stdio(server, timeoutMs) : await http3(server, timeoutMs);
}

// src/skills-mcp.ts
var SKILLS_PATH = "/api/desktop/skills";
var MCP_PATH = "/api/desktop/mcp";
var MCP_TEST_PATH = "/api/desktop/mcp/test";
function method(res, allow) {
  res.writeHead(405, { allow, "cache-control": "no-store" });
  res.end("method not allowed");
}
function forbidden(req, res) {
  if (sameOriginMutatingRequest(req)) return false;
  res.writeHead(403, { "cache-control": "no-store" });
  res.end("forbidden");
  return true;
}
function rejectFor(ctx, req, res) {
  return rejectUnauthorized(req, res, ctx.connection.requestRejection(req));
}
async function listSkills(ctx) {
  const registry = ctx.get("skills");
  if (registry === void 0) return { available: false, skills: [] };
  const presets = ctx.get("agentPresets");
  let lease;
  try {
    lease = await presets?.acquireScope();
    const skills = await registry.list({ cwd: process.cwd(), ...lease === void 0 ? {} : { scope: lease.key } });
    return {
      available: true,
      skills: skills.map((skill) => ({
        name: skill.name,
        description: skill.description,
        whenToUse: skill.whenToUse ?? null,
        modelInvocable: skill.invocation.modelInvocable,
        userInvocable: skill.invocation.userInvocable,
        source: skill.source
      }))
    };
  } finally {
    await lease?.[Symbol.asyncDispose]();
  }
}
function logFailure(ctx, operation, cause) {
  ctx.logger.warn(`dsh-overlay-custom: ${operation}: ${cause instanceof Error ? cause.message : String(cause)}`);
}
function applyOverlaySkillsMcpRoutes(ctx) {
  const home = resolveDshHome();
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: SKILLS_PATH,
    handler: (req, res) => {
      if (rejectFor(ctx, req, res)) return;
      if (req.method !== "GET") return method(res, "GET");
      void listSkills(ctx).then((value) => writeJson(res, 200, value)).catch((cause) => {
        logFailure(ctx, "skill catalog failed", cause);
        writeJson(res, 500, { error: "skill-catalog-unavailable" });
      });
    }
  }), "dsh-overlay-custom: skills route");
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: MCP_PATH,
    handler: (req, res) => {
      if (rejectFor(ctx, req, res)) return;
      if (req.method !== "GET" && req.method !== "POST") return method(res, "GET, POST");
      if (req.method === "GET") {
        void readOverlayMcpServers(home).then((servers) => writeJson(res, 200, { servers: servers.map(projectOverlayMcpServer), restartRequired: true })).catch((cause) => {
          logFailure(ctx, "MCP state read failed", cause);
          writeJson(res, 500, { error: "mcp-state-unavailable" });
        });
        return;
      }
      if (forbidden(req, res)) return;
      void (async () => {
        try {
          const body = await readJsonBody(req);
          if (!Array.isArray(body.servers)) throw new Error("servers must be an array");
          const servers = await writeOverlayMcpServers(home, body.servers);
          writeJson(res, 200, {
            accepted: true,
            servers: servers.map(projectOverlayMcpServer),
            restartRequired: true,
            restartScheduled: false
          });
        } catch (cause) {
          logFailure(ctx, "MCP state write rejected", cause);
          writeJson(res, 400, { error: "invalid-mcp-state" });
        }
      })();
    }
  }), "dsh-overlay-custom: MCP state route");
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: MCP_TEST_PATH,
    handler: (req, res) => {
      if (rejectFor(ctx, req, res)) return;
      if (req.method !== "POST") return method(res, "POST");
      if (forbidden(req, res)) return;
      void (async () => {
        try {
          const body = await readJsonBody(req);
          if (body.row === null || typeof body.row !== "object" || Array.isArray(body.row)) {
            throw new Error("row must be an object");
          }
          const rows = await readOverlayMcpServers(home);
          const id = typeof body.id === "string" ? body.id : void 0;
          const current = id === void 0 ? void 0 : rows.find((row) => row.id === id);
          const candidate = mergeOverlayMcpServer(current, body.row);
          writeJson(res, 200, await probeOverlayMcpServer(candidate));
        } catch (cause) {
          logFailure(ctx, "MCP probe rejected", cause);
          writeJson(res, 400, { ok: false, error: "protocol", detail: "probe could not run" });
        }
      })();
    }
  }), "dsh-overlay-custom: MCP test route");
}
async function importMcpClient() {
  try {
    return await import("@deepseek-ai/dsh-mcp-client");
  } catch (directError) {
    try {
      const requireFromRuntime = createRequire3(join9(process.cwd(), "package.json"));
      return await import(pathToFileURL(requireFromRuntime.resolve("@deepseek-ai/dsh-mcp-client")).href);
    } catch {
      throw directError;
    }
  }
}
async function mountOverlayMcpServers(ctx) {
  const servers = await readOverlayMcpServers(resolveDshHome());
  if (!servers.some((server) => server.disabled !== true)) return;
  let mcpClient;
  try {
    mcpClient = await importMcpClient();
  } catch (cause) {
    logFailure(ctx, "MCP client package import failed", cause);
    return;
  }
  for (const server of servers) {
    if (server.disabled === true) continue;
    try {
      const input = mcpClientConfig(parseOverlayMcpServer(server));
      const config = mcpClient.Config(input);
      ctx.plugin(mcpClient, config);
      ctx.logger.info(`dsh-overlay-custom: mounted MCP server ${server.serverName}`);
    } catch (cause) {
      logFailure(ctx, `MCP server ${server.serverName} failed to mount`, cause);
    }
  }
}

// src/updates.ts
init_define_DSH_OVERLAY_META();

// src/build-meta.ts
init_define_DSH_OVERLAY_META();
var FALLBACK = { officialVersion: "0.0.0", buildVersion: "0.0.0_dev" };
function currentBuildMeta() {
  const baked = typeof define_DSH_OVERLAY_META_default === "undefined" ? void 0 : define_DSH_OVERLAY_META_default;
  if (baked !== void 0 && typeof baked.officialVersion === "string" && baked.officialVersion !== "") return baked;
  return FALLBACK;
}
function parseOfficialVersion(version) {
  const match = /^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$/u.exec(version.trim());
  if (match === null) return null;
  const pre = match[4] === void 0 ? [] : match[4].split(".").map((part) => /^\d+$/u.test(part) ? Number(part) : part);
  return { core: [Number(match[1]), Number(match[2]), Number(match[3])], pre };
}
function comparePre(a, b) {
  if (a.length === 0 && b.length === 0) return 0;
  if (a.length === 0) return 1;
  if (b.length === 0) return -1;
  for (let index = 0; index < Math.max(a.length, b.length); index += 1) {
    const left = a[index];
    const right = b[index];
    if (left === void 0) return -1;
    if (right === void 0) return 1;
    if (left === right) continue;
    const leftNumeric = typeof left === "number";
    const rightNumeric = typeof right === "number";
    if (leftNumeric && rightNumeric) return left < right ? -1 : 1;
    if (leftNumeric) return -1;
    if (rightNumeric) return 1;
    return left < right ? -1 : 1;
  }
  return 0;
}
function compareOfficialVersions(a, b) {
  const left = parseOfficialVersion(a);
  const right = parseOfficialVersion(b);
  if (left === null || right === null) return left === right ? 0 : left === null ? -1 : 1;
  for (let index = 0; index < 3; index += 1) {
    if (left.core[index] !== right.core[index]) return left.core[index] < right.core[index] ? -1 : 1;
  }
  return comparePre(left.pre, right.pre);
}
function parseBuildVersion(version) {
  const match = /^(.+?)_(\d+|dev)$/u.exec(version.trim());
  if (match === null) return null;
  const iteration = match[2] === "dev" ? 0 : Number(match[2]);
  if (!Number.isSafeInteger(iteration)) return null;
  return { officialVersion: match[1], iteration };
}
function compareBuildVersions(a, b) {
  const left = parseBuildVersion(a);
  const right = parseBuildVersion(b);
  if (left === null || right === null) return compareOfficialVersions(a, b);
  const official = compareOfficialVersions(left.officialVersion, right.officialVersion);
  if (official !== 0) return official;
  if (left.iteration === right.iteration) return 0;
  return left.iteration < right.iteration ? -1 : 1;
}

// src/updates.ts
init_overlay_config();
var OFFICIAL_REPO = "https://api.github.com/repos/deepseek-ai/deepseek-harness";
var BUILD_REPO = "https://api.github.com/repos/XingAur/deepseek-harness-desktop";
var CACHE_MS = 30 * 60 * 1e3;
var FETCH_TIMEOUT_MS = 1e4;
var NOTES_LIMIT = 1600;
function decodeEntities(text) {
  return text.replace(/&nbsp;/gu, " ").replace(/&amp;/gu, "&").replace(/&lt;/gu, "<").replace(/&gt;/gu, ">").replace(/&quot;/gu, '"').replace(/&#39;/gu, "'").replace(/&#(\d+);/gu, (_match, code) => {
    const point = Number(code);
    return Number.isSafeInteger(point) && point >= 0 && point <= 1114111 ? String.fromCodePoint(point) : "";
  });
}
function htmlToText(html) {
  return decodeEntities(html.replace(/<h[1-6][^>]*>/giu, "\n\n## ").replace(/<\/h[1-6]>/giu, "\n").replace(/<li[^>]*>/giu, "\n- ").replace(/<\/li>/giu, "").replace(/<br\s*\/?>/giu, "\n").replace(/<\/p>/giu, "\n").replace(/<[^>]+>/gu, "")).replace(/[ \t]+\n/gu, "\n").replace(/\n{3,}/gu, "\n\n").trim();
}
function chineseSection(body) {
  const start = body.search(/<h3 id="cn-[^"]*">/iu);
  if (start < 0) return null;
  const rest = body.slice(start);
  const end = rest.search(/<h3 id="en-[^"]*">/iu);
  return htmlToText(end < 0 ? rest : rest.slice(0, end));
}
function localizeNotes(body) {
  if (typeof body !== "string") return "";
  const withoutChangelog = body.split(/Full Changelog:/u)[0] ?? body;
  const withoutLanguageLinks = withoutChangelog.replace(/^\[中文\]\([^)]*\)\s*\|\s*\[English\]\([^)]*\)\s*$/gmu, "");
  const chinese = chineseSection(withoutLanguageLinks);
  const text = (chinese !== null && chinese !== "" ? chinese : htmlToText(withoutLanguageLinks)).replace(/\r\n/gu, "\n").replace(/\n{3,}/gu, "\n\n").trim();
  return text.length > NOTES_LIMIT ? `${text.slice(0, NOTES_LIMIT)}\u2026` : text;
}
async function fetchJson(url) {
  const response = await fetch(url, {
    headers: { accept: "application/vnd.github+json", "user-agent": "dsh-overlay-custom" },
    redirect: "error",
    signal: AbortSignal.timeout(FETCH_TIMEOUT_MS)
  });
  if (!response.ok) throw new Error(`HTTP ${String(response.status)}`);
  return response.json();
}
function parseReleases(value) {
  return Array.isArray(value) ? value : [];
}
function candidateOf(release, prefix) {
  if (typeof release.tag_name !== "string" || !release.tag_name.startsWith(prefix)) return null;
  if (typeof release.html_url !== "string") return null;
  return {
    version: release.tag_name.slice(prefix.length),
    url: release.html_url,
    name: typeof release.name === "string" && release.name !== "" ? release.name : release.tag_name,
    notes: localizeNotes(release.body),
    publishedAt: typeof release.published_at === "string" ? release.published_at : null
  };
}
function isDraft(release) {
  return release.draft === true;
}
function selectLatestOfficial(releases) {
  let best = null;
  let bestAt = Number.NEGATIVE_INFINITY;
  for (const release of releases) {
    if (isDraft(release)) continue;
    const candidate = candidateOf(release, "dsh-v");
    if (candidate === null) continue;
    const parsed = candidate.publishedAt === null ? Number.NaN : Date.parse(candidate.publishedAt);
    const at = Number.isFinite(parsed) ? parsed : 0;
    if (best === null || at > bestAt || at === bestAt && compareOfficialVersions(candidate.version, best.version) > 0) {
      best = candidate;
      bestAt = at;
    }
  }
  return best;
}
async function latestOfficial() {
  return selectLatestOfficial(parseReleases(await fetchJson(`${OFFICIAL_REPO}/releases?per_page=30`)));
}
async function latestBuild() {
  const releases = parseReleases(await fetchJson(`${BUILD_REPO}/releases?per_page=30`));
  let best = null;
  for (const release of releases) {
    if (isDraft(release)) continue;
    const candidate = candidateOf(release, "official-v");
    if (candidate === null) continue;
    if (best === null || compareBuildVersions(candidate.version, best.version) > 0) best = candidate;
  }
  return best;
}
async function discoverUpdates(current) {
  const [official, build] = await Promise.all([latestOfficial(), latestBuild()]).catch(() => [null, null]);
  return {
    current,
    official,
    build,
    officialUpdate: official !== null && compareOfficialVersions(official.version, current.officialVersion) > 0,
    buildUpdate: build !== null && compareBuildVersions(build.version, current.buildVersion) > 0,
    checkedAt: (/* @__PURE__ */ new Date()).toISOString(),
    error: official === null && build === null ? "network" : null
  };
}
function applyOverlayUpdates(ctx) {
  const current = currentBuildMeta();
  const config = readOverlayConfigSync(resolveDshHome());
  const updates = config.updates !== null && typeof config.updates === "object" && !Array.isArray(config.updates) ? config.updates : {};
  const autoCheck = updates.autoCheck !== false;
  let cache = null;
  let inflight = null;
  const resolve2 = async () => {
    const state2 = await discoverUpdates(current);
    cache = { at: Date.now(), state: state2 };
    return state2;
  };
  const refresh = async () => {
    if (inflight !== null) return inflight;
    inflight = resolve2().finally(() => {
      inflight = null;
    });
    return inflight;
  };
  const state = async (force) => {
    if (!force && cache !== null && Date.now() - cache.at < CACHE_MS) return cache.state;
    try {
      return await refresh();
    } catch (cause) {
      return {
        current,
        official: null,
        build: null,
        officialUpdate: false,
        buildUpdate: false,
        checkedAt: (/* @__PURE__ */ new Date()).toISOString(),
        error: cause instanceof Error ? cause.message : "failed"
      };
    }
  };
  const reject = (req, res) => rejectUnauthorized(req, res, ctx.connection.requestRejection(req));
  ctx.effect(() => ctx.webServer.register({
    kind: "exact",
    path: "/api/desktop/updates/state",
    handler: (req, res) => {
      if (reject(req, res)) return;
      if (req.method === "GET") {
        void state(false).then((value) => {
          writeJson(res, 200, { ok: true, ...value });
        });
        return;
      }
      if (req.method !== "POST") {
        res.writeHead(405, { allow: "GET, POST", "cache-control": "no-store" });
        res.end("method not allowed");
        return;
      }
      if (!sameOriginMutatingRequest(req)) {
        res.writeHead(403, { "cache-control": "no-store" });
        res.end("forbidden");
        return;
      }
      void state(true).then((value) => {
        writeJson(res, 200, { ok: true, ...value });
      });
    }
  }), "dsh-overlay-custom: update state route");
  if (!autoCheck) return;
  ctx.effect(() => {
    const timer = setTimeout(() => {
      void state(false).catch(() => {
        ctx.logger.warn("dsh-overlay-custom: update check failed");
      });
    }, 5e3);
    timer.unref?.();
    return () => {
      clearTimeout(timer);
    };
  }, "dsh-overlay-custom: startup update check");
}

// src/index.ts
var name = "dsh-overlay-custom";
var inject = ["webServer", "connection"];
function loadRelayOrigin() {
  const env = process.env.DSH_OVERLAY_RELAY_ORIGIN;
  if (typeof env === "string" && env.trim() !== "") return env.trim();
  const origin = readOverlayConfigSync(resolveDshHome()).remoteRelayOrigin;
  if (typeof origin === "string") return origin;
  return "";
}
async function apply(ctx) {
  const modelNetwork = applyOverlayProxyApi(ctx);
  applyOverlayRelay(ctx, loadRelayOrigin());
  applyOverlayModels(ctx, modelNetwork);
  applyOverlayOauth(ctx, modelNetwork);
  applyOverlaySkillsMcpRoutes(ctx);
  applyOverlayUpdates(ctx);
  await mountOverlayMcpServers(ctx);
  ctx.logger.info("dsh-overlay-custom host mounted");
}
export {
  apply,
  inject,
  name
};
