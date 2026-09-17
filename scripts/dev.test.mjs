import assert from 'node:assert/strict';
import path from 'node:path';
import test from 'node:test';

const dev = await import('./dev.mjs').catch(() => null);

test('개발 프로세스에 두 Python src 경로와 기존 PYTHONPATH를 함께 전달한다', () => {
  assert.ok(dev, 'scripts/dev.mjs가 필요합니다.');
  const repositoryRoot = path.resolve('/workspace/PickCardU');
  const environment = dev.createChildEnvironment(repositoryRoot, {
    OPENAI_API_KEY: 'already-loaded',
    PYTHONPATH: '/existing/python/path',
  });

  assert.equal(environment.OPENAI_API_KEY, 'already-loaded');
  assert.equal(
    environment.PYTHONPATH,
    [
      path.join(repositoryRoot, 'services/rag-api/src'),
      path.join(repositoryRoot, 'packages/rag-core/src'),
      '/existing/python/path',
    ].join(path.delimiter),
  );
});

test('루트 실행기는 FastAPI와 apps/main 개발 서버를 대상으로 한다', () => {
  assert.ok(dev, 'scripts/dev.mjs가 필요합니다.');
  const repositoryRoot = path.resolve('/workspace/PickCardU');
  const specs = dev.createProcessSpecs(repositoryRoot, { PICKCARDU_PYTHON: 'team-python' }, 'linux');

  assert.deepEqual(
    specs.map(({ command, args, cwd }) => ({ command, args, cwd })),
    [
      {
        command: 'team-python',
        args: ['-m', 'pickcardu_rag_api'],
        cwd: repositoryRoot,
      },
      {
        command: 'npm',
        args: ['--prefix', path.join(repositoryRoot, 'apps/main'), 'run', 'dev'],
        cwd: repositoryRoot,
      },
    ],
  );
});

test('터미널 종료 신호를 자식에게 즉시 중복 전달하지 않고 지연 정리한다', () => {
  assert.ok(dev, 'scripts/dev.mjs가 필요합니다.');
  assert.equal(typeof dev.scheduleFallbackTermination, 'function');
  const receivedSignals = [];
  const children = [{
    child: {
      exitCode: null,
      signalCode: null,
      kill(signal) {
        receivedSignals.push(signal);
      },
    },
  }];
  let fallback;

  dev.scheduleFallbackTermination(children, (callback) => {
    fallback = callback;
    return { unref() {} };
  });

  assert.deepEqual(receivedSignals, []);
  assert.equal(typeof fallback, 'function');
  fallback();
  assert.deepEqual(receivedSignals, ['SIGTERM']);
});
