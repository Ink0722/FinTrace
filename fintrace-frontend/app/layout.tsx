import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "FinTrace — Financial Research Agent",
  description: "FinTrace conversational financial research workspace",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body suppressHydrationWarning>{children}</body>
    </html>
  );
}
