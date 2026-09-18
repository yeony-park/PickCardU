type WaitForPathOptions = {
  readPath?: () => string;
  now?: () => number;
  scheduleTask?: (callback: () => void) => void;
};

const PATH_WAIT_TIMEOUT_MS = 3000;
const PATH_POLL_INTERVAL_MS = 16;

export function waitForPath(pathname: string, options: WaitForPathOptions = {}): Promise<void> {
  const readPath = options.readPath ?? (() => window.location.pathname);
  const now = options.now ?? (() => performance.now());
  const scheduleTask = options.scheduleTask ?? ((callback) => {
    window.setTimeout(callback, PATH_POLL_INTERVAL_MS);
  });
  const deadline = now() + PATH_WAIT_TIMEOUT_MS;

  return new Promise((resolve) => {
    function checkPath() {
      if (readPath() === pathname || now() >= deadline) {
        resolve();
        return;
      }
      scheduleTask(checkPath);
    }

    scheduleTask(checkPath);
  });
}

export async function settleTransition(finished: Promise<void>, cleanup: () => void): Promise<void> {
  try {
    await finished;
  } catch {
    // A skipped or timed-out animation must not turn successful navigation into an unhandled rejection.
  } finally {
    cleanup();
  }
}
