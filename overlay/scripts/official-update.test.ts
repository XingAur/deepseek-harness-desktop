/** Exercise the shipped updater only in owned local Git fixtures, never the real checkout. */
import assert from 'node:assert/strict'
import test from 'node:test'
import { execFileSync, spawnSync } from 'node:child_process'
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs'
import { basename, dirname, join } from 'node:path'
import { tmpdir } from 'node:os'
import { fileURLToPath, pathToFileURL } from 'node:url'

const source = fileURLToPath(new URL('../../scripts/update-official.sh', import.meta.url))
const release = 'dsh-v0.2.1-rc.3'
const posixOnly = process.platform === 'win32' ? 'the updater requires POSIX bash/git tools' : false

function withFixture(run: (fixture: ReturnType<typeof createFixture>) => void): void {
  const fixture = createFixture()
  try { run(fixture) } finally { fixture.remove() }
}

function createFixture() {
  const parent = realpathSync(tmpdir())
  const directory = mkdtempSync(join(parent, 'dsh-official-update-fixture-'))
  const remove = () => {
    const target = realpathSync(directory)
    assert.equal(target, directory)
    assert.equal(dirname(target), parent)
    assert.ok(basename(target).startsWith('dsh-official-update-fixture-'))
    rmSync(target, { recursive: true, force: true })
  }
  try {
    const root = join(directory, 'distribution')
    const scripts = join(root, 'scripts')
    const dest = join(root, 'deepseek-harness')
    const seed = join(directory, 'seed')
    const remote = join(directory, 'remote.git')
    const home = join(directory, 'empty-home')
    const bin = join(directory, 'bin')
    const pnpmLog = join(directory, 'pnpm.log')
    const pgrepLog = join(directory, 'pgrep.log')
    for (const path of [scripts, seed, home, bin]) mkdirSync(path, { recursive: true })
    const script = join(scripts, 'update-official.sh')
    copyFileSync(source, script)
    writeFileSync(join(root, 'overlay-preserve.txt'), 'overlay-owned fixture must survive\n')
    writeFileSync(join(home, 'preserve.txt'), 'isolated fixture home must survive\n')
    // The updater re-roots PATH at `command -v node`; fake node and pnpm must
    // share this directory so no real package manager is ever selected.
    writeFileSync(join(bin, 'node'), '#!/bin/sh\necho "fixture node must never execute" >&2\nexit 91\n', { mode: 0o755 })
    writeFileSync(join(bin, 'pnpm'), '#!/bin/sh\nprintf "%s\\n" "$*" >> "${DSH_UPDATE_TEST_PNPM_LOG:?}"\nexit 0\n', { mode: 0o755 })
    writeFileSync(join(bin, 'pgrep'), '#!/bin/sh\nprintf "%s\\n" "$*" >> "${DSH_UPDATE_TEST_PGREP_LOG:?}"\nif [ "${DSH_UPDATE_TEST_PROCESS_RUNNING:-0}" = 1 ]; then exit 0; fi\nexit 1\n', { mode: 0o755 })
    const env: NodeJS.ProcessEnv = {
      PATH: `${bin}:/usr/bin:/bin:/usr/sbin:/sbin`, HOME: home, XDG_CONFIG_HOME: home,
      TMPDIR: directory, LANG: 'C', LC_ALL: 'C',
      GIT_CONFIG_GLOBAL: '/dev/null', GIT_CONFIG_NOSYSTEM: '1', GIT_TERMINAL_PROMPT: '0',
      GIT_ALLOW_PROTOCOL: 'file', GIT_AUTHOR_NAME: 'Fixture Author', GIT_AUTHOR_EMAIL: 'fixture@example.invalid',
      GIT_COMMITTER_NAME: 'Fixture Committer', GIT_COMMITTER_EMAIL: 'fixture@example.invalid',
      GIT_CONFIG_COUNT: '4',
      GIT_CONFIG_KEY_0: 'core.hooksPath', GIT_CONFIG_VALUE_0: join(home, 'no-hooks'),
      GIT_CONFIG_KEY_1: 'commit.gpgsign', GIT_CONFIG_VALUE_1: 'false',
      GIT_CONFIG_KEY_2: 'tag.gpgsign', GIT_CONFIG_VALUE_2: 'false',
      GIT_CONFIG_KEY_3: 'core.fsmonitor', GIT_CONFIG_VALUE_3: 'false',
      DSH_UPDATE_TEST_PNPM_LOG: pnpmLog, DSH_UPDATE_TEST_PGREP_LOG: pgrepLog,
    }
    const git = (cwd: string, ...args: string[]) => execFileSync('git', ['-C', cwd, ...args], {
      env, encoding: 'utf8', timeout: 10_000, stdio: ['ignore', 'pipe', 'pipe'],
    }).trim()
    git(seed, 'init', '--initial-branch=master')
    writeFileSync(join(seed, 'upstream.txt'), 'base\n')
    writeFileSync(join(seed, 'untouched.txt'), 'upstream-preserved\n')
    git(seed, 'add', '.')
    git(seed, 'commit', '-m', 'fixture base')
    const base = git(seed, 'rev-parse', 'HEAD')
    git(directory, 'clone', '--bare', seed, remote)
    const url = pathToFileURL(remote).href
    git(seed, 'remote', 'add', 'origin', url)
    git(root, 'clone', url, dest)
    writeFileSync(join(dest, 'untracked-preserve.txt'), 'untracked fixture must survive\n')

    const commit = (text: string, cwd = seed) => {
      writeFileSync(join(cwd, 'upstream.txt'), `${text}\n`)
      git(cwd, 'add', 'upstream.txt')
      git(cwd, 'commit', '-m', text)
      return git(cwd, 'rev-parse', 'HEAD')
    }
    const publish = (tag?: string) => {
      if (tag) git(seed, 'tag', '-a', tag, '-m', 'fixture released tag')
      git(seed, 'push', 'origin', 'master')
      if (tag) git(seed, 'push', 'origin', `refs/tags/${tag}`)
    }
    const log = (path: string) => existsSync(path) ? readFileSync(path, 'utf8').trim().split('\n').filter(Boolean) : []
    const update = (extra: NodeJS.ProcessEnv = {}) => {
      const result = spawnSync('/bin/bash', [script], { env: { ...env, ...extra }, cwd: directory, encoding: 'utf8', timeout: 10_000 })
      if (result.error) throw result.error
      return { status: result.status, output: `${result.stdout}\n${result.stderr}` }
    }
    const preserved = () => {
      assert.equal(readFileSync(join(root, 'overlay-preserve.txt'), 'utf8'), 'overlay-owned fixture must survive\n')
      assert.equal(readFileSync(join(home, 'preserve.txt'), 'utf8'), 'isolated fixture home must survive\n')
      assert.equal(readFileSync(join(dest, 'untouched.txt'), 'utf8'), 'upstream-preserved\n')
      assert.equal(readFileSync(join(dest, 'untracked-preserve.txt'), 'utf8'), 'untracked fixture must survive\n')
      assert.equal(git(dest, 'remote', 'get-url', 'origin'), url)
      assert.deepEqual(log(pgrepLog), [`-f ${dest}/apps/desktop`], 'the fixture pgrep, not the real process list, must be used')
    }
    return { root, dest, seed, directory, remote, home, url, base, git, commit, publish, update, preserved, remove, pnpm: () => log(pnpmLog), pgrep: () => log(pgrepLog) }
  } catch (error) { remove(); throw error }
}

