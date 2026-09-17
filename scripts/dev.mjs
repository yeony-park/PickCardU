import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const scriptPath = fileURLToPath(import.meta.url);
const defaultRepositoryRoot = path.resolve(path.dirname(scriptPath), '..');

export function createChildEnvironment(repositoryRoot, baseEnvironment = process.env) {
  const pythonPaths = [
    path.join(repositoryRoot, 'services/rag-api/src'),
    path.join(repositoryRoot, 'packages/rag-core/src'),
  ];
  if (baseEnvironment.PYTHONPATH) {
    pythonPaths.push(baseEnvironment.PYTHONPATH);
  }
  return {
    ...baseEnvironment,
    PYTHONPATH: pythonPaths.join(path.delimiter),
  };
}

export function createProcessSpecs(
  repositoryRoot = defaultRepositoryRoot,
  environment = process.env,
  platform = process.platform,
) {
  const childEnvironment = createChildEnvironment(repositoryRoot, environment);
  return [
    {
      name: 'api',
      command: environment.PICKCARDU_PYTHON || 'python',
      args: ['-m', 'pickcardu_rag_api'],
      cwd: repositoryRoot,
      env: childEnvironment,
    },
    {
      name: 'web',
      command: platform === 'win32' ? 'npm.cmd' : 'npm',
      args: ['--prefix', path.join(repositoryRoot, 'apps/main'), 'run', 'dev'],
      cwd: repositoryRoot,
      env: childEnvironment,
    },
  ];
}

function terminateChildren(children, signal) {
  for (const { child } of children) {
    if (child.exitCode === null && child.signalCode === null) {
      child.kill(signal);
    }
  }
}

export function scheduleFallbackTermination(children, setTimeoutImpl = setTimeout) {
  const timer = setTimeoutImpl(() => terminateChildren(children, 'SIGTERM'), 2000);
  timer.unref?.();
  return timer;
}

export async function runDevelopment(repositoryRoot = defaultRepositoryRoot) {
  const children = createProcessSpecs(repositoryRoot).map((spec) => {
    console.log(`[${spec.name}] ${spec.command} ${spec.args.join(' ')}`);
    const child = spawn(spec.command, spec.args, {
      cwd: spec.cwd,
      env: spec.env,
      stdio: 'inherit',
    });
    return { ...spec, child };
  });
  let stopping = false;

  function stop(signal) {
    if (stopping) return;
    stopping = true;
    terminateChildren(children, signal);
  }

  function handleTerminalSignal(exitCode) {
    if (stopping) return;
    stopping = true;
    process.exitCode = exitCode;
    scheduleFallbackTermination(children);
  }

  process.once('SIGINT', () => {
    handleTerminalSignal(130);
  });
  process.once('SIGTERM', () => {
    handleTerminalSignal(143);
  });

  for (const { name, child } of children) {
    child.once('error', (error) => {
      console.error(`[${name}] ${error.message}`);
      process.exitCode = 1;
      stop('SIGTERM');
    });
    child.once('exit', (code, signal) => {
      if (!stopping) {
        console.error(`[${name}] exited (${signal ?? code ?? 'unknown'})`);
        process.exitCode = code ?? 1;
        stop('SIGTERM');
      }
    });
  }

  await Promise.all(children.map(({ child }) => new Promise((resolve) => {
    child.once('close', resolve);
  })));
}

if (process.argv[1] && path.resolve(process.argv[1]) === scriptPath) {
  await runDevelopment();
}
