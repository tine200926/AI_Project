// chrome.storage.local layout, shared by the service worker and the pages.
//   targets  [{id, label, url, watch: string[], country, enabled}]
//   settings see DEFAULT_SETTINGS
//   state    {"<targetId>::<option>": {soldOut, checkedAt, notifiedAt}, "error::notifiedAt": iso}
//   lastRun  {at, results: {<targetId>: {ok, error?, statusCode, options: [[name, soldOut, watched]], missing}}}
//   log      [{t, level, msg}] newest last, capped at LOG_MAX

export const DEFAULT_SETTINGS = Object.freeze({
  intervalMinutes: 5,
  cooldownMinutes: 180,
  notifyOnFirstRun: false,
  notifyOnSoldOut: false,
  notifyOnError: true,
  politeDelayMs: 1500,
  requestTimeoutSec: 30,
  maxRetries: 2,
});

export const LIMITS = Object.freeze({
  intervalMinutes: [1, 1440],
  cooldownMinutes: [0, 10080],
  politeDelayMs: [0, 60000],
  requestTimeoutSec: [5, 120],
  maxRetries: [1, 5],
});

const LOG_MAX = 300;

export async function getTargets() {
  const { targets } = await chrome.storage.local.get('targets');
  return Array.isArray(targets) ? targets : [];
}

export async function saveTargets(targets) {
  await chrome.storage.local.set({ targets });
}

export function cleanSettings(raw) {
  const out = { ...DEFAULT_SETTINGS };
  for (const k of Object.keys(DEFAULT_SETTINGS)) {
    if (!raw || raw[k] === undefined || raw[k] === null || raw[k] === '') continue;
    if (typeof DEFAULT_SETTINGS[k] === 'boolean') {
      out[k] = !!raw[k];
    } else {
      const n = Math.round(Number(raw[k]));
      if (Number.isFinite(n)) {
        const [lo, hi] = LIMITS[k];
        out[k] = Math.min(hi, Math.max(lo, n));
      }
    }
  }
  return out;
}

export async function getSettings() {
  const { settings } = await chrome.storage.local.get('settings');
  return cleanSettings(settings);
}

export async function saveSettings(settings) {
  await chrome.storage.local.set({ settings: cleanSettings(settings) });
}

export async function getState() {
  const { state } = await chrome.storage.local.get('state');
  return state && typeof state === 'object' ? state : {};
}

export async function saveState(state) {
  await chrome.storage.local.set({ state });
}

/** Forget saved stock readings for one target, so a re-add starts clean. */
export async function dropTargetState(targetId) {
  const state = await getState();
  for (const k of Object.keys(state)) {
    if (k.startsWith(`${targetId}::`)) delete state[k];
  }
  await saveState(state);
  const { lastRun } = await chrome.storage.local.get('lastRun');
  if (lastRun && lastRun.results && lastRun.results[targetId]) {
    delete lastRun.results[targetId];
    await chrome.storage.local.set({ lastRun });
  }
}

// Serialize log writes: several log() calls in one check would otherwise
// read-modify-write over each other.
let logChain = Promise.resolve();

export function log(msg, level = 'INFO') {
  const entry = { t: new Date().toISOString(), level, msg };
  (level === 'ERROR' ? console.error : level === 'WARN' ? console.warn : console.log)(`[${level}] ${msg}`);
  logChain = logChain.then(async () => {
    const { log: entries } = await chrome.storage.local.get('log');
    const list = Array.isArray(entries) ? entries : [];
    list.push(entry);
    await chrome.storage.local.set({ log: list.slice(-LOG_MAX) });
  }).catch(() => {});
  return logChain;
}
