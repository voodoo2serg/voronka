import type { NextConfig } from 'next';

const basePath = process.env.PORTAL_BASE || '/crm';

const config: NextConfig = {
  output: 'export',
  basePath,
  trailingSlash: true,
  images: { unoptimized: true },
  env: {
    NEXT_PUBLIC_PORTAL: process.env.NEXT_PUBLIC_PORTAL || 'crm',
  },
};

export default config;
