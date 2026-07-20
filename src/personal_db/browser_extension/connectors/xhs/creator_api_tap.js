// App-owned XHS creator API tap. The stable extension runtime registers this
// only after validating the catalog hash and the baked XHS policy.
(() => {
  const STATE = '__personalDbXhsCreatorApiTap'
  const MANAGER_PATH = '/new/note-manager'
  const ALLOWED_ENDPOINTS = new Set([
    'https://creator.xiaohongshu.com/api/galaxy/v2/creator/note/user/posted',
    'https://edith.xiaohongshu.com/web_api/sns/v5/creator/note/managemaent/search',
  ])
  if (location.pathname !== MANAGER_PATH || window[STATE]) return

  const rows = new Map()
  const state = { state: 'waiting', requestCount: 0, responseCount: 0, rows: [] }
  window[STATE] = state
  const noteId = (value) => typeof value === 'string' && /^[0-9a-f]{24}$/i.test(value) ? value.toLowerCase() : ''
  const string = (value, limit = 500) => typeof value === 'string' ? value.replace(/\s+/g, ' ').trim().slice(0, limit) : ''
  const count = (value) => (typeof value === 'number' || typeof value === 'string') ? value : null
  const first = (...values) => values.find((value) => value !== undefined && value !== null && value !== '')
  const imageUrl = (value) => {
    const candidate = typeof value === 'string' ? value : value && typeof value === 'object' ? first(value.url, value.url_default, value.urlDefault, value.origin_url, value.originUrl) : ''
    try { const url = new URL(String(candidate || ''), location.origin); return /^https?:$/.test(url.protocol) ? `${url.origin}${url.pathname}` : '' } catch { return '' }
  }
  const normalize = (item) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return null
    const id = noteId(first(item.note_id, item.noteId, item.id))
    const title = string(first(item.title, item.display_title, item.displayTitle, item.note_title, item.noteTitle))
    const cover = first(item.cover, item.cover_info, item.coverInfo, item.image, item.images?.[0], item.imagesList?.[0], item.images_list?.[0], item.image_list?.[0], item.imageList?.[0])
    const thumbnail = imageUrl(cover)
    const hasNoteFields = Boolean(title || thumbnail || item.type || item.refTip || item.videoInfo || item.video_info || item.likes !== undefined || item.like_count !== undefined || item.likeCount !== undefined)
    if (!id || !hasNoteFields) return null
    const interact = first(item.interact_info, item.interactInfo, item.interaction, {}) || {}
    return {
      note_id: id, title, thumbnail_url: thumbnail,
      posted_at: first(item.publish_time, item.publishTime, item.published_at, item.publishedAt, item.create_time, item.createTime, item.time),
      visibility_label: string(first(item.visibility_label, item.visibilityLabel, item.visibility, item.note_visibility, item.permission_msg, item.permissionMsg, item.refTip), 80),
      note_type: string(item.type, 40),
      video_duration: count(first(item.videoInfo?.duration, item.video_info?.duration, item.video_duration, item.videoDuration)),
      view_count: count(first(item.view_count, item.viewCount, item.browse_count, item.browseCount, interact.view_count, interact.viewCount, interact.browse_count, interact.browseCount)),
      comment_count: count(first(item.comment_count, item.commentCount, item.comments_count, interact.comment_count, interact.commentCount, interact.comments_count)),
      liked_count: count(first(item.likes, item.like_count, item.likeCount, item.liked_count, item.likedCount, interact.likes, interact.like_count, interact.likeCount, interact.liked_count, interact.likedCount)),
      collected_count: count(first(item.collect_count, item.collectCount, item.collected_count, item.collectedCount, interact.collect_count, interact.collectCount, interact.collected_count, interact.collectedCount)),
      share_count: count(first(item.share_count, item.shareCount, item.shared_count, interact.share_count, interact.shareCount, interact.shared_count)), source: 'creator-api',
    }
  }
  const publish = () => { state.rows = Array.from(rows.values()); state.state = state.responseCount ? 'captured' : 'waiting'; state.capturedAt = Date.now() }
  const record = (payload) => {
    const seen = new WeakSet()
    const walk = (value, depth = 0) => {
      if (!value || typeof value !== 'object' || depth > 10 || seen.has(value)) return
      seen.add(value); const row = normalize(value)
      if (row) rows.set(row.note_id, { ...rows.get(row.note_id), ...row })
      for (const child of Array.isArray(value) ? value : Object.values(value)) walk(child, depth + 1)
    }
    walk(payload); state.responseCount += 1; publish()
  }
  const exactSearch = (input) => { try { const url = new URL(typeof input === 'string' ? input : input?.url, location.href); return ALLOWED_ENDPOINTS.has(`${url.origin}${url.pathname}`) } catch { return false } }
  const captureResponse = (response) => { if (response?.ok) response.clone().json().then(record).catch(() => { state.responseCount += 1; publish() }) }
  const originalFetch = window.fetch
  if (typeof originalFetch === 'function') window.fetch = function (...args) { const requested = exactSearch(args[0]); if (requested) state.requestCount += 1; const result = originalFetch.apply(this, args); if (requested) Promise.resolve(result).then(captureResponse).catch(() => {}); return result }
  const originalOpen = XMLHttpRequest.prototype.open
  const originalSend = XMLHttpRequest.prototype.send
  XMLHttpRequest.prototype.open = function (method, url, ...args) { this.__personalDbXhsCreatorSearch = exactSearch(url); return originalOpen.call(this, method, url, ...args) }
  XMLHttpRequest.prototype.send = function (...args) {
    if (this.__personalDbXhsCreatorSearch) {
      state.requestCount += 1
      this.addEventListener('loadend', () => { if (this.status < 200 || this.status >= 300) return; try { record(this.responseType === 'json' ? this.response : JSON.parse(this.responseText)) } catch { state.responseCount += 1; publish() } }, { once: true })
    }
    return originalSend.apply(this, args)
  }
})()
