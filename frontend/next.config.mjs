/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  env: {
    NEXT_PUBLIC_CANVASFLOW_API:
      process.env.NEXT_PUBLIC_CANVASFLOW_API ?? "http://localhost:8000",
  },
};

export default nextConfig;
