/** @type {import('next').NextConfig} */
const config = {
  reactStrictMode: true,
  devIndicators: { position: 'bottom-right' },
  poweredByHeader: false,
  experimental: { cpus: 2 },
  distDir: process.env.HAEDAP_NEXT_DIST || '.next',
};
export default config;