test('released annotated tag fast-forwards local master to that tag, not later remote master', { skip: posixOnly }, () => {
  withFixture(f => {
    const tagged = f.commit('released version')
    f.publish(release)
    const latest = f.commit('unreleased master change')
    f.publish()
    const result = f.update({ DSH_OFFICIAL_REF: release })
    assert.equal(result.status, 0, result.output)
    assert.equal(f.git(f.dest, 'rev-parse', 'HEAD'), tagged)
    assert.notEqual(tagged, latest)
    assert.equal(f.git(f.dest, 'branch', '--show-current'), 'master')
    assert.equal(f.git(f.dest, 'rev-parse', 'FETCH_HEAD^{commit}'), tagged)
    assert.equal(readFileSync(join(f.dest, 'upstream.txt'), 'utf8'), 'released version\n')
    assert.deepEqual(f.pnpm(), ['install --frozen-lockfile', 'run build'])
    assert.match(result.output, /official checkout updated:/)
    f.preserved()
  })
})

test('released tag accepts an explicit local file remote without changing origin configuration', { skip: posixOnly }, () => {
  withFixture(f => {
    const tagged = f.commit('custom fetch released version')
    f.publish(release)
    const result = f.update({ DSH_OFFICIAL_REF: release, DSH_OFFICIAL_FETCH_URL: f.url })
    assert.equal(result.status, 0, result.output)
    assert.equal(f.git(f.dest, 'rev-parse', 'HEAD'), tagged)
    assert.equal(f.git(f.dest, 'branch', '--show-current'), 'master')
    assert.deepEqual(f.pnpm(), ['install --frozen-lockfile', 'run build'])
    f.preserved()
  })
})

