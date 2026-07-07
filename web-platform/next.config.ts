import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: 'standalone',
  poweredByHeader: false,
  transpilePackages: ['react-map-gl', 'mapbox-gl', '@react-sigma/core', 'sigma', 'graphology', 'graphology-layout-forceatlas2'],
};

export default nextConfig;
