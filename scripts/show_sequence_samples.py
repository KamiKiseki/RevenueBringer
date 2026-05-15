import csv
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "sequences_export.csv"
with path.open(newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))

# Representative leads: generic greeting, business-derived name, law firm
pick = [rows[0], rows[2], rows[18]]
labels = ["Real estate (Hi there)", "Plumbing/HVAC (Hi there)", "Law firm (named greeting)"]

for label, r in zip(labels, pick):
    print("=" * 72)
    print(f"{label} — {r['email']}")
    print(f"Merge tag for Instantly: {{{{first_name}}}} or use: {r['first_name']}")
    for step in (1, 2, 3):
        print(f"\n--- Step {step} ---")
        print(f"Subject: {r[f'sequence_{step}_subject']}")
        print(r[f"sequence_{step}_body"])
    print()
