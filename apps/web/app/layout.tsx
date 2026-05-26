import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'AgentForge',
  description:
    'Finance-team-facing app for building and repairing Python finance agents from Excel/CSV samples.',
};

export default function RootLayout({
  children,
}: {
  readonly children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen">{children}</body>
    </html>
  );
}
