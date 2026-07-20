/* Personal DB XHS collector — MV3 service worker.
 *
 * This extension is intentionally not a general browser-automation runtime.
 * Its native bridge accepts only the two bundled XHS collectors, and every
 * collection runs in its own unfocused, labelled window in the user's existing
 * Chrome session. The window is closed even when a collector fails.
 */

const GROUP_TITLE = 'Personal DB XHS Collector'
const NATIVE_HOST = 'com.personaldb.xhs_collector'
const MAX_COLLECT_TIMEOUT_MS = 600_000
const CREATOR_API_TAP_ID = 'personal-db-xhs-creator-api-tap'
const CREATOR_API_STATE = '__personalDbXhsCreatorApiTap'
const CREATOR_API_EMPTY_GRACE_MS = 8_000
const CREATOR_API_OBSERVATION_GRACE_MS = 20_000
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

const COLLECTORS = {
  'collectors/xhs/creator.js': {
    source: 'xhs', globalName: '__personalDbXhsCreator', host: 'creator.xiaohongshu.com',
    cfg: new Set(['maxScrolls', 'delayMs', 'settleMs']),
  },
  'collectors/xhs/saved.js': {
    source: 'xhs_saved', globalName: '__personalDbXhsSaved', host: null,
    hosts: new Set(['www.xiaohongshu.com', 'xiaohongshu.com']),
    cfg: new Set(['maxScrolls', 'delayMs', 'settleMs', 'knownIds', 'overlapStop', 'deepBackfill']),
  },
}

async function setStatus(patch) {
  const previous = (await chrome.storage.local.get('status')).status || {}
  await chrome.storage.local.set({ status: { ...previous, ...patch, at: Date.now() } })
}

function validateJob(job) {
  if (!job || typeof job !== 'object') throw new Error('collect job must be an object')
  const allowed = new Set(['source', 'url', 'collectorFile', 'globalName', 'cfg', 'timeoutMs'])
  if (Object.keys(job).some((key) => !allowed.has(key))) throw new Error('collect job contains unsupported fields')
  const spec = COLLECTORS[job.collectorFile]
  if (!spec || job.source !== spec.source || job.globalName !== spec.globalName) throw new Error('collector is not allowlisted')
  let url
  try { url = new URL(job.url) } catch { throw new Error('collection URL is invalid') }
  const allowedHost = spec.host ? url.hostname === spec.host : spec.hosts.has(url.hostname)
  if (url.protocol !== 'https:' || !allowedHost) throw new Error('collection URL is not an allowlisted XHS URL')
  const cfg = job.cfg || {}
  if (!cfg || typeof cfg !== 'object' || Array.isArray(cfg) || Object.keys(cfg).some((key) => !spec.cfg.has(key))) {
    throw new Error('collector configuration is not allowlisted')
  }
  for (const key of ['maxScrolls', 'delayMs', 'settleMs', 'overlapStop']) {
    if (key in cfg && (!Number.isInteger(cfg[key]) || cfg[key] < 0)) throw new Error(`${key} must be a non-negative integer`)
  }
  if ('deepBackfill' in cfg && typeof cfg.deepBackfill !== 'boolean') throw new Error('deepBackfill must be a boolean')
  if ('knownIds' in cfg && (!Array.isArray(cfg.knownIds) || cfg.knownIds.length > 50000 || cfg.knownIds.some((id) => typeof id !== 'string' || id.length > 128))) {
    throw new Error('knownIds must be a bounded list of strings')
  }
  const timeoutMs = job.timeoutMs == null ? MAX_COLLECT_TIMEOUT_MS : job.timeoutMs
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > MAX_COLLECT_TIMEOUT_MS) throw new Error('invalid collection timeout')
  return { ...job, cfg, timeoutMs }
}

async function waitTabComplete(tabId, timeoutMs = 45_000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const tab = await chrome.tabs.get(tabId).catch(() => null)
    if (!tab) throw new Error('collection tab closed unexpectedly')
    if (tab.status === 'complete') return
    await sleep(300)
  }
  throw new Error('collection tab did not finish loading')
}

