// Runs in the extension's isolated world. Result: window.__personalDbXhsSaved.
// XHS lazy-loads saved notes only after real scroll/wheel activity, so the
// collector scrolls the feed container rather than querying a private API.
(() => {
  const cfg = window.__PERSONAL_DB_XHS_CFG || {}
  const maxScrolls = Math.max(1, Number(cfg.maxScrolls) || 240)
  const delayMs = Math.max(250, Number(cfg.delayMs) || 900)
  const knownIds = new Set(Array.isArray(cfg.knownIds) ? cfg.knownIds : [])
  const overlapStop = Number.isInteger(cfg.overlapStop) ? cfg.overlapStop : 25
  const deepBackfill = cfg.deepBackfill === true
  const noteRe = /(?:explore|user\/profile\/[^/]+)\/([0-9a-f]{24})(?:[/?#]|$)/i
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
  const text = (element) => (element?.innerText || element?.textContent || '').replace(/\s+/g, ' ').trim()
  const visible = (element) => {
    const rect = element.getBoundingClientRect()
    const style = getComputedStyle(element)
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none'
  }
  const hrefFor = (anchor) => {
    try { return new URL(anchor.getAttribute('href'), location.href).href } catch { return '' }
  }
  const noteLinks = (root = document) => Array.from(root.querySelectorAll('a[href]'))
    .filter((anchor) => noteRe.test(hrefFor(anchor)))
  const savedActive = () => Array.from(document.querySelectorAll('.reds-tab-item'))
    .some((element) => text(element).startsWith('收藏') && String(element.className).includes('active'))
  const clickSavedTab = () => {
    const candidates = Array.from(document.querySelectorAll('[role="tab"], .reds-tab-item, button, a, div, span'))
      .filter((element) => visible(element) && (text(element) === '收藏' || text(element).startsWith('收藏 ')))
      .sort((a, b) => a.getBoundingClientRect().width - b.getBoundingClientRect().width)
    const target = candidates[0]?.closest('.reds-tab-item, button, a') || candidates[0]
    if (!target) return false
    target.scrollIntoView({ block: 'center', inline: 'center' })
    target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }))
    target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }))
    target.click()
    return true
  }
  const expectedCount = () => {
    const active = Array.from(document.querySelectorAll('.reds-tab-item.active')).map(text).find((label) => label.startsWith('笔记')) || ''
    const match = active.match(/笔记[・·\s]*(\d+)/)
    return match ? Number(match[1]) : 0
  }
  const feedContainer = () => {
    const direct = document.querySelector('.tab-content-item')
    if (direct?.scrollHeight > direct?.clientHeight + 20) return direct
    const link = noteLinks()[0]
    for (let element = link?.parentElement; element && element !== document.documentElement; element = element.parentElement) {
      const style = getComputedStyle(element)
      if (element.scrollHeight > element.clientHeight + 20 && /(auto|scroll|overlay)/.test(style.overflowY)) return element
    }
    return direct || document.scrollingElement || document.documentElement
  }
  const scrollFeed = async () => {
    const element = feedContainer()
    element.scrollTop = Math.max(0, element.scrollTop - 300)
    element.dispatchEvent(new Event('scroll', { bubbles: true }))
    await sleep(120)
    element.scrollTop = element.scrollHeight
    element.dispatchEvent(new WheelEvent('wheel', { deltaY: 1600, bubbles: true, cancelable: true }))
    element.dispatchEvent(new Event('scroll', { bubbles: true }))
    noteLinks().at(-1)?.scrollIntoView({ block: 'end' })
    window.scrollTo(0, (document.scrollingElement || document.documentElement).scrollHeight)
  }

  const notes = new Map()
  const collect = () => {
    const added = []
    for (const anchor of noteLinks()) {
      const href = hrefFor(anchor)
      const match = href.match(noteRe)
      if (!match) continue
      const noteId = match[1].toLowerCase()
      const url = new URL(href)
      const previous = notes.get(noteId) || {}
      const candidateTitle = text(anchor) || text(anchor.closest('section, article, div'))
      notes.set(noteId, {
        note_id: noteId, url: href,
        title: previous.title || candidateTitle.slice(0, 180),
        xsec_token: url.searchParams.get('xsec_token') || previous.xsec_token || '',
        xsec_source: url.searchParams.get('xsec_source') || previous.xsec_source || '',
        first_seen_url: previous.first_seen_url || href,
      })
      if (!previous.note_id) added.push(noteId)
    }
    return added
  }
  const publish = (state, extra = {}) => {
    window.__personalDbXhsSaved = {
      state, count: notes.size, notes: Array.from(notes.values()), knownIdCount: knownIds.size,
      overlapStop, incremental: !deepBackfill, ...extra,
    }
  }

  window.__personalDbXhsSaved = { state: 'running', notes: [], clickedSaved: false }
  ;(async () => {
    try {
      const url = new URL(location.href)
      if (url.searchParams.get('tab') !== 'fav' || url.searchParams.get('subTab') !== 'note') {
        throw new Error('XHS profile must use tab=fav&subTab=note')
      }
      const clickedSaved = savedActive() || clickSavedTab()
      await sleep(2200)
      if (!savedActive()) throw new Error('XHS saved tab is not active')
      for (let retry = 0, stable = 0, previous = 0; retry < 12; retry++) {
        await sleep(700)
        const count = noteLinks().length
        stable = count > 0 && count === previous ? stable + 1 : 0
        previous = count
        if (stable >= 2) break
      }
      collect()
      const expected = expectedCount()
      let previous = notes.size
      let stable = 0
      let overlapRun = 0
      let stoppedForOverlap = false
      let exhausted = false
      for (let scroll = 0; scroll < maxScrolls; scroll++) {
        await scrollFeed()
        await sleep(Math.min(Math.max(delayMs, 1500) + stable * 300, 3000))
        for (const noteId of collect()) overlapRun = knownIds.has(noteId) ? overlapRun + 1 : 0
        stable = notes.size === previous ? stable + 1 : 0
        previous = notes.size
        publish('running', { clickedSaved, scrolls: scroll + 1, expectedCount: expected, overlapRun })
        if (!deepBackfill && overlapStop > 0 && overlapRun >= overlapStop) { stoppedForOverlap = true; break }
        if (stable >= 5) { exhausted = true; break }
      }
      collect()
      publish('done', {
        href: location.href, title: document.title, clickedSaved: savedActive(),
        scrolls: window.__personalDbXhsSaved.scrolls || 0, expectedCount: expected,
        stoppedForOverlap, complete: stoppedForOverlap || exhausted || (expected > 0 && notes.size >= expected),
        overlapRun, finishedAt: Date.now(),
      })
    } catch (error) {
      window.__personalDbXhsSaved = { state: 'error', error: String(error), message: error?.message }
    }
  })()
})()
