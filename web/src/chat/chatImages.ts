import type { ChatImage } from './chatWire';

export const MAX_CHAT_IMAGES = 4;
/** All images of one prompt together: the frame must stay under the relay's 4 MiB limit. */
export const MAX_TOTAL_BASE64 = 3_500_000;
const MAX_SIDE = 1568;
const MAX_BASE64 = 1_500_000;

export interface PreparedImage extends ChatImage {
  /** An object URL for the thumbnail; revoke it when the image leaves the composer. */
  previewUrl: string;
  name: string;
}

function toBase64(bytes: Uint8Array): string {
  let binary = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

/** The blob as Base64, or `null` without encoding it when that would exceed `MAX_BASE64`. */
async function blobToBase64(blob: Blob): Promise<string | null> {
  if (Math.ceil(blob.size / 3) * 4 > MAX_BASE64) return null;
  return toBase64(new Uint8Array(await blob.arrayBuffer()));
}

function canvasToBlob(
  canvas: HTMLCanvasElement,
  type: 'image/png' | 'image/jpeg',
  quality?: number,
): Promise<Blob | null> {
  return new Promise((resolve) => {
    canvas.toBlob(resolve, type, quality);
  });
}

export function totalBase64(images: readonly ChatImage[]): number {
  return images.reduce((sum, image) => sum + image.data.length, 0);
}

/**
 * Read a picked, pasted or dropped file into a prompt image. The pixels are
 * always redrawn on a canvas, so no original bytes (EXIF, GPS) leave the
 * browser: scaled to a 1568 px longest side, a JPEG stays JPEG and anything
 * else becomes PNG (a GIF keeps its first frame), with JPEG as the fallback
 * when the PNG is too large. Throws an `Error` with a user-facing message.
 */
export async function prepareChatImage(file: File): Promise<PreparedImage> {
  if (!file.type.startsWith('image/')) throw new Error(`${file.name || 'This file'} is not an image.`);
  const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' }).catch(() => {
    throw new Error(`Could not read ${file.name || 'the image'}.`);
  });
  try {
    const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext('2d');
    if (context === null) throw new Error('Could not process the image.');

    if (file.type !== 'image/jpeg') {
      context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      const blob = await canvasToBlob(canvas, 'image/png');
      if (blob !== null) {
        const data = await blobToBase64(blob);
        if (data !== null) {
          return { mime: 'image/png', data, previewUrl: URL.createObjectURL(blob), name: file.name };
        }
      }
    }

    // JPEG has no alpha: a transparent screenshot would otherwise turn black.
    context.fillStyle = '#ffffff';
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    for (const quality of [0.8, 0.65, 0.5]) {
      const blob = await canvasToBlob(canvas, 'image/jpeg', quality);
      if (blob === null) break;
      const data = await blobToBase64(blob);
      if (data !== null) {
        return { mime: 'image/jpeg', data, previewUrl: URL.createObjectURL(blob), name: file.name };
      }
    }
    throw new Error(`${file.name || 'The image'} is too large.`);
  } finally {
    bitmap.close();
  }
}

export function imageFilesFrom(list: FileList | readonly File[] | null | undefined): File[] {
  if (list === null || list === undefined) return [];
  return Array.from(list).filter((file) => file.type.startsWith('image/'));
}
