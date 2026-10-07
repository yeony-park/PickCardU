/// <reference types="vite/client" />
declare namespace Cloudflare {
  interface Env {
    DB?: D1Database;
    BUCKET?: R2Bucket;
  }
}

declare namespace Cloudflare {
  interface Env { ASSETS?: Fetcher; }
}
