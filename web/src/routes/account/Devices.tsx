import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router';
import {
  listDevices,
  platformLabel,
  revokeDevice,
  type Device,
} from '../../api/deviceRepository';
import { getDeviceId } from '../../auth/deviceIdentity';
import { queryKeys } from '../../lib/queryKeys';
import { useToastStore } from '../../stores/toastStore';

export function Devices() {
  const queryClient = useQueryClient();
  const currentDeviceId = getDeviceId();
  const [target, setTarget] = useState<Device | null>(null);

  const devices = useQuery({
    queryKey: queryKeys.devices(),
    queryFn: listDevices,
  });

  const revoke = useMutation({
    mutationFn: revokeDevice,
    // Refetch rather than removing the row locally: revoking is idempotent
    // server-side (a second attempt 404s), so the list is the only honest
    // source of what is still active.
    onSuccess: async () => {
      setTarget(null);
      await queryClient.invalidateQueries({ queryKey: queryKeys.devices() });
    },
    onError: () => {
      setTarget(null);
      useToastStore.getState().show('Failed to revoke device', 'error');
    },
  });

  return (
    <section className="page">
      <header className="page-header">
        <h1>My devices</h1>
        <Link to="/account">Back to account</Link>
      </header>

      <p className="page-note">
        Revoking a device signs out every agent paired with it — that device
        stops working on its next token refresh, while your other devices stay
        signed in.
      </p>

      {devices.isPending && <p>Loading devices…</p>}
      {devices.isError && <p className="auth-error">Failed to load devices.</p>}

      {devices.data?.length === 0 && (
        <p className="empty-state">
          No devices yet. Pair an agent to register this device.
        </p>
      )}

      <ul className="device-list">
        {devices.data?.map((device) => (
          <li key={device.deviceId} className="device-card">
            <div className="device-card-head">
              <span className="device-name">{device.name ?? 'Unnamed device'}</span>
              {device.deviceId === currentDeviceId && (
                <span className="device-current">This device</span>
              )}
              <button
                type="button"
                className="button button-danger"
                onClick={() => {
                  setTarget(device);
                }}
              >
                Revoke
              </button>
            </div>
            <dl className="device-meta">
              <div>
                <dt>Platform</dt>
                <dd>{platformLabel(device.platform)}</dd>
              </div>
              <div>
                <dt>Device id</dt>
                <dd className="device-id">{device.deviceId}</dd>
              </div>
              <div>
                <dt>Added</dt>
                <dd>{device.createdAt}</dd>
              </div>
              <div>
                <dt>Last seen</dt>
                <dd>{device.lastSeenAt ?? '—'}</dd>
              </div>
            </dl>
          </li>
        ))}
      </ul>

      {target !== null && (
        <div className="confirm-panel" role="alertdialog" aria-labelledby="revoke-title">
          <h2 id="revoke-title">Revoke device?</h2>
          <p>
            {target.deviceId === currentDeviceId
              ? 'Revoking this device will sign you out on the next token refresh. You can sign back in afterwards.'
              : `${target.name ?? target.deviceId} will be signed out on its next token refresh.`}{' '}
            Every agent paired with it is signed out too.
          </p>
          <div className="button-row">
            <button
              type="button"
              className="button button-danger"
              disabled={revoke.isPending}
              onClick={() => {
                revoke.mutate(target.deviceId);
              }}
            >
              Revoke
            </button>
            <button
              type="button"
              className="button"
              onClick={() => {
                setTarget(null);
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
