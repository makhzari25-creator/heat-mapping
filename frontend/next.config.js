/** @type {import('next').NextConfig} */
const nextConfig = {
  env: {
    BACKEND_URL: process.env.BACKEND_URL || 'https://visionretail-iq.onrender.com',
  },
  experimental: {
    // no extras needed
  },
};

module.exports = nextConfig;