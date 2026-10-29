import { useEffect, useId, useState } from "react";
import { shrinkPhoto } from "../lib/photo";
import { Icon } from "./Icon";
import { Alert, Button } from "./ui";

/** Take a photo with the phone camera, preview it, then send it. */
export function PhotoCapture({
  instructions,
  sendText,
  send,
  disabled,
}: {
  instructions: string;
  sendText: string;
  send: (photo: Blob) => Promise<void>;
  disabled?: boolean;
}) {
  const inputId = useId();
  const [photo, setPhoto] = useState<Blob>();
  const [preview, setPreview] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (!photo) return;
    const url = URL.createObjectURL(photo);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [photo]);

  const choose = async (file: File | undefined) => {
    setError(undefined);
    if (file) setPhoto(await shrinkPhoto(file));
  };

  const submit = async () => {
    if (!photo) return;
    setBusy(true);
    setError(undefined);
    try {
      await send(photo);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The photo couldn't be sent. Try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="photo-capture">
      <p className="lead">{instructions}</p>
      <label htmlFor={inputId} className="viewfinder">
        {preview ? (
          <img src={preview} alt="Your photo" />
        ) : (
          <span className="viewfinder-empty">
            <Icon name="camera" size={40} />
            Tap to open the camera
          </span>
        )}
      </label>
      <input
        id={inputId}
        className="visually-hidden"
        type="file"
        accept="image/jpeg,image/png,image/webp"
        capture="environment"
        onChange={(e) => void choose(e.target.files?.[0])}
      />
      <p className="hint">
        Photos are used only to check this pickup. Faces and the inside of your home don't need to be
        in the frame.
      </p>
      {error && <Alert>{error}</Alert>}
      {photo && (
        <div className="stack-row">
          <label htmlFor={inputId} className="button button-secondary">
            Retake
          </label>
          <Button busy={busy} disabled={disabled} onClick={submit}>
            {sendText}
          </Button>
        </div>
      )}
    </div>
  );
}