test('default master still fast-forwards from origin and builds', { skip: posixOnly }, () => {
  withFixture(f => {
    const latest = f.commit('default master update')
    f.publish()
    const result = f.update()
    assert.equal(result.status, 0, result.output)
    assert.equal(f.git(f.dest, 'rev-parse', 'HEAD'), latest)
    assert.equal(f.git(f.dest, 'rev-parse', 'refs/remotes/origin/master'), latest)
    assert.deepEqual(f.pnpm(), ['install --frozen-lockfile', 'run build'])
    f.preserved()
  })
})

test('default master custom-source path updates the remote-tracking ref and fast-forwards', { skip: posixOnly }, () => {
  withFixture(f => {
    const latest = f.commit('custom-source master update')
    f.publish()
    const result = f.update({ DSH_OFFICIAL_FETCH_URL: f.url })
    assert.equal(result.status, 0, result.output)
    assert.equal(f.git(f.dest, 'rev-parse', 'HEAD'), latest)
    assert.equal(f.git(f.dest, 'rev-parse', 'refs/remotes/origin/master'), latest)
    assert.deepEqual(f.pnpm(), ['install --frozen-lockfile', 'run build'])
    f.preserved()
  })
})

for (const staged of [false, true]) {
  test(`tracked ${staged ? 'staged' : 'unstaged'} edits reject before fetch/build and remain untouched`, { skip: posixOnly }, () => {
    withFixture(f => {
      f.commit('remote update')
      f.publish(release)
      writeFileSync(join(f.dest, 'upstream.txt'), 'local dirty data\n')
      if (staged) f.git(f.dest, 'add', 'upstream.txt')
      const result = f.update({ DSH_OFFICIAL_REF: release })
      assert.notEqual(result.status, 0)
      assert.match(result.output, /tracked local changes/)
      assert.equal(f.git(f.dest, 'rev-parse', 'HEAD'), f.base)
      assert.equal(readFileSync(join(f.dest, 'upstream.txt'), 'utf8'), 'local dirty data\n')
      assert.equal(existsSync(join(f.dest, '.git', 'FETCH_HEAD')), false)
      assert.deepEqual(f.pnpm(), [])
      f.preserved()
    })
  })
}

test('non-fast-forward released tag refuses to merge or overwrite a local commit', { skip: posixOnly }, () => {
  withFixture(f => {
    f.commit('remote divergent version')
    f.publish(release)
    const local = f.commit('local committed version', f.dest)
    const result = f.update({ DSH_OFFICIAL_REF: release })
    assert.notEqual(result.status, 0)
    assert.match(result.output, /fast-forward/i)
    assert.equal(f.git(f.dest, 'rev-parse', 'HEAD'), local)
    assert.equal(readFileSync(join(f.dest, 'upstream.txt'), 'utf8'), 'local committed version\n')
    assert.equal(f.git(f.dest, 'status', '--porcelain', '--untracked-files=no'), '')
    assert.deepEqual(f.pnpm(), [])
    f.preserved()
  })
})