async function execute(tabId, func, args = []) {
  const [result] = await chrome.scripting.executeScript({ target: { tabId }, func, args })
  return result?.result
}

async function executeMain(tabId, func, args = []) {
  const [result] = await chrome.scripting.executeScript({ target: { tabId }, func, args, world: 'MAIN' })
  return result?.result
}

async function installCreatorApiTap() {
  // Dynamic registration is done before creating the collection window so the
  // MAIN-world tap sees the page's initial authenticated request. It is removed
  // in finally below and is constrained both by page URL and endpoint path.
  await chrome.scripting.unregisterContentScripts({ ids: [CREATOR_API_TAP_ID] }).catch(() => {})
  await chrome.scripting.registerContentScripts([{
    id: CREATOR_API_TAP_ID,
    js: ['collectors/xhs/creator_api_tap.js'],
    matches: ['https://creator.xiaohongshu.com/new/note-manager*'],
    runAt: 'document_start',
    world: 'MAIN',
    persistAcrossSessions: false,
  }])
}

async function removeCreatorApiTap() {
  await chrome.scripting.unregisterContentScripts({ ids: [CREATOR_API_TAP_ID] }).catch(() => {})
}

function creatorApiRows(value) {
  if (!value || typeof value !== 'object' || !Array.isArray(value.rows)) return []
  return value.rows.filter((row) => row && typeof row === 'object' && typeof row.note_id === 'string' && /^[0-9a-f]{24}$/i.test(row.note_id))
}

function creatorApiSummary(value) {
  if (!value || typeof value !== 'object') return null
  const bounded = (input) => Number.isInteger(input) && input >= 0 && input <= 1_000_000 ? input : 0
  return {
    state: value.state === 'captured' ? 'captured' : 'waiting',
    requestCount: bounded(value.requestCount), responseCount: bounded(value.responseCount),
    rowCount: creatorApiRows(value).length,
  }
}

function creatorApiMayStillPopulate(value) {
  const summary = creatorApiSummary(value)
  if (!summary || summary.rowCount) return false
  if (summary.state === 'waiting') return true
  const capturedAt = Number(value?.capturedAt)
  return Number.isFinite(capturedAt) && Date.now() - capturedAt < CREATOR_API_EMPTY_GRACE_MS
}

function creatorApiSuffix(value) {
  const summary = creatorApiSummary(value)
  if (!summary) return ''
  return ` [creator-api: state=${summary.state}; requests=${summary.requestCount}; responses=${summary.responseCount}; rows=${summary.rowCount}]`
}

function mergeCreatorApiState(state, apiState) {
  const rows = creatorApiRows(apiState)
  if (!rows.length) return state
  return {
    ...state,
    state: 'done', count: rows.length, rows, empty: false,
    api: creatorApiSummary(apiState),
  }
}

async function closeCollection(windowId, tabId) {
  try { if (tabId != null) await chrome.tabs.remove(tabId) } catch {}
  try { if (windowId != null) await chrome.windows.remove(windowId) } catch {}
}

// Collector diagnostics are deliberately small and non-sensitive: the page
// origin/path, title, selector counts, and named login/verification markers.
// Keep only that shape before surfacing it in the extension status or an IPC
// error; never accidentally include page text, cookies, or a full URL query.
function safeDiagnostics(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const boundedString = (input, limit) => typeof input === 'string' ? input.replace(/\s+/g, ' ').slice(0, limit) : ''
  const safePath = (input) => {
    try {
      const url = new URL(input)
      return url.protocol === 'https:' && url.hostname.endsWith('xiaohongshu.com') ? `${url.origin}${url.pathname}` : ''
    } catch { return '' }
  }
  const selectorCounts = {}
  if (value.selectorCounts && typeof value.selectorCounts === 'object' && !Array.isArray(value.selectorCounts)) {
    for (const [key, count] of Object.entries(value.selectorCounts)) {
      if (/^[A-Za-z][A-Za-z0-9]{0,31}$/.test(key) && Number.isInteger(count) && count >= 0 && count <= 1_000_000) selectorCounts[key] = count
    }
  }
  const loginMarkers = Array.isArray(value.loginMarkers)
    ? value.loginMarkers.filter((marker) => typeof marker === 'string' && /^[a-z-]{1,32}$/.test(marker)).slice(0, 8)
    : []
  const tabs = {}
  if (value.tabs && typeof value.tabs === 'object' && !Array.isArray(value.tabs)) {
    for (const key of ['published', 'allNotes']) if (typeof value.tabs[key] === 'boolean') tabs[key] = value.tabs[key]
  }
  return {
    href: safePath(value.href), title: boundedString(value.title, 180),
    selectorCounts, loginMarkers, tabs,
  }
}

