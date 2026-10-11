-- Pickup photos: a private bucket, read and written only with the Engine's
-- secret key. Photos arrive re-encoded as JPEG, well under the limit. A bucket
-- of this name made by hand earlier is brought into line, not left as it was.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('pickup-photos', 'pickup-photos', false, 2097152, array['image/jpeg'])
on conflict (id) do update
  set public = false,
      file_size_limit = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;
