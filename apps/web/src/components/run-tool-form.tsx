"use client";

import { useMemo, useState } from "react";

interface RunToolFormProps {
  investigationId: string;
}

// Iris-IA accept (2026-05-11 wave-3): partition adapters by the
// six-primitive scope anchor she locked in her persona §104-122 so
// dropdown grouping mirrors the dossier's entity-model taxonomy.
// Half-life test: an adapter's primitive doesn't change over time; its
// reliability tier and domain might. Re-open trigger (Iris): adapter
// count >50 OR any single primitive >12. Image is already at 11.
type AdapterGroup = "addrgeo" | "email" | "person" | "social" | "image" | "smoke";

const GROUP_LABELS: Record<AdapterGroup, string> = {
  addrgeo: "Address / Geo",
  email: "Email",
  person: "Person — name-based",
  social: "Social handle",
  image: "Image",
  smoke: "Smoke / test",
};

// Order in which optgroups render -- mirrors the dossier event facet
// ordering (Triage/Disprove first, image+smoke last).
const GROUP_ORDER: ReadonlyArray<AdapterGroup> = [
  "addrgeo",
  "email",
  "person",
  "social",
  "image",
  "smoke",
];

const GROUP_FOR: Record<string, AdapterGroup> = {
  nominatim_geocode: "addrgeo",
  inside_airbnb_listings: "addrgeo",
  kartaview_nearby: "addrgeo",
  email_mx_validate: "email",
  hibp_breach_check: "email",
  true_people_search: "person",
  rocketreach_search: "person",
  google_serp_linkedin: "person",
  wayback_linkedin: "person",
  linkedin_profile: "social",
  github_profile: "social",
  twitter_public: "social",
  twitter_followers: "social",
  instagram_public: "social",
  instagram_followers: "social",
  tiktok_public: "social",
  tiktok_followers: "social",
  github_followers: "social",
  bluesky_followers: "social",
  mastodon_followers: "social",
  twstalker: "social",
  bluesky_post_likes: "social",
  mastodon_post_likes: "social",
  tineye_image: "image",
  yandex_image_reverse: "image",
  bing_visual_reverse: "image",
  reverse_image_aggregator: "image",
  image_flip_check: "image",
  exiftool_full: "image",
  image_ela_check: "image",
  image_provenance_check: "image",
  ai_image_detection: "image",
  c2pa_verify: "image",
  phash_dedupe: "image",
  seasonal_metadata_check: "image",
  wayback_snapshot: "image",
  echo: "smoke",
  m0_gate_stress: "smoke",
};

