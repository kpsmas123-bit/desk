// Public Desk config — Supabase project URL + PUBLISHABLE (anon) key only.
//
// This file is SAFE to commit and ship in the browser because Row Level Security
// is enabled on every table (migration 0004). The publishable key can only do
// what RLS allows for a signed-in user. The SECRET key is NEVER here — it lives
// only in the backend .env and is used by the ingest/* scripts.
window.DESK_CONFIG = {
  url: "https://qhrtqtnrduambvchjxqw.supabase.co",
  key: "sb_publishable_UAG7Ru6PRdNnOLCbchpQVg_8vE0jG5N"
};
