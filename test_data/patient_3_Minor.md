# Patient 3 — Minor Injury (Expected ESI 4)

## For use in: Free Text Input tab

---

## Free Text Version
Paste this into the Free Text Input tab:

> 24 year old male, walk-in. Twisted right ankle playing basketball approximately 2 hours ago. Pain 5/10, mild swelling and bruising on the lateral ankle, able to bear weight with a limp. BP 122/78, HR 74, RR 14, SpO2 99%, Temp 36.7°C, GCS 15. No significant medical history. No allergies.

---

## What this demonstrates

- **ESI 4 — Less Urgent** response
- Triggers the Trauma / Orthopedic Protocol
- All vitals completely normal — the system correctly reads nothing as alarming
- Shows the system is not over-escalating: a real ESI failure mode would be calling everything urgent
- Good demo contrast: run this after Patient 1 or 2 to show the system correctly distinguishing a minor case from a critical one
- Ottawa ankle rules should be referenced in the protocol output

---

## Expected key outputs
- ESI Score: 4
- Care area: Fast Track or General
- Interventions: Ottawa ankle rules assessment, X-ray if indicated, pain management, ice and elevation
- Time to physician: 30–60 minutes
