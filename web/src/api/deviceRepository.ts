import { relayClient } from './clients';
import { toApiError } from './errors';

export interface Device {
  deviceId: string;
  name: string | null;
  createdAt: string;
  lastSeenAt: string | null;
  platform: string | null;
}

/**
 * Display label for the `platform` a device reported when it last minted agent
 * tokens. Null for rows registered by a client that omitted the field —
 * Android did so until CLA-190, so existing Android rows read "Unknown" until
 * that device next mints a token.
 */
export function platformLabel(platform: string | null): string {
  switch (platform) {
    case 'web':
      return 'Browser';
    case 'ios':
      return 'iOS';
    case 'android':
      return 'Android';
    default:
      return 'Unknown';
  }
}

export async function listDevices(): Promise<Device[]> {
  const { data, error, response } = await relayClient.GET('/api/devices');
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return data.map((device) => ({
    deviceId: device.device_id,
    name: device.name ?? null,
    createdAt: device.created_at,
    lastSeenAt: device.last_seen_at ?? null,
    platform: device.platform ?? null,
  }));
}

export async function revokeDevice(deviceId: string): Promise<void> {
  const { error, response } = await relayClient.DELETE(
    '/api/devices/{device_id}',
    { params: { path: { device_id: deviceId } } },
  );
  if (error !== undefined) throw toApiError(response, error);
}
