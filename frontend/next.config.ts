import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The production image ships only the files the server needs (see Dockerfile).
  output: "standalone",
};

export default nextConfig;
