function buildVncUrl(raw?: string | null): URL | null {
  if (!raw) return null;
  try {
    const url = new URL(raw);
    url.searchParams.set('autoconnect', '1');
    url.searchParams.set('reconnect', 'true');
    return url;
  } catch (error) {
    console.warn('Failed to build VNC URL', error);
    return null;
  }
}

export function buildVncEmbedUrl(raw?: string | null): string | null {
  const url = buildVncUrl(raw);
  if (!url) return raw ?? null;
  url.searchParams.set('resize', 'scale');
  url.searchParams.set('view_only', 'true');
  return url.toString();
}

export function buildVncViewerUrl(raw?: string | null): string | null {
  const url = buildVncUrl(raw);
  if (!url) return raw ?? null;
  url.searchParams.set('view_only', 'true');
  return url.toString();
}
