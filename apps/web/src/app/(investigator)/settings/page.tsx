export default function SettingsPage() {
  return (
    <section>
      <h1 style={{ fontSize: 20, margin: 0, marginBottom: 8 }}>Settings</h1>
      <p style={{ color: "#a3a3a3", fontSize: 14, maxWidth: 640 }}>
        Configuration: OPSEC profile, Tor circuit policy, rate-limit budgets, lawful-basis
        defaults, retention policy.
      </p>
      <p style={{ color: "#737373", fontSize: 13, marginTop: 16 }}>
        Real settings UI lands in WI-0708 (Sprint 7) alongside OPSEC profile manager.
      </p>
    </section>
  );
}