for (const detached of [false, true]) {
  test(`${detached ? 'detached HEAD' : 'wrong branch'} refuses a release update before fetch`, { skip: posixOnly }, () => {
    withFixture(f => {
      f.commit('remote released version')
      f.publish(release)
      if (detached) f.git(f.dest, 'checkout', '--detach', 'HEAD')
      else f.git(f.dest, 'checkout', '-b', 'fixture-topic')
      const result = f.update({ DSH_OFFICIAL_REF: release })
      assert.notEqual(result.status, 0)
      assert.match(result.output, /expected 'master'/)
      assert.equal(f.git(f.dest, 'rev-parse', 'HEAD'), f.base)
      assert.equal(existsSync(join(f.dest, '.git', 'FETCH_HEAD')), false)
      assert.deepEqual(f.pnpm(), [])
      f.preserved()
    })
  })
}

test('malformed release tags reject before inspecting a process or fetching refs', { skip: posixOnly }, () => {
  withFixture(f => {
    for (const value of ['dsh-v1.2', 'dsh-v1.2.3;echo injected', 'dsh-v../master', 'dsh-v1.2.3 ref', 'dsh-v1.2.3\nmaster']) {
      const result = f.update({ DSH_OFFICIAL_REF: value })
      assert.notEqual(result.status, 0)
      assert.match(result.output, /invalid official release tag/)
      assert.equal(f.git(f.dest, 'rev-parse', 'HEAD'), f.base)
    }
    assert.equal(existsSync(join(f.dest, '.git', 'FETCH_HEAD')), false)
    assert.deepEqual(f.pnpm(), [])
    assert.deepEqual(f.pgrep(), [])
  })
})

test('a simulated running desktop rejects without invoking real pgrep or build', { skip: posixOnly }, () => {
  withFixture(f => {
    const result = f.update({ DSH_UPDATE_TEST_PROCESS_RUNNING: '1' })
    assert.notEqual(result.status, 0)
    assert.match(result.output, /desktop is running/)
    assert.equal(f.git(f.dest, 'rev-parse', 'HEAD'), f.base)
    assert.equal(existsSync(join(f.dest, '.git', 'FETCH_HEAD')), false)
    assert.deepEqual(f.pnpm(), [])
    f.preserved()
  })
})

test('a syntactically valid but missing release never changes HEAD or builds', { skip: posixOnly }, () => {
  withFixture(f => {
    const result = f.update({ DSH_OFFICIAL_REF: 'dsh-v99.99.99-rc.1' })
    assert.notEqual(result.status, 0)
    assert.match(result.output, /couldn't find remote ref/)
    assert.equal(f.git(f.dest, 'rev-parse', 'HEAD'), f.base)
    assert.deepEqual(f.pnpm(), [])
    f.preserved()
  })
})

test('already-current checkout with complete artifact markers skips package installation', { skip: posixOnly }, () => {
  withFixture(f => {
    mkdirSync(join(f.dest, 'node_modules'))
    mkdirSync(join(f.dest, 'apps/web/dist'), { recursive: true })
    writeFileSync(join(f.dest, 'apps/web/dist/index.html'), 'fixture artifact\n')
    const result = f.update()
    assert.equal(result.status, 0, result.output)
    assert.match(result.output, /already current/)
    assert.equal(f.git(f.dest, 'rev-parse', 'HEAD'), f.base)
    assert.deepEqual(f.pnpm(), [])
    f.preserved()
  })
})

test('already-current checkout without artifact markers still installs and builds', { skip: posixOnly }, () => {
  withFixture(f => {
    const result = f.update()
    assert.equal(result.status, 0, result.output)
    assert.equal(f.git(f.dest, 'rev-parse', 'HEAD'), f.base)
    assert.deepEqual(f.pnpm(), ['install --frozen-lockfile', 'run build'])
    f.preserved()
  })
})
