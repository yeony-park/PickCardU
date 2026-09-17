import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const scriptPath = fileURLToPath(import.meta.url);
const defaultRepositoryRoot = path.resolve(path.dirname(scriptPath), '..');

export function assertSupportedRuntime({ platform = process.platform, nodeVersion = process.versions.node } = {}) {
  if (!['darwin', 'linux'].includes(platform)) {
    throw new Error('PickCardU setup supports macOS or Linux/WSL; native Windows is not supported.');
  }
  const parts = nodeVersion.split('.').map((value) => Number.parseInt(value, 10));
  if (parts.length < 2 || parts.some(Number.isNaN) || parts[0] < 22 || (parts[0] === 22 && parts[1] < 13)) {
    throw new Error(`Node.js 22.13.0 or newer is required; found ${nodeVersion}.`);
  }
}

export function createSetupSpecs(
  repositoryRoot = defaultRepositoryRoot,
  environment = process.env,
  platform = process.platform,
) {
  const python = environment.PICKCARDU_PYTHON || 'python';
  const npm = platform === 'win32' ? 'npm.cmd' : 'npm';
  return [
    {
      name: 'Python 3.11+ check',
      command: python,
      args: [
        '-c',
        'import sys; print(f"Python {sys.version.split()[0]}"); raise SystemExit(0 if sys.version_info >= (3, 11) else "Python 3.11 or newer is required")',
      ],
      cwd: repositoryRoot,
      env: environment,
    },
    {
      name: 'frontend dependencies',
      command: npm,
      args: ['--prefix', path.join(repositoryRoot, 'apps/main'), 'ci'],
      cwd: repositoryRoot,
      env: environment,
    },
    {
      name: 'Python dependencies',
      command: python,
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
      env: environment,
    },
    {
      name: 'RAG assets',
      command: python,
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
      env: environment,
    },
  ];
}

export function runProcess(spec) {
  console.log(`[setup] ${spec.name}`);
  return new Promise((resolve, reject) => {
    const child = spawn(spec.command, spec.args, {
      cwd: spec.cwd,
      env: spec.env,
      stdio: 'inherit',
    });
    child.once('error', reject);
    child.once('close', (code) => resolve(code ?? 1));
  });
}

export async function runSequentially(specs, runner = runProcess) {
  for (const spec of specs) {
    const code = await runner(spec);
    if (code !== 0) {
      throw new Error(`${spec.name} failed with exit code ${code}.`);
    }
  }
}

export async function runSetup(repositoryRoot = defaultRepositoryRoot) {
  assertSupportedRuntime();
  await runSequentially(createSetupSpecs(repositoryRoot));
  console.log('[setup] complete. Run `npm run dev` to start PickCardU.');
}

if (process.argv[1] && path.resolve(process.argv[1]) === scriptPath) {
  try {
    await runSetup();
  } catch (error) {
    console.error(`[setup] ${error instanceof Error ? error.message : String(error)}`);
    process.exitCode = 1;
  }
}
