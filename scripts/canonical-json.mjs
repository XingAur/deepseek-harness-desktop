export function canonicalJson(value, omittedRootKey = '') {
  const sort = (input, root) => {
    if (Array.isArray(input)) return input.map((item) => sort(item, false))
    if (input === null || typeof input !== 'object') return input
    return Object.fromEntries(Object.keys(input).sort().filter((key) => !(root && key === omittedRootKey)).map((key) => [key, sort(input[key], false)]))
  }
  return JSON.stringify(sort(value, true))
}
