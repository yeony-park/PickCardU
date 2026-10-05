import {build} from 'esbuild';
await build({entryPoints:['lib/embedding-worker.ts'],outfile:'public/embedding-worker.js',bundle:true,format:'esm',platform:'browser',target:'es2022',minify:true});
