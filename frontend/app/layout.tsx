import type { Metadata, Viewport } from "next";
import "@xyflow/react/dist/style.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "CanvasFlow",
  description:
    "An infinite, node-based visual workspace for content creation and multi-agent marketing workflows.",
};

export const viewport: Viewport = {
  themeColor: "#0B0F19",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="h-full">
      <body className="h-full overflow-hidden">{children}</body>
    </html>
  );
}
