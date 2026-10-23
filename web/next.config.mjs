/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Camera/microphone are required by the liveness widget; everything else is denied.
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "Permissions-Policy", value: "camera=(self), microphone=(self), geolocation=()" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
        ],
      },
    ];
  },
};
export default nextConfig;
