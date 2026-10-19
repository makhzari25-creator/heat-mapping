import './globals.css';

export const metadata = {
  title: 'VisionRetail-IQ — Brigade Bangalore',
  description: 'Real-time retail analytics for VisionRetail-IQ stores',
  icons: {
    icon: '/favicon.ico',
    shortcut: '/favicon.ico',
    apple: '/appLogoMain.png',
  }
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <link rel="icon" href="/favicon.ico" />
        <link rel="apple-touch-icon" href="/appLogoMain.png" />
      </head>
      <body style={{
        margin: 0,
        padding: 0,
        background: '#0f1117',
        color: '#e5e7eb',
        fontFamily: "'Inter', 'Segoe UI', monospace",
        minHeight: '100vh',
      }}>
        {children}
      </body>
    </html>
  );
}
