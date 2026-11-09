import type { NextConfig } from "next";

const config: NextConfig = {
  output: "standalone", // small production image: `node server.js`
  eslint: { ignoreDuringBuilds: true }, // Python side uses ruff; no ESLint setup here
};

export default config;
