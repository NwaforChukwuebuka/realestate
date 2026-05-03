Build only the core backend pipeline first.

Do NOT focus on dashboard, CRM, outreach, skip tracing, or advanced features yet.

Goal:
Take the Miami-Dade bulk property ZIP/CSV file, extract usable residential property addresses, geocode them, pull Google Street View images, calculate the correct heading toward the property, verify the image shows the target house, and score visible distress with AI.

Build Steps:

1. CSV/ZIP Importer
- Accept Miami-Dade “munroll - 00 re - all properties.zip”
- Extract CSV
- Read large CSV efficiently
- Store raw rows
- Deduplicate by folio/parcel ID

2. Column Mapper
Map Miami-Dade fields into normalized fields:
- parcel_id
- property_address
- city
- state
- zip
- owner_name
- mailing_address
- property_type
- year_built
- last_sale_date
- assessed_value

3. Residential Filter
Keep:
- single-family
- duplex
- small residential/multifamily if available

Remove:
- condos
- commercial
- vacant land
- government/institutional properties

4. Motivation Signal Engine
Calculate:
- absentee_owner = property address != mailing address
- out_of_state_owner = mailing state != FL
- years_owned = current year - last sale year
- old_property = year built <= 1980

5. Geocoding Module
- Use Google Geocoding API
- Convert property_address into lat/lng
- Cache results
- Do not geocode duplicate addresses
- Store geocode confidence/status

6. Street View Metadata Module
- Use Google Street View Metadata API
- Check if Street View exists
- Store pano_id, pano_lat, pano_lng, image_date
- If no pano exists, mark as no_street_view

7. Heading Calculation + Image Fetcher
- Calculate heading from pano location to property coordinates
- Fetch images at:
  - heading
  - heading +15
  - heading -15
  - heading +30
  - heading -30
- Use fov 90 first
- Store image URLs and local image paths

8. AI Verification + Distress Scoring
First verify:
- Is the target property visible?
- Is the house centered/clear enough?
- Confidence 0–100

Only score distress if confidence >= 70.

Detect:
- boarded windows
- broken windows
- overgrown grass
- roof damage
- roof tarp
- peeling paint
- trash/debris
- abandoned vehicles
- broken fence
- vacancy signs
- general neglect

Return:
{
  "target_confidence": 0-100,
  "distress_score": 0-100,
  "visible_signs": [],
  "condition_summary": "",
  "recommended_action": "call_now | verify | skip"
}