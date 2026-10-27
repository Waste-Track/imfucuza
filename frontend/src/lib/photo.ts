// The Engine accepts photos up to 1600 px on the long side. Shrinking on the
// phone first saves mobile data on slow connections.
export const MAX_SIDE = 1600;

export function fitWithin(width: number, height: number, max = MAX_SIDE): [number, number] {
  const scale = Math.min(1, max / Math.max(width, height));
  return [Math.round(width * scale), Math.round(height * scale)];
}

export async function shrinkPhoto(file: File): Promise<Blob> {
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch {
    // A browser that can't decode it here: let the Engine decide.
    return file;
  }
  const [width, height] = fitWithin(bitmap.width, bitmap.height);
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  canvas.getContext("2d")?.drawImage(bitmap, 0, 0, width, height);
  bitmap.close();
  const blob = await new Promise<Blob | null>((resolve) =>
    canvas.toBlob(resolve, "image/jpeg", 0.85),
  );
  return blob ?? file;
}