function diagnosticSuffix(value) {
  const diagnostics = safeDiagnostics(value)
  if (!diagnostics) return ''
  const selectors = Object.entries(diagnostics.selectorCounts).map(([key, count]) => `${key}=${count}`).join(',')
  const parts = [
    diagnostics.href && `href=${diagnostics.href}`,
    diagnostics.title && `title=${diagnostics.title}`,
    diagnostics.loginMarkers.length && `markers=${diagnostics.loginMarkers.join(',')}`,
    selectors && `selectors=${selectors}`,
  ].filter(Boolean)
  return parts.length ? ` [${parts.join('; ')}]` : ''
}

async function withCollectionWindow(url, cfg, fn) {
  const win = await chrome.windows.create({ url, focused: false, width: 1280, height: 1800, top: 40, left: 40 })
  const tabId = win.tabs?.[0]?.id
  try {
    if (tabId == null) throw new Error('collection window opened without a tab')
    try {
      const groupId = await chrome.tabs.group({ tabIds: [tabId] })
      await chrome.tabGroups.update(groupId, { title: GROUP_TITLE, color: 'blue' })
    } catch { /* grouping is cosmetic; collection remains safe without it */ }
    await waitTabComplete(tabId)
    const settleMs = Number.isFinite(cfg.settleMs) ? Math.min(Math.max(cfg.settleMs, 0), 30_000) : 6000
    await sleep(settleMs)
    return await fn(tabId)
  } finally {
    await closeCollection(win.id, tabId)
  }
}

