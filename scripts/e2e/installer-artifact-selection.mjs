export function selectChangedInstaller(before, after) {
  const changed = [...after.entries()]
    .filter(([path, hash]) => before.get(path) !== hash)
    .map(([path]) => path)
  if (changed.length !== 1) {
    const names = changed.map((path) => path.split(/[\\/]/).pop()).join(', ')
    throw new Error(changed.length === 0
      ? 'Tauri 没有生成本次构建的 NSIS 安装包'
      : `Tauri 本次构建生成多个候选安装包：${names}`)
  }
  return changed[0]
}
