# Sample data

Fictional POC data for the `PRODUCTS`, `STORES`, `FAQ` and `KIOSKS` worksheets. Import
each CSV into the tab with the same name (File → Import → Upload → Replace current
sheet). Replace it with real store data before any customer-facing test.

## KIOSKS tab (kiosk → store registry)

| Column | Example | Meaning |
|---|---|---|
| `kiosk_id` | KIOSK-001 | The ID the app sends (and its key is bound to) |
| `store_id` | STORE-001 | The store this kiosk serves. The app can't override it. |
| `name` | Downtown entrance | For people |
| `active` | TRUE | FALSE blocks the kiosk |

The tab is optional in development: without it, kiosks use the `store_id` they send,
or `DEFAULT_STORE_ID` (STORE-001) when they send none.
With `KIOSK_REGISTRY_REQUIRED=true` (production), only listed, active kiosks can
connect. KIOSK-099 is an inactive sample row.

The automated tests in `tests/` use these files, so update the tests when you change them.

One product can appear once per store: `(product_id, store_id)` is unique, and the
same product has a different location in each store.

## Test scenarios built into the data

| Scenario | Query (kiosk at STORE-001 unless noted) | Expected |
|---|---|---|
| Exact name | "Coca Cola 1L" | P001, A03/R02/S02 |
| Multiple matches, one brand | "Coca Cola" | P001, P002, P007, P008 (P032 is inactive and hidden) |
| Multiple matches, by category | "shampoo" | 4 shampoos (H&S ×2, Sunsilk, Pantene) |
| Brand across categories | "Dettol" | Soap (Personal Care) and antiseptic liquid (Household) |
| Brand with several product types | "Colgate" | 2 toothpastes + 1 toothbrush |
| SKU | "CC-1L" | P001 |
| Barcode | "100001" | P001 |
| Partial name | "Coca" | Coca Cola products |
| Name with `&` | "Head & Shoulders" | P003, P015 |
| Low stock | "Coca Cola Zero" | P008, LOW_STOCK |
| Out of stock | "Pantene" | P017, OUT_OF_STOCK (not "not found") |
| Inactive product | "Coca Cola Vanilla" | Not found; other Coca Cola products offered as suggestions |
| Unknown product | "iPhone" | Not found |
| **Store isolation** | "ice cream" at STORE-001 | Not found (only STORE-002 has P033) |
| **Store isolation** | "Coca Cola 1L" at STORE-002 | B02/R01/S03, not the STORE-001 location |
| Different stock per store | "Head & Shoulders 400ml" at STORE-002 | OUT_OF_STOCK (AVAILABLE at STORE-001) |
| Store hours | "What time do you close?" STORE-001 / STORE-002 | 21:00 / 22:00 |
| Parking differs | "Parking?" STORE-001 / STORE-002 | Yes / No |
| Restroom differs | "Restroom?" STORE-003 | No |
| Inactive store | Kiosk configured for STORE-004 | Rejected as an invalid store |
| Global FAQ | "How do I pay?" | FAQ002 |
| **Store FAQ overrides global** | "Return policy?" at STORE-002 | FAQ008 (3 days), not FAQ001 (7 days) |
| Store-only FAQ | "Delivery?" STORE-001 / STORE-002 | FAQ004 (yes) / FAQ009 (no) |
| Inactive FAQ | "Gift card?" | FAQ011 never returned. Keyword search still surfaces the payment FAQ via "card", so the agent must check relevance. |
| Burmese FAQ | "ပြန်အပ်လို့ရလား" (my-MM) | FAQ001 `answer_mm` |