async function runCollectJob(rawJob) {
  const job = validateJob(rawJob)
  const startedAt = Date.now()
  await setStatus({ source: job.source, phase: 'opening', startedAt, note: job.url })
  const useCreatorApiTap = job.collectorFile === 'collectors/xhs/creator.js'
  if (useCreatorApiTap) await installCreatorApiTap()
  let data
  try {
    data = await withCollectionWindow(job.url, job.cfg, async (tabId) => {
      await execute(tabId, (cfg) => { window.__PERSONAL_DB_XHS_CFG = cfg }, [job.cfg])
      await chrome.scripting.executeScript({ target: { tabId }, files: [job.collectorFile] })
      await setStatus({ phase: 'collecting' })
      const deadline = Date.now() + job.timeoutMs
      while (Date.now() < deadline) {
        await sleep(1500)
        const state = await execute(tabId, (name) => {
          const value = window[name]
          return value ? JSON.parse(JSON.stringify(value)) : null
        }, [job.globalName])
        const apiState = useCreatorApiTap
          ? await executeMain(tabId, (name) => {
            const value = window[name]
            return value ? JSON.parse(JSON.stringify(value)) : null
          }, [CREATOR_API_STATE]).catch(() => null)
          : null
        const mergedState = useCreatorApiTap ? mergeCreatorApiState(state || {}, apiState) : state
        const apiSummary = creatorApiSummary(apiState)
        if (mergedState) {
          await setStatus({
            phase: mergedState.state, count: mergedState.count ?? mergedState.rows?.length ?? mergedState.notes?.length ?? 0,
            durationMs: Date.now() - startedAt, diagnostics: safeDiagnostics(mergedState.diagnostics),
            api: apiSummary,
          })
        }
        if (useCreatorApiTap && (mergedState?.state === 'done' || mergedState?.code === 'selector_mismatch')) {
          // A current creator page can expose unrelated data-impression IDs on
          // cards. Do not ever treat those DOM guesses as a successful sync:
          // once the exact manager endpoint is seen, only its normalized rows
          // can complete collection.
          if (apiSummary?.rowCount) return mergedState
          if (apiSummary?.requestCount) {
            if (creatorApiMayStillPopulate(apiState)) continue
            throw new Error(`XHS creator API returned no recognizable note rows${creatorApiSuffix(apiState)}`)
          }
          if (Date.now() - startedAt < CREATOR_API_OBSERVATION_GRACE_MS) continue
          throw new Error(`XHS creator API was not observed${creatorApiSuffix(apiState)}`)
        }
        if (mergedState?.state === 'done') return mergedState
        if (mergedState?.state === 'error') {
          // The DOM collector can only see presentation cards. When the current
          // UI omits note IDs, give the exact first-party request a chance to
          // finish instead of treating this expected selector mismatch as final.
          if (useCreatorApiTap && mergedState.code === 'selector_mismatch' && creatorApiMayStillPopulate(apiState)) continue
          throw new Error(`${mergedState.message || mergedState.error || 'XHS collector failed'}${diagnosticSuffix(mergedState.diagnostics)}${creatorApiSuffix(apiState)}`)
        }
      }
      throw new Error('timed out before collector reported completion')
    })
  } finally {
    if (useCreatorApiTap) await removeCreatorApiTap()
  }
  const result = { source: job.source, collectedAt: new Date(startedAt).toISOString(), durationMs: Date.now() - startedAt, data }
  await chrome.storage.local.set({ [`last_${job.source}`]: result })
  await setStatus({ phase: 'done', durationMs: result.durationMs, count: data.count ?? data.rows?.length ?? data.notes?.length ?? 0 })
  return result
}

let jobChain = Promise.resolve()
function runSerialized(fn) {
  const result = jobChain.then(fn)
  jobChain = result.catch(() => {})
  return result
}

async function sweepWindows() {
  try {
    const groups = await chrome.tabGroups.query({ title: GROUP_TITLE })
    for (const group of groups) {
      const tabs = await chrome.tabs.query({ groupId: group.id })
      const ids = tabs.map((tab) => tab.id).filter((id) => id != null)
      if (ids.length) await chrome.tabs.remove(ids)
    }
  } catch {}
}

let bridgePort = null
let reconnectTimer = null

function scheduleReconnect() {
  if (reconnectTimer) return
  reconnectTimer = setTimeout(() => { reconnectTimer = null; connectBridge() }, 5000)
}

function connectBridge() {
  if (bridgePort) return
  let port
  try { port = chrome.runtime.connectNative(NATIVE_HOST) } catch { scheduleReconnect(); return }
  bridgePort = port
  port.onMessage.addListener(async (message) => {
    if (!message || typeof message.id !== 'number') return
    try {
      let result
      if (message.cmd === 'ping') result = { pong: true }
      else if (message.cmd === 'collect') result = await runSerialized(() => runCollectJob(message.job))
      else throw new Error('unsupported bridge command')
      port.postMessage({ id: message.id, result })
    } catch (error) {
      const text = String(error?.message || error)
      await setStatus({ phase: 'error', error: text })
      port.postMessage({ id: message.id, error: text })
    }
  })
  port.onDisconnect.addListener(() => { bridgePort = null; scheduleReconnect() })
}

function startup() { sweepWindows().finally(connectBridge) }
chrome.runtime.onInstalled.addListener(startup)
chrome.runtime.onStartup.addListener(startup)
startup()

// Keep a reconnect opportunity available after MV3 suspends an idle worker.
chrome.alarms.create('personal-db-xhs-bridge-keepalive', { periodInMinutes: 0.5 })
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'personal-db-xhs-bridge-keepalive' && !bridgePort) connectBridge()
})
