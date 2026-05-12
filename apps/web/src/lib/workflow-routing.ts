// Workflow auto-selection — deterministic mapping from filled
// investigation fields to the workflow ids that should dispatch.
//
// Property-vetting investigator's mental model: fill the fields you
// have, click Investigate, the system runs the relevant workflows in
// parallel. No dropdown, no JSON payload editor. The "What this will
// run" preview block in InvestigationForm calls into this module so
// the rules are transparent before the click.
//
// Rules:
//   - W9.pv      property-vetting    requires: address (+ host_name + photo_url ideal)
//   - W11.em+    deep email          requires: email
//   - W10.ip     ip vetting          requires: ip
//   - W3.ph      phone pivot         requires: phone
//   - W5.do      domain timeline     requires: domain
//   - W1.un      username dossier    requires: username
//   - W4.im      image OSINT         requires: photo_url (when no address)
//
// Note: W9.pv is the umbrella for property-vetting. If address +
// photo_url are both provided, run W9.pv (which internally covers the
// image leg) — don't double-dispatch W4.im. If photo_url is provided
// alone, fall back to W4.im.

export interface InvestigationFields {
  readonly address?: string;
  readonly host_name?: string;
  readonly photo_url?: string;
  readonly email?: string;
  readonly username?: string;
  readonly phone?: string;
  readonly ip?: string;
  readonly domain?: string;
  readonly notes?: string;
  readonly csv_path?: string;
}

export interface WorkflowSelection {
  /** Workflow id (e.g. "w9.pv"). */
  readonly id: string;
  /** Human-readable label for the preview block. */
  readonly label: string;
  /** Seed payload to send to /api/investigations/{id}/run. */
  readonly seed: Record<string, string>;
  /** One-line rationale shown in the preview. */
  readonly why: string;
}

function trimmed(s: string | undefined): string {
  return typeof s === "string" ? s.trim() : "";
}

function nonempty(s: string | undefined): boolean {
  return trimmed(s).length > 0;
}

export function routeWorkflows(
  fields: InvestigationFields,
): ReadonlyArray<WorkflowSelection> {
  const out: WorkflowSelection[] = [];
  const address = trimmed(fields.address);
  const host_name = trimmed(fields.host_name);
  const photo_url = trimmed(fields.photo_url);
  const email = trimmed(fields.email);
  const username = trimmed(fields.username);
  const phone = trimmed(fields.phone);
  const ip = trimmed(fields.ip);
  const domain = trimmed(fields.domain);
  const csv_path = trimmed(fields.csv_path);

  // Property-vetting is the umbrella when an address is in play. It
  // chains nominatim -> overpass -> inside-airbnb -> reverse-image +
  // EXIF + provenance + AI-detect on the photo -> TPS on host_name ->
  // HIBP on the email. Other workflows still fire for the legs the
  // property workflow doesn't cover at depth.
  if (address) {
    out.push({
      id: "w9.pv",
      label: "Property Vetting",
      seed: {
        address,
        host_name,
        photo_url,
        email,
        csv_path,
      },
      why: photo_url
        ? "Full property chain: geocode + neighborhood + listings + photo provenance + AI + host + breach"
        : "Property chain: geocode + neighborhood + listings + host + breach (no photo provided)",
    });
  } else if (photo_url) {
    // Photo URL but no address — pure image OSINT.
    out.push({
      id: "w4.im",
      label: "Image OSINT",
      seed: { image_url: photo_url, case_id: "" },
      why: "Reverse-image + EXIF + provenance + AI-detect on the photo.",
    });
  }

  // Deep Email chain (the W11.em+ free-stack: MX -> HIBP -> Gravatar ->
  // GitHub commits -> Hudson Rock -> user-scanner). Fires whenever an
  // email is present, ALONGSIDE the property workflow if both given.
  if (email) {
    out.push({
      id: "w11.em",
      label: "Deep Email (free-stack)",
      seed: { email },
      why: "MX + HIBP + Gravatar + GitHub commits + Hudson Rock + user-scanner",
    });
  }

  if (ip) {
    out.push({
      id: "w10.ip",
      label: "IP Vetting",
      seed: { ip },
      why: "Geolocation + reverse DNS + ASN + reputation",
    });
  }

  if (phone) {
    out.push({
      id: "w3.ph",
      label: "Phone Pivot",
      seed: { phone, region: "" },
      why: "Format + carrier + timezone + Google-SERP mention scan",
    });
  }

  if (domain) {
    out.push({
      id: "w5.do",
      label: "Domain + CT Timeline",
      seed: { domain },
      why: "CT log + Wayback CDX + subfinder + amass",
    });
  }

  if (username) {
    out.push({
      id: "w1.un",
      label: "Username Dossier",
      seed: { username },
      why: "Maigret + Twitter/GitHub/Bluesky social fan-out",
    });
  }

  return out;
}

/** Convenience: are there any fields filled enough to trigger a workflow? */
export function hasInvestigatableFields(fields: InvestigationFields): boolean {
  return routeWorkflows(fields).length > 0;
}

/** All field keys we care about, in the order the form should render them. */
export const FIELD_ORDER: ReadonlyArray<keyof InvestigationFields> = [
  "address",
  "host_name",
  "photo_url",
  "email",
  "phone",
  "username",
  "ip",
  "domain",
  "csv_path",
  "notes",
];

/** Display metadata per field (label, placeholder, hint, input type). */
export const FIELD_META: Record<
  keyof InvestigationFields,
  {
    readonly label: string;
    readonly placeholder: string;
    readonly hint?: string;
    readonly type?: "text" | "url" | "email" | "tel";
    readonly monospace?: boolean;
  }
> = {
  address: {
    label: "Address",
    placeholder: "123 Main St, Springfield IL",
    hint: "Geocodes via OSM, then runs the property chain",
  },
  host_name: {
    label: "Host / owner name",
    placeholder: "Alice Smith",
    hint: "TruePeopleSearch + breach overlap",
  },
  photo_url: {
    label: "Listing photo URL",
    placeholder: "https://example.com/listing.jpg",
    hint: "Reverse-image + EXIF + ELA + AI-detect",
    type: "url",
    monospace: true,
  },
  email: {
    label: "Email",
    placeholder: "owner@example.com",
    hint: "6-step free-stack: MX → HIBP → Gravatar → GitHub → Hudson Rock → user-scanner",
    type: "email",
    monospace: true,
  },
  phone: {
    label: "Phone",
    placeholder: "+1 217 555 0123",
    hint: "Carrier + timezone + SERP mention",
    type: "tel",
    monospace: true,
  },
  username: {
    label: "Username / handle",
    placeholder: "alice",
    hint: "Maigret + social fan-out",
    monospace: true,
  },
  ip: {
    label: "IP address",
    placeholder: "8.8.8.8",
    hint: "Geolocation + ASN + reputation",
    monospace: true,
  },
  domain: {
    label: "Domain",
    placeholder: "example.com",
    hint: "CT log + Wayback + subdomain enum",
    monospace: true,
  },
  csv_path: {
    label: "Inside Airbnb CSV (optional)",
    placeholder: "data/inside-airbnb/city.csv",
    hint: "Local CSV path for host commercial-operator fingerprint",
    monospace: true,
  },
  notes: {
    label: "Notes",
    placeholder: "Anything else worth recording about this case",
  },
};
