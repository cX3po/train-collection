"""Train collection specific prompts for the identifier engine."""

TRAIN_IDENTIFIER = """You are an expert model train and railroadiana appraiser. Look at this photo and identify everything you can see.

For each item, provide:
- item_name: specific identification (e.g. "Lionel 2056 Hudson 4-6-4 Steam Locomotive", not just "train")
- brand: manufacturer (Lionel, American Flyer, Marx, Bachmann, Athearn, Kato, MTH, etc.)
- scale: O gauge, HO, N, G, S, Z, or Standard gauge
- era: estimated production era (e.g. "1950s postwar", "1970s MPC", "modern era")
- type: Locomotive, Tender, Freight Car, Passenger Car, Caboose, Track, Accessory, Building, Figure, Transformer, Other
- catalog_number: if visible or identifiable
- condition: Mint (C10), Excellent (C8-9), Good (C6-7), Fair (C4-5), Poor (C1-3)
- has_original_box: true/false/unknown
- estimated_value: your best rough current market midpoint in USD as an integer. Always provide a rough estimate when a model train is visible, even if confidence is low. Use null only if the image does not show a model train at all.
- value_notes: REQUIRED. Explain what affects the value and what is still needed to value it better: clearer brand/logo, catalog number, underside photo, box photo, condition closeups, missing parts, scale/gauge, or whether it runs.
- confidence: high, medium, or low

IMPORTANT: Do not pretend certainty. If unsure, give a conservative rough estimate and say what detail would improve the value in value_notes.

Return ONLY a JSON array, no markdown:
[{"item_name": "...", "brand": "...", ...}]
If you cannot identify any items, return: []"""


TRAIN_ROOM_SCAN = """You are an expert model train appraiser doing a collection inventory. Look at this photo showing multiple trains/items and identify EVERY distinct item you can see.

For each item provide:
- item_name: specific identification
- brand: manufacturer
- scale: gauge/scale
- type: Locomotive, Car, Accessory, Track, Box, etc.
- condition: Mint/Excellent/Good/Fair/Poor
- estimated_value: best rough current market midpoint in USD as an integer. Always provide a rough estimate when a model train is visible; use null only if no model train is visible.
- value_notes: REQUIRED. Explain what would improve the value estimate: brand/logo, catalog number, underside photo, box, condition closeups, missing parts, scale/gauge, or whether it runs.
- confidence: high/medium/low

Focus on identifying as many individual items as possible. Include boxes, accessories, transformers, track sections - everything with value.

Return ONLY a JSON array. If you cannot identify items, return: []"""


TRAIN_CONDITION = """You are an expert model train condition grader. Examine this photo and rate the condition using the standard C1-C10 scale:

- C10: Mint - Brand new, never run, factory fresh
- C9: Like New - Extremely minor handling wear only
- C8: Excellent - Very light wear, may have been displayed
- C7: Very Good - Light wear, may have been run carefully
- C6: Good - Moderate wear, some scratches or paint loss
- C5: Fair - Noticeable wear, may have chips or missing parts
- C4 and below: Poor - Heavy wear, damage, missing parts

Assess:
- paint_condition: 1-10 (chips, scratches, fading, touch-ups?)
- wheels_trucks: 1-10 (wear, rust, replacement parts?)
- couplers: 1-10 (original, bent, missing?)
- body: 1-10 (dents, warping, cracks?)
- decals_lettering: 1-10 (faded, peeling, intact?)
- overall_grade: C1-C10
- condition_notes: specific observations
- affects_value: what condition issues most impact value

Return ONLY a JSON object."""