// The eight property-vetting + smoke adapters wired in apps/workers.
// Names mirror osint_goblin_workers.adapters + adapters_property.
// Kept inline (not fetched) so the form renders synchronously; a future
// /adapters endpoint can replace this when the registry surface lands.
const ADAPTERS: ReadonlyArray<{
  readonly id: string;
  readonly label: string;
  readonly hint: string;
  readonly examplePayload: string;
}> = [
  {
    id: "nominatim_geocode",
    label: "Nominatim — address → lat/lon",
    hint: "OSM geocoding. 1 req/sec self-throttled.",
    examplePayload: '{\n  "q": "1600 Pennsylvania Ave, Washington DC"\n}',
  },
  {
    id: "email_mx_validate",
    label: "Email — DNS MX validate",
    hint: "Format + DNS-A record probe. Catches typos.",
    examplePayload: '{\n  "email": "user@example.com"\n}',
  },
  {
    id: "hibp_breach_check",
    label: "HIBP — breach by domain",
    hint: "Have I Been Pwned free 'breaches-by-domain' endpoint.",
    examplePayload: '{\n  "email": "user@example.com"\n}',
  },
  {
    id: "inside_airbnb_listings",
    label: "Inside Airbnb — host fingerprint",
    hint: "Search pre-downloaded city CSV; flags commercial operators.",
    examplePayload:
      '{\n  "csv_path": "data/inside-airbnb/city.csv",\n  "host_name": "Alice"\n}',
  },
  {
    id: "true_people_search",
    label: "TruePeopleSearch — person",
    hint: "Scrapling stealth subprocess. Top 5 results, single page.",
    examplePayload: '{\n  "name": "Alice Smith",\n  "city": "Springfield",\n  "state": "IL"\n}',
  },
  {
    id: "linkedin_profile",
    label: "LinkedIn — public profile",
    hint: "Public-view fetch (no login). Needs profile URL.",
    examplePayload: '{\n  "profile_url": "https://www.linkedin.com/in/alice-smith"\n}',
  },
  {
    id: "google_serp_linkedin",
    label: "Google SERP — LinkedIn URL discovery",
    hint: "site:linkedin.com/in name search. Feeds linkedin_profile.",
    examplePayload: '{\n  "name": "Alice Smith",\n  "company": "Acme Corp"\n}',
  },
  {
    id: "wayback_linkedin",
    label: "Wayback — LinkedIn snapshot",
    hint: "archive.org snapshot of a LinkedIn URL (use when LinkedIn 429s).",
    examplePayload: '{\n  "profile_url": "https://www.linkedin.com/in/alice-smith"\n}',
  },
  {
    id: "github_profile",
    label: "GitHub — public profile",
    hint: "GitHub REST v3. Free, 60 req/h unauth. Tech-host verification.",
    examplePayload: '{\n  "username": "octocat"\n}',
  },
  {
    id: "rocketreach_search",
    label: "RocketReach — name search",
    hint: "Free-tier name search (no email/phone). Scrapling subprocess.",
    examplePayload: '{\n  "name": "Alice Smith",\n  "company": "Acme Corp"\n}',
  },
  {
    id: "twitter_public",
    label: "Twitter/X — public profile",
    hint: "Bio + counts + joined date via nitter mirror or x.com fallback.",
    examplePayload: '{\n  "handle": "username"\n}',
  },
  {
    id: "instagram_public",
    label: "Instagram — public profile",
    hint: "Bio + counts + is_private flag. No login, no follower list.",
    examplePayload: '{\n  "handle": "username"\n}',
  },
  {
    id: "tiktok_public",
    label: "TikTok — public profile",
    hint: "Bio + follower/like/video counts. Public-view only.",
    examplePayload: '{\n  "handle": "username"\n}',
  },
  {
    id: "github_followers",
    label: "GitHub — public followers",
    hint: "REST v3 follower-list (public, no login).",
    examplePayload: '{\n  "username": "octocat",\n  "limit": 100\n}',
  },
  {
    id: "bluesky_followers",
    label: "Bluesky — public followers",
    hint: "AT Protocol getFollowers (no auth, public reads).",
    examplePayload: '{\n  "handle": "alice.bsky.social",\n  "limit": 100\n}',
  },
  {
    id: "mastodon_followers",
    label: "Mastodon — public followers",
    hint: "Instance REST API. Handle format: user@instance.tld",
    examplePayload: '{\n  "acct": "alice@mastodon.social",\n  "limit": 80\n}',
  },
  {
    id: "wayback_snapshot",
    label: "Wayback — any-URL snapshot",
    hint: "Generalized archive.org availability check. Pre-wall follower pages.",
    examplePayload:
      '{\n  "url": "https://www.instagram.com/username/followers/"\n}',
  },
  {
    id: "bluesky_post_likes",
    label: "Bluesky — who liked a post",
    hint: "AT Protocol getLikes. Post URL or at:// URI.",
    examplePayload:
      '{\n  "post_url": "https://bsky.app/profile/alice.bsky.social/post/abc123",\n  "limit": 100\n}',
  },
  {
    id: "mastodon_post_likes",
    label: "Mastodon — who favourited a post",
    hint: "Instance REST API. Public favourites list.",
    examplePayload:
      '{\n  "post_url": "https://mastodon.social/@alice/112345678901234567",\n  "limit": 80\n}',
  },
  {
    id: "twitter_followers",
    label: "Twitter/X — public followers (nitter)",
    hint: "Via nitter mirror. Private accounts blocked by design.",
    examplePayload: '{\n  "handle": "username",\n  "limit": 50\n}',
  },
  {
    id: "instagram_followers",
    label: "Instagram — public followers (auth-walled)",
    hint: "Honest: auth wall. Surfaces error pointing at wayback_snapshot.",
    examplePayload: '{\n  "handle": "username"\n}',
  },
  {
    id: "tiktok_followers",
    label: "TikTok — public followers (auth-walled)",
    hint: "Honest: auth wall. Surfaces error pointing at wayback_snapshot.",
    examplePayload: '{\n  "handle": "username"\n}',
  },
  {
    id: "tineye_image",
    label: "TinEye — reverse image (exact)",
    hint: "Exact-match reverse image search. Pair with image_flip_check for flipped duplicates.",
    examplePayload: '{\n  "image_url": "https://example.com/photo.jpg"\n}',
  },
  {
    id: "yandex_image_reverse",
    label: "Yandex — reverse image (catches flips)",
    hint: "Neural-feature matcher; best engine for flipped/cropped variants.",
    examplePayload: '{\n  "image_url": "https://example.com/photo.jpg"\n}',
  },
  // google_lens_reverse intentionally NOT in the dropdown (Tomas review
  // 2026-05-11): captcha-fragile; kept registered so the aggregator can
  // fan it out for free, but standalone use is rarely the right call.
  {
    id: "bing_visual_reverse",
    label: "Bing Visual Search — reverse image",
    hint: "Third triangulation engine; catches what Google misses.",
    examplePayload: '{\n  "image_url": "https://example.com/photo.jpg"\n}',
  },
  {
    id: "reverse_image_aggregator",
    label: "Reverse image — ALL engines (meta)",
    hint: "Fan-out: TinEye + Yandex + Google Lens + Bing. Single call.",
    examplePayload: '{\n  "image_url": "https://example.com/photo.jpg"\n}',
  },
  {
    id: "image_flip_check",
    label: "Image — generate flipped variant",
    hint: "Horizontally flip; feed result into exact-match engines.",
    examplePayload:
      '{\n  "image_url": "https://example.com/photo.jpg",\n  "output_format": "file"\n}',
  },
  // image_exif intentionally NOT in dropdown (Tomas review 2026-05-11):
  // three EXIF-class tools is one too many. Kept registered as the
  // fallback inside image_provenance_check when exiftool is not on PATH;
  // standalone use is dropdown clutter at 30+ adapters.
  {
    id: "exiftool_full",
    label: "ExifTool — full metadata (gold standard)",
    hint: "~23,000 tags. Requires exiftool on PATH.",
    examplePayload: '{\n  "image_url": "https://example.com/photo.jpg"\n}',
  },
  {
    id: "image_ela_check",
    label: "Image ELA — manipulation detector",
    hint: "Error Level Analysis. Flags retouched / clone-stamped regions.",
    examplePayload:
      '{\n  "image_url": "https://example.com/photo.jpg",\n  "quality": 90\n}',
  },
  {
    id: "image_provenance_check",
    label: "Image provenance — composite (EXIF + ELA + C2PA)",
    hint: "One call, aggregated verdict (high-risk / elevated / low).",
    examplePayload: '{\n  "image_url": "https://example.com/photo.jpg"\n}',
  },
  {
    id: "phash_dedupe",
    label: "pHash dedupe — multi-listing photo theft",
    hint: "Catches same photo reused across cases. Append-only local DB.",
    examplePayload:
      '{\n  "image_url": "https://example.com/photo.jpg",\n  "case_id": "case-2026-05-alice"\n}',
  },
  {
    id: "seasonal_metadata_check",
    label: "Image seasonal check — EXIF date vs claim",
    hint: "Catches summer-photo-on-winter-listing fraud. Solar angle bonus.",
    examplePayload:
      '{\n  "image_url": "https://example.com/photo.jpg",\n  "claimed_season": "winter"\n}',
  },
  {
    id: "ai_image_detection",
    label: "AI image detection (Sightengine)",
    hint: "GenAI-fabricated photo detector. Needs OSINT_SIGHTENGINE_* keys.",
    examplePayload: '{\n  "image_url": "https://example.com/photo.jpg"\n}',
  },
  {
    id: "c2pa_verify",
    label: "C2PA — Content Credentials verify",
    hint: "Cryptographic provenance chain (2024+ Sony/Leica/Nikon).",
    examplePayload: '{\n  "image_url": "https://example.com/photo.jpg"\n}',
  },
  {
    id: "kartaview_nearby",
    label: "KartaView — OSM street-level at lat/lon",
    hint: "Open street-level imagery. No API key.",
    examplePayload: '{\n  "lat": 39.78,\n  "lon": -89.65,\n  "radius_m": 200\n}',
  },
  {
    id: "twstalker",
    label: "TWStalker — Twitter mirror (public-view)",
    hint: "Third no-login Twitter surface; complements nitter + x.com.",
    examplePayload: '{\n  "handle": "username"\n}',
  },
  {
    id: "echo",
    label: "echo — smoke",
    hint: "Trivial in-process adapter. Returns payload as tool-run-result.",
    examplePayload: '{\n  "hello": "world"\n}',
  },
  {
    id: "m0_gate_stress",
    label: "m0_gate_stress — 32-event burst",
    hint: "M0 exit gate path. In-process; bypasses Dramatiq.",
    examplePayload: "{}",
  },
];

