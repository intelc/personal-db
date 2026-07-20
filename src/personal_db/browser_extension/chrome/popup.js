function render(status) {
  if (!status) { document.getElementById('status').textContent = 'idle'; return }
  const lines = [
    `source:  ${status.source ?? '—'}`,
    `phase:   ${status.phase ?? '—'}`,
    `count:   ${status.count ?? 0}`,
    `elapsed: ${status.durationMs == null ? '—' : `${(status.durationMs / 1000).toFixed(1)}s`}`,
  ]
  if (status.error) lines.push(`error:   ${status.error}`)
  document.getElementById('status').textContent = lines.join('\n')
}

chrome.storage.local.get('status').then((value) => render(value.status))
chrome.storage.onChanged.addListener((changes) => { if (changes.status) render(changes.status.newValue) })
