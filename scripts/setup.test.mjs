import assert from 'node:assert/strict';
import path from 'node:path';
import test from 'node:test';

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

test('현재 선택한 Python으로 의존성과 자산을 순서대로 준비한다', () => {
  assert.ok(setup, 'scripts/setup.mjs가 필요합니다.');
  const repositoryRoot = path.resolve('/workspace/PickCardU');
  const specs = setup.createSetupSpecs(
    repositoryRoot,
    { PICKCARDU_PYTHON: 'team-python' },
    'darwin',
  );

  assert.deepEqual(
    specs.map(({ name, command, args, cwd }) => ({ name, command, args, cwd })),
    [
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
        name: 'frontend dependencies',
        command: 'npm',
        args: ['--prefix', path.join(repositoryRoot, 'apps/main'), 'ci'],
        cwd: repositoryRoot,
      },
      {
        name: 'Python dependencies',
        command: 'team-python',
        args: [
          '-m',
          'pip',
          'install',
          '-e',
          'packages/rag-core[reranker]',
          '-e',
          'services/rag-api',
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
});

test('PICKCARDU_PYTHON이 없으면 python을 사용하고 Windows에서는 npm.cmd를 선택한다', () => {
  assert.ok(setup, 'scripts/setup.mjs가 필요합니다.');
  const specs = setup.createSetupSpecs('C:\\PickCardU', {}, 'win32');

  assert.equal(specs[0].command, 'python');
  assert.equal(specs[1].command, 'npm.cmd');
  assert.equal(specs[2].command, 'python');
  assert.equal(specs[3].command, 'python');
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
