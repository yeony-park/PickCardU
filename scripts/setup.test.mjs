import assert from 'node:assert/strict';
import path from 'node:path';
import test from 'node:test';
import { mkdtempSync, mkdirSync, readFileSync, readdirSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const setup = await import('./setup.mjs').catch(() => null);

test('macOS와 Linux에서 Node 22.13 이상만 허용한다', () => {
  assert.ok(setup, 'scripts/setup.mjs가 필요합니다.');
  assert.doesNotThrow(() => setup.assertSupportedRuntime({ platform: 'darwin', nodeVersion: '22.13.0' }));
  assert.doesNotThrow(() => setup.assertSupportedRuntime({ platform: 'linux', nodeVersion: '24.1.0' }));
  assert.throws(
    () => setup.assertSupportedRuntime({ platform: 'win32', nodeVersion: '22.13.0' }),
    /macOS or Linux\/WSL/,
  );
  assert.throws(
    () => setup.assertSupportedRuntime({ platform: 'darwin', nodeVersion: '22.12.9' }),
    /22\.13\.0 or newer/,
  );
});

test('프론트와 Python을 설치하지 않고 확인한 뒤 프로젝트 로컬 자산만 준비한다', () => {
  assert.ok(setup, 'scripts/setup.mjs가 필요합니다.');
  const repositoryRoot = path.resolve('/workspace/PickCardU');
  const specs = setup.createSetupSpecs(
    repositoryRoot,
    { PICKCARDU_PYTHON: 'team-python', PYTHONPATH: '/shared/python' },
  );

  assert.deepEqual(
    specs.map(({ name, command, args, cwd }) => ({ name, command, args, cwd })),
    [
      {
        name: 'frontend dependencies check',
        command: process.execPath,
        args: [fileURLToPath(new URL('./setup.mjs', import.meta.url)), '--check-frontend', path.join(repositoryRoot, 'apps/main')],
        cwd: repositoryRoot,
      },
      {
        name: 'Python 3.11+ check',
        command: 'team-python',
        args: [
          '-c',
          'import sys; print(f"Python {sys.version.split()[0]}"); raise SystemExit(0 if sys.version_info >= (3, 11) else "Python 3.11 or newer is required")',
        ],
        cwd: repositoryRoot,
      },
      {
        name: 'Python runtime dependencies check',
        command: 'team-python',
        args: [
          '-c',
          setup.PYTHON_RUNTIME_CHECK,
        ],
        cwd: repositoryRoot,
      },
      {
        name: 'RAG assets',
        command: 'team-python',
        args: [
          'scripts/setup_assets.py',
          '--config',
          path.join(repositoryRoot, 'config/dev-assets.json'),
          '--repository-root',
          repositoryRoot,
          '--runtime-root',
          path.join(repositoryRoot, 'data/rag/runtime'),
        ],
        cwd: repositoryRoot,
      },
    ],
  );

  assert.equal(specs.some(({ args }) => args.includes('pip')), false);
  assert.equal(
    specs.at(-1).env.PYTHONPATH,
    [
      path.join(repositoryRoot, 'services/rag-api/src'),
      path.join(repositoryRoot, 'packages/rag-core/src'),
      '/shared/python',
    ].join(path.delimiter),
  );
});

test('PICKCARDU_PYTHON이 없으면 현재 PATH의 python을 사용한다', () => {
  assert.ok(setup, 'scripts/setup.mjs가 필요합니다.');
  const specs = setup.createSetupSpecs('/workspace/PickCardU', {});

  assert.equal(specs[0].command, process.execPath);
  assert.equal(specs[1].command, 'python');
  assert.equal(specs[2].command, 'python');
  assert.equal(specs[3].command, 'python');
});

function fixture(t, installed = {}, applicationDirectory = '') {
  const temporaryRoot = mkdtempSync(path.join(tmpdir(), 'pickcardu-setup-test-'));
  t.after(() => rmSync(temporaryRoot, { recursive: true, force: true }));
  const root = path.join(temporaryRoot, applicationDirectory);
  mkdirSync(root, { recursive: true });
  writeFileSync(path.join(root, 'package.json'), JSON.stringify({
    dependencies: { react: '19.2.6' },
    devDependencies: { '@types/react': '19.2.14', tailwindcss: '4.3.3' },
  }));
  for (const [name, metadata] of Object.entries(installed)) {
    const directory = path.join(root, 'node_modules', name);
    mkdirSync(directory, { recursive: true });
    writeFileSync(path.join(directory, 'package.json'), metadata);
  }
  return root;
}

function snapshot(root) {
  return readdirSync(root, { recursive: true, withFileTypes: true })
    .filter((entry) => entry.isFile())
    .map((entry) => {
      const filename = path.join(entry.parentPath, entry.name);
      return [path.relative(root, filename), readFileSync(filename, 'utf8')];
    }).sort(([a], [b]) => a.localeCompare(b));
}

test('누락된 프론트 패키지를 모두 안내하고 파일이나 node_modules를 생성하지 않는다', (t) => {
  const root = fixture(t);
  const before = snapshot(root);
  assert.equal(typeof setup.checkFrontendDependencies, 'function');
  assert.throws(() => setup.checkFrontendDependencies(root), /react, @types\/react, tailwindcss/);
  assert.deepEqual(snapshot(root), before);
  assert.deepEqual(readdirSync(root), ['package.json']);
});

test('설치 버전이 선언 버전과 달라도 기존 프론트 패키지를 교체하지 않는다', (t) => {
  const installed = Object.fromEntries(['react', '@types/react', 'tailwindcss'].map((name) => [
    name, JSON.stringify({ name, version: '1.0.0' }),
  ]));
  const root = fixture(t, installed);
  const before = snapshot(root);
  assert.equal(typeof setup.checkFrontendDependencies, 'function');
  assert.deepEqual(setup.checkFrontendDependencies(root), ['react', '@types/react', 'tailwindcss']);
  assert.deepEqual(snapshot(root), before);
});

test('손상된 패키지 메타데이터는 자동 복구하지 않고 실패한다', (t) => {
  const root = fixture(t, {
    react: '{broken',
    '@types/react': JSON.stringify({ name: '@types/react' }),
    tailwindcss: JSON.stringify({ name: 'tailwindcss', version: '4.3.3' }),
  });
  const before = snapshot(root);
  assert.equal(typeof setup.checkFrontendDependencies, 'function');
  assert.throws(() => setup.checkFrontendDependencies(root), /react, @types\/react/);
  assert.deepEqual(snapshot(root), before);
});

test('프로젝트 로컬 node_modules의 정상 심볼릭 링크 패키지도 확인한다', (t) => {
  const installed = Object.fromEntries(['react', '@types/react'].map((name) => [
    name, JSON.stringify({ name, version: '1.0.0' }),
  ]));
  const root = fixture(t, installed);
  const target = path.join(root, 'linked-tailwind');
  mkdirSync(target);
  writeFileSync(path.join(target, 'package.json'), JSON.stringify({ name: 'tailwindcss', version: '4.3.3' }));
  const link = path.join(root, 'node_modules/tailwindcss');
  symlinkSync(target, link, 'dir');
  const before = readFileSync(path.join(target, 'package.json'), 'utf8');
  assert.deepEqual(setup.checkFrontendDependencies(root), ['react', '@types/react', 'tailwindcss']);
  assert.equal(readFileSync(path.join(target, 'package.json'), 'utf8'), before);
});

test('실제 프론트 확인 명령이 실패하면 Python 검사나 자산 다운로드로 넘어가지 않는다', async (t) => {
  const root = fixture(t, {}, 'apps/main');
  const specs = setup.createSetupSpecs(path.resolve(root, '../..'));
  const calls = [];
  let output = '';
  await assert.rejects(setup.runSequentially(specs, async (spec) => {
    calls.push(spec.name);
    if (spec.command !== process.execPath) return 7;
    const result = spawnSync(spec.command, spec.args, { encoding: 'utf8' });
    output = result.stdout + result.stderr;
    return result.status ?? 1;
  }), /frontend dependencies check failed/);
  assert.deepEqual(calls, ['frontend dependencies check']);
  assert.match(output, /Prepare.*manually/);
  assert.match(output, /does not install/);
  assert.deepEqual(readdirSync(root), ['package.json']);
});

test('명령 실패 시 다음 setup 단계를 실행하지 않는다', async () => {
  assert.ok(setup, 'scripts/setup.mjs가 필요합니다.');
  const calls = [];
  const specs = [{ name: 'first' }, { name: 'second' }];

  await assert.rejects(
    setup.runSequentially(specs, async (spec) => {
      calls.push(spec.name);
      return 7;
    }),
    /first failed with exit code 7/,
  );
  assert.deepEqual(calls, ['first']);
});
