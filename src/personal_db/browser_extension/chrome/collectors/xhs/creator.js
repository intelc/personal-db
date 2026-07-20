// Runs in the extension's isolated world. Result: window.__personalDbXhsCreator.
//
// The creator centre is a frequently changing SPA.  Keep extraction based on
// stable data attributes and note IDs, and return small, non-content diagnostics
// when its presentation changes.  In particular, never report a successful empty
// collection for a logged-out, verification, or unrecognised manager page.
(() => {
  const cfg = window.__PERSONAL_DB_XHS_CFG || {}
  const maxScrolls = Math.max(1, Number(cfg.maxScrolls) || 30)
  const delayMs = Math.max(250, Number(cfg.delayMs) || 900)
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
  const compact = (value) => String(value || '').replace(/\s+/g, ' ').trim()
  const text = (element) => compact(element?.textContent)
  const query = (selector, root = document) => {
    try { return Array.from(root.querySelectorAll(selector)) } catch { return [] }
  }
  const visible = (element) => {
    if (!element || element.closest?.('[hidden], [inert], [aria-hidden="true"]')) return false
    try {
      const style = getComputedStyle(element)
      const rect = element.getBoundingClientRect()
      return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden'
    } catch { return true }
  }
  const safeHref = () => {
    try { return `${location.origin}${location.pathname}` } catch { return '' }
  }
  const safeTitle = () => compact(document.title).slice(0, 180)
  const noteIdPattern = /(?:^|[^0-9a-f])([0-9a-f]{24})(?:[^0-9a-f]|$)/i
  const noteIdFromJson = (value, depth = 0) => {
    if (depth > 6 || value == null) return ''
    if (typeof value === 'object') {
      for (const [key, child] of Object.entries(value)) {
        if (/^note_?id$/i.test(key) && typeof child === 'string' && child) return child
        const found = noteIdFromJson(child, depth + 1)
        if (found) return found
      }
    }
    return ''
  }
  const noteIdFromElement = (element) => {
    if (!element) return ''
    const candidates = [element, ...query('[data-impression], [data-note-id], [data-noteid], [data-note-id], a[href]', element)]
    for (const candidate of candidates) {
      const impression = candidate.getAttribute?.('data-impression') || ''
      if (impression) {
        try {
          const id = noteIdFromJson(JSON.parse(impression))
          if (id) return id
        } catch { /* data-impression is only one possible creator-card format */ }
      }
      for (const name of candidate.getAttributeNames?.() || []) {
        const value = candidate.getAttribute(name) || ''
        if (/note[_-]?id/i.test(name) && value) return value
        if (/(?:href|id|impression)/i.test(name)) {
          const match = value.match(noteIdPattern)
          if (match) return match[1]
        }
      }
    }
    return ''
  }
  const cardSelectors = [
    '.note', '.note-item', '.note-card', '[data-note-id]', '[data-noteid]',
    '[data-impression*="noteId"]', '[data-impression*="note_id"]',
    '[class*="note-item"]', '[class*="noteItem"]', '[class*="note-card"]',
    '[class*="noteCard"]', '[class*="NoteCard"]', '[data-testid*="note"]',
  ]
  const cardRoots = () => {
    const roots = new Set()
    for (const selector of cardSelectors) for (const element of query(selector)) roots.add(element)
    // New creator-manager variants often keep the stable note ID only on a
    // descendant.  Promote that descendant to a reasonably sized card parent.
    for (const element of query('[data-impression], [data-note-id], [data-noteid], a[href]')) {
      if (!noteIdFromElement(element)) continue
      roots.add(element.closest?.('.note, .note-item, .note-card, [class*="note-item"], [class*="note-card"], article, li') || element.parentElement || element)
    }
    return Array.from(roots)
  }
  const thumbnailFor = (element) => query('img', element)
    .map((img) => img.currentSrc || img.src || '')
    .find((src) => src && !src.startsWith('data:'))?.split('?')[0].replace(/^http:/, 'https:') || ''
  const collect = () => {
    const rows = new Map()
    for (const element of cardRoots()) {
      const noteId = noteIdFromElement(element)
      if (!noteId) continue
      const row = { note_id: noteId, text: text(element), thumbnail_url: thumbnailFor(element) }
      const previous = rows.get(noteId)
      if (!previous || row.text.length > previous.text.length) rows.set(noteId, row)
    }
    return Array.from(rows.values())
  }
  const selectorCounts = () => ({
    note: query('.note').length,
    noteItem: query('.note-item, [class*="note-item"], [class*="noteItem"]').length,
    noteCard: query('.note-card, [class*="note-card"], [class*="noteCard"], [class*="NoteCard"]').length,
    dataImpression: query('[data-impression]').length,
    dataNoteId: query('[data-note-id], [data-noteid]').length,
    noteLinks: query('a[href]').filter((anchor) => Boolean(noteIdFromElement(anchor))).length,
  })
  const pageText = () => compact(document.body?.innerText || document.body?.textContent).slice(0, 8000)
  const pageMarkers = () => {
    const value = pageText()
    const markers = []
    if (/登录|扫码登录|请先登录|登录后/.test(value) || query('input[type="password"], [class*="login"], [data-testid*="login"]').length) markers.push('login')
    if (/安全验证|人机验证|验证码|滑块验证|captcha|verify/i.test(value) || query('iframe[src*="captcha"], [class*="captcha"], [class*="verify"]').length) markers.push('verification')
    if (/暂无笔记|暂无内容|还没有发布|空空如也/.test(value)) markers.push('empty-state')
    return markers
  }
  const tabs = { published: false, allNotes: false }
  const diagnostics = () => ({
    href: safeHref(),
    title: safeTitle(),
    selectorCounts: selectorCounts(),
    loginMarkers: pageMarkers(),
    tabs: { ...tabs },
  })
  const clickTab = (label) => {
    const candidates = query('[role="tab"], button, a, div, span')
      .filter((element) => visible(element))
      .map((element) => ({ element, value: text(element) }))
      .filter(({ value }) => value === label || (value.startsWith(label) && value.length <= label.length + 12))
      .sort((a, b) => {
        const exact = Number(b.value === label) - Number(a.value === label)
        if (exact) return exact
        const semantic = Number(b.element.matches?.('[role="tab"], button, a')) - Number(a.element.matches?.('[role="tab"], button, a'))
        if (semantic) return semantic
        return a.value.length - b.value.length
      })
    const target = candidates[0]?.element
    if (!target) return false
    try { target.scrollIntoView?.({ block: 'center', inline: 'center' }) } catch {}
    try { target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window })) } catch {}
    try { target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window })) } catch {}
    target.click()
    return true
  }
  const scrollContainers = () => {
    const cards = cardRoots()
    const candidates = [document.scrollingElement, ...query('div, main, section')].filter(Boolean)
    return candidates
      .filter((element) => {
        try {
          const style = getComputedStyle(element)
          return element.scrollHeight > element.clientHeight + 50
            && (/(auto|scroll|overlay)/.test(style.overflowY) || element === document.scrollingElement)
        } catch { return false }
      })
      .sort((a, b) => {
        const aContains = cards.some((card) => a.contains?.(card)) ? 1 : 0
        const bContains = cards.some((card) => b.contains?.(card)) ? 1 : 0
        return bContains - aContains || (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight)
      })
      .slice(0, 4)
  }
  const scrollOnce = () => {
    for (const element of scrollContainers()) {
      element.scrollTop = element.scrollHeight
      try { element.dispatchEvent(new WheelEvent('wheel', { deltaY: 1600, bubbles: true, cancelable: true })) } catch {}
      try { element.dispatchEvent(new Event('scroll', { bubbles: true })) } catch {}
    }
    try { window.scrollTo(0, document.documentElement.scrollHeight) } catch {}
  }
  const publish = (state, extra = {}) => {
    const rows = collect()
    window.__personalDbXhsCreator = { state, count: rows.length, rows, diagnostics: diagnostics(), ...extra }
  }
  const fail = (code, message) => {
    const error = new Error(message)
    error.code = code
    throw error
  }

  publish('running')
  ;(async () => {
    try {
      tabs.published = clickTab('已发布')
      await sleep(1800)
      tabs.allNotes = clickTab('全部笔记')
      for (let retry = 0; retry < 8 && collect().length === 0; retry++) await sleep(1000)

      const initialMarkers = pageMarkers()
      if (initialMarkers.includes('login')) fail('login_required', 'XHS creator centre requires login')
      if (initialMarkers.includes('verification')) fail('verification_required', 'XHS creator centre requires verification')

      let previous = 0
      let stable = 0
      for (let pass = 0; pass < maxScrolls; pass++) {
        scrollOnce()
        await sleep(delayMs)
        const rows = collect()
        stable = rows.length === previous ? stable + 1 : 0
        previous = rows.length
        publish('running', { pass: pass + 1 })
        if (stable >= 3) break
      }
      const rows = collect()
      const markers = pageMarkers()
      if (!rows.length && markers.includes('login')) fail('login_required', 'XHS creator centre requires login')
      if (!rows.length && markers.includes('verification')) fail('verification_required', 'XHS creator centre requires verification')
      if (!rows.length && !markers.includes('empty-state')) {
        fail('selector_mismatch', 'XHS creator centre exposed no recognizable note rows')
      }
      publish('done', { empty: rows.length === 0, complete: stable >= 3, finishedAt: Date.now() })
    } catch (error) {
      publish('error', {
        code: String(error?.code || 'collector_error'),
        message: String(error?.message || error),
      })
    }
  })()
})()
