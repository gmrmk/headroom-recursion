import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import "./tokens.css";

export const metadata: Metadata = {
  title: "OSINT Goblin",
  description: "FOSS-first OSINT investigation dashboard",
};

export const viewport: Viewport = {
  themeColor: "oklch(15% 0.005 240)",
};

interface RootLayoutProps {
  children: ReactNode;
}

export default function RootLayout({ children }: RootLayoutProps) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body suppressHydrationWarning>{children}</body>
    </html>
  );
}
