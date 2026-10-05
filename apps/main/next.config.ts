import path from 'node:path';
import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  outputFileTracingRoot: path.resolve(process.cwd(), '../..'),
  outputFileTracingIncludes: {
    '/api/card-documents/*': ['../../data/raw/**/*.pdf'],
  },
};

export default nextConfig;
