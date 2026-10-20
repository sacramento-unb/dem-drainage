import type { Metadata } from "next";
import { Montserrat, Roboto } from "next/font/google";
import "./globals.css";
import "leaflet/dist/leaflet.css";

const roboto = Roboto({
  variable: "--font-manrope",
  subsets: ["latin"],
  weight: ["400", "500", "700"],
});

const display = Montserrat({
  variable: "--font-display",
  subsets: ["latin"],
  weight: ["500", "600", "700"],
});

export const metadata: Metadata = {
  title: "Dossel Ambiental — Estúdio de Drenagem",
  description:
    "Extraia e explore redes de drenagem ordenadas por Hack a partir de pastas DEM com WhiteboxTools.",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="pt-BR">
      <body className={`${roboto.variable} ${display.variable}`}>
        {children}
      </body>
    </html>
  );
}