export function RunToolForm({ investigationId }: RunToolFormProps) {
  const [adapterId, setAdapterId] = useState<string>(ADAPTERS[0]!.id);
  const [payloadText, setPayloadText] = useState<string>(ADAPTERS[0]!.examplePayload);
  const [status, setStatus] = useState<"idle" | "submitting" | "ok" | "error">("idle");
  const [errorMessage, setErrorMessage] = useState<string>("");

  const selectedAdapter = ADAPTERS.find((a) => a.id === adapterId) ?? ADAPTERS[0]!;

  // Partition once per render-pass; preserves Iris-IA's optgroup order
  // and keeps adapters with no explicit GROUP_FOR mapping in a fallback
  // bucket (visible as "Other" rather than silently dropping them).
  const grouped = useMemo(() => {
    type Bucket = Array<(typeof ADAPTERS)[number]>;
    const buckets: Record<AdapterGroup, Bucket> = {
      addrgeo: [],
      email: [],
      person: [],
      social: [],
      image: [],
      smoke: [],
    };
    for (const a of ADAPTERS) {
      const g = GROUP_FOR[a.id] ?? "smoke";
      buckets[g].push(a);
    }
    return buckets;
  }, []);

  function selectAdapter(next: string) {
    setAdapterId(next);
    const adapter = ADAPTERS.find((a) => a.id === next);
    if (adapter) {
      setPayloadText(adapter.examplePayload);
    }
    setStatus("idle");
    setErrorMessage("");
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    let parsedPayload: unknown;
    try {
      parsedPayload = payloadText.trim() === "" ? {} : JSON.parse(payloadText);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "invalid JSON";
      setStatus("error");
      setErrorMessage(`Payload JSON parse: ${msg}`);
      return;
    }
    setStatus("submitting");
    setErrorMessage("");
    try {
      const res = await fetch(`/api/investigations/${investigationId}/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ adapter_id: adapterId, payload: parsedPayload }),
      });
      if (!res.ok) {
        const text = await res.text();
        setStatus("error");
        setErrorMessage(`HTTP ${res.status}: ${text.slice(0, 200)}`);
        return;
      }
      setStatus("ok");
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "network error";
      setStatus("error");
      setErrorMessage(msg);
    }
  }

  return (
    <form
      onSubmit={onSubmit}
      style={{
        marginBottom: 16,
        padding: 12,
        background: "#0f0f0f",
        border: "1px solid #1f1f1f",
        borderRadius: 4,
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <label
          htmlFor="adapter-select"
          style={{ color: "#a3a3a3", fontSize: 12, minWidth: 60 }}
        >
          Adapter
        </label>
        <select
          id="adapter-select"
          value={adapterId}
          onChange={(e) => selectAdapter(e.target.value)}
          style={{
            flex: 1,
            background: "#1a1a1a",
            color: "#e5e5e5",
            border: "1px solid #2a2a2a",
            borderRadius: 4,
            padding: "4px 6px",
            fontSize: 12,
          }}
        >
          {GROUP_ORDER.map((g) =>
            grouped[g].length === 0 ? null : (
              <optgroup key={g} label={GROUP_LABELS[g]}>
                {grouped[g].map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.label}
                  </option>
                ))}
              </optgroup>
            ),
          )}
        </select>
      </div>
      <p style={{ color: "#525252", fontSize: 11, margin: 0 }}>{selectedAdapter.hint}</p>
      <textarea
        value={payloadText}
        onChange={(e) => setPayloadText(e.target.value)}
        aria-label="JSON payload"
        spellCheck={false}
        rows={6}
        style={{
          background: "#1a1a1a",
          color: "#d4d4d4",
          border: "1px solid #2a2a2a",
          borderRadius: 4,
          padding: 8,
          fontFamily: "ui-monospace, SFMono-Regular, monospace",
          fontSize: 12,
          resize: "vertical",
        }}
      />
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <button
          type="submit"
          disabled={status === "submitting"}
          style={{
            padding: "6px 14px",
            background: status === "submitting" ? "#1a1a1a" : "#1f1f1f",
            color: status === "submitting" ? "#525252" : "#e5e5e5",
            border: "1px solid #404040",
            borderRadius: 4,
            fontSize: 12,
            cursor: status === "submitting" ? "not-allowed" : "pointer",
          }}
        >
          {status === "submitting" ? "Submitting..." : "Run"}
        </button>
        <span style={{ color: statusColor(status), fontSize: 12 }}>{statusLabel(status)}</span>
        {errorMessage ? (
          <span style={{ color: "#f87171", fontSize: 11 }}>{errorMessage}</span>
        ) : null}
      </div>
    </form>
  );
}

function statusLabel(s: "idle" | "submitting" | "ok" | "error"): string {
  switch (s) {
    case "idle":
      return "";
    case "submitting":
      return "submitting...";
    case "ok":
      return "enqueued — watch the stream below";
    case "error":
      return "error";
  }
}

function statusColor(s: "idle" | "submitting" | "ok" | "error"): string {
  switch (s) {
    case "idle":
      return "#525252";
    case "submitting":
      return "#f59e0b";
    case "ok":
      return "#34d399";
    case "error":
      return "#f87171";
  }
}
