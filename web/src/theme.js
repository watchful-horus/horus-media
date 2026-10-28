// Light / dark / follow-the-system. The choice is remembered in this browser only.
const KEY = 'horus.theme'
export const MODES = ['auto', 'light', 'dark']

export function savedMode() {
  try {
    const v = localStorage.getItem(KEY)
    return MODES.includes(v) ? v : 'auto'
  } catch {
    return 'auto'
  }
}

export function applyMode(mode) {
  const root = document.documentElement
  if (mode === 'auto') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', mode)
  try {
    localStorage.setItem(KEY, mode)
  } catch {
    /* private window: the choice just doesn't stick */
  }
  window.dispatchEvent(new Event('horus:theme')) // charts redraw in the new colours
}

export function nextMode(mode) {
  return MODES[(MODES.indexOf(mode) + 1) % MODES.length]
}
