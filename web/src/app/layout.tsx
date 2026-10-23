import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "ZeroSpoof: real-time deepfake & liveness firewall",
  description: "Is the person on this call a real, live human? Replay, deepfake, virtual-camera and voice-clone detection.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <nav className="nav">
          <Link href="/" className="brand">
            Zero<span>Spoof</span>
          </Link>
          <Link href="/" className="link">Live demo</Link>
          <Link href="/dashboard" className="link">Dashboard</Link>
          <span className="grow" />
          <a className="link" href={(process.env.ZEROSPOOF_API_URL ?? "http://localhost:8000") + "/docs"} target="_blank" rel="noreferrer">
            API docs
          </a>
        </nav>
        {children}
      </body>
    </html>
  );
}
