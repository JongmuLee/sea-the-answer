// UUID v4 for client-generated IDs.
// crypto.randomUUID() exists only in secure contexts (HTTPS or localhost), so it is
// undefined when the app is opened over plain http:// on a LAN IP or server name.
// crypto.getRandomValues() works in every context.
export function uuid(cryptoImpl = globalThis.crypto) {
  if (typeof cryptoImpl.randomUUID === 'function') return cryptoImpl.randomUUID();
  const b = cryptoImpl.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40; // version 4
  b[8] = (b[8] & 0x3f) | 0x80; // RFC 4122 variant
  const h = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}
