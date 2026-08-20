const runtimeTagPattern = /^runtime-v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$/
const repositoryPattern = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/

export function runtimeSourceConfig({ tag, repository, target = 'windows-x86_64' }) {
  if (!runtimeTagPattern.test(tag)) {
    throw new Error('Runtime tag 必须是固定的 runtime-v<semver>，不能使用 latest、branch 或 URL')
  }
  if (!repositoryPattern.test(repository)) {
    throw new Error('Repository 必须是 owner/repo')
  }
  if (!/^(windows-x86_64|darwin-aarch64)$/.test(target)) {
    throw new Error(`不支持的 Runtime target: ${target}`)
  }
  return {
    endpoint: `https://github.com/${repository}/releases/download/${tag}/runtime-${target}.json`,
    allowedHosts: [
      'github.com',
      'objects.githubusercontent.com',
      'release-assets.githubusercontent.com',
    ],
  }
}

if (import.meta.url === `file://${process.argv[1]?.replaceAll('\\', '/')}`) {
  const [, , tag, repository, target] = process.argv
  try {
    process.stdout.write(`${JSON.stringify(runtimeSourceConfig({ tag, repository, target }), null, 2)}\n`)
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`)
    process.exitCode = 1
  }
}
