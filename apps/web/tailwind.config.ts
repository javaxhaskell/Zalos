import type { Config } from 'tailwindcss';

const config: Config = {
  content: [
    './app/**/*.{ts,tsx}',
    './src/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        // Severity (centralised; the same map used in Phase 7 components)
        severity: {
          info: '#64748b',     // slate-500
          low: '#3b82f6',      // blue-500
          medium: '#f59e0b',   // amber-500
          high: '#f97316',     // orange-500
          critical: '#ef4444', // red-500
        },
        // Risk (matches RiskLevel enum)
        risk: {
          read: '#64748b',
          low_write: '#f59e0b',
          high_write: '#f97316',
        },
      },
      fontFamily: {
        sans: ['ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      keyframes: {
        'fade-in-up': {
          '0%': { opacity: '0', transform: 'translateY(4px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'status-in': {
          '0%': { opacity: '0', transform: 'scale(0.96)' },
          '100%': { opacity: '1', transform: 'scale(1)' },
        },
        'row-in': {
          '0%': { opacity: '0', transform: 'translateY(3px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'row-highlight': {
          '0%': { backgroundColor: 'rgb(241 245 249 / 0.95)' },
          '100%': { backgroundColor: 'transparent' },
        },
      },
      animation: {
        'fade-in-up': 'fade-in-up 200ms ease-out',
        'status-in': 'status-in 180ms ease-out',
        'row-in': 'row-in 200ms ease-out',
        'row-highlight': 'row-highlight 900ms ease-out',
      },
    },
  },
  plugins: [],
};

export default config;
