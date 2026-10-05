import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const scriptPath = fileURLToPath(import.meta.url);
const defaultRepositoryRoot = path.resolve(path.dirname(scriptPath), '..');

export const PYTHON_RUNTIME_CHECK = [
  'import importlib',
  'modules = ["chromadb", "dotenv", "fastapi", "huggingface_hub", "numpy", "openai", "pydantic", "pickcardu_rag", "pickcardu_rag_api.release_install", "torch", "transformers", "uvicorn"]',
  'missing = []',
  'for name in modules:',
  '    try:',
  '        importlib.import_module(name)',
  '    except Exception as error:',
  '        missing.append(f"{name} ({type(error).__name__})")',
  'if missing:',
  '    raise SystemExit("Missing or unusable Python runtime dependencies: " + ", ".join(missing))',
  'print("Python runtime dependencies are available; existing versions were not changed.")',
].join('\n');

export function assertSupportedRuntime({ platform = process.platform, nodeVersion = process.versions.node } = {}) {
  if (!['darwin', 'linux'].includes(platform)) {
    throw new Error('PickCardU setup supports macOS or Linux/WSL; native Windows is not supported.');
  }
  const parts = nodeVersion.split('.').map((value) => Number.parseInt(value, 10));
  if (parts.length < 2 || parts.some(Number.isNaN) || parts[0] < 22 || (parts[0] === 22 && parts[1] < 13)) {
    throw new Error(`Node.js 22.13.0 or newer is required; found ${nodeVersion}.`);
  }
}

export function checkFrontendDependencies(applicationRoot) {
  const manifest = JSON.parse(readFileSync(path.join(applicationRoot, 'package.json'), 'utf8'));
  const names = Object.keys({ ...manifest.dependencies, ...manifest.devDependencies });
  const missing = names.filter((name) => {
    try {
      const installed = JSON.parse(readFileSync(path.join(applicationRoot, 'node_modules', name, 'package.json'), 'utf8'));
      return installed.name !== name || typeof installed.version !== 'string' || !installed.version;
    } catch {
      return true;
    }
  });
  if (missing.length) {
    throw new Error(`Missing or unreadable frontend packages: ${missing.join(', ')}. Prepare frontend dependencies manually in ${applicationRoot}, then rerun setup. Setup does not install, remove, or update packages.`);
  }
  return names;
}

export function createSetupSpecs(
  repositoryRoot = defaultRepositoryRoot,
  environment = process.env,
) {
  const python = environment.PICKCARDU_PYTHON || 'python';
  const pythonPaths = [
    path.join(repositoryRoot, 'services/rag-api/src'),
    path.join(repositoryRoot, 'packages/rag-core/src'),
  ];
  if (environment.PYTHONPATH) {
    pythonPaths.push(environment.PYTHONPATH);
  }
  const pythonEnvironment = {
    ...environment,
    PYTHONPATH: pythonPaths.join(path.delimiter),
  };
  return [
    {
      name: 'frontend dependencies check',
      command: process.execPath,
      args: [scriptPath, '--check-frontend', path.join(repositoryRoot, 'apps/main')],
      cwd: repositoryRoot,
      env: environment,
    },
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
      name: 'Python runtime dependencies check',
      command: python,
      args: ['-c', PYTHON_RUNTIME_CHECK],
      cwd: repositoryRoot,
      env: pythonEnvironment,
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
      env: pythonEnvironment,
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
    if (process.argv[2] === '--check-frontend') {
      if (!process.argv[3]) throw new Error('An application directory is required for --check-frontend.');
      const names = checkFrontendDependencies(path.resolve(process.argv[3]));
      console.log(`[setup] Frontend packages available: ${names.join(', ')}. Existing versions were not changed; version compatibility was not checked.`);
    } else {
      await runSetup();
    }
  } catch (error) {
    console.error(`[setup] ${error instanceof Error ? error.message : String(error)}`);
    process.exitCode = 1;
  }
}
